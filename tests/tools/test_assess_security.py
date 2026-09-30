import pytest

from src.domain.models import SecurityVerdict, Severity, UpgradeDistance
from src.tools.assess_security import assess_security_tool, upgrade_distance
from tests.support import ScriptedChatModel, advisory, draft, enriched_package

PARAMIKO = enriched_package(
    name="paramiko",
    version="2.7.2",
    vulnerabilities=(advisory(),),
    latest_version="3.4.0",
)
CLEAN = enriched_package(name="urllib3", version="2.0.7")


async def assess(packages, script, **kwargs) -> list[SecurityVerdict]:
    model = ScriptedChatModel(script=list(script))
    result = await assess_security_tool(packages, model=model, **kwargs)
    return result.verdicts


async def assess_one(package, entry, **kwargs) -> SecurityVerdict:
    return (await assess([package], [entry], **kwargs))[0]


async def test_returns_one_verdict_per_package():
    verdicts = await assess([PARAMIKO, CLEAN], [draft(("CVE-2023-48795",), "HIGH", "3.4.0")])

    assert [v.package for v in verdicts] == ["paramiko", "urllib3"]


async def test_returns_no_verdicts_for_an_empty_input():
    assert await assess([], []) == []


async def test_carries_the_pinned_version_into_the_verdict():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795",), "HIGH", "3.4.0"))

    assert verdict.version == "2.7.2"


async def test_reports_the_advisories_the_model_deemed_applicable():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795",), "HIGH", "3.4.0"))

    assert verdict.applicable_cves == ["CVE-2023-48795"]
    assert verdict.max_severity == Severity.HIGH


async def test_keeps_the_evidence_the_model_produced():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795",), "HIGH", "3.4.0", "Terrapin truncates negotiation."))

    assert verdict.evidence == "Terrapin truncates negotiation."


async def test_accepts_an_advisory_referenced_by_its_primary_id():
    verdict = await assess_one(PARAMIKO, draft(("GHSA-45x7-px36-x8w8",), "HIGH", "3.4.0"))

    assert verdict.applicable_cves == ["GHSA-45x7-px36-x8w8"]


async def test_matches_advisory_identifiers_case_insensitively():
    verdict = await assess_one(PARAMIKO, draft(("cve-2023-48795",), "HIGH", "3.4.0"))

    assert verdict.applicable_cves == ["cve-2023-48795"]
    assert verdict.corrections == []


async def test_drops_an_identifier_the_advisories_never_mentioned():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795", "CVE-2021-99999"), "HIGH", "3.4.0"))

    assert verdict.applicable_cves == ["CVE-2023-48795"]
    assert "CVE-2021-99999" in verdict.corrections[0]


async def test_drops_a_fix_version_no_advisory_reports():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795",), "HIGH", "9.9.9"))

    assert verdict.fixed_in is None
    assert "9.9.9" in verdict.corrections[0]


async def test_reports_a_clean_package_when_every_identifier_was_invented():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2021-99999",), "CRITICAL", "3.4.0"))

    assert verdict.applicable_cves == []
    assert verdict.max_severity == Severity.NONE
    assert verdict.fixed_in is None
    assert verdict.upgrade_distance == UpgradeDistance.NONE


async def test_records_every_correction_it_applied():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2021-99999",), "CRITICAL", "9.9.9"))

    assert len(verdict.corrections) == 3


async def test_never_calls_the_model_for_a_package_without_advisories():
    model = ScriptedChatModel(script=[draft(("CVE-2023-48795",), "CRITICAL")])

    result = await assess_security_tool([CLEAN], model=model)

    assert model.prompts == []
    assert result.verdicts[0].max_severity == Severity.NONE
    assert result.verdicts[0].upgrade_distance == UpgradeDistance.NONE


async def test_shows_the_agent_the_advisory_text():
    model = ScriptedChatModel(script=[draft(("CVE-2023-48795",), "HIGH", "3.4.0")])

    await assess_security_tool([PARAMIKO], model=model)

    prompt = model.prompts[0]
    assert "paramiko" in prompt
    assert "2.7.2" in prompt
    assert "GHSA-45x7-px36-x8w8" in prompt
    assert "Terrapin attack truncates the SSH extension negotiation." in prompt


async def test_shows_the_agent_the_declared_severity_and_reported_fix():
    model = ScriptedChatModel(script=[draft()])

    await assess_security_tool([PARAMIKO], model=model)

    assert "HIGH" in model.prompts[0]
    assert "3.4.0" in model.prompts[0]


async def test_records_an_error_when_the_model_fails():
    verdict = await assess_one(PARAMIKO, RuntimeError("provider is down"))

    assert verdict.error == "RuntimeError: provider is down"
    assert verdict.max_severity == Severity.UNKNOWN


async def test_one_failing_package_does_not_sink_the_others():
    second = enriched_package(name="requests", version="2.31.0", vulnerabilities=(advisory(id="GHSA-9hjg", aliases=(), fixed_in="2.32.4"),))
    model = ScriptedChatModel(script=[RuntimeError("boom"), draft(("GHSA-9hjg",), "MODERATE", "2.32.4")])

    result = await assess_security_tool([PARAMIKO, second], model=model, max_concurrency=1)

    assert result.verdicts[0].error is not None
    assert result.verdicts[1].error is None
    assert result.verdicts[1].applicable_cves == ["GHSA-9hjg"]


async def test_holds_concurrent_model_calls_below_the_cap():
    packages = [
        enriched_package(name=f"pkg{i}", version="1.0.0", vulnerabilities=(advisory(id=f"GHSA-{i}", aliases=(), fixed_in="1.0.1"),))
        for i in range(10)
    ]
    model = ScriptedChatModel(script=[draft(("GHSA-0",), "LOW", "1.0.1")], latency=0.01)

    await assess_security_tool(packages, model=model, max_concurrency=3)

    assert model.peak_in_flight <= 3


async def test_never_recommends_an_action():
    assert "action" not in set(SecurityVerdict.model_fields)
    assert "recommendation" not in set(SecurityVerdict.model_fields)


@pytest.mark.parametrize(
    "current, fixed, expected",
    [
        ("2.0.4", "2.0.7", UpgradeDistance.PATCH),
        ("2.0.4", "2.1.0", UpgradeDistance.MINOR),
        ("2.7.2", "3.4.0", UpgradeDistance.MAJOR),
        ("2.0.4", "2.0.4", UpgradeDistance.NONE),
        ("2.0.7", "2.0.4", UpgradeDistance.NONE),
        ("2.0", "2.0.1", UpgradeDistance.PATCH),
        ("1.0", "2.0", UpgradeDistance.MAJOR),
        ("2.0.4", None, UpgradeDistance.UNKNOWN),
        ("nightly", "2.0.7", UpgradeDistance.UNKNOWN),
        ("2.0.4", "not-a-version", UpgradeDistance.UNKNOWN),
    ],
)
def test_derives_the_upgrade_distance_from_the_two_versions(current, fixed, expected):
    assert upgrade_distance(current, fixed) == expected


async def test_derives_the_upgrade_distance_rather_than_asking_the_model():
    verdict = await assess_one(PARAMIKO, draft(("CVE-2023-48795",), "HIGH", "3.4.0"))

    assert verdict.upgrade_distance == UpgradeDistance.MAJOR


async def test_reports_an_unknown_distance_when_no_fix_exists():
    unfixable = enriched_package(
        name="pycrypto",
        version="2.6.1",
        vulnerabilities=(advisory(id="CVE-2013-7459", aliases=(), fixed_in=None),),
    )

    verdict = await assess_one(unfixable, draft(("CVE-2013-7459",), "HIGH", None))

    assert verdict.fixed_in is None
    assert verdict.upgrade_distance == UpgradeDistance.UNKNOWN
