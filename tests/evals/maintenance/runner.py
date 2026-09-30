"""Runs the golden dataset against a chat model and prints a scorecard.

    uv run python -m tests.evals.maintenance.runner
    MOLT_SERVER_ASSESS_MAINTENANCE_LLM_MODEL="ollama:llama3.1" uv run python -m tests.evals.maintenance.runner

The scorecard is the artifact you compare between models: same cases, same
scorers, one row per scorer. Swap the env var, rerun, diff the two tables.
"""

from langchain_core.language_models import BaseChatModel

from src.constants import ASSESS_MAINTENANCE
from src.tools.assess_maintenance import assess_maintenance_tool
from tests.evals.maintenance.dataset import CASES, TODAY, EvalCase
from tests.evals.maintenance.scorers import score_case
from tests.evals.scoring import CaseResult, run_scorecard


async def run_cases(model: BaseChatModel, cases: tuple[EvalCase, ...] = CASES) -> list[CaseResult]:
    """One batched call for the whole dataset, over a frozen `today`.

    The reference date is pinned in the dataset rather than taken from the clock,
    because "days since last commit" is an input to every label here — letting it
    drift would silently relabel the cases as the months pass.
    """
    assessment = await assess_maintenance_tool(
        [case.package for case in cases], model=model, today=TODAY
    )
    return [
        CaseResult(case=case, verdict=verdict, scores=score_case(case, verdict))
        for case, verdict in zip(cases, assessment.verdicts, strict=True)
    ]


def main() -> int:
    return run_scorecard(ASSESS_MAINTENANCE, run_cases)


if __name__ == "__main__":
    raise SystemExit(main())
