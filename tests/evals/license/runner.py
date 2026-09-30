"""Runs the golden dataset against a chat model and prints a scorecard.

    uv run python -m tests.evals.license.runner
    MOLT_SERVER_ASSESS_LICENSE_LLM_MODEL="openai:gpt-5.5" uv run python -m tests.evals.license.runner

The scorecard is the artifact you compare between models: same cases, same scorers,
one row per scorer. Swap the env var, rerun, diff the two tables.
"""

from collections import defaultdict

from langchain_core.language_models import BaseChatModel

from src.constants import ASSESS_LICENSE
from src.domain.models import LicenseVerdict
from src.tools.assess_license import assess_license_tool
from tests.evals.license.dataset import CASES, EvalCase
from tests.evals.license.scorers import score_case
from tests.evals.scoring import CaseResult, run_scorecard


async def run_cases(model: BaseChatModel, cases: tuple[EvalCase, ...] = CASES) -> list[CaseResult]:
    """Cases sharing a project license and distribution model are assessed in one call.

    The tool takes those two as arguments, not per package, so grouping is what lets
    the eval exercise the same batched path production uses instead of a one-package
    special case.
    """
    grouped: dict[tuple[str | None, str], list[EvalCase]] = defaultdict(list)
    for case in cases:
        grouped[(case.project_license, case.distribution)].append(case)

    verdicts: dict[str, LicenseVerdict] = {}
    for (project_license, distribution), group in grouped.items():
        assessment = await assess_license_tool(
            [case.package for case in group], project_license, distribution, model=model
        )
        verdicts |= {
            case.id: verdict
            for case, verdict in zip(group, assessment.verdicts, strict=True)
        }
    return [
        CaseResult(case=case, verdict=verdicts[case.id], scores=score_case(case, verdicts[case.id]))
        for case in cases
    ]


def main() -> int:
    return run_scorecard(ASSESS_LICENSE, run_cases)


if __name__ == "__main__":
    raise SystemExit(main())
