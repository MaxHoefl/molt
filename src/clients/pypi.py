import asyncio
import re
from datetime import date
from typing import Any

from packaging.version import InvalidVersion, Version

from src.clients.parsing import parse_iso_date
from src.constants import PYPI_API_URL
from src.domain.models import Package
from src.http_client import HttpError, HttpSession

GITHUB_REPO_PATTERN = re.compile(r"^https?://(?:www\.)?github\.com/([^/]+)/([^/#?]+)", re.IGNORECASE)
LICENSE_CLASSIFIER_PREFIX = "License ::"


class PyPiMetadata:
    """Normalized view over PyPI's project document and, optionally, one release's.

    The project document (`/<name>/json`) always describes the *latest* release, so
    it answers "what versions exist?" but not "what license does the version we
    pinned carry?" — licenses change between releases (chardet went LGPL → 0BSD).
    License facts therefore come from the pinned release's document when one is
    given, and from the project document only when no pinned release was asked for.
    """

    def __init__(self, payload: dict[str, Any], release_payload: dict[str, Any] | None = None) -> None:
        self._payload = payload
        self._info = payload.get("info") or {}
        self._releases = payload.get("releases") or {}
        self._release_info = self._info if release_payload is None else release_payload.get("info") or {}

    @property
    def latest_version(self) -> str | None:
        return self._info.get("version") or None

    @property
    def available_versions(self) -> list[str]:
        try:
            return sorted(self._releases, key=Version)
        except InvalidVersion:
            return sorted(self._releases)

    @property
    def license_declared(self) -> str | None:
        for candidate in (self._release_info.get("license_expression"), self._release_info.get("license")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return self.license_from_classifiers()

    def license_from_classifiers(self) -> str | None:
        for classifier in self._release_info.get("classifiers") or []:
            if classifier.startswith(LICENSE_CLASSIFIER_PREFIX):
                trailing = classifier.split("::")[-1].strip()
                if trailing and trailing != "OSI Approved":
                    return trailing
        return None

    @property
    def summary(self) -> str | None:
        summary = self._info.get("summary")
        return summary.strip() if isinstance(summary, str) and summary.strip() else None

    @property
    def latest_release_date(self) -> date | None:
        return self.release_date(self.latest_version)

    def release_date(self, version: str | None) -> date | None:
        uploads = [
            parsed
            for file in self._releases.get(version) or []
            if (parsed := parse_iso_date(file.get("upload_time_iso_8601") or file.get("upload_time")))
        ]
        return min(uploads) if uploads else None

    @property
    def repo_url(self) -> str | None:
        """First GitHub URL declared in project_urls or home_page, normalized to owner/repo."""
        candidates = list((self._info.get("project_urls") or {}).values())
        candidates.append(self._info.get("home_page"))
        for candidate in candidates:
            if not isinstance(candidate, str):
                continue
            if match := GITHUB_REPO_PATTERN.match(candidate.strip()):
                owner, repo = match.group(1), match.group(2).removesuffix(".git")
                return f"https://github.com/{owner}/{repo}"
        return None



class PyPiClient:
    """Fetches release history and metadata from the PyPI JSON API."""

    def __init__(self, session: HttpSession, api_url: str = PYPI_API_URL) -> None:
        self._session = session
        self._api_url = api_url

    async def metadata(self, package: Package) -> PyPiMetadata | None:
        """Returns None when PyPI does not know the package (404) rather than treating it as a failure.

        Fetches the project document and the pinned release's document concurrently.
        A pinned release PyPI does not know yields no license rather than the latest
        release's license: an unknown license escalates, a wrong one clears.
        """
        project, pinned = await asyncio.gather(
            self._session.get_json(f"{self._api_url}/{package.name}/json"),
            self._session.get_json(f"{self._api_url}/{package.name}/{package.version}/json"),
        )
        if project.not_found:
            return None
        for response, what in ((project, package.name), (pinned, f"{package.name} {package.version}")):
            if not response.ok and not response.not_found:
                raise HttpError(f"PyPI returned HTTP {response.status} for {what}")
        return PyPiMetadata(project.data or {}, {} if pinned.not_found else pinned.data or {})
