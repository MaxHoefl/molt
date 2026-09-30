"""Where the process-wide singletons are made, and the only place they are made.

The tools take their collaborators as arguments — a store, a session, a model, a
retriever — which is what makes every one of them testable without a filesystem or a
network. This module is where the real ones come from, exactly once, so the MCP
layer can stay a thin adapter over functions that never learned about MCP.
"""

from functools import cache
from pathlib import Path

from src.cache import EnrichmentCache, SqliteCache
from src.config.app_config import AppConfig, locate_env_file
from src.constants import ENV_PREFIX
from src.memory.store import MemoryStore, SqliteMemoryStore


@cache
def acquire_app_config() -> AppConfig:
    """Configuration from the active env file, or from defaults when there is none."""
    try:
        env_file = locate_env_file()
    except FileNotFoundError:
        env_file = None
    return AppConfig(_env_file=env_file, _env_prefix=ENV_PREFIX, _env_file_encoding="utf-8")


@cache
def acquire_cache() -> EnrichmentCache:
    return SqliteCache(Path(acquire_app_config().cache_path))


@cache
def acquire_memory_store() -> MemoryStore:
    return SqliteMemoryStore(Path(acquire_app_config().memory_path).expanduser())
