from datetime import date

import pytest

from src.cache import InMemoryCache, NullCache, SqliteCache
from src.domain.models import EnrichedPackage, RepoHealth, Vulnerability

PYCRYPTO = EnrichedPackage(
    name="pycrypto",
    version="2.6.1",
    vulnerabilities=[Vulnerability(id="CVE-2013-7459", severity="HIGH")],
    available_versions=["2.6.1"],
    license_declared="Public Domain",
    repo_health=RepoHealth(archived=True, last_commit=date(2014, 6, 20), contributors=12),
)


@pytest.fixture(params=["sqlite", "memory"])
def cache(request, tmp_path):
    if request.param == "sqlite":
        return SqliteCache(tmp_path / "molt.db")
    return InMemoryCache()


@pytest.fixture(params=["sqlite", "memory"])
def expired_cache(request, tmp_path):
    if request.param == "sqlite":
        return SqliteCache(tmp_path / "molt.db", ttl_seconds=-1)
    return InMemoryCache(ttl_seconds=-1)


def test_returns_nothing_for_a_package_never_stored(cache):
    assert cache.get("pycrypto", "2.6.1") is None


def test_returns_a_stored_package(cache):
    cache.put(PYCRYPTO)

    assert cache.get("pycrypto", "2.6.1") == PYCRYPTO


def test_keeps_entries_for_different_versions_apart(cache):
    cache.put(PYCRYPTO)

    assert cache.get("pycrypto", "2.6.0") is None


def test_overwrites_an_earlier_entry_for_the_same_version(cache):
    cache.put(PYCRYPTO)
    cache.put(PYCRYPTO.model_copy(update={"license_declared": "BSD-2-Clause"}))

    assert cache.get("pycrypto", "2.6.1").license_declared == "BSD-2-Clause"


def test_does_not_return_an_entry_past_its_time_to_live(expired_cache):
    expired_cache.put(PYCRYPTO)

    assert expired_cache.get("pycrypto", "2.6.1") is None


def test_hands_out_copies_so_callers_cannot_mutate_the_cache(cache):
    cache.put(PYCRYPTO)

    cache.get("pycrypto", "2.6.1").vulnerabilities.clear()

    assert cache.get("pycrypto", "2.6.1").vulnerabilities != []


def test_sqlite_cache_survives_a_restart(tmp_path):
    SqliteCache(tmp_path / "molt.db").put(PYCRYPTO)

    assert SqliteCache(tmp_path / "molt.db").get("pycrypto", "2.6.1") == PYCRYPTO


def test_sqlite_cache_creates_its_parent_directory(tmp_path):
    SqliteCache(tmp_path / "nested" / "dir" / "molt.db").put(PYCRYPTO)

    assert (tmp_path / "nested" / "dir" / "molt.db").exists()


def test_sqlite_cache_discards_an_entry_it_can_no_longer_read(tmp_path):
    import sqlite3

    db_path = tmp_path / "molt.db"
    cache = SqliteCache(db_path)
    cache.put(PYCRYPTO)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE enrichment_cache SET payload = 'not json'")

    assert cache.get("pycrypto", "2.6.1") is None


def test_null_cache_never_returns_what_was_stored():
    cache = NullCache()
    cache.put(PYCRYPTO)

    assert cache.get("pycrypto", "2.6.1") is None
