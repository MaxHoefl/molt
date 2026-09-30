from datetime import UTC, datetime

import pytest

from src.domain.models import (
    Approval,
    ApprovalStatus,
    ChangeKind,
    DecisionAction,
    Replacement,
    TriageBranch,
    TriageEntry,
    TriagePlan,
    UpgradeDistance,
)
from src.memory.store import InMemoryStore, project_key
from src.tools.apply_plan import apply_plan_tool
from src.tools.propose_upgrade_plan import propose_upgrade_plan_tool

PYPROJECT = """\
[project]
name = "demo"
dependencies = [
    "urllib3==2.0.4",
    "paramiko==2.7.2",
    "pycrypto==2.6.1",
]
"""

NOW = datetime(2026, 9, 1, tzinfo=UTC)

UPGRADE = TriageEntry(
    package="urllib3",
    version="2.0.4",
    branch=TriageBranch.AUTO_UPGRADE,
    target="2.0.7",
    rationale="Moderate CVE fixed in a patch release.",
    upgrade_distance=UpgradeDistance.PATCH,
)
BREAKING = TriageEntry(
    package="paramiko",
    version="2.7.2",
    branch=TriageBranch.UPGRADE_BREAKING,
    target="3.4.0",
    rationale="High CVE fixed only in 3.x.",
    upgrade_distance=UpgradeDistance.MAJOR,
)
REPLACE = TriageEntry(
    package="pycrypto",
    version="2.6.1",
    branch=TriageBranch.REPLACE,
    rationale="Archived since 2014.",
)
REVIEW = TriageEntry(
    package="chardet",
    version="5.0.0",
    branch=TriageBranch.HUMAN_REVIEW,
    rationale="LGPL-2.1 in a binary distribution.",
)
PYCRYPTODOME = Replacement(package="pycrypto", name="pycryptodome", version="3.20.0")


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    return tmp_path


@pytest.fixture
def store():
    return InMemoryStore()


async def propose(project, store, *entries, approvals=None, replacements=()):
    async def approver(proposal):
        return {package: Approval(decision) for package, decision in approvals.items()}

    return await propose_upgrade_plan_tool(
        TriagePlan(entries=list(entries)),
        str(project),
        replacements=list(replacements),
        store=store,
        approver=approver if approvals is not None else None,
        now=NOW,
    )


def manifest(project) -> str:
    return (project / "pyproject.toml").read_text()


# --- the happy path ----------------------------------------------------------


async def test_applies_an_approved_change(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert '"urllib3==2.0.7",' in manifest(project)


async def test_backs_the_original_up_before_writing(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.backup_path.endswith("pyproject.toml.molt.bak")
    assert (project / "pyproject.toml.molt.bak").read_text() == PYPROJECT


async def test_reports_what_it_applied(project, store):
    proposal = await propose(
        project, store, REPLACE, replacements=[PYCRYPTODOME], approvals={"pycrypto": "approved"}
    )

    result = apply_plan_tool(proposal.proposal_id, ["pycrypto"], store, NOW)

    assert result.applied[0].kind == ChangeKind.REPLACE
    assert result.applied[0].to == "pycryptodome==3.20.0"


async def test_applies_only_the_packages_that_were_named(project, store):
    proposal = await propose(
        project,
        store,
        UPGRADE,
        BREAKING,
        approvals={"urllib3": "approved", "paramiko": "approved"},
    )

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert '"urllib3==2.0.7",' in manifest(project)
    assert '"paramiko==2.7.2",' in manifest(project)


async def test_says_why_each_skipped_package_was_skipped(project, store):
    proposal = await propose(
        project,
        store,
        UPGRADE,
        BREAKING,
        REVIEW,
        approvals={"urllib3": "approved", "paramiko": "rejected"},
    )

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert "paramiko (rejected during review)" in result.skipped
    assert any("LGPL-2.1" in line for line in result.skipped)


async def test_applying_nothing_is_allowed_and_changes_nothing(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "rejected"})

    result = apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert result.status == "applied"
    assert manifest(project) == PYPROJECT


# --- the four checks ---------------------------------------------------------


def test_refuses_a_proposal_it_has_never_seen(store):
    result = apply_plan_tool("prop_nope", ["urllib3"], store, NOW)

    assert result.status == "aborted"
    assert "run propose_upgrade_plan first" in result.error


async def test_refuses_to_apply_the_same_proposal_twice(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})
    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.status == "aborted"
    assert "already been applied" in result.error


async def test_refuses_a_package_the_user_rejected(project, store):
    proposal = await propose(
        project, store, UPGRADE, BREAKING, approvals={"urllib3": "approved", "paramiko": "rejected"}
    )

    result = apply_plan_tool(proposal.proposal_id, ["urllib3", "paramiko"], store, NOW)

    assert result.status == "aborted"
    assert "paramiko" in result.error


async def test_refuses_a_package_that_is_not_in_the_proposal(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})

    result = apply_plan_tool(proposal.proposal_id, ["requests"], store, NOW)

    assert result.status == "aborted"
    assert "Not part of proposal" in result.error


async def test_a_rejected_package_aborts_the_whole_apply(project, store):
    """Atomicity is the point: no partial write happens on a bad request."""
    proposal = await propose(
        project, store, UPGRADE, BREAKING, approvals={"urllib3": "approved", "paramiko": "rejected"}
    )

    apply_plan_tool(proposal.proposal_id, ["urllib3", "paramiko"], store, NOW)

    assert manifest(project) == PYPROJECT


async def test_refuses_when_the_file_changed_since_the_proposal(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})
    (project / "pyproject.toml").write_text(PYPROJECT.replace("urllib3==2.0.4", "urllib3==2.0.5"))

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.status == "aborted"
    assert "no longer describes this file" in result.error


async def test_refuses_when_the_file_is_gone(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})
    (project / "pyproject.toml").unlink()

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.status == "aborted"
    assert "no longer exists" in result.error


async def test_an_aborted_apply_leaves_the_proposal_usable(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})
    apply_plan_tool(proposal.proposal_id, ["requests"], store, NOW)

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.status == "applied"


# --- the degraded path -------------------------------------------------------


async def test_a_pending_proposal_can_be_applied_by_naming_the_packages(project, store):
    """Without elicitation the deliberate second call is the approval."""
    proposal = await propose(project, store, UPGRADE)
    assert proposal.approval_status == ApprovalStatus.PENDING_EXPLICIT_APPLY

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert result.status == "applied"


async def test_a_pending_proposal_still_refuses_a_package_it_never_covered(project, store):
    proposal = await propose(project, store, UPGRADE)

    result = apply_plan_tool(proposal.proposal_id, ["paramiko"], store, NOW)

    assert result.status == "aborted"


async def test_a_pending_proposal_applies_nothing_by_default(project, store):
    proposal = await propose(project, store, UPGRADE)

    apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert manifest(project) == PYPROJECT


# --- what it remembers -------------------------------------------------------


def episodes(store, project):
    return store.episodes(project_key(project))


async def test_records_what_the_user_approved(project, store):
    proposal = await propose(project, store, UPGRADE, approvals={"urllib3": "approved"})

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    assert episodes(store, project)[0].action == DecisionAction.APPROVED


async def test_records_what_the_user_rejected(project, store):
    proposal = await propose(
        project, store, UPGRADE, BREAKING, approvals={"urllib3": "approved", "paramiko": "rejected"}
    )

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    rejected = [e for e in episodes(store, project) if e.action == DecisionAction.REJECTED]
    assert [e.package for e in rejected] == ["paramiko"]


async def test_records_the_branch_a_decision_was_taken_on(project, store):
    """Without the branch, three rejections teach nothing about major bumps."""
    proposal = await propose(project, store, BREAKING, approvals={"paramiko": "rejected"})

    apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert episodes(store, project)[0].branch == TriageBranch.UPGRADE_BREAKING


async def test_remembers_an_accepted_replacement_for_next_time(project, store):
    proposal = await propose(
        project, store, REPLACE, replacements=[PYCRYPTODOME], approvals={"pycrypto": "approved"}
    )

    apply_plan_tool(proposal.proposal_id, ["pycrypto"], store, NOW)

    assert store.fact(project_key(project), "replacement:pycrypto").value == "pycryptodome"


async def test_does_not_remember_a_replacement_the_user_refused(project, store):
    proposal = await propose(
        project, store, REPLACE, replacements=[PYCRYPTODOME], approvals={"pycrypto": "rejected"}
    )

    apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert store.fact(project_key(project), "replacement:pycrypto") is None


async def test_a_package_nobody_answered_for_is_deferred_not_rejected(project, store):
    """Without elicitation, silence is not a no — and must not teach a preference."""
    proposal = await propose(project, store, BREAKING)

    apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert episodes(store, project)[0].action == DecisionAction.DEFERRED


async def test_derives_a_preference_once_the_pattern_is_there(project, store):
    for package in ("a", "b", "c"):
        (project / "pyproject.toml").write_text(PYPROJECT.replace("paramiko", package))
        entry = BREAKING.model_copy(update={"package": package})
        proposal = await propose_upgrade_plan_tool(
            TriagePlan(entries=[entry]),
            str(project),
            store=store,
            approver=lambda proposal: _reject_everything(proposal),
            now=datetime(2026, 9, 1 + ord(package) - ord("a"), tzinfo=UTC),
        )
        apply_plan_tool(proposal.proposal_id, [], store, NOW)

    assert [r.rule for r in store.rules(project_key(project))] == ["avoid_major_bumps"]


async def _reject_everything(proposal):
    return {change.package: Approval.REJECTED for change in proposal.changes}


async def test_files_the_audit_for_later_reference(project, store):
    proposal = await propose(project, store, UPGRADE, REVIEW, approvals={"urllib3": "approved"})

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    audit = store.latest_audit(project_key(project))
    assert audit.summary == {"proposed": 1, "applied": 1, "skipped": 0, "open": 1}


async def test_the_audit_keeps_the_open_items(project, store):
    proposal = await propose(project, store, UPGRADE, REVIEW, approvals={"urllib3": "approved"})

    apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    audit = store.latest_audit(project_key(project))
    assert "LGPL-2.1" in audit.open_items[0]["note"]
