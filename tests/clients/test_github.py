from datetime import date

import pytest

from src.clients.github import GitHubClient
from src.constants import GITHUB_API_URL
from src.http_client import HttpError, HttpResponse
from tests.support import FakeSession, contributors_page, github_repo

REPO_URL = "https://github.com/psf/requests"


async def fetch(repo=None, contributors=None, repo_url: str = REPO_URL):
    session = FakeSession(
        repos={"psf/requests": repo if repo is not None else github_repo()},
        contributors={"psf/requests": contributors} if contributors is not None else None,
    )
    return await GitHubClient(session).repo_health(repo_url), session


async def test_reports_the_archived_flag():
    health, _ = await fetch(github_repo(archived=True))

    assert health.archived is True


async def test_reports_the_open_issue_count():
    health, _ = await fetch(github_repo(open_issues_count=194))

    assert health.open_issues == 194


async def test_reports_the_date_of_the_last_push_as_the_last_commit():
    health, _ = await fetch(github_repo(pushed_at="2014-06-20T11:00:00Z"))

    assert health.last_commit == date(2014, 6, 20)


async def test_reports_no_last_commit_when_github_omits_the_timestamp():
    health, _ = await fetch(github_repo(pushed_at=None))

    assert health.last_commit is None


async def test_reports_the_canonical_repository_url():
    health, _ = await fetch(github_repo(html_url="https://github.com/psf/requests"))

    assert health.repo_url == "https://github.com/psf/requests"


async def test_counts_contributors_from_the_pagination_header():
    health, _ = await fetch(contributors=contributors_page(total=766))

    assert health.contributors == 766


async def test_counts_contributors_directly_when_there_is_a_single_page():
    health, _ = await fetch(contributors=[{"login": "a"}, {"login": "b"}])

    assert health.contributors == 2


async def test_reports_no_contributor_count_when_the_endpoint_fails():
    health, _ = await fetch(contributors=HttpResponse(status=403))

    assert health.contributors is None


async def test_still_reports_repository_health_when_contributors_are_unavailable():
    health, _ = await fetch(contributors=HttpResponse(status=403))

    assert health.open_issues == 7


async def test_queries_the_repository_and_its_contributors():
    _, session = await fetch()

    assert session.requested == [
        f"{GITHUB_API_URL}/repos/psf/requests",
        f"{GITHUB_API_URL}/repos/psf/requests/contributors",
    ]


async def test_returns_nothing_for_a_repository_that_no_longer_exists():
    session = FakeSession(repos={})

    assert await GitHubClient(session).repo_health(REPO_URL) is None


async def test_returns_nothing_for_a_url_that_is_not_a_github_repository():
    session = FakeSession()

    assert await GitHubClient(session).repo_health("https://gitlab.com/psf/requests") is None
    assert session.requested == []


async def test_raises_when_github_rate_limits_the_request():
    session = FakeSession(repos={"psf/requests": HttpResponse(status=403)})

    with pytest.raises(HttpError, match="HTTP 403"):
        await GitHubClient(session).repo_health(REPO_URL)


@pytest.mark.parametrize(
    "repo_url, expected",
    [
        ("https://github.com/psf/requests", "psf/requests"),
        ("http://github.com/psf/requests/", "psf/requests"),
        ("https://www.github.com/psf/requests.git", "psf/requests"),
        ("https://github.com/psf/requests/tree/main", "psf/requests"),
        ("https://github.com/psf", None),
        ("https://example.com/psf/requests", None),
        ("", None),
    ],
)
def test_extracts_the_owner_and_repository_from_a_url(repo_url, expected):
    assert GitHubClient.repo_slug(repo_url) == expected
