"""The whole audit, end to end, with every external edge faked.

This is the test that would have caught the seams the unit tests each look past:
that `scan_project`'s output really is `enrich_dependencies`' input, that the three
specialists' verdicts really do line up for the manager, and that a proposal
generated in one step is the proposal `apply_plan` refuses to act on without
approval.

It is also the shortest readable description of what Molt does, which is why it is
written as one narrative rather than as a dozen small cases.
"""

from datetime import UTC, date, datetime

import pytest

from src.domain.models import (
    Approval,
    LicenseCompatibility,
    MaintenanceStatus,
    Replacement,
    Severity,
    TriageBranch,
)
from src.memory.store import InMemoryStore, project_key
from src.rag.retrieval import NullRetriever
from src.tools.apply_plan import apply_plan_tool
from src.tools.assess_license import assess_license_tool
from src.tools.assess_maintenance import assess_maintenance_tool
from src.tools.assess_security import assess_security_tool
from src.tools.enrich_dependencies import enrich_dependencies_tool
from src.tools.find_replacement import find_replacement_tool
from src.tools.propose_upgrade_plan import propose_upgrade_plan_tool
from src.tools.scan_project import scan_project_tool
from src.tools.triage_dependencies import triage_dependencies_tool
from tests.support import (
    FakeSession,
    ScriptedChatModel,
    candidate,
    draft,
    github_repo,
    license_draft,
    maintenance_draft,
    osv_payload,
    pypi_payload,
    replacement_draft,
    tool_call,
    triage_draft,
    vuln,
)

TODAY = date(2026, 9, 1)
NOW = datetime(2026, 9, 1, tzinfo=UTC)

PYPROJECT = """\
[project]
name = "demo"
version = "0.1.0"
license = "MIT"
dependencies = [
    "urllib3==2.0.4",
    "pycrypto==2.6.1",
    "chardet==5.0.0",
]
"""

UV_LOCK = """\
version = 1

[[package]]
name = "urllib3"
version = "2.0.4"

[[package]]
name = "pycrypto"
version = "2.6.1"

[[package]]
name = "chardet"
version = "5.0.0"
"""


@pytest.fixture
def project(tmp_path):
    (tmp_path / "pyproject.toml").write_text(PYPROJECT)
    (tmp_path / "uv.lock").write_text(UV_LOCK)
    return tmp_path


def world() -> FakeSession:
    """One vulnerable package with a patch fix, one archived one, one licence question."""
    return FakeSession(
        osv={
            ("urllib3", "2.0.4"): osv_payload(
                vuln(
                    id="GHSA-34jh-p97f-mpxf",
                    aliases=("CVE-2024-37891",),
                    severity="MODERATE",
                    fixed=("2.0.7",),
                    package_name="urllib3",
                    summary="Proxy-Authorization is not stripped on cross-origin redirect.",
                )
            ),
            ("pycrypto", "2.6.1"): osv_payload(
                vuln(
                    id="CVE-2013-7459",
                    severity="HIGH",
                    package_name="pycrypto",
                    summary="Heap buffer overflow in ALGnew.",
                )
            ),
            ("chardet", "5.0.0"): osv_payload(),
        },
        pypi={
            "urllib3": pypi_payload(
                version="2.0.7",
                releases={"2.0.4": [], "2.0.7": [{"upload_time_iso_8601": "2026-08-01T00:00:00Z"}]},
                license="MIT",
                project_urls={"Source": "https://github.com/urllib3/urllib3"},
            ),
            "pycrypto": pypi_payload(
                version="2.6.1",
                releases={"2.6.1": [{"upload_time_iso_8601": "2013-10-17T00:00:00Z"}]},
                license="Public Domain",
                project_urls={"Source": "https://github.com/pycrypto/pycrypto"},
            ),
            "chardet": pypi_payload(
                version="5.0.0",
                releases={"5.0.0": [{"upload_time_iso_8601": "2026-05-01T00:00:00Z"}]},
                license="LGPL-2.1",
            ),
            "pycryptodome": pypi_payload(
                version="3.20.0",
                releases={"3.20.0": [{"upload_time_iso_8601": "2026-07-01T00:00:00Z"}]},
                license="BSD-2-Clause",
                project_urls={"Source": "https://github.com/Legrandin/pycryptodome"},
            ),
        },
        repos={
            "urllib3/urllib3": github_repo(
                html_url="https://github.com/urllib3/urllib3", pushed_at="2026-08-20T00:00:00Z"
            ),
            "pycrypto/pycrypto": github_repo(
                html_url="https://github.com/pycrypto/pycrypto",
                archived=True,
                pushed_at="2014-06-20T00:00:00Z",
                open_issues_count=194,
            ),
            "Legrandin/pycryptodome": github_repo(
                html_url="https://github.com/Legrandin/pycryptodome",
                pushed_at="2026-08-15T00:00:00Z",
            ),
        },
        dependents={"pycryptodome": 3120},
    )


def answering(answers, schema) -> ScriptedChatModel:
    return ScriptedChatModel(answers=answers, schema_name=schema)


async def run_audit(project, store, session, approvals):
    """The pipeline exactly as the audit_and_upgrade prompt orders it."""
    manifest = scan_project_tool(project)
    enrichment = await enrich_dependencies_tool(manifest.dependencies, session=session)
    enriched = enrichment.enriched

    security = await assess_security_tool(
        enriched,
        model=answering(
            {
                "urllib3": draft(("CVE-2024-37891",), "MODERATE", "2.0.7", "Proxy-Authorization leaks."),
                "pycrypto": draft(("CVE-2013-7459",), "HIGH", None, "Heap overflow, never fixed."),
            },
            "SecurityVerdictDraft",
        ),
    )
    licenses = await assess_license_tool(
        enriched,
        manifest.project_license,
        model=answering(
            {
                "chardet": license_draft(
                    "requires_human_review",
                    "LGPL-2.1 section 6 requires that the user can relink the library.",
                    ("charset-normalizer (MIT)",),
                ),
                "pycrypto": license_draft("compatible"),
                "urllib3": license_draft("compatible"),
            },
            "LicenseVerdictDraft",
        ),
        retriever=NullRetriever(),
    )
    maintenance = await assess_maintenance_tool(
        enriched,
        model=answering(
            {
                "urllib3": maintenance_draft("healthy", 0.9, "Commits 12 days ago."),
                "pycrypto": maintenance_draft("abandoned", 0.98, "Archived; last release 2013."),
                "chardet": maintenance_draft("healthy", 0.7, "Released 123 days ago."),
            },
            "MaintenanceVerdictDraft",
        ),
        today=TODAY,
    )
    plan = await triage_dependencies_tool(
        security.verdicts,
        licenses.verdicts,
        maintenance.verdicts,
        project=str(project),
        model=ScriptedChatModel(
            script=[
                triage_draft(
                    ("urllib3", "AUTO_UPGRADE", "CVE-2024-37891 is fixed in 2.0.7, a patch bump."),
                    ("pycrypto", "REPLACE", "Archived since 2014 and CVE-2013-7459 was never fixed."),
                    ("chardet", "HUMAN_REVIEW", "LGPL-2.1 relinking obligation in a binary product."),
                )
            ],
            schema_name="TriagePlanDraft",
        ),
        store=store,
    )
    replacement = await find_replacement_tool(
        "pycrypto",
        context="AES encryption of local config files",
        reason="archived since 2014",
        project=str(project),
        model=ScriptedChatModel(
            script=[
                tool_call("lookup_package", name="pycryptodome"),
                tool_call("count_dependents", name="pycryptodome"),
                replacement_draft((candidate(),), ("iter1: crypto primitives", "iter2: verified")),
            ],
            schema_name="ReplacementSearchDraft",
        ),
        session=session,
        store=store,
        today=TODAY,
    )

    async def approve(proposal):
        return {package: Approval(decision) for package, decision in approvals.items()}

    proposal = await propose_upgrade_plan_tool(
        plan,
        str(project),
        replacements=[
            Replacement(
                package="pycrypto",
                name=replacement.candidates[0].name,
                version=replacement.candidates[0].latest_version,
                compatibility=replacement.candidates[0].compatibility,
            )
        ],
        store=store,
        approver=approve,
        now=NOW,
    )
    return manifest, enrichment, security, licenses, maintenance, plan, replacement, proposal


@pytest.fixture
async def audit(project):
    store = InMemoryStore()
    parts = await run_audit(
        project, store, world(), {"urllib3": "approved", "pycrypto": "approved"}
    )
    return (store, *parts)


async def test_the_scan_finds_the_project_licence_and_its_dependencies(audit):
    _, manifest, *_ = audit

    assert manifest.project_license == "MIT"
    assert {d.name for d in manifest.dependencies} == {"urllib3", "pycrypto", "chardet"}


async def test_enrichment_gathers_facts_from_all_three_sources(audit):
    _, _, enrichment, *_ = audit

    pycrypto = next(p for p in enrichment.enriched if p.name == "pycrypto")
    assert pycrypto.vulnerabilities[0].id == "CVE-2013-7459"
    assert pycrypto.license_declared == "Public Domain"
    assert pycrypto.repo_health.archived is True


async def test_the_three_specialists_each_report_on_every_package(audit):
    _, _, _, security, licenses, maintenance, *_ = audit

    for assessment in (security, licenses, maintenance):
        assert len(assessment.verdicts) == 3


async def test_the_specialists_disagree_about_different_things(audit):
    _, _, _, security, licenses, maintenance, *_ = audit

    assert next(v for v in security.verdicts if v.package == "urllib3").max_severity == Severity.MODERATE
    assert (
        next(v for v in licenses.verdicts if v.package == "chardet").verdict
        == LicenseCompatibility.REQUIRES_HUMAN_REVIEW
    )
    assert (
        next(v for v in maintenance.verdicts if v.package == "pycrypto").status
        == MaintenanceStatus.ABANDONED
    )


async def test_triage_routes_each_package_down_a_different_branch(audit):
    *_, plan, _, _ = audit

    assert {e.package: e.branch for e in plan.entries} == {
        "urllib3": TriageBranch.AUTO_UPGRADE,
        "pycrypto": TriageBranch.REPLACE,
        "chardet": TriageBranch.HUMAN_REVIEW,
    }


async def test_the_react_loop_verifies_its_replacement_against_pypi(audit):
    *_, replacement, _ = audit

    assert replacement.candidates[0].name == "pycryptodome"
    assert replacement.candidates[0].license == "BSD-2-Clause"
    assert replacement.candidates[0].dependents == 3120
    assert replacement.corrections == []


async def test_the_proposal_changes_only_what_can_be_changed(audit):
    *_, proposal = audit

    assert {c.package for c in proposal.changes} == {"urllib3", "pycrypto"}
    assert any("LGPL-2.1" in item for item in proposal.unresolved)


async def test_the_proposal_is_a_diff_of_the_file_the_user_edits(audit):
    *_, proposal = audit

    assert '+    "urllib3==2.0.7",' in proposal.diff
    assert '+    "pycryptodome==3.20.0",' in proposal.diff
    assert "chardet" not in proposal.diff.replace(' "chardet==5.0.0",', "")


async def test_proposing_writes_nothing_to_disk(project, audit):
    assert (project / "pyproject.toml").read_text() == PYPROJECT


async def test_applying_only_what_the_user_approved_changes_the_file(project, audit):
    store, *_, proposal = audit

    result = apply_plan_tool(proposal.proposal_id, ["urllib3"], store, NOW)

    text = (project / "pyproject.toml").read_text()
    assert result.status == "applied"
    assert '"urllib3==2.0.7",' in text
    assert '"pycrypto==2.6.1",' in text


async def test_the_audit_teaches_the_next_one(project, audit):
    store, *_, proposal = audit
    apply_plan_tool(proposal.proposal_id, ["urllib3", "pycrypto"], store, NOW)

    assert store.fact(project_key(project), "replacement:pycrypto").value == "pycryptodome"
    assert store.latest_audit(project_key(project)).summary["applied"] == 2


async def test_the_second_audit_skips_the_replacement_search(project, audit):
    """Semantic memory short-circuits the most expensive loop in the pipeline."""
    store, *_, proposal = audit
    apply_plan_tool(proposal.proposal_id, ["urllib3", "pycrypto"], store, NOW)
    silent = ScriptedChatModel(script=[replacement_draft()], schema_name="ReplacementSearchDraft")

    again = await find_replacement_tool(
        "pycrypto", project=str(project), model=silent, session=world(), store=store, today=TODAY
    )

    assert silent.prompts == []
    assert again.from_memory is True
    assert again.candidates[0].name == "pycryptodome"
