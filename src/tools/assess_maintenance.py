import asyncio
from collections.abc import Sequence
from datetime import date

from langchain_core.language_models import BaseChatModel

from src.agents.maintenance_agent import build_maintenance_agent, render_package
from src.agents.outcome import no_structure_reason
from src.config.llm_config import chat_model_for
from src.config.log_config import logger
from src.constants import (
    ABANDONED_AFTER_DAYS,
    ASSESS_MAINTENANCE,
    DECLINING_AFTER_DAYS,
    MAX_CONCURRENT_LLM_CALLS,
)
from src.domain.models import (
    EnrichedPackage,
    MaintenanceAssessment,
    MaintenanceSignals,
    MaintenanceStatus,
    MaintenanceVerdict,
    MaintenanceVerdictDraft,
)

# How worrying a status is. Used to take the more pessimistic of two, never the
# more optimistic — the same asymmetry the license matrix enforces, for the same
# reason: the cheap error is worrying about a package that turns out to be fine.
CONCERN: dict[MaintenanceStatus, int] = {
    MaintenanceStatus.UNKNOWN: -1,
    MaintenanceStatus.HEALTHY: 0,
    MaintenanceStatus.DECLINING: 1,
    MaintenanceStatus.ABANDONED: 2,
}

NO_SIGNALS = (
    "No repository or release signals were available for this package, so its "
    "maintenance state could not be established."
)
ARCHIVED = "the repository is archived, which settles the question on its own"


def days_since(then: date | None, today: date) -> int | None:
    return (today - then).days if then is not None else None


def signals_for(package: EnrichedPackage, today: date) -> MaintenanceSignals:
    """Turns dates into durations before the model ever sees them.

    Date arithmetic is the part of this judgment a test can verify for free, so the
    model is handed the answer rather than the inputs — exactly as `upgrade_distance`
    does for semver in the security tool.
    """
    health = package.repo_health
    return MaintenanceSignals(
        archived=health.archived if health else None,
        days_since_last_commit=days_since(health.last_commit if health else None, today),
        days_since_last_release=days_since(package.latest_release_date, today),
        open_issues=health.open_issues if health else None,
        contributors=health.contributors if health else None,
        releases_published=len(package.available_versions) or None,
    )


def floor_status(signals: MaintenanceSignals) -> MaintenanceStatus | None:
    """The least worrying status the measurements themselves permit.

    An archived repository is abandoned whatever prose surrounds it; three years of
    silence on both the commit log and the release history is abandonment in all but
    name. Below that, eighteen months of silence is at least decline. Everything
    softer than these is the model's call.
    """
    if signals.archived:
        return MaintenanceStatus.ABANDONED
    quiet = [
        days
        for days in (signals.days_since_last_commit, signals.days_since_last_release)
        if days is not None
    ]
    if not quiet:
        return None
    if min(quiet) >= ABANDONED_AFTER_DAYS:
        return MaintenanceStatus.ABANDONED
    if min(quiet) >= DECLINING_AFTER_DAYS:
        return MaintenanceStatus.DECLINING
    return None


def floor_reason(signals: MaintenanceSignals) -> str:
    if signals.archived:
        return ARCHIVED
    quiet = min(
        days
        for days in (signals.days_since_last_commit, signals.days_since_last_release)
        if days is not None
    )
    return f"nothing has moved for {quiet} days"


def verify(
    package: EnrichedPackage, signals: MaintenanceSignals, draft: MaintenanceVerdictDraft
) -> MaintenanceVerdict:
    """Reconciles the model's classification with what the measurements already prove.

    The model may be more worried than the signals require — a two-person project with
    a rising issue count is a judgment call, and that is what it is here for. It may
    not be less worried: an archived repository does not become healthy because the
    prose was persuasive.
    """
    corrections: list[str] = []
    status = draft.status
    floor = floor_status(signals)
    if floor is not None and CONCERN[status] < CONCERN[floor]:
        corrections.append(
            f"raised status '{status}' to '{floor}': {floor_reason(signals)}"
        )
        status = floor

    evidence = draft.evidence.strip()
    confidence = draft.confidence
    if not evidence:
        corrections.append("no evidence cited")
    if status == MaintenanceStatus.UNKNOWN and confidence > 0.0:
        corrections.append(f"reset confidence {confidence} to 0.0: the status is unknown")
        confidence = 0.0

    return MaintenanceVerdict(
        package=package.name,
        version=package.version,
        status=status,
        confidence=round(confidence, 2),
        evidence=evidence,
        signals=signals,
        corrections=corrections,
    )


def unknown_verdict(package: EnrichedPackage, signals: MaintenanceSignals) -> MaintenanceVerdict:
    """Nothing was measured, so there is no judgment to ask for and no LLM call is made."""
    return MaintenanceVerdict(
        package=package.name,
        version=package.version,
        status=MaintenanceStatus.UNKNOWN,
        confidence=0.0,
        evidence=NO_SIGNALS,
        signals=signals,
    )


def failed_verdict(
    package: EnrichedPackage, signals: MaintenanceSignals, error: Exception
) -> MaintenanceVerdict:
    """A failure falls back to whatever the measurements alone establish, and no further."""
    floor = floor_status(signals)
    return MaintenanceVerdict(
        package=package.name,
        version=package.version,
        status=floor or MaintenanceStatus.UNKNOWN,
        confidence=0.0,
        evidence=f"Classified from signals alone: {floor_reason(signals)}." if floor else "",
        signals=signals,
        error=f"{type(error).__name__}: {error}",
    )


class MaintenanceAssessor:
    def __init__(
        self,
        model: BaseChatModel,
        today: date | None = None,
        max_concurrency: int = MAX_CONCURRENT_LLM_CALLS,
    ) -> None:
        self._agent = build_maintenance_agent(model)
        self._today = today or date.today()
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def assess(self, packages: Sequence[EnrichedPackage]) -> list[MaintenanceVerdict]:
        return list(await asyncio.gather(*(self.assess_one(p) for p in packages)))

    async def assess_one(self, package: EnrichedPackage) -> MaintenanceVerdict:
        signals = signals_for(package, self._today)
        if signals.is_empty:
            return unknown_verdict(package, signals)
        async with self._semaphore:
            try:
                result = await self._agent.ainvoke(
                    {"messages": [{"role": "user", "content": render_package(package, signals)}]}
                )
            except Exception as e:
                logger.warning(f"Maintenance assessment failed for {package.name}: {e}")
                return failed_verdict(package, signals, e)
        draft = result.get("structured_response")
        if not isinstance(draft, MaintenanceVerdictDraft):
            return failed_verdict(
                package, signals, ValueError(no_structure_reason(result, "structured verdict"))
            )
        return verify(package, signals, draft)


async def assess_maintenance_tool(
    enriched: Sequence[EnrichedPackage],
    model: BaseChatModel | None = None,
    today: date | None = None,
    max_concurrency: int = MAX_CONCURRENT_LLM_CALLS,
) -> MaintenanceAssessment:
    """Assess the maintenance health of each dependency from repository activity signals.

    Classifies each package as 'healthy', 'declining', 'abandoned' or 'unknown' from
    last commit and release recency, archived status, contributor count (bus factor),
    open-issue pressure and release count. Date arithmetic is computed before the model
    is asked, and a classification is never allowed to be more optimistic than the
    measurements permit. Returns a confidence score and a concise evidence summary per
    package. This tool characterizes maintenance state only; it never recommends actions.

    Args:
        enriched: Output of enrich_dependencies (list of EnrichedPackage).
        model: Chat model to use; defaults to the model configured for this tool.
        today: Reference date for the recency arithmetic; defaults to today.
        max_concurrency: Cap on concurrent LLM calls.

    Returns:
        A MaintenanceAssessment holding one MaintenanceVerdict per package with fields:
        package, status, confidence, evidence, the measured signals, and any corrections
        applied to the model's output.
    """
    if not enriched:
        return MaintenanceAssessment()
    model = model or chat_model_for(ASSESS_MAINTENANCE)
    logger.info(f"Assessing maintenance of {len(enriched)} packages with {type(model).__name__}")
    verdicts = await MaintenanceAssessor(model, today, max_concurrency).assess(enriched)
    return MaintenanceAssessment(verdicts=verdicts)
