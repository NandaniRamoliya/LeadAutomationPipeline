"""SQLite storage. One file (data/leadpipe.db) holds the whole pipeline.

WAL mode lets the engine and the dashboard use the database at the same time.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    website       TEXT,
    domain        TEXT,
    phone         TEXT,
    listed_email  TEXT,
    address       TEXT,
    city          TEXT,
    country       TEXT,
    instagram     TEXT,
    facebook      TEXT,
    timezone      TEXT,
    target        TEXT,
    source        TEXT,
    source_ref    TEXT,
    signals       TEXT DEFAULT '{}',
    problems      TEXT DEFAULT '[]',
    site_summary  TEXT,
    score         REAL DEFAULT 0,
    status        TEXT DEFAULT 'new',
    status_reason TEXT,
    enriched_at   TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    UNIQUE (source, source_ref)
);
CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
CREATE INDEX IF NOT EXISTS idx_companies_domain ON companies(domain);

CREATE TABLE IF NOT EXISTS contacts (
    id                  INTEGER PRIMARY KEY,
    company_id          INTEGER REFERENCES companies(id) ON DELETE CASCADE,
    full_name           TEXT,
    first_name          TEXT,
    role                TEXT,
    email               TEXT,
    email_status        TEXT DEFAULT 'unknown',
    email_source        TEXT,
    channel             TEXT DEFAULT 'email',
    stage               TEXT DEFAULT 'new',
    sequence            TEXT,
    step                INTEGER DEFAULT 0,
    sequence_started_at TEXT,
    next_action_at      TEXT,
    last_outbound_at    TEXT,
    last_inbound_at     TEXT,
    lost_reason         TEXT,
    notes               TEXT,
    created_at          TEXT,
    updated_at          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email) WHERE email IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_contacts_stage ON contacts(stage);
CREATE INDEX IF NOT EXISTS idx_contacts_next ON contacts(next_action_at);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY,
    contact_id       INTEGER REFERENCES contacts(id) ON DELETE CASCADE,
    company_id       INTEGER,
    direction        TEXT,            -- out | in
    channel          TEXT DEFAULT 'email',
    kind             TEXT,            -- sequence | reply | onboarding | manual | inbound
    sequence         TEXT,
    step             INTEGER,
    purpose          TEXT,
    subject          TEXT,
    body             TEXT,
    status           TEXT,            -- pending | approved | sent | failed | rejected | cancelled | received
    check_issues     TEXT DEFAULT '[]',
    message_id       TEXT,
    in_reply_to      TEXT,
    references_hdr   TEXT,
    from_addr        TEXT,
    to_addr          TEXT,
    intent           TEXT,
    intent_detail    TEXT,
    needs_human      INTEGER DEFAULT 0,
    handled          INTEGER DEFAULT 0,
    generator        TEXT,
    scheduled_for    TEXT,
    sent_at          TEXT,
    received_at      TEXT,
    error            TEXT,
    created_at       TEXT,
    updated_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_msgid ON messages(message_id) WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS suppression (
    value      TEXT PRIMARY KEY,      -- email, @domain, or sha256:<hash>
    reason     TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS deals (
    id          INTEGER PRIMARY KEY,
    contact_id  INTEGER UNIQUE REFERENCES contacts(id) ON DELETE CASCADE,
    company_id  INTEGER,
    status      TEXT DEFAULT 'won',   -- won | onboarding | active | lost
    value       REAL,
    currency    TEXT,
    checklist   TEXT DEFAULT '{}',
    reminders   TEXT DEFAULT '{}',
    won_at      TEXT,
    completed_at TEXT,
    notes       TEXT,
    created_at  TEXT,
    updated_at  TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY,
    ts         TEXT,
    level      TEXT,
    kind       TEXT,
    contact_id INTEGER,
    company_id INTEGER,
    message    TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS seen_inbound (
    message_id TEXT PRIMARY KEY,
    ts         TEXT
);

CREATE TABLE IF NOT EXISTS kv (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def now_iso() -> str:
    return iso(utcnow())  # type: ignore[return-value]


class DB:
    """Thin helper around sqlite3 with dict rows. Safe to share across threads."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
            self.conn.executescript(SCHEMA)
            self.conn.commit()

    # ---- low level -----------------------------------------------------
    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self.conn.execute(sql, tuple(params))
            self.conn.commit()
            return cur

    def all(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self.conn.execute(sql, tuple(params)).fetchall()]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return dict(row) if row else None

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        with self._lock:
            row = self.conn.execute(sql, tuple(params)).fetchone()
            return row[0] if row else None

    def insert(self, table: str, values: dict) -> int:
        values = dict(values)
        if table not in ("events", "kv", "suppression"):
            values.setdefault("created_at", now_iso())
            values.setdefault("updated_at", values["created_at"])
        cols = ", ".join(values)
        qs = ", ".join("?" for _ in values)
        cur = self.execute(f"INSERT INTO {table} ({cols}) VALUES ({qs})", values.values())
        return int(cur.lastrowid)

    def update(self, table: str, row_id: int, values: dict) -> None:
        values = dict(values)
        if table not in ("events", "kv", "suppression"):
            values["updated_at"] = now_iso()
        sets = ", ".join(f"{k} = ?" for k in values)
        self.execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*values.values(), row_id])

    # ---- key/value settings (runtime toggles, counters) -------------------
    def kv_get(self, key: str, default: Any = None) -> Any:
        row = self.one("SELECT value FROM kv WHERE key = ?", (key,))
        if not row:
            return default
        try:
            return json.loads(row["value"])
        except (TypeError, ValueError):
            return row["value"]

    def kv_set(self, key: str, value: Any) -> None:
        self.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    # ---- audit log -------------------------------------------------------
    def log(self, kind: str, message: str, level: str = "info", contact_id: int | None = None,
            company_id: int | None = None) -> None:
        self.insert("events", {"ts": now_iso(), "level": level, "kind": kind, "contact_id": contact_id,
                               "company_id": company_id, "message": message[:2000]})

    # ---- convenience getters ------------------------------------------------
    def company(self, company_id: int) -> dict | None:
        return self.one("SELECT * FROM companies WHERE id = ?", (company_id,))

    def contact(self, contact_id: int) -> dict | None:
        return self.one("SELECT * FROM contacts WHERE id = ?", (contact_id,))

    def message(self, message_id: int) -> dict | None:
        return self.one("SELECT * FROM messages WHERE id = ?", (message_id,))

    def close(self) -> None:
        with self._lock:
            self.conn.close()


def jloads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
