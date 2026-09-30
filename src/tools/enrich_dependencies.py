import asyncio
import time
from collections.abc import Awaitable, Sequence

from src.cache import EnrichmentCache, NullCache
from src.clients.github import GitHubClient
from src.clients.osv import OsvClient
from src.clients.pypi import PyPiClient, PyPiMetadata
from src.config.log_config import logger
from src.constants import MAX_CONCURRENT_REQUESTS
from src.domain.models import (
    EnrichedPackage,
    EnrichmentResult,
    FetchStats,
    Package,
    RepoHealth,
    Vulnerability,
)
from src.http_client import AiohttpSession, HttpSession, MeteredSession


class DependencyEnricher:
    """Gathers raw security, registry, and repository facts for a set of packages.

    Every package is enriched concurrently, and within a package the OSV and PyPI
    queries run concurrently too; the GitHub lookup follows PyPI because only PyPI
    metadata reveals the repository URL. No judgment is applied to the facts: a
    source that fails is reported in the package's `errors` rather than silently
    changing the shape of the result.
    """

    def __init__(
        self,
        session: HttpSession,
        cache: EnrichmentCache | None = None,
    ) -> None:
        self._osv = OsvClient(session)
        self._pypi = PyPiClient(session)
        self._github = GitHubClient(session)
        self._cache = cache or NullCache()
        self.cache_hits = 0

    async def enrich(self, packages: Sequence[Package]) -> list[EnrichedPackage]:
        return list(await asyncio.gather(*(self.enrich_one(package) for package in packages)))

    async def enrich_one(self, package: Package) -> EnrichedPackage:
        if cached := self._cache.get(package.name, package.version):
            self.cache_hits += 1
            return cached
        vulnerabilities, metadata = await asyncio.gather(
            self.fetch_vulnerabilities(package),
            self.fetch_metadata(package),
        )
        enriched = self.assemble(package, vulnerabilities, metadata)
        enriched.repo_health, repo_error = await self.fetch_repo_health(metadata.value)
        enriched.errors.extend(error for error in (repo_error,) if error)
        if not enriched.errors:
            # A partial result must not be served from cache for the next 24 hours.
            self._cache.put(enriched)
        return enriched

    async def fetch_vulnerabilities(self, package: Package) -> "Attempt[list[Vulnerability]]":
        return await attempt("osv", self._osv.vulnerabilities(package))

    async def fetch_metadata(self, package: Package) -> "Attempt[PyPiMetadata | None]":
        return await attempt("pypi", self._pypi.metadata(package))

    async def fetch_repo_health(
        self, metadata: PyPiMetadata | None
    ) -> tuple[RepoHealth | None, str | None]:
        if metadata is None or not (repo_url := metadata.repo_url):
            return None, None
        result = await attempt("github", self._github.repo_health(repo_url))
        return result.value, result.error

    @staticmethod
    def assemble(
        package: Package,
        vulnerabilities: "Attempt[list[Vulnerability]]",
        metadata: "Attempt[PyPiMetadata | None]",
    ) -> EnrichedPackage:
        info = metadata.value
        return EnrichedPackage(
            name=package.name,
            version=package.version,
            vulnerabilities=vulnerabilities.value or [],
            available_versions=info.available_versions if info else [],
            latest_version=info.latest_version if info else None,
            latest_release_date=info.latest_release_date if info else None,
            license_declared=info.license_declared if info else None,
            errors=[error for error in (vulnerabilities.error, metadata.error) if error],
        )


class Attempt[T]:
    """The outcome of one API call: either a value, or the error that replaced it."""

    def __init__(self, value: T | None = None, error: str | None = None) -> None:
        self.value = value
        self.error = error


async def attempt[T](source: str, coroutine: Awaitable[T]) -> Attempt[T]:
    try:
        return Attempt(value=await coroutine)
    except Exception as e:
        logger.warning(f"{source} lookup failed: {e}")
        return Attempt(error=f"{source}: {e}")


def deduplicate(packages: Sequence[Package]) -> list[Package]:
    seen: dict[tuple[str, str], Package] = {}
    for package in packages:
        seen.setdefault((package.name, package.version), package)
    return list(seen.values())


async def enrich_dependencies_tool(
    packages: Sequence[Package],
    session: HttpSession | None = None,
    cache: EnrichmentCache | None = None,
    max_concurrency: int = MAX_CONCURRENT_REQUESTS,
) -> EnrichmentResult:
    unique = deduplicate(packages)
    logger.info(f"Enriching {len(unique)} packages (max {max_concurrency} concurrent requests)")
    if not unique:
        return EnrichmentResult()
    if session is None:
        async with AiohttpSession() as live_session:
            return await run_enrichment(unique, live_session, cache, max_concurrency)
    return await run_enrichment(unique, session, cache, max_concurrency)


async def run_enrichment(
    packages: Sequence[Package],
    session: HttpSession,
    cache: EnrichmentCache | None,
    max_concurrency: int,
) -> EnrichmentResult:
    metered = MeteredSession(session, max_concurrency)
    enricher = DependencyEnricher(metered, cache)
    started = time.perf_counter()
    enriched = await enricher.enrich(packages)
    return EnrichmentResult(
        enriched=enriched,
        fetch_stats=FetchStats(
            requests=metered.requests,
            wall_clock_seconds=round(time.perf_counter() - started, 3),
            cache_hits=enricher.cache_hits,
        ),
    )
