from typing import Any

import pytest

from src.clients.deps_dev import DepsDevClient
from src.constants import DEPS_DEV_API_URL
from src.http_client import HttpError, HttpResponse, HttpSession

NOT_FOUND = HttpResponse(status=404)


class FakeDepsDev(HttpSession):
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requested: list[str] = []

    async def get_json(self, url: str, params: dict[str, str] | None = None) -> HttpResponse:
        self.requested.append(url)
        entry = self.routes.get(url)
        if entry is None:
            return NOT_FOUND
        if isinstance(entry, HttpResponse):
            return entry
        return HttpResponse(status=200, data=entry)

    async def post_json(self, url: str, payload: dict[str, Any]) -> HttpResponse:
        return NOT_FOUND


def package_url(name: str) -> str:
    return f"{DEPS_DEV_API_URL}/systems/pypi/packages/{name}"


def dependents_url(name: str, version: str) -> str:
    return f"{package_url(name)}/versions/{version}:dependents"


def versions(*entries: tuple[str, bool]) -> dict[str, Any]:
    return {
        "versions": [
            {"versionKey": {"version": version}, "isDefault": is_default}
            for version, is_default in entries
        ]
    }


async def test_reads_the_dependent_count():
    session = FakeDepsDev(
        {
            package_url("pycryptodome"): versions(("3.20.0", True)),
            dependents_url("pycryptodome", "3.20.0"): {"dependentCount": 312},
        }
    )

    assert await DepsDevClient(session).dependents("pycryptodome") == 312


async def test_skips_the_version_lookup_when_the_version_is_known():
    session = FakeDepsDev({dependents_url("pycryptodome", "3.20.0"): {"dependentCount": 312}})

    await DepsDevClient(session).dependents("pycryptodome", "3.20.0")

    assert session.requested == [dependents_url("pycryptodome", "3.20.0")]


async def test_prefers_the_default_version():
    session = FakeDepsDev(
        {
            package_url("p"): versions(("1.0.0", False), ("2.0.0", True), ("3.0.0rc1", False)),
            dependents_url("p", "2.0.0"): {"dependentCount": 7},
        }
    )

    assert await DepsDevClient(session).dependents("p") == 7


async def test_falls_back_to_the_last_version_when_none_is_marked_default():
    session = FakeDepsDev(
        {
            package_url("p"): versions(("1.0.0", False), ("2.0.0", False)),
            dependents_url("p", "2.0.0"): {"dependentCount": 3},
        }
    )

    assert await DepsDevClient(session).dependents("p") == 3


async def test_says_nothing_for_a_package_deps_dev_does_not_know():
    assert await DepsDevClient(FakeDepsDev({})).dependents("no-such-package") is None


async def test_says_nothing_when_the_count_is_missing_from_the_response():
    session = FakeDepsDev(
        {
            package_url("p"): versions(("1.0.0", True)),
            dependents_url("p", "1.0.0"): {},
        }
    )

    assert await DepsDevClient(session).dependents("p") is None


async def test_says_nothing_for_a_package_with_no_published_versions():
    assert await DepsDevClient(FakeDepsDev({package_url("p"): {}})).dependents("p") is None


async def test_raises_when_deps_dev_fails():
    session = FakeDepsDev({package_url("p"): HttpResponse(status=503)})

    with pytest.raises(HttpError):
        await DepsDevClient(session).dependents("p")
