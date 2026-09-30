"""The manager: five branches, one per package, and the ranking the user reads.

Routing is deterministic and the rationale is not, which is the same division this
codebase makes everywhere. Which branch a package lands on decides whether a file
gets edited, so it follows from the specialists' verdicts by rules a test can pin
down; *why* it landed there is prose, and prose is what the model is for.

The manager may move a package to HUMAN_REVIEW and to nothing else. An agent that
talks a package down from REPLACE to AUTO_UPGRADE would be editing a manifest on the
strength of an argument nobody checked.
"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel

from src.agents.outcome import no_structure_reason
from src.agents.triage_agent import build_triage_agent, render_plan
from src.config.llm_config import chat_model_for
from src.config.log_config import logger
from src.constants import TRIAGE_DEPENDENCIES
from src.domain.models import (
    LicenseCompatibility,
    LicenseVerdict,
    MaintenanceStatus,
    MaintenanceVerdict,
    ProceduralRule,
    SecurityVerdict,
    Severity,
    TriageBranch,
    TriageEntry,
    TriagePlan,
    TriagePlanDraft,
    UpgradeDistance,
)
from src.memory.procedural import (
    AVOID_MAJOR_BUMPS,
    AVOID_REPLACEMENTS,
    TRUSTS_AUTO_UPGRADES,
    cleared_packages,
    rule_named,
)
from src.memory.store import MemoryStore, project_key

# How cautious a branch is. The manager may raise an entry to HUMAN_REVIEW; every
# other change to a routed branch is reverted and recorded.
CAUTION: dict[TriageBranch, int] = {
    TriageBranch.NO_ACTION: 0,
    TriageBranch.AUTO_UPGRADE: 1,
    TriageBranch.UPGRADE_BREAKING: 2,
    TriageBranch.REPLACE: 3,
    TriageBranch.HUMAN_REVIEW: 4,
}

# Reading order in the proposal: what can be applied now, then what needs a decision,
# then what needs nothing at all.
BRANCH_ORDER: dict[TriageBranch, int] = {
    TriageBranch.AUTO_UPGRADE: 0,
    TriageBranch.UPGRADE_BREAKING: 1,
    TriageBranch.REPLACE: 2,
    TriageBranch.HUMAN_REVIEW: 3,
    TriageBranch.NO_ACTION: 4,
}

SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 0,
    Severity.HIGH: 1,
    Severity.MODERATE: 2,
    Severity.LOW: 3,
    Severity.UNKNOWN: 4,
    Severity.NONE: 5,
}

LIKELY_REJECTED = "likely_to_be_rejected: {why}"
LIKELY_APPROVED = "likely_to_be_approved: {why}"
LICENSE_CLEARED = (
    "license_cleared: a recorded decision cleared this package's license for this "
    "project, so the license flag was suppressed ({why})"
)
NO_ACTION_RATIONALE = "No applicable advisories, no license conflict, no maintenance concern."


class Verdicts:
    """The three specialist opinions for one package, with the absent ones absent."""

    def __init__(
        self,
        security: SecurityVerdict | None,
        license: LicenseVerdict | None,
        maintenance: MaintenanceVerdict | None,
    ) -> None:
        self.security = security
        self.license = license
        self.maintenance = maintenance

    @property
    def failed(self) -> list[str]:
        return [
            name
            for name, verdict in (
                ("security", self.security),
                ("license", self.license),
                ("maintenance", self.maintenance),
            )
            if verdict is not None and verdict.error
        ]

    @property
    def version(self) -> str:
        for verdict in (self.security, self.license, self.maintenance):
            if verdict is not None:
                return verdict.version
        return ""

    @property
    def has_unfixable_vulnerability(self) -> bool:
        return bool(
            self.security and self.security.applicable_cves and not self.security.fixed_in
        )

    @property
    def fix(self) -> str | None:
        return self.security.fixed_in if self.security and self.security.applicable_cves else None


def collect(
    security: Sequence[SecurityVerdict],
    licenses: Sequence[LicenseVerdict],
    maintenance: Sequence[MaintenanceVerdict],
) -> dict[str, Verdicts]:
    """One entry per package named by any specialist, in first-seen order."""
    by_security = {v.package: v for v in security}
    by_license = {v.package: v for v in licenses}
    by_maintenance = {v.package: v for v in maintenance}
    names = list(dict.fromkeys([*by_security, *by_license, *by_maintenance]))
    return {
        name: Verdicts(by_security.get(name), by_license.get(name), by_maintenance.get(name))
        for name in names
    }


def route(verdicts: Verdicts, license_cleared: bool = False) -> tuple[TriageBranch, str | None]:
    """The routing rules, in precedence order, and the target version each implies.

    Precedence is what makes the branches exclusive: a package that is both abandoned
    and license-ambiguous is a decision for a person, not an automatic replacement, so
    HUMAN_REVIEW is tested first and NO_ACTION is what is left over.
    """
    license = verdicts.license
    review = license is not None and license.verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    blocked = license is not None and license.verdict == LicenseCompatibility.INCOMPATIBLE

    if verdicts.failed:
        return TriageBranch.HUMAN_REVIEW, None
    if review and not license_cleared:
        return TriageBranch.HUMAN_REVIEW, None
    if blocked and not license_cleared:
        return TriageBranch.REPLACE, None
    if verdicts.maintenance and verdicts.maintenance.status == MaintenanceStatus.ABANDONED:
        return TriageBranch.REPLACE, None
    if verdicts.has_unfixable_vulnerability:
        return TriageBranch.REPLACE, None
    if fix := verdicts.fix:
        distance = verdicts.security.upgrade_distance
        if distance == UpgradeDistance.MAJOR:
            return TriageBranch.UPGRADE_BREAKING, fix
        if distance in (UpgradeDistance.PATCH, UpgradeDistance.MINOR):
            return TriageBranch.AUTO_UPGRADE, fix
        return TriageBranch.HUMAN_REVIEW, fix
    return TriageBranch.NO_ACTION, None


def base_entry(package: str, verdicts: Verdicts, cleared: set[str]) -> TriageEntry:
    is_cleared = package in cleared
    branch, target = route(verdicts, license_cleared=is_cleared)
    return TriageEntry(
        package=package,
        version=verdicts.version,
        branch=branch,
        target=target,
        max_severity=verdicts.security.max_severity if verdicts.security else Severity.UNKNOWN,
        license_verdict=verdicts.license.verdict if verdicts.license else None,
        maintenance_status=(
            verdicts.maintenance.status if verdicts.maintenance else MaintenanceStatus.UNKNOWN
        ),
        upgrade_distance=(
            verdicts.security.upgrade_distance if verdicts.security else UpgradeDistance.UNKNOWN
        ),
        rationale=NO_ACTION_RATIONALE if branch == TriageBranch.NO_ACTION else "",
    )


def annotate(entry: TriageEntry, rules: list[ProceduralRule], cleared: set[str]) -> TriageEntry:
    """Learned preferences change how an entry is presented, never what it is.

    A preference is evidence about the user, not about the package, so it annotates
    and re-ranks. The one exception is a license clearance, which is not a preference
    at all but a decision the user recorded — and that one *is* allowed to move a
    branch, loudly.
    """
    for name, template in (
        (AVOID_MAJOR_BUMPS, LIKELY_REJECTED),
        (AVOID_REPLACEMENTS, LIKELY_REJECTED),
        (TRUSTS_AUTO_UPGRADES, LIKELY_APPROVED),
    ):
        rule = rule_named(rules, name)
        if rule is None or entry.branch != _branch_of(name):
            continue
        entry.annotations.append(
            template.format(why=f"{rule.rule} at {rule.confidence}, from {len(rule.derived_from)} past decisions")
        )
    if entry.package in cleared and entry.license_verdict == LicenseCompatibility.REQUIRES_HUMAN_REVIEW:
        rule = rule_named(rules, f"license_cleared:{entry.package}")
        entry.annotations.append(
            LICENSE_CLEARED.format(why=", ".join(rule.derived_from) if rule else "recorded decision")
        )
    return entry


def _branch_of(rule_name: str) -> TriageBranch:
    return {
        AVOID_MAJOR_BUMPS: TriageBranch.UPGRADE_BREAKING,
        AVOID_REPLACEMENTS: TriageBranch.REPLACE,
        TRUSTS_AUTO_UPGRADES: TriageBranch.AUTO_UPGRADE,
    }[rule_name]


def rank(entries: list[TriageEntry]) -> list[TriageEntry]:
    """Actionable first, worst first, and anything the user habitually rejects last."""
    return sorted(
        entries,
        key=lambda entry: (
            any(a.startswith("likely_to_be_rejected") for a in entry.annotations),
            BRANCH_ORDER[entry.branch],
            SEVERITY_ORDER.get(entry.max_severity, 4),
            entry.package,
        ),
    )


def verify(entries: list[TriageEntry], draft: TriagePlanDraft) -> list[TriageEntry]:
    """Takes the manager's prose, and only the branch changes it was allowed to make."""
    drafted = {entry.package: entry for entry in draft.entries}
    routed = {entry.package for entry in entries}
    for entry in entries:
        written = drafted.get(entry.package)
        if written is None:
            entry.corrections.append("the manager wrote no entry for this package")
            continue
        entry.rationale = written.rationale.strip() or entry.rationale
        if written.branch == entry.branch:
            continue
        if CAUTION[written.branch] > CAUTION[entry.branch] and written.branch == TriageBranch.HUMAN_REVIEW:
            entry.branch = TriageBranch.HUMAN_REVIEW
            entry.target = None
            continue
        entry.corrections.append(
            f"reverted branch '{written.branch}' to the routed '{entry.branch}': the manager may "
            f"only escalate to HUMAN_REVIEW"
        )
    if invented := [name for name in drafted if name not in routed]:
        for entry in entries[:1]:
            entry.corrections.append(f"ignored entries for unrouted packages: {', '.join(invented)}")
    return entries


def load_rules(store: MemoryStore | None, project: str | None) -> list[ProceduralRule]:
    if store is None or project is None:
        return []
    return store.rules(project_key(project))


async def triage_dependencies_tool(
    security: Sequence[SecurityVerdict],
    licenses: Sequence[LicenseVerdict],
    maintenance: Sequence[MaintenanceVerdict],
    project: str | None = None,
    model: BaseChatModel | None = None,
    store: MemoryStore | None = None,
) -> TriagePlan:
    """Merge the three specialist verdicts into a single ranked triage plan.

    Acts as the manager agent over assess_security, assess_license and
    assess_maintenance. Routes every package into exactly one action branch —
    AUTO_UPGRADE, UPGRADE_BREAKING, REPLACE, HUMAN_REVIEW or NO_ACTION — by
    deterministic rules, then asks the manager agent to write the merged rationale.
    The manager may escalate an entry to HUMAN_REVIEW and may make no other change;
    anything else it attempts is reverted and recorded. Consults procedural memory for
    this project to annotate and rank entries. Packages routed to REPLACE should be
    resolved via find_replacement before building a proposal. Produces no diff and
    touches no files; feed its output to propose_upgrade_plan.

    Args:
        security: Output of assess_security (list of SecurityVerdict).
        licenses: Output of assess_license (list of LicenseVerdict).
        maintenance: Output of assess_maintenance (list of MaintenanceVerdict).
        project: Project path, used to load procedural memory.
        model: Chat model to use; defaults to the model configured for this tool.
        store: Long-term memory store; defaults to none, disabling preferences.

    Returns:
        A TriagePlan with one routed, ranked entry per package, each carrying the
        branch, the target version where one applies, the merged rationale, any
        preference annotations, and any corrections applied to the manager's output.
    """
    verdicts = collect(security, licenses, maintenance)
    if not verdicts:
        return TriagePlan(project=project)
    rules = load_rules(store, project)
    cleared = cleared_packages(rules)
    entries = [
        annotate(base_entry(package, packaged, cleared), rules, cleared)
        for package, packaged in verdicts.items()
    ]
    logger.info(f"Triaged {len(entries)} packages into {len({e.branch for e in entries})} branches")

    actionable = [entry for entry in entries if entry.branch != TriageBranch.NO_ACTION]
    if not actionable:
        # Nothing to explain: every package is clean, and the rationale is the same
        # sentence for all of them. Asking a model for it would be theatre.
        return TriagePlan(project=project, entries=rank(entries))

    model = model or chat_model_for(TRIAGE_DEPENDENCIES)
    agent = build_triage_agent(model)
    rendered = render_plan(
        project, entries, {name: (v.security, v.license, v.maintenance) for name, v in verdicts.items()}
    )
    try:
        result = await agent.ainvoke({"messages": [{"role": "user", "content": rendered}]})
    except Exception as e:
        logger.warning(f"Triage rationale generation failed: {e}")
        return TriagePlan(project=project, entries=rank(entries), error=f"{type(e).__name__}: {e}")
    draft = result.get("structured_response")
    if not isinstance(draft, TriagePlanDraft):
        return TriagePlan(
            project=project,
            entries=rank(entries),
            error=f"ValueError: {no_structure_reason(result, 'structured plan')}",
        )
    return TriagePlan(project=project, entries=rank(verify(entries, draft)))
