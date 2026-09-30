import asyncio
from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from packaging.version import InvalidVersion, Version

from src.agents.outcome import no_structure_reason
from src.agents.security_agent import build_security_agent, render_package
from src.config.llm_config import chat_model_for
from src.config.log_config import logger
from src.constants import ASSESS_SECURITY, MAX_CONCURRENT_LLM_CALLS
from src.domain.models import (
    EnrichedPackage,
    SecurityAssessment,
    SecurityVerdict,
    SecurityVerdictDraft,
    Severity,
    UpgradeDistance,
)


def known_identifiers(package: EnrichedPackage) -> set[str]:
    """Every advisory id and alias enrichment actually saw, upper-cased for matching."""
    identifiers = set()
    for vulnerability in package.vulnerabilities:
        identifiers.add(vulnerability.id.upper())
        identifiers.update(alias.upper() for alias in vulnerability.aliases)
    return identifiers - {""}


def reported_fixes(package: EnrichedPackage) -> set[str]:
    return {v.fixed_in for v in package.vulnerabilities if v.fixed_in}


def upgrade_distance(current: str, fixed: str | None) -> UpgradeDistance:
    """Derived from the two versions, never asked of the model.

    Semver distance is arithmetic, and arithmetic is exactly what an LLM is worst
    at and what a test can verify for free.
    """
    if fixed is None:
        return UpgradeDistance.UNKNOWN
    try:
        current_version, fixed_version = Version(current), Version(fixed)
    except InvalidVersion:
        return UpgradeDistance.UNKNOWN
    if fixed_version <= current_version:
        return UpgradeDistance.NONE
    current_release = current_version.release + (0, 0)
    fixed_release = fixed_version.release + (0, 0)
    if fixed_release[0] != current_release[0]:
        return UpgradeDistance.MAJOR
    if fixed_release[1] != current_release[1]:
        return UpgradeDistance.MINOR
    return UpgradeDistance.PATCH


def verify(package: EnrichedPackage, draft: SecurityVerdictDraft) -> SecurityVerdict:
    """Reconciles the model's judgment with the facts enrichment gathered.

    Anything the model asserted that the input does not support is dropped and
    recorded in `corrections`, so a hallucination becomes visible data rather
    than a silent finding downstream.
    """
    corrections: list[str] = []
    known = known_identifiers(package)
    applicable = [cve for cve in draft.applicable_cves if cve.upper() in known]
    if invented := [cve for cve in draft.applicable_cves if cve.upper() not in known]:
        corrections.append(f"dropped identifiers absent from the advisories: {', '.join(invented)}")

    fixed_in = draft.fixed_in
    if fixed_in and fixed_in not in reported_fixes(package):
        corrections.append(f"dropped fixed_in '{fixed_in}': no advisory reports that fix")
        fixed_in = None

    severity = draft.max_severity
    if not applicable:
        if severity not in (Severity.NONE, Severity.UNKNOWN):
            corrections.append(f"reset severity {severity} to NONE: no applicable advisories remain")
        severity, fixed_in = Severity.NONE, None

    return SecurityVerdict(
        package=package.name,
        version=package.version,
        applicable_cves=applicable,
        max_severity=severity,
        fixed_in=fixed_in,
        upgrade_distance=(
            UpgradeDistance.NONE if not applicable else upgrade_distance(package.version, fixed_in)
        ),
        evidence=draft.evidence,
        corrections=corrections,
    )


def clean_verdict(package: EnrichedPackage) -> SecurityVerdict:
    """No advisories means no judgment to make, so no LLM call is made."""
    return SecurityVerdict(
        package=package.name,
        version=package.version,
        max_severity=Severity.NONE,
        upgrade_distance=UpgradeDistance.NONE,
        evidence="OSV.dev reported no advisories affecting this version.",
    )


def failed_verdict(package: EnrichedPackage, error: Exception) -> SecurityVerdict:
    return SecurityVerdict(
        package=package.name,
        version=package.version,
        max_severity=Severity.UNKNOWN,
        upgrade_distance=UpgradeDistance.UNKNOWN,
        evidence="",
        error=f"{type(error).__name__}: {error}",
    )


class SecurityAssessor:
    def __init__(self, model: BaseChatModel, max_concurrency: int = MAX_CONCURRENT_LLM_CALLS) -> None:
        self._agent = build_security_agent(model)
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def assess(self, packages: Sequence[EnrichedPackage]) -> list[SecurityVerdict]:
        return list(await asyncio.gather(*(self.assess_one(p) for p in packages)))

    async def assess_one(self, package: EnrichedPackage) -> SecurityVerdict:
        if not package.vulnerabilities:
            return clean_verdict(package)
        async with self._semaphore:
            try:
                result = await self._agent.ainvoke(
                    {"messages": [{"role": "user", "content": render_package(package)}]}
                )
            except Exception as e:
                logger.warning(f"Security assessment failed for {package.name}: {e}")
                return failed_verdict(package, e)
        draft = result.get("structured_response")
        if not isinstance(draft, SecurityVerdictDraft):
            return failed_verdict(
                package, ValueError(no_structure_reason(result, "structured verdict"))
            )
        return verify(package, draft)


async def assess_security_tool(
    enriched: Sequence[EnrichedPackage],
    model: BaseChatModel | None = None,
    max_concurrency: int = MAX_CONCURRENT_LLM_CALLS,
) -> SecurityAssessment:
    """Assess the security posture of enriched packages using an LLM agent grounded in OSV advisories.

    For each package, determines which known vulnerabilities apply to the
    pinned version, rates contextual severity, identifies the minimal fixed
    version, and classifies the upgrade distance to that fix (patch, minor,
    major). Reasoning is grounded in the OSV advisory text gathered during
    enrichment. This tool characterizes risk only; it never recommends actions.
    Feed its output to triage_dependencies for action planning.

    Args:
        enriched: Output of enrich_dependencies (list of EnrichedPackage).
        model: Chat model to use; defaults to the model configured for this tool.
        max_concurrency: Cap on concurrent LLM calls.

    Returns:
        A SecurityAssessment holding one SecurityVerdict per package with fields:
        package, applicable_cves, max_severity, fixed_in, upgrade_distance,
        evidence, and any corrections applied to the model's output.
    """
    if not enriched:
        return SecurityAssessment()
    model = model or chat_model_for(ASSESS_SECURITY)
    logger.info(f"Assessing security of {len(enriched)} packages with {type(model).__name__}")
    verdicts = await SecurityAssessor(model, max_concurrency).assess(enriched)
    return SecurityAssessment(verdicts=verdicts)
