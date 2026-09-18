"""SQLite-хранилище: кэш файлов, сводки сессий, пины, журнал токенов.

Одна база на пользователя (по умолчанию ~/.tokensave/store.db).
Оригиналы файлов НИКОГДА не трогаются и не переписываются — здесь лежат
только производные данные, которые можно пересоздать заново.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("TOKENSAVE_DB", Path.home() / ".tokensave" / "store.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
    hash        TEXT PRIMARY KEY,
    path        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    size        INTEGER,
    parsed_at   REAL NOT NULL,
    parser_ver  INTEGER NOT NULL,
    meta        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chunks (
    hash        TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    locator     TEXT NOT NULL,
    critical    INTEGER NOT NULL DEFAULT 0,
    text        TEXT NOT NULL,
    PRIMARY KEY (hash, idx)
);

CREATE TABLE IF NOT EXISTS derived (
    key         TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id              TEXT PRIMARY KEY,
    created_at      REAL NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    summarized_upto INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS turns (
    session     TEXT NOT NULL,
    idx         INTEGER NOT NULL,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    ts          REAL NOT NULL,
    PRIMARY KEY (session, idx)
);

CREATE TABLE IF NOT EXISTS pins (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session     TEXT NOT NULL,
    kind        TEXT NOT NULL,
    text        TEXT NOT NULL,
    source      TEXT NOT NULL,
    ts          REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS pins_session ON pins(session);

CREATE TABLE IF NOT EXISTS ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    session         TEXT NOT NULL,
    mode            TEXT NOT NULL,
    model           TEXT NOT NULL,
    input_tokens    INTEGER NOT NULL DEFAULT 0,
    output_tokens   INTEGER NOT NULL DEFAULT 0,
    cache_read      INTEGER NOT NULL DEFAULT 0,
    cache_write     INTEGER NOT NULL DEFAULT 0,
    baseline_input  INTEGER NOT NULL DEFAULT 0,
    cost_usd        REAL NOT NULL DEFAULT 0,
    baseline_usd    REAL NOT NULL DEFAULT 0,
    escalated       INTEGER NOT NULL DEFAULT 0,
    note            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ledger_ts ON ledger(ts);
"""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else DEFAULT_DB
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def get_derived(conn: sqlite3.Connection, key: str):
    """Производный артефакт по ключу. Ключ обязан включать хеш исходника,
    версию парсера и версию промпта — иначе можно отдать устаревшее."""
    row = conn.execute("SELECT value FROM derived WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else None


def put_derived(conn: sqlite3.Connection, key: str, kind: str, value) -> None:
    import time

    conn.execute(
        "INSERT OR REPLACE INTO derived (key, kind, value, created_at) VALUES (?,?,?,?)",
        (key, kind, json.dumps(value, ensure_ascii=False), time.time()),
    )
    conn.commit()
