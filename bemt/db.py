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
#: rev 2 (0.1.1): multi-character - items/stock keyed by (character_id,
#: type_id), and each character carries its own market location.
_SCHEMA_REVISION = 2

_local = threading.local()

SCHEMA = """
-- Every logged-in character. Each carries its own market: the tool refreshes
-- and lists all of them, merged or side by side.
CREATE TABLE IF NOT EXISTS characters (
    character_id   INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    access_token   TEXT,
    access_expires REAL,
    scopes         TEXT,
    location_id    INTEGER,
    location_name  TEXT
);

-- The tracked stock list: one row per (character, item) pair kept on the
-- market. `target_qty` is the par level; it is seeded from the character's own
-- orders on first import and is theirs to edit afterwards - an import never
-- overwrites it.
CREATE TABLE IF NOT EXISTS items (
    character_id INTEGER NOT NULL DEFAULT 0,
    type_id     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    target_qty  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'manual',   -- 'manual' | 'import'
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (character_id, type_id)
);

-- What the last refresh actually observed. Separate from `items` because it is
-- derived data: wiping it loses nothing the next refresh can't rebuild.
CREATE TABLE IF NOT EXISTS stock (
    character_id INTEGER NOT NULL DEFAULT 0,
    type_id     INTEGER NOT NULL,
    listed_qty  INTEGER NOT NULL DEFAULT 0,   -- sum of volume_remain
    orders      INTEGER NOT NULL DEFAULT 0,   -- how many open sell orders
    hangar_qty  INTEGER NOT NULL DEFAULT 0,
    price       REAL,                          -- lowest of his own sell prices
    updated_at  TEXT,
    PRIMARY KEY (character_id, type_id)
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


def _columns(c: sqlite3.Connection, table: str) -> list[str]:
    return [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]


def _tables(c: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def init() -> None:
    """Create the schema if needed, migrating an older install in place."""
    c = conn()
    with c:
        # Detect a pre-0.1.1 (single character) database before SCHEMA runs:
        # its items/stock lack the character_id column.
        needs_v2 = ("items" in _tables(c)
                    and "character_id" not in _columns(c, "items"))
        if needs_v2:
            c.execute("ALTER TABLE items RENAME TO items_v1")
            c.execute("ALTER TABLE stock RENAME TO stock_v1")
        if "characters" in _tables(c) \
                and "location_id" not in _columns(c, "characters"):
            c.execute("ALTER TABLE characters ADD COLUMN location_id INTEGER")
            c.execute("ALTER TABLE characters ADD COLUMN location_name TEXT")

        c.executescript(SCHEMA)

        if needs_v2:
            from . import config  # config never imports db, so no cycle
            cfg = config.load()
            owner = cfg.character_id or 0
            c.execute(
                "INSERT INTO items(character_id, type_id, name, target_qty, "
                "active, source, created_at, updated_at) "
                "SELECT ?, type_id, name, target_qty, active, source, "
                "created_at, updated_at FROM items_v1", (owner,))
            c.execute(
                "INSERT INTO stock(character_id, type_id, listed_qty, orders, "
                "hangar_qty, price, updated_at) "
                "SELECT ?, type_id, listed_qty, orders, hangar_qty, price, "
                "updated_at FROM stock_v1", (owner,))
            c.execute("DROP TABLE items_v1")
            c.execute("DROP TABLE stock_v1")
            # The chosen market used to live in config; it belongs to the
            # character now.
            if owner and cfg.location_id:
                c.execute(
                    "UPDATE characters SET location_id=?, location_name=? "
                    "WHERE character_id=? AND location_id IS NULL",
                    (cfg.location_id, cfg.location_name or None, owner))

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
