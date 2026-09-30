"""The eval harness must be able to fail, or it measures nothing."""

from tests.evals.maintenance.dataset import CASES
from tests.evals.maintenance.runner import run_cases
from tests.support import ScriptedChatModel, maintenance_draft

PERFECT_ANSWERS = {
    case.package.name: maintenance_draft(
        status=case.expected_status.value,
        confidence=round((case.min_confidence + case.max_confidence) / 2, 2),
        evidence=f"The recency figures supplied for {case.package.name}, over 5 releases.",
    )
    for case in CASES
}


def answering(answers) -> ScriptedChatModel:
    return ScriptedChatModel(answers=answers, schema_name="MaintenanceVerdictDraft")


def score_for(results, package: str, scorer: str):
    result = next(r for r in results if r.case.package.name == package)
    return result, next(s for s in result.scores if s.name == scorer)


async def test_a_model_that_matches_the_labels_scores_clean():
    results = await run_cases(answering(PERFECT_ANSWERS))

    failures = [(r.case.id, s.name, s.detail) for r in results for s in r.scores if not s.passed]
    assert failures == []


async def test_the_harness_catches_a_declining_package_called_healthy():
    """nu-queue has no floor to rescue it — the guard rails cannot catch this one."""
    answers = PERFECT_ANSWERS | {
        "nu-queue": maintenance_draft("healthy", 0.9, "committed 30 days ago, looks fine")
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "nu-queue", "never_downplays")
    assert not score.passed


async def test_the_harness_catches_a_guard_rail_intervention():
    """An archived repository called healthy is corrected — and the correction is the signal."""
    answers = PERFECT_ANSWERS | {
        "gamma-orm": maintenance_draft("healthy", 0.7, "commits as recently as 33 days ago")
    }

    results = await run_cases(answering(answers))

    result, score = score_for(results, "gamma-orm", "no_corrections")
    assert result.verdict.corrections != []
    assert not score.passed


async def test_the_harness_catches_evidence_that_cites_nothing():
    answers = PERFECT_ANSWERS | {
        "alpha-http": maintenance_draft("healthy", 0.8, "This is a well known library.")
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "alpha-http", "evidence_cites_a_measurement")
    assert not score.passed


async def test_the_harness_catches_an_invented_signal():
    answers = PERFECT_ANSWERS | {
        "lambda-utils": maintenance_draft("healthy", 0.5, "120 contributors and 40 recent commits")
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "lambda-utils", "no_invented_signal")
    assert not score.passed


async def test_the_harness_catches_an_overconfident_verdict():
    answers = PERFECT_ANSWERS | {
        "mu-serial": maintenance_draft("healthy", 0.99, "released 78 days ago")
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "mu-serial", "confidence_calibrated")
    assert not score.passed


async def test_the_harness_catches_a_recommendation():
    answers = PERFECT_ANSWERS | {
        "beta-crypto": maintenance_draft(
            "abandoned", 0.9, "Archived in 2014; you should switch to something maintained."
        )
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "beta-crypto", "no_action_language")
    assert not score.passed


async def test_the_harness_catches_a_shrug_where_signals_existed():
    answers = PERFECT_ANSWERS | {
        "iota-web": maintenance_draft("unknown", 0.0, "1400 open issues, hard to say")
    }

    results = await run_cases(answering(answers))

    _, score = score_for(results, "iota-web", "unknown_is_last_resort")
    assert not score.passed
