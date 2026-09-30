from datetime import date

import pytest

from src.clients.pypi import PyPiClient, PyPiMetadata
from src.domain.models import Package
from src.http_client import HttpError, HttpResponse
from tests.support import FakeSession, pypi_payload, release

REQUESTS = Package(name="requests", version="2.31.0")


async def fetch(payload, package: Package = REQUESTS) -> PyPiMetadata | None:
    session = FakeSession(pypi={package.name: payload})
    return await PyPiClient(session).metadata(package)


async def test_reports_the_latest_published_version():
    metadata = await fetch(pypi_payload(version="2.32.3"))

    assert metadata.latest_version == "2.32.3"


async def test_lists_every_released_version():
    metadata = await fetch(pypi_payload(releases={"1.0.0": [], "2.0.0": [], "2.31.0": []}))

    assert metadata.available_versions == ["1.0.0", "2.0.0", "2.31.0"]


async def test_orders_released_versions_by_version_not_alphabetically():
    metadata = await fetch(pypi_payload(releases={"2.10.0": [], "2.9.0": [], "2.2.0": []}))

    assert metadata.available_versions == ["2.2.0", "2.9.0", "2.10.0"]


async def test_falls_back_to_alphabetical_order_for_unparsable_versions():
    metadata = await fetch(pypi_payload(releases={"2.0.0": [], "nightly": []}))

    assert metadata.available_versions == ["2.0.0", "nightly"]


async def test_reports_no_versions_for_a_project_without_releases():
    metadata = await fetch(pypi_payload(releases={}))

    assert metadata.available_versions == []


async def test_reads_the_release_date_of_the_latest_version():
    metadata = await fetch(
        pypi_payload(version="2.0.0", releases={"2.0.0": release("2024-03-09T18:00:00.000000Z")})
    )

    assert metadata.latest_release_date == date(2024, 3, 9)


async def test_uses_the_earliest_upload_of_a_multi_artifact_release():
    metadata = await fetch(
        pypi_payload(
            version="2.0.0",
            releases={"2.0.0": release("2024-03-09T18:00:00Z", "2024-03-07T08:00:00Z")},
        )
    )

    assert metadata.latest_release_date == date(2024, 3, 7)


async def test_reports_no_release_date_when_the_latest_version_has_no_files():
    metadata = await fetch(pypi_payload(version="2.0.0", releases={"2.0.0": []}))

    assert metadata.latest_release_date is None


async def test_ignores_unparsable_upload_timestamps():
    metadata = await fetch(pypi_payload(version="2.0.0", releases={"2.0.0": release("whenever")}))

    assert metadata.latest_release_date is None


async def test_reads_the_declared_license():
    metadata = await fetch(pypi_payload(license="Apache-2.0"))

    assert metadata.license_declared == "Apache-2.0"


async def test_prefers_the_spdx_license_expression_over_the_free_text_field():
    metadata = await fetch(pypi_payload(license="Apache Software License", license_expression="Apache-2.0"))

    assert metadata.license_declared == "Apache-2.0"


async def test_falls_back_to_the_license_classifier_when_no_license_is_declared():
    metadata = await fetch(
        pypi_payload(license="", classifiers=("License :: OSI Approved :: MIT License",))
    )

    assert metadata.license_declared == "MIT License"


async def test_ignores_the_bare_osi_approved_classifier():
    metadata = await fetch(pypi_payload(classifiers=("License :: OSI Approved",)))

    assert metadata.license_declared is None


async def test_reports_no_license_when_nothing_declares_one():
    metadata = await fetch(pypi_payload(license="   "))

    assert metadata.license_declared is None


async def test_finds_the_repository_url_in_project_urls():
    metadata = await fetch(pypi_payload(project_urls={"Source": "https://github.com/psf/requests"}))

    assert metadata.repo_url == "https://github.com/psf/requests"


async def test_normalizes_a_repository_url_with_a_git_suffix_and_trailing_path():
    metadata = await fetch(
        pypi_payload(project_urls={"Source": "https://github.com/psf/requests.git"})
    )

    assert metadata.repo_url == "https://github.com/psf/requests"


async def test_strips_extra_path_segments_from_the_repository_url():
    metadata = await fetch(
        pypi_payload(project_urls={"Issues": "https://github.com/psf/requests/issues"})
    )

    assert metadata.repo_url == "https://github.com/psf/requests"


async def test_falls_back_to_home_page_for_the_repository_url():
    metadata = await fetch(pypi_payload(home_page="https://github.com/psf/requests"))

    assert metadata.repo_url == "https://github.com/psf/requests"


async def test_ignores_project_urls_that_are_not_github_repositories():
    metadata = await fetch(
        pypi_payload(project_urls={"Docs": "https://requests.readthedocs.io"}, home_page="")
    )

    assert metadata.repo_url is None


async def test_reports_no_repository_when_the_project_declares_no_urls():
    metadata = await fetch(pypi_payload())

    assert metadata.repo_url is None


async def test_returns_nothing_for_a_package_pypi_does_not_know():
    session = FakeSession(pypi={})

    assert await PyPiClient(session).metadata(REQUESTS) is None


async def test_raises_when_pypi_fails():
    session = FakeSession(pypi={"requests": HttpResponse(status=503)})

    with pytest.raises(HttpError, match="HTTP 503"):
        await PyPiClient(session).metadata(REQUESTS)


async def test_tolerates_a_payload_without_info_or_releases():
    metadata = PyPiMetadata({})

    assert (metadata.latest_version, metadata.available_versions, metadata.repo_url) == (None, [], None)


async def test_reads_the_license_of_the_pinned_release_not_the_latest():
    chardet = Package(name="chardet", version="4.0.0")
    session = FakeSession(pypi={
        "chardet": pypi_payload(version="7.6.0", license_expression="0BSD"),
        "chardet/4.0.0": pypi_payload(version="4.0.0", license="LGPL"),
    })

    metadata = await PyPiClient(session).metadata(chardet)

    assert metadata.license_declared == "LGPL"
    assert metadata.latest_version == "7.6.0"


async def test_reports_no_license_when_pypi_does_not_know_the_pinned_release():
    session = FakeSession(pypi={"requests": pypi_payload(license="Apache-2.0"), "requests/2.31.0": None})

    metadata = await PyPiClient(session).metadata(REQUESTS)

    assert metadata.license_declared is None
    assert metadata.latest_version == "1.0.0"


async def test_raises_when_the_pinned_release_lookup_fails():
    session = FakeSession(pypi={"requests": pypi_payload(), "requests/2.31.0": HttpResponse(status=503, data=None)})

    with pytest.raises(HttpError):
        await PyPiClient(session).metadata(REQUESTS)
