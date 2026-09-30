from datetime import UTC, datetime

import pytest

from src.domain.models import (
    AuditRecord,
    DecisionAction,
    EpisodicEntry,
    ProceduralRule,
    SemanticFact,
    TriageBranch,
    UpgradeProposal,
)
from src.memory.store import GLOBAL, InMemoryStore, SqliteMemoryStore, project_key

PROJECT = "/tmp/some-project"


def episode(package: str = "paramiko", action=DecisionAction.REJECTED) -> EpisodicEntry:
    return EpisodicEntry(
        recorded_at=datetime.now(UTC).isoformat(),
        package=package,
        action=action,
        branch=TriageBranch.UPGRADE_BREAKING,
    )


def proposal(proposal_id: str = "prop_a81f3c") -> UpgradeProposal:
    return UpgradeProposal(
        proposal_id=proposal_id,
        project=PROJECT,
        manifest_path=f"{PROJECT}/pyproject.toml",
        manifest_hash="abc123",
        created_at="2026-09-01T00:00:00+00:00",
        diff="--- a\n+++ b\n",
    )


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    """Every test below runs against both implementations, which is the contract."""
    if request.param == "memory":
        return InMemoryStore()
    return SqliteMemoryStore(tmp_path / "molt.db")


def test_returns_nothing_for_a_project_it_has_never_seen(store):
    assert store.episodes(PROJECT) == []
    assert store.facts(PROJECT) == []
    assert store.rules(PROJECT) == []
    assert store.latest_audit(PROJECT) is None


def test_keeps_episodes_in_the_order_they_happened(store):
    store.append_episode(PROJECT, episode("urllib3"))
    store.append_episode(PROJECT, episode("paramiko"))

    assert [e.package for e in store.episodes(PROJECT)] == ["urllib3", "paramiko"]


def test_keeps_one_project_history_out_of_another(store):
    store.append_episode(PROJECT, episode("urllib3"))

    assert store.episodes("/tmp/other") == []


def test_stores_a_fact_under_its_key(store):
    store.put_fact(PROJECT, SemanticFact(key="k", fact="pycrypto -> pycryptodome", established="x"))

    assert store.fact(PROJECT, "k").fact == "pycrypto -> pycryptodome"


def test_a_later_fact_supersedes_an_earlier_one_with_the_same_key(store):
    store.put_fact(PROJECT, SemanticFact(key="k", fact="old", established="x"))
    store.put_fact(PROJECT, SemanticFact(key="k", fact="new", established="y"))

    assert [f.fact for f in store.facts(PROJECT)] == ["new"]


def test_replaces_the_rule_set_wholesale(store):
    """Rules are derived, so a rule that survives a re-derivation is a stale opinion."""
    store.put_rules(PROJECT, [ProceduralRule(rule="a", confidence=0.8)])
    store.put_rules(PROJECT, [ProceduralRule(rule="b", confidence=0.9)])

    assert [r.rule for r in store.rules(PROJECT)] == ["b"]


def test_round_trips_a_proposal(store):
    store.put_proposal(proposal())

    assert store.proposal("prop_a81f3c").manifest_hash == "abc123"


def test_returns_nothing_for_an_unknown_proposal_id(store):
    assert store.proposal("prop_nope") is None


def test_a_stored_proposal_is_a_copy_not_a_handle(store):
    """apply_plan reads the proposal back to check it; a live reference would defeat that."""
    original = proposal()
    store.put_proposal(original)
    original.applied = True

    assert store.proposal("prop_a81f3c").applied is False


def test_serves_the_most_recent_audit(store):
    store.put_audit(AuditRecord(date="2026-07-12", proposal_id="p1", project=PROJECT))
    store.put_audit(AuditRecord(date="2026-08-09", proposal_id="p2", project=PROJECT))

    assert store.latest_audit(PROJECT).proposal_id == "p2"


def test_sqlite_survives_being_reopened(tmp_path):
    path = tmp_path / "molt.db"
    SqliteMemoryStore(path).append_episode(PROJECT, episode("urllib3"))

    assert [e.package for e in SqliteMemoryStore(path).episodes(PROJECT)] == ["urllib3"]


def test_sqlite_skips_a_row_written_by_an_older_schema(tmp_path):
    import sqlite3

    path = tmp_path / "molt.db"
    store = SqliteMemoryStore(path)
    store.append_episode(PROJECT, episode("urllib3"))
    with sqlite3.connect(path) as conn:
        conn.execute("INSERT INTO episodic (project, payload) VALUES (?, ?)", (PROJECT, "{}"))

    assert [e.package for e in store.episodes(PROJECT)] == ["urllib3"]


def test_two_spellings_of_one_path_are_one_project():
    assert project_key("/tmp/../tmp/x") == project_key("/tmp/x")


def test_a_missing_project_keys_to_the_global_bucket():
    assert project_key(None) == GLOBAL
