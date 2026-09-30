"""The eval harness must be able to fail, or it measures nothing."""

from tests.evals.security.dataset import CASES
from tests.evals.scoring import scorecard
from tests.evals.security.runner import run_cases
from tests.support import ScriptedChatModel, draft

PERFECT_ANSWERS = {
    case.package.name: draft(
        applicable_cves=tuple(sorted(case.expected_cves)),
        max_severity=case.expected_severity.value,
        fixed_in=case.expected_fixed_in,
        evidence=f"The advisories for {case.package.name} describe the issue and its state.",
    )
    for case in CASES
}


def answering(answers) -> ScriptedChatModel:
    return ScriptedChatModel(answers=answers)


async def test_a_model_that_matches_the_labels_scores_clean():
    results = await run_cases(answering(PERFECT_ANSWERS))

    failures = [(r.case.id, s.name, s.detail) for r in results for s in r.scores if not s.passed]
    assert failures == []


async def test_the_harness_catches_an_invented_identifier():
    answers = PERFECT_ANSWERS | {
        "paramiko": draft(("CVE-2023-48795", "CVE-2020-00000"), "HIGH", "3.4.0")
    }

    results = await run_cases(answering(answers))

    paramiko = next(r for r in results if r.case.package.name == "paramiko")
    assert paramiko.verdict.corrections != []
    assert not next(s for s in paramiko.scores if s.name == "no_corrections").passed


async def test_the_harness_catches_a_missed_advisory():
    answers = PERFECT_ANSWERS | {"example-multi": draft(("GHSA-aaaa",), "LOW", "1.2.1")}

    results = await run_cases(answering(answers))

    multi = next(r for r in results if r.case.package.name == "example-multi")
    recall = next(s for s in multi.scores if s.name == "cve_recall")
    assert not recall.passed
    assert "GHSA-bbbb" in recall.detail


async def test_the_harness_catches_a_miscalibrated_severity():
    answers = PERFECT_ANSWERS | {"pycrypto": draft(("CVE-2013-7459",), "LOW", None)}

    results = await run_cases(answering(answers))

    pycrypto = next(r for r in results if r.case.package.name == "pycrypto")
    assert not next(s for s in pycrypto.scores if s.name == "severity_exact").passed
    assert not next(s for s in pycrypto.scores if s.name == "severity_within_one_band").passed


async def test_the_harness_catches_a_repeated_distractor():
    answers = PERFECT_ANSWERS | {
        "example-lure": draft(("GHSA-ffff",), "MODERATE", "4.0.2", "Related to CVE-2019-11111.")
    }

    results = await run_cases(answering(answers))

    lure = next(r for r in results if r.case.package.name == "example-lure")
    assert not next(s for s in lure.scores if s.name == "no_lure").passed
    assert not next(s for s in lure.scores if s.name == "evidence_grounded").passed


async def test_the_harness_catches_action_language_in_the_evidence():
    answers = PERFECT_ANSWERS | {
        "urllib3": draft(("CVE-2024-37891",), "MODERATE", "2.0.7", "You should upgrade to 2.0.7.")
    }

    results = await run_cases(answering(answers))

    urllib3 = next(r for r in results if r.case.package.name == "urllib3")
    assert not next(s for s in urllib3.scores if s.name == "no_action_language").passed


async def test_the_clean_package_case_costs_no_llm_call():
    model = answering(PERFECT_ANSWERS)

    await run_cases(model)

    assert not any("requests" in prompt for prompt in model.prompts)


async def test_the_scorecard_reports_every_scorer_and_the_model():
    results = await run_cases(answering(PERFECT_ANSWERS))

    rendered = scorecard(results, "scripted:perfect")

    assert "scripted:perfect" in rendered
    assert "cve_precision" in rendered
    assert "100%" in rendered


async def test_the_scorecard_lists_what_failed():
    answers = PERFECT_ANSWERS | {"pycrypto": draft(("CVE-2013-7459",), "LOW", None)}

    rendered = scorecard(await run_cases(answering(answers)), "scripted:weak")

    assert "failures:" in rendered
    assert "no-fix-available" in rendered


def test_every_case_names_the_failure_mode_it_probes():
    assert all(case.probes for case in CASES)
    assert len({case.id for case in CASES}) == len(CASES)
