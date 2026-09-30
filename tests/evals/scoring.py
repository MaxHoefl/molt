"""Scaffolding shared by every eval suite: one score, one case result, one scorecard.

A suite supplies a dataset and its scorers; everything below is the same whether the
verdict under test is a security verdict or a license verdict, and keeping it in one
place is what makes two suites' scorecards directly comparable.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel

from src.config.llm_config import build_chat_model, load_llm_config


@dataclass(frozen=True)
class Score:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class CaseResult:
    case: Any
    verdict: Any
    scores: list[Score]

    @property
    def passed(self) -> bool:
        return all(score.passed for score in self.scores)


def scorecard(results: list[CaseResult], model_name: str) -> str:
    scorer_names = [score.name for score in results[0].scores]
    lines = [
        f"model: {model_name}",
        f"cases: {len(results)}   passed: {sum(r.passed for r in results)}",
        "",
        f"{'scorer':<28}{'pass':>6}{'rate':>8}",
        "-" * 42,
    ]
    for name in scorer_names:
        passed = sum(1 for r in results for s in r.scores if s.name == name and s.passed)
        lines.append(f"{name:<28}{passed:>3}/{len(results):<2}{passed / len(results):>8.0%}")
    failures = [
        f"  {r.case.id:<26} {s.name}: {s.detail}"
        for r in results
        for s in r.scores
        if not s.passed
    ]
    if failures:
        lines += ["", "failures:", *failures]
    return "\n".join(lines)


def run_scorecard(
    tool_name: str, run_cases: Callable[[BaseChatModel], Awaitable[list[CaseResult]]]
) -> int:
    """Resolves the tool's configured model, runs its cases, prints the scorecard."""
    config = load_llm_config(tool_name)
    if not os.environ.get("ANTHROPIC_API_KEY") and config.provider == "anthropic":
        print("ANTHROPIC_API_KEY is not set; export it or point the tool at another provider.")
        return 2
    results = asyncio.run(run_cases(build_chat_model(config)))
    print(scorecard(results, config.model))
    return 0 if all(result.passed for result in results) else 1
