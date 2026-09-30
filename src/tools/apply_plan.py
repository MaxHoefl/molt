"""The only tool in this server that writes to disk.

Everything else is read-only, which means the safety property is structural rather
than a convention a model is asked to honour: there is exactly one function that can
change a file, and it refuses to run without a proposal that was generated,
persisted, and reviewed first.

Four checks stand between a call and a write, and all four abort before anything is
touched:

1. the proposal exists in the store,
2. it has not already been applied,
3. every requested package was approved during review (or, in the degraded
   no-elicitation path, was part of the proposal and is being named deliberately now),
4. the file on disk still hashes to what the diff was computed against.

The fourth is the one that earns its keep: a proposal is a claim about a file at a
moment, and a file someone edited in between is a different file.
"""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from src.config.log_config import logger
from src.constants import BACKUP_SUFFIX
from src.domain.models import (
    AppliedChange,
    ApplyResult,
    Approval,
    ApprovalStatus,
    AuditRecord,
    ChangeKind,
    Decision,
    DecisionAction,
    ProposedChange,
    UpgradeProposal,
)
from src.manifest import ManifestFile, PyProjectFile, RequirementsFile, content_hash
from src.memory.store import MemoryStore, project_key
from src.tools.propose_upgrade_plan import edit_for
from src.tools.record_decision import record_decision_tool

UNKNOWN_PROPOSAL = "No proposal {proposal_id} is on record; run propose_upgrade_plan first."
ALREADY_APPLIED = "Proposal {proposal_id} has already been applied."
NOT_APPROVED = "Not approved during review: {packages}. Nothing was applied."
NOT_IN_PROPOSAL = "Not part of proposal {proposal_id}: {packages}. Nothing was applied."
MANIFEST_MOVED = (
    "{manifest} has changed since proposal {proposal_id} was generated, so its diff no longer "
    "describes this file. Nothing was applied; re-run the audit."
)
MANIFEST_GONE = "{manifest} no longer exists. Nothing was applied."
REJECTED_DURING_REVIEW = "{package} (rejected during review)"
NOT_REQUESTED = "{package} (not among the approved packages passed to apply_plan)"
STILL_OPEN = "{item}"


def manifest_for(path: str) -> ManifestFile:
    file = Path(path)
    return RequirementsFile(file) if file.name == "requirements.txt" else PyProjectFile(file)


def failure(proposal_id: str, error: str) -> ApplyResult:
    logger.warning(f"apply_plan aborted: {error}")
    return ApplyResult(status="aborted", proposal_id=proposal_id, error=error)


def unauthorized(proposal: UpgradeProposal, requested: Sequence[str]) -> str | None:
    """Which of the requested packages this proposal does not authorize, if any."""
    known = {change.package for change in proposal.changes}
    if unknown := [name for name in requested if name not in known]:
        return NOT_IN_PROPOSAL.format(
            proposal_id=proposal.proposal_id, packages=", ".join(sorted(unknown))
        )
    if proposal.approval_status == ApprovalStatus.PENDING_EXPLICIT_APPLY:
        # Nobody was asked, so naming the packages here *is* the approval — which is
        # why this path requires a separate, deliberate call rather than a default.
        return None
    approved = set(proposal.approved_packages())
    if refused := [name for name in requested if name not in approved]:
        return NOT_APPROVED.format(packages=", ".join(sorted(refused)))
    return None


def applied_change(change: ProposedChange) -> AppliedChange:
    return AppliedChange(
        package=change.package,
        kind=change.kind,
        from_version=change.from_version,
        to=(
            f"{change.to_package}=={change.to_version}"
            if change.kind == ChangeKind.REPLACE
            else change.to_version
        ),
    )


def skipped_lines(proposal: UpgradeProposal, requested: set[str]) -> list[str]:
    skipped = [
        (
            REJECTED_DURING_REVIEW.format(package=change.package)
            if proposal.approvals.get(change.package) == Approval.REJECTED
            else NOT_REQUESTED.format(package=change.package)
        )
        for change in proposal.changes
        if change.package not in requested
    ]
    return skipped + [STILL_OPEN.format(item=item) for item in proposal.unresolved]


def back_up(manifest: ManifestFile, text: str) -> Path:
    backup = manifest.path.with_name(manifest.path.name + BACKUP_SUFFIX)
    backup.write_text(text, encoding="utf-8")
    return backup


def remember(
    store: MemoryStore,
    proposal: UpgradeProposal,
    applied: list[ProposedChange],
    requested: set[str],
    now: datetime,
) -> list[str]:
    """Every change in the proposal becomes an episode, applied or not.

    A rejection is the more informative half: `avoid_major_bumps` exists because the
    user said no three times, and a memory that only recorded the yeses could never
    have learned it.
    """
    updates: list[str] = []
    for change in proposal.changes:
        decision = Decision(
            package=change.package,
            action=(
                DecisionAction.APPROVED
                if change.package in requested
                else DecisionAction.REJECTED
                if proposal.approvals.get(change.package) == Approval.REJECTED
                else DecisionAction.DEFERRED
            ),
            branch=change.branch,
            reason=change.rationale,
            replacement=(
                change.to_package
                if change.kind == ChangeKind.REPLACE and change.package in requested
                else None
            ),
        )
        result = record_decision_tool(proposal.project, decision, store, now)
        updates += [f"{store_name}: {decision.package} {decision.action.value}" for store_name in result.updated]
    store.put_audit(
        AuditRecord(
            date=now.isoformat(),
            proposal_id=proposal.proposal_id,
            project=project_key(proposal.project),
            summary={
                "proposed": len(proposal.changes),
                "applied": len(applied),
                "skipped": len(proposal.changes) - len(applied),
                "open": len(proposal.unresolved),
            },
            open_items=[{"note": item} for item in proposal.unresolved],
            diff=proposal.diff,
        )
    )
    return updates


def apply_plan_tool(
    proposal_id: str,
    approved_packages: Sequence[str],
    store: MemoryStore,
    now: datetime | None = None,
) -> ApplyResult:
    """Apply the approved subset of a reviewed upgrade proposal to the manifest file.

    Verifies that proposal_id references a known, unapplied proposal, that every
    package in approved_packages was approved during review (or, when the client could
    not elicit, that it was part of the proposal and is being named deliberately now),
    and that the file on disk is unchanged since the proposal was generated (content
    hash check). Backs the original up to <name>.molt.bak, applies only the approved
    changes, and records the outcome — approvals and rejections alike — to episodic,
    semantic and procedural memory. Aborts atomically with no partial writes on any
    mismatch. This is the only tool in this server that modifies files.

    Args:
        proposal_id: ID returned by propose_upgrade_plan.
        approved_packages: Exact names of the packages to apply; must be a subset of
            the packages the proposal covers, and of those approved during review.
        store: Long-term memory store holding the proposal.
        now: Timestamp for the memory entries; defaults to the current time.

    Returns:
        An ApplyResult with the backup path, the applied changes, what was skipped and
        why, and the memory updates the outcome produced. On any mismatch, status
        'aborted' and an error explaining which check failed.
    """
    proposal = store.proposal(proposal_id)
    if proposal is None:
        return failure(proposal_id, UNKNOWN_PROPOSAL.format(proposal_id=proposal_id))
    if proposal.applied:
        return failure(proposal_id, ALREADY_APPLIED.format(proposal_id=proposal_id))

    requested = list(dict.fromkeys(approved_packages))
    if error := unauthorized(proposal, requested):
        return failure(proposal_id, error)

    manifest = manifest_for(proposal.manifest_path)
    if not manifest.path.exists():
        return failure(proposal_id, MANIFEST_GONE.format(manifest=proposal.manifest_path))
    before = manifest.read()
    if content_hash(before) != proposal.manifest_hash:
        return failure(
            proposal_id,
            MANIFEST_MOVED.format(manifest=proposal.manifest_path, proposal_id=proposal_id),
        )

    wanted = set(requested)
    changes = [change for change in proposal.changes if change.package in wanted]
    after = manifest.apply([edit_for(change) for change in changes], before)
    backup = back_up(manifest, before)
    manifest.write(after)

    proposal.applied = True
    store.put_proposal(proposal)
    updates = remember(store, proposal, changes, wanted, now or datetime.now(UTC))
    logger.info(f"Applied {len(changes)} changes from {proposal_id} to {manifest.path}")
    return ApplyResult(
        status="applied",
        proposal_id=proposal_id,
        manifest_path=str(manifest.path),
        backup_path=str(backup),
        applied=[applied_change(change) for change in changes],
        skipped=skipped_lines(proposal, wanted),
        memory_updates=updates,
    )
