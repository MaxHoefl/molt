"""Runs the golden dataset against a chat model and prints a scorecard.

    uv run python -m tests.evals.security.runner
    MOLT_SERVER_ASSESS_SECURITY_LLM_MODEL="openai:gpt-5.5" uv run python -m tests.evals.security.runner

The scorecard is the artifact you compare between models: same cases, same
scorers, one row per scorer. Swap the env var, rerun, diff the two tables.
"""

from langchain_core.language_models import BaseChatModel

from src.constants import ASSESS_SECURITY
from src.tools.assess_security import assess_security_tool
from tests.evals.scoring import CaseResult, run_scorecard
from tests.evals.security.dataset import CASES, EvalCase
from tests.evals.security.scorers import score_case


async def run_cases(model: BaseChatModel, cases: tuple[EvalCase, ...] = CASES) -> list[CaseResult]:
    assessment = await assess_security_tool([case.package for case in cases], model=model)
    return [
        CaseResult(case=case, verdict=verdict, scores=score_case(case, verdict))
        for case, verdict in zip(cases, assessment.verdicts, strict=True)
    ]


def main() -> int:
    return run_scorecard(ASSESS_SECURITY, run_cases)


if __name__ == "__main__":
    raise SystemExit(main())
