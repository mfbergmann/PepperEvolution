"""
Long-term memory: people who agreed to be remembered, facts, and a short record of each session (docs/MEMORY.md).

One SQLite file on the host (``MEMORY_DIR``, git-ignored). Rules, enforced here rather than left to the model:

- A person is stored only with a note of their consent (what they said when they agreed).
- Facts about a person hang off that person and go when they go; facts about the lab have no person.
- ``forget()`` deletes a person, their facts and their place in session episodes in one transaction.
- People and their facts expire (``RETENTION_DAYS``) unless renewed; ``sweep()`` deletes what has expired and runs
  at start-up.
- Search is plain text (FTS5 when the SQLite build has it, else LIKE); the text is kept, so an embedding index can
  be added later without migrating anything.

All calls are synchronous and take well under a millisecond at this size; the store is used from the event loop
thread only.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Sequence

from loguru import logger

RETENTION_DAYS = 90  # people and personal facts, unless renewed (docs/MEMORY.md)
EPISODE_RETENTION_DAYS = 365  # session episodes hold no words, only who (by id), when and counts
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    consent_at TEXT NOT NULL,
    consent_note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS people_name ON people(name_key);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    person_id INTEGER REFERENCES people(id) ON DELETE CASCADE,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    session TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT
);
CREATE INDEX IF NOT EXISTS facts_person ON facts(person_id);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY,
    session TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    summary TEXT NOT NULL,
    stats TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS episode_people (
    episode_id INTEGER NOT NULL REFERENCES episodes(id) ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (episode_id, person_id)
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(text, content='facts', content_rowid='id');
CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts BEGIN
    INSERT INTO facts_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
    INSERT INTO facts_fts(facts_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;
"""


def name_key(name: str) -> str:
    return " ".join(name.casefold().split())


def _iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds")


@dataclass(frozen=True)
class Person:
    id: int
    name: str
    consent_at: str
    consent_note: str
    created_at: str
    last_seen_at: Optional[str]
    expires_at: str


@dataclass(frozen=True)
class Fact:
    id: int
    person_id: Optional[int]
    text: str
    source: str
    created_at: str


@dataclass(frozen=True)
class Episode:
    id: int
    session: str
    started_at: str
    ended_at: str
    summary: str
    stats: Dict[str, Any]


class ConsentRequired(ValueError):
    """Raised when someone would be stored without a note of their consent."""


class MemoryStore:
    def __init__(self, path: str, now: Callable[[], datetime] = datetime.now):
        self.path = path
        self._now = now
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._db.execute("PRAGMA journal_mode = WAL")
        self._db.executescript(SCHEMA)
        try:
            self._db.executescript(FTS_SCHEMA)
            self.fts = True
        except sqlite3.OperationalError:  # SQLite built without FTS5: fall back to LIKE
            self.fts = False
        self._db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self._db.commit()
        self.logger = logger.bind(module="MemoryStore")

    @classmethod
    def open(cls, folder: str, now: Callable[[], datetime] = datetime.now) -> "MemoryStore":
        return cls(os.path.join(folder, "pepper.sqlite"), now=now)

    def close(self):
        self._db.close()

    # -- people ----------------------------------------------------------------------------------------------------

    def add_person(self, name: str, consent_note: str) -> Person:
        """Store someone who agreed to be remembered. ``consent_note`` is what they said when they agreed."""
        name = " ".join(name.split())
        if not name:
            raise ValueError("a name is needed")
        if not consent_note or not consent_note.strip():
            raise ConsentRequired("nobody is remembered without their agreement")
        now = self._now()
        cur = self._db.execute(
            "INSERT INTO people (name, name_key, consent_at, consent_note, created_at, last_seen_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, name_key(name), _iso(now), consent_note.strip()[:300], _iso(now), _iso(now), self._expiry(now)),
        )
        self._db.commit()
        self.logger.info(f"Remembering person #{cur.lastrowid} (consent noted)")
        return self.person(int(cur.lastrowid))  # type: ignore[return-value]

    def person(self, person_id: int) -> Optional[Person]:
        row = self._db.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
        return self._person(row) if row else None

    def find_people(self, name: str) -> List[Person]:
        rows = self._db.execute("SELECT * FROM people WHERE name_key = ? ORDER BY id", (name_key(name),)).fetchall()
        return [self._person(r) for r in rows]

    def people(self) -> List[Person]:
        return [self._person(r) for r in self._db.execute("SELECT * FROM people ORDER BY id").fetchall()]

    def seen(self, person_id: int):
        """They were here today: note it (consent is renewed only on request, see ``renew``)."""
        self._db.execute("UPDATE people SET last_seen_at = ? WHERE id = ?", (_iso(self._now()), person_id))
        self._db.commit()

    def renew(self, person_id: int, consent_note: str):
        """They agreed again: keep them (and their facts) for another retention period."""
        now = self._now()
        expires = self._expiry(now)
        self._db.execute(
            "UPDATE people SET consent_at = ?, consent_note = ?, expires_at = ?, last_seen_at = ? WHERE id = ?",
            (_iso(now), consent_note.strip()[:300], expires, _iso(now), person_id),
        )
        self._db.execute("UPDATE facts SET expires_at = ? WHERE person_id = ?", (expires, person_id))
        self._db.commit()

    def forget(self, person_id: int) -> bool:
        """Delete a person, their facts and their place in episodes, in one transaction."""
        with self._db:
            cur = self._db.execute("DELETE FROM people WHERE id = ?", (person_id,))
        if cur.rowcount:
            self.logger.info(f"Forgot person #{person_id}")
        return bool(cur.rowcount)

    # -- facts -----------------------------------------------------------------------------------------------------

    def add_fact(
        self, text: str, person_id: Optional[int] = None, source: str = "person", session: Optional[str] = None
    ) -> Fact:
        text = " ".join(text.split())
        if not text:
            raise ValueError("an empty fact")
        expires = None
        if person_id is not None:
            person = self.person(person_id)
            if person is None:
                raise ConsentRequired("facts about a person are kept only for people who agreed to be remembered")
            expires = person.expires_at
        cur = self._db.execute(
            "INSERT INTO facts (person_id, text, source, session, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?)",
            (person_id, text[:500], source, session, _iso(self._now()), expires),
        )
        self._db.commit()
        return self.fact(int(cur.lastrowid))  # type: ignore[return-value]

    def fact(self, fact_id: int) -> Optional[Fact]:
        row = self._db.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
        return self._fact(row) if row else None

    def facts_about(self, person_id: Optional[int], limit: int = 20) -> List[Fact]:
        if person_id is None:
            rows = self._db.execute(
                "SELECT * FROM facts WHERE person_id IS NULL ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT * FROM facts WHERE person_id = ? ORDER BY id DESC LIMIT ?", (person_id, limit)
            ).fetchall()
        return [self._fact(r) for r in rows]

    def delete_fact(self, fact_id: int) -> bool:
        with self._db:
            cur = self._db.execute("DELETE FROM facts WHERE id = ?", (fact_id,))
        return bool(cur.rowcount)

    def search(self, query: str, person_id: Optional[int] = None, limit: int = 5) -> List[Fact]:
        """Facts matching any of the words in ``query`` (best first with FTS5), optionally about one person."""
        words = [w for w in "".join(c if c.isalnum() else " " for c in query).split() if len(w) > 1]
        if not words:
            return []
        if self.fts:
            match = " OR ".join(f'"{w}"' for w in words)
            sql = (
                "SELECT facts.* FROM facts_fts JOIN facts ON facts.id = facts_fts.rowid WHERE facts_fts MATCH ?"
                + (" AND facts.person_id = ?" if person_id is not None else "")
                + " ORDER BY rank LIMIT ?"
            )
            args: Sequence[Any] = (match, person_id, limit) if person_id is not None else (match, limit)
        else:
            like = " OR ".join("text LIKE ?" for _ in words)
            sql = (
                f"SELECT * FROM facts WHERE ({like})"
                + (" AND person_id = ?" if person_id is not None else "")
                + " ORDER BY id DESC LIMIT ?"
            )
            args = [f"%{w}%" for w in words] + ([person_id] if person_id is not None else []) + [limit]
        return [self._fact(r) for r in self._db.execute(sql, args).fetchall()]

    # -- episodes --------------------------------------------------------------------------------------------------

    def add_episode(
        self,
        session: str,
        started_at: datetime,
        ended_at: datetime,
        summary: str,
        stats: Dict[str, Any],
        people: Sequence[int] = (),
    ) -> Episode:
        expires = _iso(ended_at + timedelta(days=EPISODE_RETENTION_DAYS))
        with self._db:
            cur = self._db.execute(
                "INSERT INTO episodes (session, started_at, ended_at, summary, stats, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (session, _iso(started_at), _iso(ended_at), summary, json.dumps(stats), expires),
            )
            episode_id = int(cur.lastrowid)  # type: ignore[arg-type]
            for person_id in sorted(set(people)):
                if self.person(person_id) is not None:
                    self._db.execute(
                        "INSERT INTO episode_people (episode_id, person_id) VALUES (?, ?)", (episode_id, person_id)
                    )
        return self.episodes(limit=1)[0]

    def episodes(self, person_id: Optional[int] = None, limit: int = 5) -> List[Episode]:
        if person_id is None:
            rows = self._db.execute("SELECT * FROM episodes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        else:
            rows = self._db.execute(
                "SELECT episodes.* FROM episodes JOIN episode_people ON episodes.id = episode_people.episode_id "
                "WHERE episode_people.person_id = ? ORDER BY episodes.id DESC LIMIT ?",
                (person_id, limit),
            ).fetchall()
        return [
            Episode(r["id"], r["session"], r["started_at"], r["ended_at"], r["summary"], json.loads(r["stats"]))
            for r in rows
        ]

    def episode_people(self, episode_id: int) -> List[int]:
        rows = self._db.execute(
            "SELECT person_id FROM episode_people WHERE episode_id = ? ORDER BY person_id", (episode_id,)
        ).fetchall()
        return [r["person_id"] for r in rows]

    # -- housekeeping ----------------------------------------------------------------------------------------------

    def sweep(self) -> Dict[str, int]:
        """Delete what has expired. Returns how many people, facts and episodes went."""
        now = _iso(self._now())
        with self._db:
            people = self._db.execute("DELETE FROM people WHERE expires_at <= ?", (now,)).rowcount
            facts = self._db.execute(
                "DELETE FROM facts WHERE expires_at IS NOT NULL AND expires_at <= ?", (now,)
            ).rowcount
            episodes = self._db.execute("DELETE FROM episodes WHERE expires_at <= ?", (now,)).rowcount
        if people or facts or episodes:
            self.logger.info(f"Memory sweep: {people} people, {facts} facts, {episodes} episodes expired")
        return {"people": people, "facts": facts, "episodes": episodes}

    def counts(self) -> Dict[str, int]:
        return {
            table: int(self._db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in ("people", "facts", "episodes")
        }

    def _expiry(self, now: datetime) -> str:
        return _iso(now + timedelta(days=RETENTION_DAYS))

    @staticmethod
    def _person(row: sqlite3.Row) -> Person:
        return Person(
            row["id"],
            row["name"],
            row["consent_at"],
            row["consent_note"],
            row["created_at"],
            row["last_seen_at"],
            row["expires_at"],
        )

    @staticmethod
    def _fact(row: sqlite3.Row) -> Fact:
        return Fact(row["id"], row["person_id"], row["text"], row["source"], row["created_at"])
