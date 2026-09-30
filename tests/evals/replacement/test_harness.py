"""The eval harness must be able to fail, or it measures nothing.

Scripting a ReAct loop takes a turn-by-turn plan rather than a single answer: the
model looks a candidate up, then answers. `sequences` on the scripted model plays
one turn per invocation, which is what lets a fake model exercise the real loop.
"""

from tests.evals.replacement.dataset import CASES
from tests.evals.replacement.runner import run_cases
from tests.support import ScriptedChatModel, candidate, replacement_draft, tool_call

BEST = {
    "pycrypto": "pycryptodome",
    "nose": "pytest",
    "raven": "sentry-sdk",
    "mysql-python": "mysqlclient",
}


def perfect_plan(package: str) -> list:
    best = BEST[package]
    return [
        tool_call("lookup_package", name=best),
        tool_call("repository_activity", name=best),
        replacement_draft(
            (candidate(name=best, evidence=f"{best} released recently and is widely depended on."),),
            (f"iter1: {package} is being replaced", f"iter2: verified {best} on PyPI"),
        ),
    ]


def scripted(plans: dict[str, list]) -> ScriptedChatModel:
    return ScriptedChatModel(sequences=plans, schema_name="ReplacementSearchDraft")


PERFECT = {case.package: perfect_plan(case.package) for case in CASES}


def score_for(results, package: str, scorer: str):
    result = next(r for r in results if r.case.package == package)
    return result, next(s for s in result.scores if s.name == scorer)


async def test_a_model_that_verifies_its_candidates_scores_clean():
    results = await run_cases(scripted(PERFECT))

    failures = [(r.case.id, s.name, s.detail) for r in results for s in r.scores if not s.passed]
    assert failures == []


async def test_the_harness_catches_a_candidate_that_was_never_looked_up():
    plans = PERFECT | {"pycrypto": [replacement_draft((candidate(name="pycryptodome"),), ("guessed",))]}

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "pycrypto", "no_unverified_candidates")
    assert not score.passed


async def test_the_harness_catches_an_answer_produced_without_any_lookup():
    plans = PERFECT | {"nose": [replacement_draft((), ("no idea",))]}

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "nose", "used_the_tools")
    assert not score.passed


async def test_the_harness_catches_a_dormant_candidate():
    plans = PERFECT | {
        "nose": [
            tool_call("lookup_package", name="nose2"),
            replacement_draft((candidate(name="nose2", evidence="successor by name"),), ("iter1",)),
        ]
    }

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "nose", "finds_an_expected_candidate")
    assert not score.passed


async def test_the_harness_catches_recommending_a_disqualified_fork():
    plans = PERFECT | {
        "pycrypto": [
            tool_call("lookup_package", name="pycrypto-fork"),
            tool_call("lookup_package", name="pycryptodome"),
            replacement_draft(
                (
                    candidate(name="pycrypto-fork", evidence="same namespace"),
                    candidate(name="pycryptodome", evidence="live fork"),
                ),
                ("iter1",),
            ),
        ]
    }

    results = await run_cases(scripted(plans))

    result, score = score_for(results, "pycrypto", "avoids_the_traps")
    assert not score.passed
    assert not next(s for s in result.scores if s.name == "top_candidate_is_maintained").passed


async def test_the_harness_catches_a_candidate_with_no_evidence():
    plans = PERFECT | {
        "raven": [
            tool_call("lookup_package", name="sentry-sdk"),
            replacement_draft((candidate(name="sentry-sdk", evidence="  "),), ("iter1",)),
        ]
    }

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "raven", "evidence_present")
    assert not score.passed


async def test_the_harness_catches_a_missing_effort_estimate():
    plans = PERFECT | {
        "mysql-python": [
            tool_call("lookup_package", name="mysqlclient"),
            replacement_draft(
                (candidate(name="mysqlclient", migration_effort="unknown"),), ("iter1",)
            ),
        ]
    }

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "mysql-python", "effort_estimated")
    assert not score.passed


async def test_the_harness_catches_a_missing_trace():
    plans = PERFECT | {
        "raven": [
            tool_call("lookup_package", name="sentry-sdk"),
            replacement_draft((candidate(name="sentry-sdk"),), ()),
        ]
    }

    results = await run_cases(scripted(plans))

    _, score = score_for(results, "raven", "trace_written")
    assert not score.passed
