import re

from src.clients.parsing import parse_iso_date
from src.constants import GITHUB_API_URL
from src.domain.models import RepoHealth
from src.http_client import HttpError, HttpSession

REPO_PATH_PATTERN = re.compile(r"github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)
LAST_PAGE_PATTERN = re.compile(r'[?&]page=(\d+)[^>]*>;\s*rel="last"')


class GitHubClient:
    """Reads repository activity signals from the unauthenticated GitHub REST API."""

    def __init__(self, session: HttpSession, api_url: str = GITHUB_API_URL) -> None:
        self._session = session
        self._api_url = api_url

    async def repo_health(self, repo_url: str) -> RepoHealth | None:
        """Returns None when the repository is gone (404); raises for any other failure."""
        if not (slug := self.repo_slug(repo_url)):
            return None
        response = await self._session.get_json(f"{self._api_url}/repos/{slug}")
        if response.not_found:
            return None
        if not response.ok:
            raise HttpError(f"GitHub returned HTTP {response.status} for {slug}")
        repo = response.data or {}
        return RepoHealth(
            repo_url=repo.get("html_url") or repo_url,
            archived=repo.get("archived"),
            last_commit=parse_iso_date(repo.get("pushed_at")),
            open_issues=repo.get("open_issues_count"),
            contributors=await self.contributor_count(slug),
        )

    async def contributor_count(self, slug: str) -> int | None:
        """Reads the count off the pagination header instead of downloading every contributor."""
        response = await self._session.get_json(
            f"{self._api_url}/repos/{slug}/contributors",
            params={"per_page": "1", "anon": "true"},
        )
        if not response.ok:
            return None
        if match := LAST_PAGE_PATTERN.search(response.headers.get("Link", "")):
            return int(match.group(1))
        return len(response.data or [])

    @staticmethod
    def repo_slug(repo_url: str) -> str | None:
        if not repo_url or not (match := REPO_PATH_PATTERN.search(repo_url)):
            return None
        return f"{match.group(1)}/{match.group(2).removesuffix('.git')}"

