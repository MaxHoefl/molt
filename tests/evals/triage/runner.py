"""Runs the golden dataset against a chat model and prints a scorecard.

    uv run python -m tests.evals.triage.runner
    MOLT_SERVER_TRIAGE_DEPENDENCIES_LLM_MODEL="openai:gpt-5.5" uv run python -m tests.evals.triage.runner

Every case is triaged in one call, because that is how the tool works: the manager
sees the whole project at once, and a rationale written with eight packages in view
is a different artifact from one written in isolation.
"""

from langchain_core.language_models import BaseChatModel

from src.constants import TRIAGE_DEPENDENCIES
from src.tools.triage_dependencies import triage_dependencies_tool
from tests.evals.scoring import CaseResult, run_scorecard
from tests.evals.triage.dataset import CASES, PackageCase
from tests.evals.triage.scorers import score_case


async def run_cases(model: BaseChatModel, cases: tuple[PackageCase, ...] = CASES) -> list[CaseResult]:
    plan = await triage_dependencies_tool(
        [case.security for case in cases],
        [case.license for case in cases],
        [case.maintenance for case in cases],
        project="/tmp/eval-project",
        model=model,
    )
    entries = {entry.package: entry for entry in plan.entries}
    return [
        CaseResult(
            case=case,
            verdict=entries[case.package],
            scores=score_case(case, entries[case.package]),
        )
        for case in cases
    ]


def main() -> int:
    return run_scorecard(TRIAGE_DEPENDENCIES, run_cases)


if __name__ == "__main__":
    raise SystemExit(main())
