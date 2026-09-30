from datetime import UTC, datetime

import pytest

from src.domain.models import (
    Approval,
    ApprovalStatus,
    ChangeKind,
    Replacement,
    Severity,
    TriageBranch,
    TriageEntry,
    TriagePlan,
    UpgradeDistance,
)
from src.memory.store import InMemoryStore
from src.tools.propose_upgrade_plan import propose_upgrade_plan_tool

PYPROJECT = """\
[project]
name = "demo"
dependencies = [
    "urllib3==2.0.4",
    "paramiko==2.7.2",
    "pycrypto==2.6.1",
    "chardet==5.0.0",
]
"""

NOW = datetime(2026, 9, 1, tzinfo=UTC)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


def entry(
    package,
    branch,
    target=None,
    version="2.0.4",
    rationale="because the specialists said so",
    distance=UpgradeDistance.PATCH,
) -> TriageEntry:
    return TriageEntry(
        package=package,
        version=version,
        branch=branch,
        target=target,
        rationale=rationale,
        upgrade_distance=distance,
        max_severity=Severity.MODERATE,
    )


def plan(*entries) -> TriagePlan:
    return TriagePlan(project="demo", entries=list(entries))


async def propose(project, *entries, **kwargs):
    return await propose_upgrade_plan_tool(plan(*entries), str(project), now=NOW, **kwargs)


UPGRADE = entry("urllib3", TriageBranch.AUTO_UPGRADE, "2.0.7")
BREAKING = entry(
    "paramiko", TriageBranch.UPGRADE_BREAKING, "3.4.0", "2.7.2", distance=UpgradeDistance.MAJOR
)
REPLACE = entry("pycrypto", TriageBranch.REPLACE, version="2.6.1")
REVIEW = entry(
    "chardet", TriageBranch.HUMAN_REVIEW, version="5.0.0", rationale="LGPL-2.1 in a binary."
)
PYCRYPTODOME = Replacement(
    package="pycrypto",
    name="pycryptodome",
    version="3.20.0",
    compatibility="Drop-in for the Crypto.* namespace.",
)


# --- the diff ----------------------------------------------------------------


async def test_writes_nothing_to_disk(project):
    await propose(project, UPGRADE)

    assert (project / "pyproject.toml").read_text() == PYPROJECT


async def test_renders_the_upgrade_as_a_diff(project):
    proposal = await propose(project, UPGRADE)

    assert '-    "urllib3==2.0.4",' in proposal.diff
    assert '+    "urllib3==2.0.7",' in proposal.diff


async def test_renders_a_replacement_as_a_diff(project):
    proposal = await propose(project, REPLACE, replacements=[PYCRYPTODOME])

    assert '+    "pycryptodome==3.20.0",' in proposal.diff


async def test_leaves_a_package_awaiting_review_out_of_the_diff(project):
    proposal = await propose(project, REVIEW)

    assert proposal.diff == ""


async def test_renders_every_approved_branch_in_one_diff(project):
    proposal = await propose(project, UPGRADE, BREAKING, REPLACE, replacements=[PYCRYPTODOME])

    added = [line for line in proposal.diff.splitlines() if line.startswith("+    ")]
    assert added == ['+    "urllib3==2.0.7",', '+    "paramiko==3.4.0",', '+    "pycryptodome==3.20.0",']


async def test_records_the_hash_of_the_file_the_diff_was_computed_against(project):
    proposal = await propose(project, UPGRADE)

    assert len(proposal.manifest_hash) == 64


# --- what becomes a change and what does not ---------------------------------


async def test_a_patch_upgrade_becomes_a_change(project):
    proposal = await propose(project, UPGRADE)

    assert [c.package for c in proposal.changes] == ["urllib3"]
    assert proposal.changes[0].to_version == "2.0.7"


async def test_a_replacement_becomes_a_change_naming_both_packages(project):
    proposal = await propose(project, REPLACE, replacements=[PYCRYPTODOME])

    change = proposal.changes[0]
    assert (change.kind, change.package, change.to_package) == (
        ChangeKind.REPLACE,
        "pycrypto",
        "pycryptodome",
    )


async def test_an_unresolved_replacement_is_reported_rather_than_guessed(project):
    proposal = await propose(project, REPLACE)

    assert proposal.changes == []
    assert "run find_replacement first" in proposal.unresolved[0]


async def test_a_human_review_item_stays_open(project):
    proposal = await propose(project, REVIEW)

    assert proposal.changes == []
    assert "LGPL-2.1 in a binary." in proposal.unresolved[0]


async def test_a_transitive_dependency_is_reported_rather_than_pinned(project):
    """A package only the lockfile knows cannot be pinned in a declaration it is absent from."""
    proposal = await propose(project, entry("certifi", TriageBranch.AUTO_UPGRADE, "2024.8.30"))

    assert proposal.changes == []
    assert "transitive dependency" in proposal.warnings[0]
    assert proposal.diff == ""


async def test_a_clean_package_produces_no_change(project):
    proposal = await propose(project, entry("urllib3", TriageBranch.NO_ACTION))

    assert proposal.changes == []


# --- rationale and migration notes -------------------------------------------


async def test_every_package_gets_a_line_of_rationale(project):
    proposal = await propose(project, UPGRADE, REVIEW)

    assert set(proposal.rationale) == {"urllib3", "chardet"}


async def test_says_plainly_when_a_package_was_not_changed(project):
    proposal = await propose(project, REVIEW)

    assert proposal.rationale["chardet"].startswith("NOT CHANGED —")


async def test_a_breaking_upgrade_carries_migration_notes(project):
    proposal = await propose(project, BREAKING)

    assert "2.7.2 -> 3.4.0" in proposal.changes[0].migration_notes


async def test_a_patch_upgrade_needs_no_migration_notes(project):
    proposal = await propose(project, UPGRADE)

    assert proposal.changes[0].migration_notes == ""


async def test_a_replacement_carries_the_compatibility_finding(project):
    proposal = await propose(project, REPLACE, replacements=[PYCRYPTODOME])

    assert "Drop-in for the Crypto.* namespace." in proposal.changes[0].migration_notes


async def test_a_replacement_with_no_compatibility_finding_says_so(project):
    replacement = Replacement(package="pycrypto", name="pycryptodome", version="3.20.0")

    proposal = await propose(project, REPLACE, replacements=[replacement])

    assert "Compatibility was not established." in proposal.changes[0].migration_notes


# --- approval ----------------------------------------------------------------


async def approve_all(proposal):
    return {change.package: Approval.APPROVED for change in proposal.changes}


async def test_records_the_decisions_the_user_made(project):
    async def approver(proposal):
        return {"urllib3": Approval.APPROVED, "paramiko": Approval.REJECTED}

    proposal = await propose(project, UPGRADE, BREAKING, approver=approver)

    assert proposal.approvals == {"urllib3": Approval.APPROVED, "paramiko": Approval.REJECTED}
    assert proposal.approval_status == ApprovalStatus.REVIEWED


async def test_a_package_the_user_did_not_answer_for_stays_pending(project):
    async def approver(proposal):
        return {"urllib3": Approval.APPROVED}

    proposal = await propose(project, UPGRADE, BREAKING, approver=approver)

    assert proposal.approvals["paramiko"] == Approval.PENDING


async def test_an_answer_about_a_package_that_is_not_in_the_proposal_is_ignored(project):
    async def approver(proposal):
        return {"urllib3": Approval.APPROVED, "ghost": Approval.APPROVED}

    proposal = await propose(project, UPGRADE, approver=approver)

    assert "ghost" not in proposal.approvals


async def test_a_client_that_cannot_elicit_gets_a_pending_proposal(project):
    proposal = await propose(project, UPGRADE)

    assert proposal.approval_status == ApprovalStatus.PENDING_EXPLICIT_APPLY
    assert proposal.approvals == {"urllib3": Approval.PENDING}


async def test_a_failing_elicitation_degrades_rather_than_aborts(project):
    async def approver(proposal):
        raise RuntimeError("client hung up")

    proposal = await propose(project, UPGRADE, approver=approver)

    assert proposal.approval_status == ApprovalStatus.PENDING_EXPLICIT_APPLY


async def test_the_user_is_shown_the_diff_before_being_asked(project):
    shown = {}

    async def approver(proposal):
        shown["diff"] = proposal.diff
        return await approve_all(proposal)

    await propose(project, UPGRADE, approver=approver)

    assert "urllib3==2.0.7" in shown["diff"]


# --- persistence -------------------------------------------------------------


async def test_persists_the_proposal_so_apply_plan_can_find_it(project):
    store = InMemoryStore()

    proposal = await propose(project, UPGRADE, store=store)

    assert store.proposal(proposal.proposal_id).diff == proposal.diff


async def test_two_proposals_for_one_project_get_different_ids(project):
    first = await propose(project, UPGRADE)
    second = await propose_upgrade_plan_tool(
        plan(UPGRADE, BREAKING), str(project), now=datetime(2026, 9, 2, tzinfo=UTC)
    )

    assert first.proposal_id != second.proposal_id


async def test_refuses_a_project_with_nothing_to_propose_against(tmp_path):
    with pytest.raises(FileNotFoundError):
        await propose(tmp_path, UPGRADE)
