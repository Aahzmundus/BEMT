"""Upgrading a 0.1.0 (single character) database in place.

Targets are hand-tuned data; the migration must hand every existing row to the
character the install was logged in as, and move the chosen market from
config.json onto that character.
"""

from __future__ import annotations

from bemt import config, db

CID = 90000001
STATION = 60003760

V1_SCHEMA = """
CREATE TABLE characters (
    character_id   INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    refresh_token  TEXT NOT NULL,
    access_token   TEXT,
    access_expires REAL,
    scopes         TEXT
);
CREATE TABLE items (
    type_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    target_qty  INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE stock (
    type_id     INTEGER PRIMARY KEY,
    listed_qty  INTEGER NOT NULL DEFAULT 0,
    orders      INTEGER NOT NULL DEFAULT 0,
    hangar_qty  INTEGER NOT NULL DEFAULT 0,
    price       REAL,
    updated_at  TEXT
);
CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT);
"""


def _make_v1_db(logged_in: bool):
    """Rebuild the (already-initialised) test DB as a 0.1.0 one."""
    c = db.conn()
    with c:
        for (name,) in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'").fetchall():
            c.execute(f"DROP TABLE {name}")
        c.executescript(V1_SCHEMA)
        c.execute("INSERT INTO state VALUES('schema_revision', '1')")
        if logged_in:
            c.execute("INSERT INTO characters(character_id, name, "
                      "refresh_token, scopes) VALUES(?, 'Benji', 'rt', '')",
                      (CID,))
        c.execute("INSERT INTO items VALUES(34, 'Tritanium', 5000, 1, "
                  "'import', 't', 't')")
        c.execute("INSERT INTO stock(type_id, listed_qty) VALUES(34, 250)")


def test_v1_items_are_adopted_by_the_logged_in_character(env):
    config.update(character_id=CID, location_id=STATION,
                  location_name="Jita IV-4")
    _make_v1_db(logged_in=True)

    db.init()

    item = db.conn().execute("SELECT * FROM items").fetchone()
    assert item["character_id"] == CID
    assert item["target_qty"] == 5000            # the hand-tuned target survives
    assert db.conn().execute("SELECT * FROM stock").fetchone()[
        "character_id"] == CID
    ch = db.conn().execute("SELECT * FROM characters").fetchone()
    assert ch["location_id"] == STATION          # the market moved with him
    assert ch["location_name"] == "Jita IV-4"
    assert db.get_state("schema_revision") == "2"


def test_v1_items_without_a_login_wait_for_the_first_character(env):
    """Migrated while logged out: rows park on character 0 and are adopted by
    the first login (see sso.finish_login)."""
    _make_v1_db(logged_in=False)
    db.init()
    assert db.conn().execute("SELECT character_id FROM items").fetchone()[
        "character_id"] == 0


def test_init_on_a_current_database_is_a_no_op(env):
    db.init()
    db.init()
    assert db.get_state("schema_revision") == "2"
