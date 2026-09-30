"""Runs the golden dataset against a chat model and prints a scorecard.

    uv run python -m tests.evals.replacement.runner
    MOLT_SERVER_FIND_REPLACEMENT_LLM_MODEL="openai:gpt-5.5" uv run python -m tests.evals.replacement.runner

Cases run one at a time, not concurrently: each is its own ReAct loop with its own
toolbox, and the registry it reads is a fixture, so there is nothing to gain from
overlapping them and a legible trace to lose.
"""

from datetime import date

from langchain_core.language_models import BaseChatModel

from src.constants import FIND_REPLACEMENT
from src.tools.find_replacement import find_replacement_tool
from tests.evals.replacement.dataset import CASES, EvalCase
from tests.evals.replacement.registry import registry
from tests.evals.replacement.scorers import score_case
from tests.evals.scoring import CaseResult, run_scorecard

TODAY = date(2026, 9, 1)


async def run_case(model: BaseChatModel, case: EvalCase) -> CaseResult:
    search = await find_replacement_tool(
        case.package,
        context=case.context,
        reason=case.reason,
        model=model,
        session=registry(),
        today=TODAY,
    )
    return CaseResult(case=case, verdict=search, scores=score_case(case, search))


async def run_cases(model: BaseChatModel, cases: tuple[EvalCase, ...] = CASES) -> list[CaseResult]:
    return [await run_case(model, case) for case in cases]


def main() -> int:
    return run_scorecard(FIND_REPLACEMENT, run_cases)


if __name__ == "__main__":
    raise SystemExit(main())
