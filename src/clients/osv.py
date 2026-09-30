from typing import Any

from packaging.version import InvalidVersion, Version

from src.constants import OSV_API_URL
from src.domain.models import Package, Vulnerability
from src.http_client import HttpError, HttpSession

# OSV also publishes GIT ranges whose "fixed" events are commit SHAs; only these
# range types carry something a PyPI consumer can pin to.
VERSION_RANGE_TYPES = {"ECOSYSTEM", "SEMVER"}


class OsvClient:
    """Queries OSV.dev for vulnerabilities affecting one pinned package version."""

    def __init__(self, session: HttpSession, api_url: str = OSV_API_URL) -> None:
        self._session = session
        self._api_url = api_url

    async def vulnerabilities(self, package: Package) -> list[Vulnerability]:
        payload = {
            "version": package.version,
            "package": {"name": package.name, "ecosystem": "PyPI"},
        }
        response = await self._session.post_json(self._api_url, payload)
        if not response.ok:
            raise HttpError(f"OSV returned HTTP {response.status} for {package.name}=={package.version}")
        vulns = (response.data or {}).get("vulns") or []
        return [self.normalize(vuln, package.name) for vuln in vulns]

    @classmethod
    def normalize(cls, vuln: dict[str, Any], package_name: str) -> Vulnerability:
        return Vulnerability(
            id=vuln.get("id", ""),
            aliases=list(vuln.get("aliases") or []),
            severity=cls.severity(vuln),
            cvss_vector=cls.cvss_vector(vuln),
            fixed_in=cls.first_fixed_version(vuln, package_name),
            summary=vuln.get("summary"),
            details=vuln.get("details"),
        )

    @staticmethod
    def severity(vuln: dict[str, Any]) -> str | None:
        severity = (vuln.get("database_specific") or {}).get("severity")
        return severity.upper() if isinstance(severity, str) and severity else None

    @staticmethod
    def cvss_vector(vuln: dict[str, Any]) -> str | None:
        for entry in vuln.get("severity") or []:
            score = entry.get("score")
            if isinstance(score, str) and score.startswith("CVSS:"):
                return score
        return None

    @staticmethod
    def first_fixed_version(vuln: dict[str, Any], package_name: str) -> str | None:
        """Lowest version that carries the fix, across every range OSV lists for this package."""
        fixed: list[str] = []
        for affected in vuln.get("affected") or []:
            if (affected.get("package") or {}).get("name", package_name) != package_name:
                continue
            for entry in affected.get("ranges") or []:
                if entry.get("type") not in VERSION_RANGE_TYPES:
                    continue
                fixed.extend(
                    event["fixed"] for event in entry.get("events") or [] if event.get("fixed")
                )
        if not fixed:
            return None
        try:
            return min(fixed, key=Version)
        except InvalidVersion:
            return fixed[0]
