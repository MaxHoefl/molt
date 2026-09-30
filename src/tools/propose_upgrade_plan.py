"""Generation, then validation — and nothing on disk in between.

This tool produces the artifact the whole server exists to hand a person: a diff of
the file they would have edited themselves, with a reason beside every line. It
writes nothing. Its only side effect is storing the proposal, and storing it is what
makes `apply_plan` able to insist that a disk write was reviewed first.

The human checkpoint is `ctx.elicit()` in the MCP layer, injected here as an
`approver` so the decision path is testable without a client. When the client cannot
elicit, the proposal comes back `pending_explicit_apply`: the pause is gone, the
requirement that a second deliberate call names the packages is not.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime

from src.config.log_config import logger
from src.domain.models import (
    Approval,
    ApprovalStatus,
    ChangeKind,
    ProposedChange,
    Replacement,
    TriageBranch,
    TriageEntry,
    TriagePlan,
    UpgradeProposal,
)
from src.manifest import Edit, ManifestFile, content_hash, locate_manifest, proposal_id, render_diff
from src.memory.store import MemoryStore

Approver = Callable[[UpgradeProposal], Awaitable[dict[str, Approval] | None]]

CHANGED_BRANCHES = (TriageBranch.AUTO_UPGRADE, TriageBranch.UPGRADE_BREAKING, TriageBranch.REPLACE)
NOT_CHANGED = "NOT CHANGED — {rationale}"
UNRESOLVED_REPLACEMENT = (
    "{package}: routed to REPLACE but no replacement was supplied; run find_replacement first."
)
NEEDS_A_PERSON = "{package}: {rationale}"
NOT_DECLARED = (
    "{package} is not declared in {manifest} (it is a transitive dependency), so it cannot be "
    "pinned there; upgrade the package that requires it instead."
)
BREAKING_NOTES = (
    "Crosses a major version boundary ({current} -> {target}). Review the changelog and every "
    "call site you use before applying."
)
REPLACEMENT_NOTES = "Swaps {package} for {replacement}. {compatibility}"


def migration_notes(entry: TriageEntry, replacement: Replacement | None) -> str:
    if entry.branch == TriageBranch.REPLACE and replacement is not None:
        return REPLACEMENT_NOTES.format(
            package=entry.package,
            replacement=replacement.name,
            compatibility=replacement.compatibility or "Compatibility was not established.",
        ).strip()
    if entry.branch == TriageBranch.UPGRADE_BREAKING:
        return BREAKING_NOTES.format(current=entry.version, target=entry.target)
    return ""


def change_for(entry: TriageEntry, replacement: Replacement | None) -> ProposedChange | None:
    """One triage entry as a manifest edit, or None when it is not one."""
    if entry.branch == TriageBranch.REPLACE:
        if replacement is None:
            return None
        return ProposedChange(
            package=entry.package,
            kind=ChangeKind.REPLACE,
            from_version=entry.version,
            to_package=replacement.name,
            to_version=replacement.version,
            branch=entry.branch,
            rationale=entry.rationale,
            migration_notes=migration_notes(entry, replacement),
        )
    if entry.branch in (TriageBranch.AUTO_UPGRADE, TriageBranch.UPGRADE_BREAKING) and entry.target:
        return ProposedChange(
            package=entry.package,
            kind=ChangeKind.UPGRADE,
            from_version=entry.version,
            to_version=entry.target,
            branch=entry.branch,
            rationale=entry.rationale,
            migration_notes=migration_notes(entry, None),
        )
    return None


def edit_for(change: ProposedChange) -> Edit:
    return Edit(
        package=change.package,
        version=change.to_version,
        to_package=change.to_package if change.kind == ChangeKind.REPLACE else None,
    )


def partition(
    entries: Sequence[TriageEntry], replacements: dict[str, Replacement], manifest: ManifestFile
) -> tuple[list[ProposedChange], list[str], list[str]]:
    """Splits the plan into what can be changed, what needs a person, and what cannot be reached."""
    changes: list[ProposedChange] = []
    unresolved: list[str] = []
    warnings: list[str] = []
    for entry in entries:
        change = change_for(entry, replacements.get(entry.package))
        if change is None:
            if entry.branch == TriageBranch.REPLACE:
                unresolved.append(UNRESOLVED_REPLACEMENT.format(package=entry.package))
            elif entry.branch == TriageBranch.HUMAN_REVIEW:
                unresolved.append(
                    NEEDS_A_PERSON.format(package=entry.package, rationale=entry.rationale)
                )
            continue
        if not manifest.declares(entry.package):
            warnings.append(NOT_DECLARED.format(package=entry.package, manifest=manifest.name))
            unresolved.append(NOT_DECLARED.format(package=entry.package, manifest=manifest.name))
            continue
        changes.append(change)
    return changes, unresolved, warnings


def rationales(entries: Sequence[TriageEntry], changed: set[str]) -> dict[str, str]:
    """Every package gets a line, including the ones deliberately left alone."""
    return {
        entry.package: (
            entry.rationale
            if entry.package in changed
            else NOT_CHANGED.format(rationale=entry.rationale or entry.branch.value)
        )
        for entry in entries
    }


async def collect_approvals(
    proposal: UpgradeProposal, approver: Approver | None
) -> tuple[dict[str, Approval], ApprovalStatus]:
    """Asks the user package by package, or admits that nobody was asked.

    A client without elicitation does not get an implicit yes. It gets a proposal
    marked pending, which `apply_plan` will only act on if a later call names the
    packages explicitly — the safety property survives the missing UX.
    """
    pending = {change.package: Approval.PENDING for change in proposal.changes}
    if approver is None:
        return pending, ApprovalStatus.PENDING_EXPLICIT_APPLY
    try:
        answered = await approver(proposal)
    except Exception as e:
        logger.warning(f"Elicitation failed for {proposal.proposal_id}: {e}")
        return pending, ApprovalStatus.PENDING_EXPLICIT_APPLY
    if not answered:
        return pending, ApprovalStatus.PENDING_EXPLICIT_APPLY
    return pending | {
        package: decision for package, decision in answered.items() if package in pending
    }, ApprovalStatus.REVIEWED


async def propose_upgrade_plan_tool(
    triage: TriagePlan,
    project: str,
    replacements: Sequence[Replacement] = (),
    store: MemoryStore | None = None,
    approver: Approver | None = None,
    now: datetime | None = None,
) -> UpgradeProposal:
    """Render the triage plan as a reviewable unified diff and elicit per-package approval.

    Builds a unified diff of the project's dependency declaration reflecting all
    proposed changes, attaches per-package rationale and migration notes, persists the
    proposal under a unique proposal_id, and pauses to collect structured per-package
    approval from the user. Writes nothing to disk under any circumstances. Packages
    routed to HUMAN_REVIEW, REPLACE entries with no resolved replacement, and packages
    that exist only in the lockfile are reported as unresolved rather than changed. If
    the client cannot elicit, the proposal is returned marked 'pending_explicit_apply'
    instead. The returned proposal_id and approved package list are required inputs to
    apply_plan.

    Args:
        triage: Output of triage_dependencies.
        project: Project path whose declaration the diff is computed against.
        replacements: REPLACE entries resolved via find_replacement, one per package.
        store: Long-term memory store the proposal is persisted to.
        approver: Callable that presents the proposal and returns per-package
            decisions; omitted when the client cannot elicit.
        now: Timestamp for the proposal; defaults to the current time.

    Returns:
        An UpgradeProposal containing proposal_id, the unified diff, per-package
        rationale and migration notes, the unresolved items, and the user's
        per-package approval decisions (or a pending status when nobody was asked).
    """
    manifest = locate_manifest(project)
    before = manifest.read()
    resolved = {replacement.package: replacement for replacement in replacements}
    changes, unresolved, warnings = partition(triage.entries, resolved, manifest)
    after = manifest.apply([edit_for(change) for change in changes], before)
    created_at = (now or datetime.now(UTC)).isoformat()

    proposal = UpgradeProposal(
        proposal_id=proposal_id(f"{project}|{created_at}|{len(changes)}"),
        project=str(project),
        manifest_path=str(manifest.path),
        manifest_hash=content_hash(before),
        created_at=created_at,
        diff=render_diff(before, after, manifest.name),
        changes=changes,
        rationale=rationales(triage.entries, {change.package for change in changes}),
        unresolved=unresolved,
        warnings=warnings,
    )
    proposal.approvals, proposal.approval_status = await collect_approvals(proposal, approver)
    logger.info(
        f"Proposed {len(changes)} changes for {project} as {proposal.proposal_id} "
        f"({proposal.approval_status.value})"
    )
    if store is not None:
        store.put_proposal(proposal)
    return proposal
