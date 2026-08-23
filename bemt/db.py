"""SQLite storage: schema, connections, and the history tables.

One file, `data/bemt.db`. Connections are per-thread (uvicorn runs sync
endpoints in a threadpool) and opened with WAL so a long refresh can't block a
page load.
"""

from __future__ import annotations

import sqlite3
import threading

from . import paths

#: Bump when the schema changes so an existing install re-runs `init()`.
_SCHEMA_REVISION = 1

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS characters (
    character_id   INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    access_token   TEXT,
    access_expires REAL,
    scopes         TEXT
);

-- The tracked stock list: one row per item Benji wants kept on the market.
-- `target_qty` is the par level; it is seeded from his own orders on first
-- import and is his to edit afterwards - an import never overwrites it.
CREATE TABLE IF NOT EXISTS items (
    type_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    target_qty  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'import'
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

-- What the last refresh actually observed. Separate from `items` because it is
-- derived data: wiping it loses nothing the next refresh can't rebuild.
CREATE TABLE IF NOT EXISTS stock (
    type_id     INTEGER PRIMARY KEY,
    listed_qty  INTEGER NOT NULL DEFAULT 0,   -- sum of volume_remain
    orders      INTEGER NOT NULL DEFAULT 0,   -- how many open sell orders
    hangar_qty  INTEGER NOT NULL DEFAULT 0,
    price       REAL,                          -- lowest of his own sell prices
    updated_at  TEXT
);

-- Name cache so the add-item box can autocomplete without hitting ESI, and so
-- a type never renders as a bare id if ESI is unreachable.
CREATE TABLE IF NOT EXISTS type_names (
    type_id INTEGER PRIMARY KEY,
    name    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_type_names_name ON type_names(name);

-- History from day one: a snapshot per refresh, totals plus per-item detail.
-- History cannot be backfilled - a refresh that wasn't recorded is gone.
CREATE TABLE IF NOT EXISTS snapshots (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    character_id  INTEGER,
    location_id   INTEGER,
    location_name TEXT,
    tracked       INTEGER NOT NULL DEFAULT 0,
    buy_lines     INTEGER NOT NULL DEFAULT 0,
    buy_units     INTEGER NOT NULL DEFAULT 0,
    listed_units  INTEGER NOT NULL DEFAULT 0,
    hangar_units  INTEGER NOT NULL DEFAULT 0,
    imported      INTEGER NOT NULL DEFAULT 0,   -- new types auto-added this run
    count_hangar  INTEGER NOT NULL DEFAULT 1    -- frozen: the setting used here
);
CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);

CREATE TABLE IF NOT EXISTS snapshot_lines (
    snapshot_id INTEGER NOT NULL,
    type_id     INTEGER NOT NULL,
    name        TEXT,
    target_qty  INTEGER NOT NULL DEFAULT 0,
    listed_qty  INTEGER NOT NULL DEFAULT 0,
    hangar_qty  INTEGER NOT NULL DEFAULT 0,
    buy_qty     INTEGER NOT NULL DEFAULT 0,
    price       REAL,
    PRIMARY KEY (snapshot_id, type_id)
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def conn() -> sqlite3.Connection:
    c = getattr(_local, "conn", None)
    if c is None:
        paths.ensure_data_dir()
        c = sqlite3.connect(paths.DB_PATH, check_same_thread=False, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        _local.conn = c
    return c


def init() -> None:
    """Create the schema if needed. Safe to call on every start."""
    c = conn()
    with c:
        c.executescript(SCHEMA)
        c.execute(
            "INSERT INTO state(key, value) VALUES('schema_revision', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(_SCHEMA_REVISION),),
        )


def get_state(key: str, default: str | None = None) -> str | None:
    row = conn().execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(key: str, value: str) -> None:
    c = conn()
    with c:
        c.execute(
            "INSERT INTO state(key, value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def reset_connection() -> None:
    """Drop this thread's connection - used by tests that repoint DB_PATH."""
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
        _local.conn = None
