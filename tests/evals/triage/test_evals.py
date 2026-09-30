import asyncio

import pytest

from src.config.llm_config import build_chat_model, load_llm_config
from src.constants import TRIAGE_DEPENDENCIES
from tests.evals.scoring import scorecard
from tests.evals.triage.dataset import CASES
from tests.evals.triage.runner import run_cases

pytestmark = pytest.mark.eval


@pytest.fixture(scope="module")
def results():
    config = load_llm_config(TRIAGE_DEPENDENCIES)
    outcome = asyncio.run(run_cases(build_chat_model(config)))
    print("\n" + scorecard(outcome, config.model))
    return {result.case.id: result for result in outcome}


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_case_scores_clean(case, results):
    failures = [f"{s.name}: {s.detail}" for s in results[case.id].scores if not s.passed]

    assert not failures, f"{case.id} ({case.probes}) -> " + "; ".join(failures)
