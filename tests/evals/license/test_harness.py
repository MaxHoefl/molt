"""The eval harness must be able to fail, or it measures nothing."""

from src.domain.models import LicenseCompatibility
from tests.evals.license.dataset import CASES, EvalCase
from tests.evals.license.runner import run_cases
from tests.evals.license.scorers import license_families
from tests.evals.scoring import scorecard
from tests.support import ScriptedChatModel, license_draft


def perfect(case: EvalCase) -> dict:
    if case.expected_verdict == LicenseCompatibility.COMPATIBLE:
        return license_draft("compatible")
    family = sorted(license_families(case.expected_license)) or ["This license"]
    return license_draft(
        case.expected_verdict.value,
        f"{family[0]} imposes an obligation the project cannot discharge as it ships.",
        ("charset-normalizer (MIT)",) if case.expects_alternatives else (),
    )


PERFECT_ANSWERS = {case.package.name: perfect(case) for case in CASES}


def answering(answers) -> ScriptedChatModel:
    return ScriptedChatModel(answers=answers, schema_name="LicenseVerdictDraft")


def scores_for(results, package: str):
    result = next(r for r in results if r.case.package.name == package)
    return result, {score.name: score for score in result.scores}


async def test_a_model_that_matches_the_labels_scores_clean():
    results = await run_cases(answering(PERFECT_ANSWERS))

    failures = [(r.case.id, s.name, s.detail) for r in results for s in r.scores if not s.passed]
    assert failures == []


async def test_the_harness_catches_a_copyleft_dependency_the_model_cleared():
    """The matrix raises the verdict back, and the correction is what the eval sees."""
    answers = PERFECT_ANSWERS | {"pygpl-cli": license_draft("compatible")}

    results = await run_cases(answering(answers))

    result, scores = scores_for(results, "pygpl-cli")
    assert result.verdict.verdict == LicenseCompatibility.INCOMPATIBLE
    assert not scores["no_corrections"].passed


async def test_the_harness_catches_a_relaxed_verdict_the_matrix_cannot_see():
    """Where no rule applies nothing overrules the model, so the scorers have to."""
    answers = PERFECT_ANSWERS | {"berkeley-store": license_draft("compatible")}

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "berkeley-store")
    assert not scores["never_relaxes"].passed
    assert not scores["verdict_accepted"].passed


async def test_the_harness_catches_an_uncited_escalation():
    answers = PERFECT_ANSWERS | {"chardet": license_draft("requires_human_review")}

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "chardet")
    assert not scores["clause_present"].passed


async def test_the_harness_catches_a_clause_about_another_license():
    answers = PERFECT_ANSWERS | {
        "pygpl-cli": license_draft("incompatible", "The MPL requires modified files to stay MPL.")
    }

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "pygpl-cli")
    assert not scores["clause_names_the_license"].passed


async def test_the_harness_catches_a_license_the_dependency_does_not_carry():
    answers = PERFECT_ANSWERS | {
        "gpl-utils": license_draft("incompatible", "GPL section 5 covers the whole work.")
    }

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "gpl-utils")
    assert not scores["no_lure"].passed


async def test_the_harness_catches_an_alternative_without_its_license():
    answers = PERFECT_ANSWERS | {
        "chardet": license_draft(
            "requires_human_review", "LGPL section 6 requires relinking.", ("charset-normalizer",)
        )
    }

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "chardet")
    assert not scores["alternatives_carry_a_license"].passed


async def test_the_harness_catches_action_language_in_the_clause():
    answers = PERFECT_ANSWERS | {
        "chardet": license_draft("requires_human_review", "LGPL section 6 bites; switch to attrs.")
    }

    results = await run_cases(answering(answers))

    _, scores = scores_for(results, "chardet")
    assert not scores["no_action_language"].passed


async def test_the_settled_cases_cost_no_llm_call():
    """A dependency under the project's own license, or with none at all, is not a judgment."""
    model = answering(PERFECT_ANSWERS)

    await run_cases(model)

    prompts = "\n".join(model.prompts)
    assert "attrs-mit" not in prompts
    assert "mystery-lib" not in prompts
    assert "legacy-bsd" not in prompts


async def test_the_scorecard_reports_every_scorer_and_the_model():
    results = await run_cases(answering(PERFECT_ANSWERS))

    rendered = scorecard(results, "scripted:perfect")

    assert "scripted:perfect" in rendered
    assert "never_relaxes" in rendered
    assert "100%" in rendered


async def test_the_scorecard_lists_what_failed():
    answers = PERFECT_ANSWERS | {"berkeley-store": license_draft("compatible")}

    rendered = scorecard(await run_cases(answering(answers)), "scripted:weak")

    assert "failures:" in rendered
    assert "license-outside-the-matrix" in rendered


def test_every_case_names_the_failure_mode_it_probes():
    assert all(case.probes for case in CASES)
    assert len({case.id for case in CASES}) == len(CASES)
