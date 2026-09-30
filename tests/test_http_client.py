import asyncio

from src.http_client import HttpResponse, MeteredSession
from tests.support import FakeSession, pypi_payload

PYPI_URL = "https://pypi.org/pypi/requests/json"


def test_treats_2xx_as_success_and_404_as_not_found():
    assert HttpResponse(status=204).ok
    assert not HttpResponse(status=404).ok
    assert HttpResponse(status=404).not_found
    assert not HttpResponse(status=500).not_found


async def test_counts_both_reads_and_writes():
    metered = MeteredSession(FakeSession(), max_concurrency=10)

    await metered.get_json(PYPI_URL)
    await metered.post_json("https://api.osv.dev/v1/query", {"package": {"name": "x"}, "version": "1"})

    assert metered.requests == 2


async def test_passes_responses_through_untouched():
    session = FakeSession(pypi={"requests": pypi_payload(version="2.32.3")})
    metered = MeteredSession(session, max_concurrency=10)

    response = await metered.get_json(PYPI_URL)

    assert response.data["info"]["version"] == "2.32.3"


async def test_holds_in_flight_requests_below_the_cap():
    session = FakeSession(pypi={f"pkg{i}": pypi_payload() for i in range(12)}, latency=0.01)
    metered = MeteredSession(session, max_concurrency=4)

    await asyncio.gather(*(metered.get_json(f"https://pypi.org/pypi/pkg{i}/json") for i in range(12)))

    assert session.peak_in_flight <= 4


async def test_releases_its_slot_when_a_request_fails():
    session = FakeSession(pypi={"requests": RuntimeError("boom")})
    metered = MeteredSession(session, max_concurrency=1)

    for _ in range(3):
        try:
            await metered.get_json(PYPI_URL)
        except RuntimeError:
            pass

    assert metered.requests == 3
