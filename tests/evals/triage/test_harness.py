"""The eval harness must be able to fail, or it measures nothing."""

from tests.evals.scoring import CaseResult
from tests.evals.triage.dataset import CASES
from tests.evals.triage.runner import run_cases
from tests.support import ScriptedChatModel

PERFECT_RATIONALES = {
    "urllib3": "CVE-2024-37891 is a moderate issue fixed in 2.0.7; a patch bump with no other findings.",
    "paramiko": "CVE-2023-48795 (Terrapin) is high severity and fixed only in 3.4.0, a major bump.",
    "pycrypto": "The repository is archived since 2013 and CVE-2013-7459 has no fix released.",
    "chardet": "LGPL-2.1 section 6 requires that users can relink the library, which this distribution complicates.",
    "pygpl-cli": "GPL-3.0 copyleft extends to the whole conveyed work under section 5.",
    "attrs": "No advisories, a permissive licence and a release six weeks ago.",
    "lxml": "The security specialist could not report; OSV timed out, so the picture is incomplete.",
    "quiet-lib": "One contributor and a release 620 days ago, with no advisories and no licence conflict.",
}


def scripted(rationales: dict[str, str], branches: dict[str, str] | None = None):
    branches = branches or {}
    return ScriptedChatModel(
        script=[
            {
                "entries": [
                    {
                        "package": case.package,
                        "branch": branches.get(case.package, case.expected_branch.value),
                        "rationale": rationales[case.package],
                    }
                    for case in CASES
                ]
            }
        ],
        schema_name="TriagePlanDraft",
    )


def score_for(results: list[CaseResult], package: str, scorer: str):
    result = next(r for r in results if r.case.package == package)
    return result, next(s for s in result.scores if s.name == scorer)


async def test_a_manager_that_writes_the_labels_scores_clean():
    results = await run_cases(scripted(PERFECT_RATIONALES))

    failures = [(r.case.id, s.name, s.detail) for r in results for s in r.scores if not s.passed]
    assert failures == []


async def test_the_harness_catches_a_rationale_that_names_its_branch():
    rationales = PERFECT_RATIONALES | {"urllib3": "This is an AUTO_UPGRADE, target 2.0.7."}

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "urllib3", "speaks_findings_not_branches")
    assert not score.passed


async def test_the_harness_catches_a_rationale_that_reports_only_one_finding():
    rationales = PERFECT_RATIONALES | {"pycrypto": "CVE-2013-7459 was never fixed."}

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "pycrypto", "mentions_the_findings")
    assert not score.passed


async def test_the_harness_catches_an_invented_version():
    rationales = PERFECT_RATIONALES | {
        "urllib3": "CVE-2024-37891 is fixed in 2.0.7, and 2.1.9 is also available."
    }

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "urllib3", "no_invented_version")
    assert not score.passed


async def test_the_harness_catches_an_instruction_to_the_user():
    rationales = PERFECT_RATIONALES | {
        "chardet": "LGPL-2.1 section 6 requires relinking; you should switch to charset-normalizer."
    }

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "chardet", "no_action_language")
    assert not score.passed


async def test_the_harness_catches_an_inflated_worry_on_a_clean_package():
    rationales = PERFECT_RATIONALES | {
        "attrs": "No advisories, but this looks like it could be abandoned soon."
    }

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "attrs", "avoids_the_forbidden")
    assert not score.passed


async def test_the_harness_catches_an_essay():
    rationales = PERFECT_RATIONALES | {"attrs": "No advisories. " + "Nothing to report. " * 30}

    results = await run_cases(scripted(rationales))

    _, score = score_for(results, "attrs", "rationale_is_brief")
    assert not score.passed


async def test_the_harness_catches_a_manager_relaxing_a_branch():
    results = await run_cases(
        scripted(PERFECT_RATIONALES, branches={"pycrypto": "AUTO_UPGRADE"})
    )

    result, score = score_for(results, "pycrypto", "no_corrections")
    assert not score.passed
    assert next(s for s in result.scores if s.name == "branch_held").passed
