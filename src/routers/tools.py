"""The MCP tool surface: eleven tools, exactly one of which writes to disk.

Every function here is a thin adapter. It logs, resolves the process-wide
collaborators from `di`, and calls a tool function that knows nothing about MCP —
which is why the whole surface is testable without a client, and why swapping the
transport would not touch a line of the reasoning.

The docstrings are not documentation for us. They are the only thing a connected
model reads when deciding whether to call a tool and what to pass it, so they carry
the contract, the ordering, and the warnings.
"""

from pathlib import Path

import opik
from fastmcp import Context, FastMCP

from src.config.log_config import logger
from src.di import acquire_cache, acquire_memory_store
from src.domain.models import (
    ApplyResult,
    Decision,
    Distribution,
    EnrichedPackage,
    EnrichmentResult,
    LicenseAssessment,
    LicenseVerdict,
    MaintenanceAssessment,
    MaintenanceVerdict,
    Manifest,
    MemoryUpdateResult,
    Package,
    ProjectContext,
    Replacement,
    ReplacementSearch,
    SecurityAssessment,
    SecurityVerdict,
    TriagePlan,
    UpgradeProposal,
)
from src.routers.elicitation import build_approver
from src.tools.apply_plan import apply_plan_tool
from src.tools.assess_license import assess_license_tool
from src.tools.assess_maintenance import assess_maintenance_tool
from src.tools.assess_security import assess_security_tool
from src.tools.enrich_dependencies import enrich_dependencies_tool
from src.tools.find_replacement import find_replacement_tool
from src.tools.propose_upgrade_plan import propose_upgrade_plan_tool
from src.tools.recall_project_context import recall_project_context_tool
from src.tools.record_decision import record_decision_tool
from src.tools.scan_project import scan_project_tool
from src.tools.triage_dependencies import triage_dependencies_tool


def _names(names: list[str], limit: int = 15) -> str:
    """Render package names for a log line — truncated, never the full payload."""
    if len(names) <= limit:
        return ", ".join(names)
    return ", ".join(names[:limit]) + f", … (+{len(names) - limit} more)"


def register_mcp_tools(app: FastMCP) -> None:
    @app.tool
    @opik.track(type="tool")
    async def scan_project(project_path: Path) -> Manifest:
        """Scan a Python project directory and return its normalized dependency manifest.

        Locates pyproject.toml and uv.lock, parses all declared and locked dependencies, and returns a
        normalized manifest. Marks each dependency as direct or transitive, records
        the pinned version where one exists, and extracts the project's own declared
        license. Performs no network access. Call this first; its output is the
        input to enrich_dependencies.

        Args:
            project_path: Absolute path to the project root directory.

        Returns:
            A Manifest object listing all dependencies, the project's declared
            license, and which manifest files were found and parsed.

        Raises:
            FileNotFoundError: If no supported manifest file exists at the path.
        """
        logger.info(f"[TOOL] scan_project: project_path={project_path!r}")
        return scan_project_tool(project_path)

    @app.tool
    @opik.track(type="tool")
    async def enrich_dependencies(packages: list[Package]) -> EnrichmentResult:
        """Concurrently fetch security, registry, and repository data for a list of packages.

        For each package, queries OSV.dev (known vulnerabilities for the pinned
        version), the PyPI JSON API (release history, latest versions, license
        classifiers), and the GitHub REST API (last commit date, open issues,
        contributors, archived status) in parallel using asyncio.gather with a
        bounded semaphore. Returns raw, normalized facts without any assessment
        or recommendation. Results are cached in SQLite for 24 hours per
        package-version pair to avoid redundant network calls across runs.

        Args:
            packages: List of {name, version} objects, typically taken directly
                from the output of scan_project.

        Returns:
            An EnrichmentResult whose `enriched` entries contain vulnerabilities,
            available_versions, license_declared, and repo_health raw fields, plus
            per-package `errors` for any source that could not be reached.
        """
        logger.info(
            f"[TOOL] enrich_dependencies: {len(packages)} package(s) — "
            f"{_names([p.name for p in packages])}"
        )
        return await enrich_dependencies_tool(packages, cache=acquire_cache())

    @app.tool
    @opik.track(type="tool")
    async def assess_security(enriched: list[EnrichedPackage]) -> SecurityAssessment:
        """Assess the security posture of enriched packages using an LLM agent grounded in OSV advisories.

        For each package, determines which known vulnerabilities apply to the
        pinned version, rates contextual severity, identifies the minimal fixed
        version, and classifies the upgrade distance to that fix (patch, minor,
        major). Reasoning is grounded in the OSV advisory text gathered during
        enrichment. This tool characterizes risk only; it never recommends actions.
        Feed its output to triage_dependencies for action planning.

        Args:
            enriched: Output of enrich_dependencies (list of EnrichedPackage).

        Returns:
            A SecurityAssessment holding one SecurityVerdict per package with fields:
            package, applicable_cves, max_severity, fixed_in, upgrade_distance,
            evidence, and any corrections applied to the model's output.
        """
        logger.info(
            f"[TOOL] assess_security: {len(enriched)} package(s) — "
            f"{_names([p.name for p in enriched])}"
        )
        return await assess_security_tool(enriched)

    @app.tool
    @opik.track(type="tool")
    async def assess_license(
        enriched: list[EnrichedPackage],
        project_license: str | None,
        distribution: Distribution = Distribution.BINARY,
    ) -> LicenseAssessment:
        """Assess license compatibility of each dependency against the project's declared license.

        Loads the curated license-compatibility matrix into context (CAG) and grounds
        clause citations in retrieved SPDX license text (RAG) to determine, for each
        dependency, whether its license is compatible with the project's declared license
        and distribution model. Verdicts are 'compatible', 'incompatible', or
        'requires_human_review'. Ambiguous cases are always escalated to human review,
        never resolved automatically. This tool characterizes compatibility only; it
        never recommends actions. Feed its output to triage_dependencies.

        Args:
            enriched: Output of enrich_dependencies (list of EnrichedPackage).
            project_license: SPDX identifier of the project's own license, taken from
                the project_license field of scan_project output. Pass null when the
                project declares none; every package is then escalated to human review.
            distribution: How the project is shipped: 'binary', 'source', or 'saas'.
                Ask the user rather than assuming; it changes the answer for every
                copyleft dependency. Defaults to 'binary', under which the most
                obligations fire.

        Returns:
            A LicenseAssessment holding one LicenseVerdict per package with fields:
            package, license, verdict, conflicting_clause, suggested_alternatives, and
            any corrections applied to the model's output.
        """
        logger.info(
            f"[TOOL] assess_license: {len(enriched)} package(s), "
            f"project_license={project_license!r}, distribution={distribution} — "
            f"{_names([p.name for p in enriched])}"
        )
        return await assess_license_tool(enriched, project_license, distribution)

    @app.tool
    @opik.track(type="tool")
    async def assess_maintenance(enriched: list[EnrichedPackage]) -> MaintenanceAssessment:
        """Assess the maintenance health of each dependency from repository activity signals.

        Classifies each package as 'healthy', 'declining', 'abandoned' or 'unknown'
        from last commit and release recency, archived status, contributor count (bus
        factor), open-issue pressure and release count. The date arithmetic is computed
        before the model is asked, and a classification is never allowed to be more
        optimistic than the measurements permit — an archived repository is abandoned
        whatever the prose says. This tool characterizes maintenance state only; it
        never recommends actions. Feed its output to triage_dependencies.

        Args:
            enriched: Output of enrich_dependencies (list of EnrichedPackage).

        Returns:
            A MaintenanceAssessment holding one MaintenanceVerdict per package with
            fields: package, status, confidence, evidence, the measured signals, and
            any corrections applied to the model's output.
        """
        logger.info(
            f"[TOOL] assess_maintenance: {len(enriched)} package(s) — "
            f"{_names([p.name for p in enriched])}"
        )
        return await assess_maintenance_tool(enriched)

    @app.tool
    @opik.track(type="tool")
    async def triage_dependencies(
        security: list[SecurityVerdict],
        licenses: list[LicenseVerdict],
        maintenance: list[MaintenanceVerdict],
        project: str | None = None,
    ) -> TriagePlan:
        """Merge the three specialist verdicts into a single ranked triage plan.

        Acts as the manager agent over assess_security, assess_license and
        assess_maintenance. Routes every package into exactly one action branch —
        AUTO_UPGRADE, UPGRADE_BREAKING, REPLACE, HUMAN_REVIEW or NO_ACTION — by
        deterministic rules, then has the manager agent write the merged rationale.
        The manager may escalate an entry to HUMAN_REVIEW and may make no other change.
        Consults this project's procedural memory to annotate and rank entries. Every
        entry routed to REPLACE should be resolved with find_replacement before you
        build a proposal. Produces no diff and touches no files.

        Args:
            security: Output of assess_security (its `verdicts` list).
            licenses: Output of assess_license (its `verdicts` list).
            maintenance: Output of assess_maintenance (its `verdicts` list).
            project: Absolute project path, used to load learned preferences.

        Returns:
            A TriagePlan with one routed, ranked entry per package carrying the branch,
            the target version where one applies, the merged rationale, and any
            preference annotations.
        """
        logger.info(
            f"[TOOL] triage_dependencies: {len(security)} verdict(s), project={project!r} — "
            f"{_names([v.package for v in security])}"
        )
        return await triage_dependencies_tool(
            security, licenses, maintenance, project, store=acquire_memory_store()
        )

    @app.tool
    @opik.track(type="tool")
    async def find_replacement(
        package: str,
        context: str | None = None,
        reason: str | None = None,
        project: str | None = None,
    ) -> ReplacementSearch:
        """Find maintained replacement candidates for an abandoned or unfixable package using a ReAct loop.

        Iteratively reasons about what the package does, looks candidates up on PyPI,
        GitHub and deps.dev, observes what came back, and refines until it can name
        candidates it has actually verified (at most five iterations). Any candidate the
        loop did not verify is dropped and recorded in `corrections`. A replacement this
        project already validated short-circuits the search from memory. Read-only.

        Args:
            package: Name of the package to replace.
            context: How the project uses the package, if you know — it sharpens the
                compatibility judgment considerably.
            reason: Why it is being replaced, e.g. "archived since 2014".
            project: Absolute project path; enables the memory short-circuit.

        Returns:
            A ReplacementSearch with ranked candidates carrying name, license,
            maintenance_status, compatibility, migration_effort, evidence and dependent
            count, plus the reasoning trace that produced them.
        """
        logger.info(f"[TOOL] find_replacement: package={package!r}, reason={reason!r}")
        return await find_replacement_tool(
            package, context, reason, project, store=acquire_memory_store()
        )

    @app.tool
    @opik.track(type="tool")
    async def propose_upgrade_plan(
        triage: TriagePlan,
        project: str,
        replacements: list[Replacement] | None = None,
        ctx: Context = None,
    ) -> UpgradeProposal:
        """Render the triage plan as a reviewable unified diff and elicit per-package approval.

        Builds a unified diff of the project's dependency declaration reflecting all
        proposed changes, attaches per-package rationale and migration notes, persists
        the proposal under a unique proposal_id, and pauses to collect structured
        per-package approval from the user. Writes nothing to disk under any
        circumstances. Packages routed to HUMAN_REVIEW, REPLACE entries with no resolved
        replacement, and packages that exist only in the lockfile come back as
        `unresolved` rather than as changes. If your client cannot elicit, the proposal
        is returned marked 'pending_explicit_apply' — show the diff and the rationale to
        the user verbatim, and pass only what they explicitly approve to apply_plan.

        Args:
            triage: Output of triage_dependencies.
            project: Absolute path of the project the diff is computed against.
            replacements: REPLACE entries resolved via find_replacement — one entry per
                package, naming the replacement package and version.

        Returns:
            An UpgradeProposal with proposal_id, the unified diff, per-package rationale
            and migration notes, the unresolved items, and the approval decisions.
        """
        logger.info(
            f"[TOOL] propose_upgrade_plan: project={project!r}, "
            f"{len(triage.entries)} triage entry(ies), {len(replacements or [])} replacement(s)"
        )
        return await propose_upgrade_plan_tool(
            triage,
            project,
            replacements or [],
            store=acquire_memory_store(),
            approver=build_approver(ctx) if ctx is not None else None,
        )

    @app.tool
    @opik.track(type="tool")
    async def apply_plan(proposal_id: str, approved_packages: list[str]) -> ApplyResult:
        """Apply the approved subset of a reviewed upgrade proposal to the manifest file.

        THIS IS THE ONLY TOOL IN THIS SERVER THAT MODIFIES FILES. Verifies that
        proposal_id references a known, unapplied proposal, that every package named was
        approved during review (or, when your client could not elicit, that the user has
        explicitly approved it in conversation and you are naming it deliberately), and
        that the file on disk is unchanged since the proposal was generated. Backs the
        original up to <name>.molt.bak, applies only the approved changes, and records
        the outcome — approvals and rejections alike — to long-term memory. Aborts
        atomically with no partial writes on any mismatch. Never call it without a
        proposal_id from propose_upgrade_plan, and never with a package the user did not
        approve.

        Args:
            proposal_id: ID returned by propose_upgrade_plan.
            approved_packages: Exact names of the packages to apply. Pass an empty list
                if the user approved nothing.

        Returns:
            An ApplyResult with the backup path, the applied changes, what was skipped
            and why, and the memory updates the outcome produced. On any mismatch,
            status 'aborted' and an error explaining which check failed.
        """
        logger.info(
            f"[TOOL] apply_plan: proposal_id={proposal_id!r}, "
            f"{len(approved_packages)} approved — {_names(approved_packages)}"
        )
        return apply_plan_tool(proposal_id, approved_packages, acquire_memory_store())

    @app.tool
    @opik.track(type="tool")
    async def recall_project_context(project: str) -> ProjectContext:
        """Load all persisted memory about a project: past audits, learned preferences, and known package facts.

        Returns the episodic audit history for the project (findings, approvals,
        rejections, timestamps), the procedural preferences inferred from those
        decisions (e.g. 'avoid_major_bumps'), and the semantic package facts established
        in earlier sessions (validated replacement mappings, licenses a person has
        cleared). Read-only. Call it at the start of an audit so triage is conditioned
        on history rather than starting cold.

        Args:
            project: Absolute project path used as the memory key.

        Returns:
            A ProjectContext with episodic, procedural and semantic sections; all
            sections empty for a project that has never been audited.
        """
        logger.info(f"[TOOL] recall_project_context: project={project!r}")
        return recall_project_context_tool(project, acquire_memory_store())

    @app.tool
    @opik.track(type="tool")
    async def record_decision(project: str, decision: Decision) -> MemoryUpdateResult:
        """Record a user decision into long-term memory and update inferred preferences.

        Appends the decision to the project's episodic log, stores any durable package
        fact it implies (an accepted replacement, a license a person cleared), and
        re-derives the project's preference rules from the whole history. apply_plan
        calls this automatically; call it directly to record what happened outside an
        apply — most importantly the resolution of a HUMAN_REVIEW item, e.g. "legal
        cleared chardet under LGPL-2.1, do not flag it again for this project".

        Args:
            project: Absolute project path used as the memory key.
            decision: The decision — package, action ('approved', 'rejected',
                'resolved_human_review' or 'deferred'), the triage branch it was taken
                on, the user's reason in their own words, and a replacement package name
                where one was accepted.

        Returns:
            A MemoryUpdateResult listing which memory stores changed and every
            preference rule that was created, strengthened, weakened or retired.
        """
        logger.info(
            f"[TOOL] record_decision: project={project!r}, "
            f"package={decision.package!r}, action={decision.action}"
        )
        return record_decision_tool(project, decision, acquire_memory_store())
