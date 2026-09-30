from src.constants import DEPS_DEV_API_URL
from src.http_client import HttpError, HttpSession

SYSTEM = "pypi"


class DepsDevClient:
    """Reads adoption signals from deps.dev's free, unauthenticated API.

    Only one question is asked of it: how many published packages depend on this
    one. That number is the closest thing to evidence that a replacement is a real
    migration target rather than a plausible-sounding name.
    """

    def __init__(self, session: HttpSession, api_url: str = DEPS_DEV_API_URL) -> None:
        self._session = session
        self._api_url = api_url

    async def default_version(self, name: str) -> str | None:
        response = await self._session.get_json(f"{self._api_url}/systems/{SYSTEM}/packages/{name}")
        if response.not_found:
            return None
        if not response.ok:
            raise HttpError(f"deps.dev returned HTTP {response.status} for {name}")
        versions = (response.data or {}).get("versions") or []
        default = next((v for v in versions if v.get("isDefault")), None) or (
            versions[-1] if versions else None
        )
        return ((default or {}).get("versionKey") or {}).get("version")

    async def dependents(self, name: str, version: str | None = None) -> int | None:
        """Number of packages depending on this one, or None when deps.dev cannot say."""
        version = version or await self.default_version(name)
        if version is None:
            return None
        response = await self._session.get_json(
            f"{self._api_url}/systems/{SYSTEM}/packages/{name}/versions/{version}:dependents"
        )
        if response.not_found:
            return None
        if not response.ok:
            raise HttpError(f"deps.dev returned HTTP {response.status} for {name}=={version}")
        count = (response.data or {}).get("dependentCount")
        return count if isinstance(count, int) else None
