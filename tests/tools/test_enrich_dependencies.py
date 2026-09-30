import asyncio
from datetime import date

from src.cache import InMemoryCache
from src.constants import GITHUB_API_URL, OSV_API_URL
from src.domain.models import EnrichedPackage, Package
from src.http_client import HttpError, HttpResponse
from src.tools.enrich_dependencies import enrich_dependencies_tool
from tests.support import (
    FakeSession,
    contributors_page,
    github_repo,
    osv_payload,
    pypi_payload,
    release,
    vuln,
)

PYCRYPTO = Package(name="pycrypto", version="2.6.1")
URLLIB3 = Package(name="urllib3", version="2.0.4")


def pycrypto_session(**overrides) -> FakeSession:
    defaults = dict(
        osv={("pycrypto", "2.6.1"): osv_payload(vuln(id="CVE-2013-7459", severity="HIGH"))},
        pypi={
            "pycrypto": pypi_payload(
                version="2.6.1",
                releases={"2.6.1": release("2013-10-17T10:00:00Z")},
                license="Public Domain",
                project_urls={"Source": "https://github.com/pycrypto/pycrypto"},
            )
        },
        repos={
            "pycrypto/pycrypto": github_repo(
                html_url="https://github.com/pycrypto/pycrypto",
                archived=True,
                pushed_at="2014-06-20T11:00:00Z",
                open_issues_count=194,
            )
        },
        contributors={"pycrypto/pycrypto": contributors_page(total=12)},
    )
    return FakeSession(**(defaults | overrides))


async def enrich(packages, session, **kwargs):
    return await enrich_dependencies_tool(packages, session=session, **kwargs)


async def only(packages, session, **kwargs) -> EnrichedPackage:
    return (await enrich(packages, session, **kwargs)).enriched[0]


async def test_returns_one_entry_per_requested_package():
    session = FakeSession(pypi={"pycrypto": pypi_payload(), "urllib3": pypi_payload()})

    result = await enrich([PYCRYPTO, URLLIB3], session)

    assert [(e.name, e.version) for e in result.enriched] == [("pycrypto", "2.6.1"), ("urllib3", "2.0.4")]


async def test_returns_an_empty_result_for_an_empty_package_list():
    session = FakeSession()

    result = await enrich([], session)

    assert result.enriched == []
    assert result.fetch_stats.requests == 0


async def test_enriches_a_repeated_package_only_once():
    session = FakeSession(pypi={"pycrypto": pypi_payload()})

    result = await enrich([PYCRYPTO, PYCRYPTO], session)

    assert len(result.enriched) == 1


async def test_treats_two_versions_of_a_package_as_distinct():
    session = FakeSession(pypi={"pycrypto": pypi_payload()})

    result = await enrich([PYCRYPTO, Package(name="pycrypto", version="2.6.0")], session)

    assert [e.version for e in result.enriched] == ["2.6.1", "2.6.0"]


async def test_collects_vulnerabilities_for_the_pinned_version():
    enriched = await only([PYCRYPTO], pycrypto_session())

    assert [(v.id, v.severity) for v in enriched.vulnerabilities] == [("CVE-2013-7459", "HIGH")]


async def test_collects_release_history_and_declared_license():
    enriched = await only([PYCRYPTO], pycrypto_session())

    assert enriched.available_versions == ["2.6.1"]
    assert enriched.latest_release_date == date(2013, 10, 17)
    assert enriched.license_declared == "Public Domain"


async def test_collects_repository_health_signals():
    enriched = await only([PYCRYPTO], pycrypto_session())

    assert enriched.repo_health.archived is True
    assert enriched.repo_health.last_commit == date(2014, 6, 20)
    assert enriched.repo_health.open_issues == 194
    assert enriched.repo_health.contributors == 12


async def test_reports_no_repository_health_when_pypi_declares_no_repository():
    enriched = await only([PYCRYPTO], pycrypto_session(pypi={"pycrypto": pypi_payload()}))

    assert enriched.repo_health is None


async def test_does_not_call_github_when_there_is_no_repository_url():
    session = pycrypto_session(pypi={"pycrypto": pypi_payload()})

    await enrich([PYCRYPTO], session)

    assert not any(GITHUB_API_URL in url for url in session.requested)


async def test_records_a_clean_package_without_errors():
    session = FakeSession(pypi={"urllib3": pypi_payload(version="2.0.7")})

    enriched = await only([URLLIB3], session)

    assert enriched.vulnerabilities == []
    assert enriched.errors == []


async def test_still_returns_a_package_pypi_has_never_heard_of():
    session = FakeSession(pypi={})

    enriched = await only([PYCRYPTO], session)

    assert enriched.available_versions == []
    assert enriched.license_declared is None
    assert enriched.errors == []


async def test_records_an_osv_failure_without_losing_the_other_sources():
    session = pycrypto_session(osv={("pycrypto", "2.6.1"): HttpError("osv unreachable")})

    enriched = await only([PYCRYPTO], session)

    assert enriched.errors == ["osv: osv unreachable"]
    assert enriched.license_declared == "Public Domain"
    assert enriched.repo_health is not None


async def test_reports_no_vulnerabilities_rather_than_a_wrong_answer_when_osv_fails():
    session = pycrypto_session(osv={("pycrypto", "2.6.1"): HttpResponse(status=500)})

    enriched = await only([PYCRYPTO], session)

    assert enriched.vulnerabilities == []
    assert "osv" in enriched.errors[0]


async def test_records_a_pypi_failure_without_losing_vulnerabilities():
    session = pycrypto_session(pypi={"pycrypto": HttpResponse(status=503)})

    enriched = await only([PYCRYPTO], session)

    assert [v.id for v in enriched.vulnerabilities] == ["CVE-2013-7459"]
    assert enriched.errors == ["pypi: PyPI returned HTTP 503 for pycrypto"]


async def test_records_a_github_failure_without_losing_the_other_sources():
    session = pycrypto_session(repos={"pycrypto/pycrypto": HttpResponse(status=403)})

    enriched = await only([PYCRYPTO], session)

    assert enriched.repo_health is None
    assert enriched.errors == ["github: GitHub returned HTTP 403 for pycrypto/pycrypto"]
    assert enriched.license_declared == "Public Domain"


async def test_records_every_failed_source_for_a_package():
    session = pycrypto_session(
        osv={("pycrypto", "2.6.1"): HttpError("osv down")},
        pypi={"pycrypto": HttpError("pypi down")},
    )

    enriched = await only([PYCRYPTO], session)

    assert enriched.errors == ["osv: osv down", "pypi: pypi down"]


async def test_one_failing_package_does_not_fail_the_others():
    session = pycrypto_session(pypi={"pycrypto": HttpError("boom"), "urllib3": pypi_payload(version="2.0.7")})

    result = await enrich([PYCRYPTO, URLLIB3], session)

    assert result.enriched[0].errors != []
    assert result.enriched[1].errors == []
    assert result.enriched[1].latest_version == "2.0.7"


async def test_queries_osv_and_pypi_for_every_package():
    session = FakeSession(pypi={"pycrypto": pypi_payload(), "urllib3": pypi_payload()})

    await enrich([PYCRYPTO, URLLIB3], session)

    assert session.requested.count(OSV_API_URL) == 2
    # the project document and the pinned release's document, per package
    assert sum(1 for url in session.requested if url.endswith("/json")) == 4


async def test_counts_every_request_it_made():
    result = await enrich([PYCRYPTO], pycrypto_session())

    assert result.fetch_stats.requests == 5


async def test_reports_how_long_the_fan_out_took():
    result = await enrich([PYCRYPTO], pycrypto_session())

    assert result.fetch_stats.wall_clock_seconds >= 0


async def test_runs_requests_concurrently_rather_than_one_after_another():
    packages = [Package(name=f"pkg{i}", version="1.0.0") for i in range(8)]
    session = FakeSession(pypi={p.name: pypi_payload() for p in packages}, latency=0.02)

    await enrich(packages, session, max_concurrency=16)

    assert session.peak_in_flight > 1


async def test_never_exceeds_the_configured_concurrency_cap():
    packages = [Package(name=f"pkg{i}", version="1.0.0") for i in range(20)]
    session = FakeSession(pypi={p.name: pypi_payload() for p in packages}, latency=0.01)

    await enrich(packages, session, max_concurrency=3)

    assert session.peak_in_flight <= 3


async def test_finishes_faster_than_a_sequential_fan_out_would():
    packages = [Package(name=f"pkg{i}", version="1.0.0") for i in range(10)]
    session = FakeSession(pypi={p.name: pypi_payload() for p in packages}, latency=0.02)

    started = asyncio.get_running_loop().time()
    await enrich(packages, session, max_concurrency=10)
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 20 * 0.02


async def test_serves_a_second_run_from_the_cache():
    cache = InMemoryCache()
    session = pycrypto_session()
    await enrich([PYCRYPTO], session, cache=cache)
    requests_after_first_run = len(session.requested)

    result = await enrich([PYCRYPTO], session, cache=cache)

    assert len(session.requested) == requests_after_first_run
    assert result.fetch_stats.cache_hits == 1
    assert result.fetch_stats.requests == 0


async def test_returns_the_same_facts_from_the_cache_as_from_the_network():
    cache = InMemoryCache()
    live = await only([PYCRYPTO], pycrypto_session(), cache=cache)

    cached = await only([PYCRYPTO], pycrypto_session(), cache=cache)

    assert cached == live


async def test_does_not_cache_a_partially_failed_package():
    cache = InMemoryCache()
    await enrich([PYCRYPTO], pycrypto_session(pypi={"pycrypto": HttpError("boom")}), cache=cache)

    result = await enrich([PYCRYPTO], pycrypto_session(), cache=cache)

    assert result.fetch_stats.cache_hits == 0
    assert result.enriched[0].errors == []


async def test_counts_cache_hits_only_for_packages_it_did_not_fetch():
    cache = InMemoryCache()
    session = FakeSession(pypi={"pycrypto": pypi_payload(), "urllib3": pypi_payload()})
    await enrich([PYCRYPTO], session, cache=cache)

    result = await enrich([PYCRYPTO, URLLIB3], session, cache=cache)

    assert result.fetch_stats.cache_hits == 1
    assert len(result.enriched) == 2


async def test_makes_no_judgment_about_the_facts_it_gathers():
    enriched = await only([PYCRYPTO], pycrypto_session())

    assert set(EnrichedPackage.model_fields) == {
        "name",
        "version",
        "vulnerabilities",
        "available_versions",
        "latest_version",
        "latest_release_date",
        "license_declared",
        "repo_health",
        "errors",
    }
    assert enriched.repo_health.archived is True
