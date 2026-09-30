import asyncio
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel

from src.agents.license_agent import build_license_agent, render_package
from src.agents.outcome import no_structure_reason
from src.config.llm_config import chat_model_for
from src.config.log_config import logger
from src.constants import ASSESS_LICENSE, MAX_CONCURRENT_LLM_CALLS
from src.domain.models import (
    Distribution,
    EnrichedPackage,
    LicenseAssessment,
    LicenseCompatibility,
    LicenseVerdict,
    LicenseVerdictDraft,
)
from src.rag.retrieval import NullRetriever, Retriever, license_retriever
from src.rag.tools import build_license_search_tool
from src.resources.license_matrix import STRICTNESS, floor_verdict
from src.resources.spdx import normalize_license

NO_PROJECT_LICENSE = (
    "The project declares no license this tool could resolve, so there is nothing to "
    "compare the dependency against."
)
UNRESOLVED_LICENSE = (
    "The declared license does not resolve to a single SPDX identifier, so the "
    "obligations it imposes cannot be established without reading it."
)
SAME_LICENSE = (
    "The dependency is under the project's own license, so it imposes no obligation "
    "the project has not already accepted."
)


def verdict_for(
    package: EnrichedPackage,
    verdict: LicenseCompatibility,
    license: str | None = None,
    conflicting_clause: str = "",
    **fields,
) -> LicenseVerdict:
    return LicenseVerdict(
        package=package.name,
        version=package.version,
        license=license,
        license_declared=package.license_declared,
        verdict=verdict,
        conflicting_clause=conflicting_clause,
        **fields,
    )


def review_verdict(package: EnrichedPackage, reason: str, license: str | None = None) -> LicenseVerdict:
    """A fact the tool established itself, so no model is asked and none can override it."""
    return verdict_for(
        package,
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        license=license,
        conflicting_clause=reason,
    )


def failed_verdict(package: EnrichedPackage, license: str | None, error: Exception) -> LicenseVerdict:
    """A failure escalates rather than clears: an unanswered license question is open."""
    return verdict_for(
        package,
        LicenseCompatibility.REQUIRES_HUMAN_REVIEW,
        license=license,
        error=f"{type(error).__name__}: {error}",
    )


def verify(
    package: EnrichedPackage,
    license: str,
    floor: LicenseCompatibility | None,
    draft: LicenseVerdictDraft,
) -> LicenseVerdict:
    """Reconciles the model's judgment with the curated matrix.

    The matrix is a floor, never a ceiling: the agent may escalate a row, and every
    relaxation of one is reversed and recorded in `corrections`, so an agent that
    talks itself out of a copyleft obligation becomes visible data rather than a
    silently cleared package.
    """
    corrections: list[str] = []
    verdict = draft.verdict
    if floor is not None and STRICTNESS[verdict] < STRICTNESS[floor]:
        corrections.append(
            f"raised verdict '{verdict}' to '{floor}': the compatibility matrix is authoritative "
            f"and the model may not be more permissive than it"
        )
        verdict = floor

    clause = draft.conflicting_clause.strip()
    alternatives = list(draft.suggested_alternatives)
    if verdict == LicenseCompatibility.COMPATIBLE:
        if clause:
            corrections.append("dropped the conflicting clause: no obligation conflicts")
            clause = ""
        if alternatives:
            corrections.append(
                f"dropped suggested alternatives {', '.join(alternatives)}: nothing needs replacing"
            )
            alternatives = []
    elif not clause:
        corrections.append(f"verdict '{verdict}' cites no conflicting clause")

    return verdict_for(
        package,
        verdict,
        license=license,
        conflicting_clause=clause,
        suggested_alternatives=alternatives,
        corrections=corrections,
    )


def retrieval_tools(retriever: Retriever | None) -> list:
    """The clause corpus as a tool, or no tools at all when retrieval is turned off."""
    retriever = retriever if retriever is not None else license_retriever()
    if isinstance(retriever, NullRetriever):
        return []
    return [build_license_search_tool(retriever)]


class LicenseAssessor:
    def __init__(
        self,
        model: BaseChatModel,
        project_license: str | None,
        distribution: Distribution,
        max_concurrency: int = MAX_CONCURRENT_LLM_CALLS,
        retriever: Retriever | None = None,
    ) -> None:
        self._agent = build_license_agent(model, retrieval_tools(retriever))
        self._project_license = project_license
        self._distribution = distribution
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def assess(self, packages: Sequence[EnrichedPackage]) -> list[LicenseVerdict]:
        return list(await asyncio.gather(*(self.assess_one(p) for p in packages)))

    async def assess_one(self, package: EnrichedPackage) -> LicenseVerdict:
        license = normalize_license(package.license_declared)
        if self._project_license is None:
            return review_verdict(package, NO_PROJECT_LICENSE, license)
        if license is None:
            return review_verdict(package, UNRESOLVED_LICENSE)
        if license == self._project_license:
            return verdict_for(package, LicenseCompatibility.COMPATIBLE, license=license)
        floor = floor_verdict(license, self._project_license, self._distribution)
        async with self._semaphore:
            try:
                result = await self._agent.ainvoke(
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": render_package(
                                    package, license, self._project_license, self._distribution
                                ),
                            }
                        ]
                    }
                )
            except Exception as e:
                logger.warning(f"License assessment failed for {package.name}: {e}")
                return failed_verdict(package, license, e)
        draft = result.get("structured_response")
        if not isinstance(draft, LicenseVerdictDraft):
            return failed_verdict(
                package, license, ValueError(no_structure_reason(result, "structured verdict"))
            )
        return verify(package, license, floor, draft)


async def assess_license_tool(
    enriched: Sequence[EnrichedPackage],
    project_license: str | None,
    distribution: Distribution = Distribution.BINARY,
    model: BaseChatModel | None = None,
    max_concurrency: int = MAX_CONCURRENT_LLM_CALLS,
    retriever: Retriever | None = None,
) -> LicenseAssessment:
    """Assess license compatibility of each dependency against the project's declared license.

    Loads the curated license-compatibility matrix into context (CAG) and grounds
    clause citations in SPDX license text (RAG) to determine, for each dependency,
    whether its license is compatible with the project's declared license and
    distribution model. Verdicts are 'compatible', 'incompatible', or
    'requires_human_review'. Ambiguous cases are always escalated to human review,
    never resolved automatically. This tool characterizes compatibility only; it
    never recommends actions.

    Args:
        enriched: Output of enrich_dependencies (list of EnrichedPackage).
        project_license: SPDX identifier of the project's own license, taken from
            scan_project output. When it is absent or unresolvable, every package is
            escalated to human review rather than assessed against a guess.
        distribution: How the project is shipped: 'binary', 'source', or 'saas'.
            Defaults to 'binary', the model under which the most obligations fire.
        model: Chat model to use; defaults to the model configured for this tool.
        max_concurrency: Cap on concurrent LLM calls.
        retriever: Retriever over the SPDX clause corpus; defaults to the configured
            one. Pass a NullRetriever to assess without retrieval.

    Returns:
        A LicenseAssessment holding one LicenseVerdict per package with fields:
        package, license, verdict, conflicting_clause, suggested_alternatives, and
        any corrections applied to the model's output.
    """
    normalized_project_license = normalize_license(project_license)
    if not enriched:
        return LicenseAssessment(
            project_license=normalized_project_license, distribution=distribution
        )
    if project_license and normalized_project_license is None:
        logger.warning(f"Project license '{project_license}' did not resolve to an SPDX identifier")
    model = model or chat_model_for(ASSESS_LICENSE)
    logger.info(
        f"Assessing licenses of {len(enriched)} packages against "
        f"{normalized_project_license} ({distribution.value}) with {type(model).__name__}"
    )
    assessor = LicenseAssessor(
        model, normalized_project_license, distribution, max_concurrency, retriever
    )
    return LicenseAssessment(
        project_license=normalized_project_license,
        distribution=distribution,
        verdicts=await assessor.assess(enriched),
    )
