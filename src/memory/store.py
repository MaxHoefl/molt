"""Persistence for everything Molt remembers between sessions.

Four kinds of record live here behind one narrow interface:

* **episodic** — the append-only log of what the user decided, per project. Raw
  material; nothing else is derived from anything else.
* **semantic** — durable facts about packages ("pycrypto -> pycryptodome was
  validated as a drop-in here"), which let a later run skip work it already did.
* **procedural** — preference rules *derived* from the episodic log. They are
  written by the derivation in `procedural.py`, never by hand, so a rule can
  always be traced back to the decisions that produced it.
* **artifacts** — proposals and completed audits, which is what makes
  `apply_plan` able to insist on a previously reviewed artifact.

The interface is an ABC with a SQLite implementation and an in-memory one, the
same shape `cache.py` uses: tests get determinism without a temp directory, and
the tools never learn which one they were handed.
"""

import sqlite3
from abc import ABC, abstractmethod
from pathlib import Path

from src.domain.models import (
    AuditRecord,
    EpisodicEntry,
    ProceduralRule,
    SemanticFact,
    UpgradeProposal,
)

GLOBAL = "*"

SCHEMA = """
CREATE TABLE IF NOT EXISTS episodic (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS episodic_project ON episodic (project);

CREATE TABLE IF NOT EXISTS semantic (
    project TEXT NOT NULL,
    key TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (project, key)
);

CREATE TABLE IF NOT EXISTS procedural (
    project TEXT NOT NULL,
    rule TEXT NOT NULL,
    payload TEXT NOT NULL,
    PRIMARY KEY (project, rule)
);

CREATE TABLE IF NOT EXISTS proposals (
    proposal_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    payload TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audits (
    proposal_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
"""


def project_key(project: str | Path | None) -> str:
    """One project, one key, whatever spelling of its path the caller used."""
    if project is None:
        return GLOBAL
    return str(Path(project).expanduser().resolve())


class MemoryStore(ABC):
    @abstractmethod
    def append_episode(self, project: str, entry: EpisodicEntry) -> None: ...

    @abstractmethod
    def episodes(self, project: str) -> list[EpisodicEntry]: ...

    @abstractmethod
    def put_fact(self, project: str, fact: SemanticFact) -> None: ...

    @abstractmethod
    def facts(self, project: str) -> list[SemanticFact]: ...

    @abstractmethod
    def put_rules(self, project: str, rules: list[ProceduralRule]) -> None: ...

    @abstractmethod
    def rules(self, project: str) -> list[ProceduralRule]: ...

    @abstractmethod
    def put_proposal(self, proposal: UpgradeProposal) -> None: ...

    @abstractmethod
    def proposal(self, proposal_id: str) -> UpgradeProposal | None: ...

    @abstractmethod
    def put_audit(self, record: AuditRecord) -> None: ...

    @abstractmethod
    def latest_audit(self, project: str) -> AuditRecord | None: ...

    def fact(self, project: str, key: str) -> SemanticFact | None:
        return next((f for f in self.facts(project) if f.key == key), None)


class InMemoryStore(MemoryStore):
    """Everything the SQLite store does, with none of the durability."""

    def __init__(self) -> None:
        self._episodes: dict[str, list[EpisodicEntry]] = {}
        self._facts: dict[str, dict[str, SemanticFact]] = {}
        self._rules: dict[str, list[ProceduralRule]] = {}
        self._proposals: dict[str, UpgradeProposal] = {}
        self._audits: dict[str, list[AuditRecord]] = {}

    def append_episode(self, project: str, entry: EpisodicEntry) -> None:
        self._episodes.setdefault(project, []).append(entry)

    def episodes(self, project: str) -> list[EpisodicEntry]:
        return list(self._episodes.get(project, []))

    def put_fact(self, project: str, fact: SemanticFact) -> None:
        self._facts.setdefault(project, {})[fact.key] = fact

    def facts(self, project: str) -> list[SemanticFact]:
        return list(self._facts.get(project, {}).values())

    def put_rules(self, project: str, rules: list[ProceduralRule]) -> None:
        self._rules[project] = list(rules)

    def rules(self, project: str) -> list[ProceduralRule]:
        return list(self._rules.get(project, []))

    def put_proposal(self, proposal: UpgradeProposal) -> None:
        self._proposals[proposal.proposal_id] = proposal.model_copy(deep=True)

    def proposal(self, proposal_id: str) -> UpgradeProposal | None:
        found = self._proposals.get(proposal_id)
        return found.model_copy(deep=True) if found else None

    def put_audit(self, record: AuditRecord) -> None:
        self._audits.setdefault(record.project, []).append(record)

    def latest_audit(self, project: str) -> AuditRecord | None:
        records = self._audits.get(project, [])
        return records[-1] if records else None


class SqliteMemoryStore(MemoryStore):
    """The durable store: one file, five tables, no migrations to speak of yet."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = Path(db_path).expanduser()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    def append_episode(self, project: str, entry: EpisodicEntry) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO episodic (project, payload) VALUES (?, ?)",
                (project, entry.model_dump_json()),
            )

    def episodes(self, project: str) -> list[EpisodicEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM episodic WHERE project = ? ORDER BY id", (project,)
            ).fetchall()
        return _decode(rows, EpisodicEntry)

    def put_fact(self, project: str, fact: SemanticFact) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO semantic (project, key, payload) VALUES (?, ?, ?)",
                (project, fact.key, fact.model_dump_json()),
            )

    def facts(self, project: str) -> list[SemanticFact]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM semantic WHERE project = ? ORDER BY key", (project,)
            ).fetchall()
        return _decode(rows, SemanticFact)

    def put_rules(self, project: str, rules: list[ProceduralRule]) -> None:
        """Rules are replaced wholesale: they are derived, so a stale one is a bug."""
        with self._connect() as conn:
            conn.execute("DELETE FROM procedural WHERE project = ?", (project,))
            conn.executemany(
                "INSERT INTO procedural (project, rule, payload) VALUES (?, ?, ?)",
                [(project, rule.rule, rule.model_dump_json()) for rule in rules],
            )

    def rules(self, project: str) -> list[ProceduralRule]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT payload FROM procedural WHERE project = ? ORDER BY rule", (project,)
            ).fetchall()
        return _decode(rows, ProceduralRule)

    def put_proposal(self, proposal: UpgradeProposal) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO proposals (proposal_id, project, payload) VALUES (?, ?, ?)",
                (proposal.proposal_id, proposal.project, proposal.model_dump_json()),
            )

    def proposal(self, proposal_id: str) -> UpgradeProposal | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM proposals WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
        return _decode_one(row, UpgradeProposal)

    def put_audit(self, record: AuditRecord) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO audits (proposal_id, project, recorded_at, payload)"
                " VALUES (?, ?, ?, ?)",
                (record.proposal_id, record.project, record.date, record.model_dump_json()),
            )

    def latest_audit(self, project: str) -> AuditRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM audits WHERE project = ? ORDER BY recorded_at DESC, rowid DESC"
                " LIMIT 1",
                (project,),
            ).fetchone()
        return _decode_one(row, AuditRecord)


def _decode[T](rows: list[tuple[str]], model: type[T]) -> list[T]:
    decoded = []
    for (payload,) in rows:
        try:
            decoded.append(model.model_validate_json(payload))
        except ValueError:
            # A row written by an older schema is worth skipping, not crashing over.
            continue
    return decoded


def _decode_one[T](row: tuple[str] | None, model: type[T]) -> T | None:
    if row is None:
        return None
    try:
        return model.model_validate_json(row[0])
    except ValueError:
        return None
