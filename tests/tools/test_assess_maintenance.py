from datetime import date

from src.domain.models import MaintenanceStatus, MaintenanceVerdict
from src.tools.assess_maintenance import (
    assess_maintenance_tool,
    floor_status,
    signals_for,
)
from tests.support import (
    ScriptedChatModel,
    enriched_package,
    maintenance_draft,
    maintenance_model,
    repo,
)

TODAY = date(2026, 9, 1)

LIVELY = enriched_package(
    name="requests",
    version="2.32.3",
    repo_health=repo(last_commit=date(2026, 8, 20)),
    latest_release_date=date(2026, 7, 1),
    available_versions=("2.32.2", "2.32.3"),
)
ARCHIVED = enriched_package(
    name="pycrypto",
    version="2.6.1",
    repo_health=repo(archived=True, last_commit=date(2014, 6, 20), open_issues=194, contributors=12),
    latest_release_date=date(2013, 10, 17),
)
SILENT = enriched_package(
    name="quiet-lib",
    version="1.0.0",
    repo_health=repo(last_commit=date(2021, 1, 1), contributors=1),
    latest_release_date=date(2021, 1, 5),
)
UNMEASURED = enriched_package(name="mystery", version="0.1.0")


async def assess(packages, script, **kwargs) -> list[MaintenanceVerdict]:
    result = await assess_maintenance_tool(
        packages, model=maintenance_model(script), today=TODAY, **kwargs
    )
    return result.verdicts


async def assess_one(package, entry, **kwargs) -> MaintenanceVerdict:
    return (await assess([package], [entry], **kwargs))[0]


# --- the deterministic layer -------------------------------------------------


def test_measures_recency_as_days_rather_than_dates():
    signals = signals_for(ARCHIVED, TODAY)

    assert signals.days_since_last_commit == (TODAY - date(2014, 6, 20)).days
    assert signals.days_since_last_release == (TODAY - date(2013, 10, 17)).days


def test_counts_published_releases_from_the_available_versions():
    assert signals_for(LIVELY, TODAY).releases_published == 2


def test_reports_a_missing_release_count_as_missing_rather_than_zero():
    assert signals_for(UNMEASURED, TODAY).releases_published is None


def test_carries_the_repository_signals_through_unchanged():
    signals = signals_for(ARCHIVED, TODAY)

    assert (signals.archived, signals.open_issues, signals.contributors) == (True, 194, 12)


def test_has_no_signals_at_all_for_a_package_nothing_was_measured_for():
    assert signals_for(UNMEASURED, TODAY).is_empty


def test_an_archived_repository_floors_the_status_at_abandoned():
    assert floor_status(signals_for(ARCHIVED, TODAY)) == MaintenanceStatus.ABANDONED


def test_five_years_of_silence_floors_the_status_at_abandoned():
    assert floor_status(signals_for(SILENT, TODAY)) == MaintenanceStatus.ABANDONED


def test_eighteen_quiet_months_floors_the_status_at_declining():
    package = enriched_package(
        repo_health=repo(last_commit=date(2024, 8, 1)), latest_release_date=date(2024, 9, 1)
    )

    assert floor_status(signals_for(package, TODAY)) == MaintenanceStatus.DECLINING


def test_recent_activity_leaves_the_status_to_the_model():
    assert floor_status(signals_for(LIVELY, TODAY)) is None


def test_the_more_recent_of_commit_and_release_decides_the_floor():
    """A quiet release history with a live commit log is not abandonment."""
    package = enriched_package(
        repo_health=repo(last_commit=date(2026, 8, 1)), latest_release_date=date(2015, 1, 1)
    )

    assert floor_status(signals_for(package, TODAY)) is None


# --- the assessment ----------------------------------------------------------


async def test_returns_one_verdict_per_package():
    verdicts = await assess([LIVELY, ARCHIVED], [maintenance_draft()])

    assert [v.package for v in verdicts] == ["requests", "pycrypto"]


async def test_returns_no_verdicts_for_an_empty_input():
    assert await assess([], []) == []


async def test_keeps_the_status_the_model_chose_when_the_signals_permit_it():
    verdict = await assess_one(LIVELY, maintenance_draft("healthy", 0.9, "commits last week"))

    assert verdict.status == MaintenanceStatus.HEALTHY
    assert verdict.confidence == 0.9
    assert verdict.evidence == "commits last week"
    assert verdict.corrections == []


async def test_carries_the_measured_signals_into_the_verdict():
    verdict = await assess_one(LIVELY, maintenance_draft())

    assert verdict.signals.contributors == 40


async def test_lets_the_model_be_more_worried_than_the_signals_require():
    """A single-maintainer project with a growing backlog is exactly the judgment call."""
    verdict = await assess_one(LIVELY, maintenance_draft("declining", 0.6, "one contributor"))

    assert verdict.status == MaintenanceStatus.DECLINING
    assert verdict.corrections == []


async def test_never_lets_the_model_call_an_archived_repository_healthy():
    verdict = await assess_one(ARCHIVED, maintenance_draft("healthy", 0.9, "looks fine to me"))

    assert verdict.status == MaintenanceStatus.ABANDONED
    assert "raised status" in verdict.corrections[0]


async def test_records_why_a_status_was_raised():
    verdict = await assess_one(ARCHIVED, maintenance_draft("declining"))

    assert "archived" in verdict.corrections[0]


async def test_raises_a_status_the_silence_alone_establishes():
    verdict = await assess_one(SILENT, maintenance_draft("declining", 0.7, "quiet"))

    assert verdict.status == MaintenanceStatus.ABANDONED
    assert "nothing has moved for" in verdict.corrections[0]


async def test_records_a_verdict_that_cites_no_evidence():
    verdict = await assess_one(LIVELY, maintenance_draft("healthy", 0.8, "  "))

    assert verdict.corrections == ["no evidence cited"]


async def test_an_unknown_status_carries_no_confidence():
    verdict = await assess_one(LIVELY, maintenance_draft("unknown", 0.9, "not sure"))

    assert verdict.confidence == 0.0
    assert "reset confidence" in verdict.corrections[0]


async def test_skips_the_model_entirely_when_nothing_was_measured():
    model = maintenance_model([maintenance_draft("healthy")])

    result = await assess_maintenance_tool([UNMEASURED], model=model, today=TODAY)

    assert model.prompts == []
    assert result.verdicts[0].status == MaintenanceStatus.UNKNOWN


async def test_falls_back_to_the_signals_when_the_model_fails():
    verdict = await assess_one(ARCHIVED, RuntimeError("model exploded"))

    assert verdict.status == MaintenanceStatus.ABANDONED
    assert verdict.error == "RuntimeError: model exploded"


async def test_a_failure_on_an_unremarkable_package_stays_unknown():
    verdict = await assess_one(LIVELY, RuntimeError("model exploded"))

    assert verdict.status == MaintenanceStatus.UNKNOWN
    assert verdict.confidence == 0.0


async def test_one_failing_package_does_not_lose_the_others():
    verdicts = await assess([ARCHIVED, LIVELY], [RuntimeError("boom"), maintenance_draft()])

    assert [v.package for v in verdicts] == ["pycrypto", "requests"]
    assert verdicts[0].error is not None


async def test_shows_the_model_the_measured_durations_not_the_raw_dates():
    model = maintenance_model([maintenance_draft()])

    await assess_maintenance_tool([ARCHIVED], model=model, today=TODAY)

    assert "Days since last commit: 4456" in model.prompts[-1]
    assert "2014-06-20" not in model.prompts[-1]


async def test_names_the_signals_it_could_not_measure():
    package = enriched_package(repo_health=repo(contributors=None), latest_release_date=None)
    model = maintenance_model([maintenance_draft()])

    await assess_maintenance_tool([package], model=model, today=TODAY)

    assert "Contributors: not available" in model.prompts[-1]


async def test_assesses_packages_concurrently():
    model = ScriptedChatModel(
        script=[maintenance_draft()], schema_name="MaintenanceVerdictDraft", latency=0.01
    )

    await assess_maintenance_tool([LIVELY, ARCHIVED, SILENT], model=model, today=TODAY)

    assert model.peak_in_flight > 1


async def test_respects_the_concurrency_cap():
    model = ScriptedChatModel(
        script=[maintenance_draft()], schema_name="MaintenanceVerdictDraft", latency=0.01
    )

    await assess_maintenance_tool(
        [LIVELY, ARCHIVED, SILENT], model=model, today=TODAY, max_concurrency=1
    )

    assert model.peak_in_flight == 1
