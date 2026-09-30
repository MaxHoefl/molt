import sqlite3
import time
from abc import ABC, abstractmethod
from pathlib import Path

from src.config.log_config import logger
from src.constants import CACHE_TTL_SECONDS
from src.domain.models import EnrichedPackage

SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichment_cache (
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    fetched_at REAL NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (name, version)
)
"""


class EnrichmentCache(ABC):
    @abstractmethod
    def get(self, name: str, version: str) -> EnrichedPackage | None:
        raise NotImplementedError()

    @abstractmethod
    def put(self, package: EnrichedPackage) -> None:
        raise NotImplementedError()


class NullCache(EnrichmentCache):
    """Disables caching without forcing callers to special-case it."""

    def get(self, name: str, version: str) -> EnrichedPackage | None:
        return None

    def put(self, package: EnrichedPackage) -> None:
        return None


class InMemoryCache(EnrichmentCache):
    def __init__(self, ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._ttl = ttl_seconds
        self._entries: dict[tuple[str, str], tuple[float, EnrichedPackage]] = {}

    def get(self, name: str, version: str) -> EnrichedPackage | None:
        entry = self._entries.get((name, version))
        if entry is None:
            return None
        fetched_at, package = entry
        if time.time() - fetched_at > self._ttl:
            del self._entries[(name, version)]
            return None
        return package.model_copy(deep=True)

    def put(self, package: EnrichedPackage) -> None:
        self._entries[(package.name, package.version)] = (time.time(), package.model_copy(deep=True))


class SqliteCache(EnrichmentCache):
    """Persists enrichment results per package-version pair with a time-to-live."""

    def __init__(self, db_path: Path, ttl_seconds: float = CACHE_TTL_SECONDS) -> None:
        self._db_path = Path(db_path).expanduser()
        self._ttl = ttl_seconds
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def get(self, name: str, version: str) -> EnrichedPackage | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT fetched_at, payload FROM enrichment_cache WHERE name = ? AND version = ?",
                (name, version),
            ).fetchone()
        if row is None:
            return None
        fetched_at, payload = row
        if time.time() - fetched_at > self._ttl:
            self.evict(name, version)
            return None
        try:
            return EnrichedPackage.model_validate_json(payload)
        except ValueError:
            # A payload written by an older schema is worth re-fetching, not crashing over.
            logger.warning(f"Discarding unreadable cache entry for {name}=={version}")
            self.evict(name, version)
            return None

    def put(self, package: EnrichedPackage) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO enrichment_cache (name, version, fetched_at, payload)"
                " VALUES (?, ?, ?, ?)",
                (package.name, package.version, time.time(), package.model_dump_json()),
            )

    def evict(self, name: str, version: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "DELETE FROM enrichment_cache WHERE name = ? AND version = ?", (name, version)
            )
