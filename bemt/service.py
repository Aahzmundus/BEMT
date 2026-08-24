"""The I/O shell: fetch from ESI, persist, and answer the page's questions.

All the arithmetic lives in `model.py`; this module only moves data between ESI,
SQLite and the web layer.

Since 0.1.1 the tool is multi-character: every logged-in character keeps their
own item list and market, refreshes loop over all of them, and the page shows
the lists merged into one big buy list or side by side - the user's choice.
"""

from __future__ import annotations

import logging

from . import config, db, model, tsutil
from .esi import assets as esi_assets
from .esi import orders as esi_orders
from .esi import sso, universe

log = logging.getLogger(__name__)


class SetupNeeded(RuntimeError):
    """Something must be chosen before a refresh can mean anything.

    ``reason`` is a machine-readable code the page turns into a prompt; the
    message is the human fallback.
    """

    def __init__(self, reason: str, message: str, **extra) -> None:
        super().__init__(message)
        self.reason = reason
        self.extra = extra


# ------------------------------------------------------------------ characters

def characters_all() -> list[dict]:
    rows = db.conn().execute(
        "SELECT character_id, name, location_id, location_name "
        "FROM characters ORDER BY name").fetchall()
    return [{**dict(r), "missing_scopes": sso.missing_scopes(r["character_id"])}
            for r in rows]


def set_character_location(character_id: int, location_id: int,
                           location_name: str | None = None) -> None:
    if not location_name:
        location_name = universe.location_name(location_id, character_id)
    c = db.conn()
    with c:
        c.execute("UPDATE characters SET location_id=?, location_name=? "
                  "WHERE character_id=?",
                  (int(location_id), location_name, int(character_id)))


def remove_character(character_id: int) -> None:
    """Log a character out and drop their list. Their items go with them -
    keeping orphaned rows would show stale lines nobody can refresh."""
    cid = int(character_id)
    sso.logout(cid)
    c = db.conn()
    with c:
        c.execute("DELETE FROM items WHERE character_id=?", (cid,))
        c.execute("DELETE FROM stock WHERE character_id=?", (cid,))


# ------------------------------------------------------------------ item store

def items_all(character_id: int | None = None) -> list[dict]:
    q = ("SELECT character_id, type_id, name, target_qty, active, source "
         "FROM items")
    args: tuple = ()
    if character_id is not None:
        q += " WHERE character_id=?"
        args = (int(character_id),)
    return [dict(r) for r in db.conn().execute(q, args).fetchall()]


def stock_all(character_id: int | None = None) -> dict[int, dict]:
    q = ("SELECT character_id, type_id, listed_qty, orders, hangar_qty, price "
         "FROM stock")
    args: tuple = ()
    if character_id is not None:
        q += " WHERE character_id=?"
        args = (int(character_id),)
    return {r["type_id"]: dict(r)
            for r in db.conn().execute(q, args).fetchall()}


def rows_for(character_id: int) -> list[dict]:
    cfg = config.load()
    return model.build_rows(items_all(character_id), stock_all(character_id),
                            count_hangar=cfg.count_hangar,
                            lot_size=cfg.buy_lot_size,
                            threshold_pct=cfg.restock_threshold_pct)


def rows_by_character() -> list[dict]:
    """One entry per character: who they are plus their computed list."""
    out = []
    for ch in characters_all():
        rows = rows_for(ch["character_id"])
        out.append({**ch, "rows": rows, "totals": model.totals(rows),
                    "multibuy": model.multibuy_text(rows)})
    return out


def current_rows() -> list[dict]:
    """Everyone's rows folded into one list - the merged shopping view."""
    return model.merge_rows([rows_for(ch["character_id"])
                             for ch in characters_all()])


def remember_names(names: dict[int, str]) -> None:
    """Cache type names so the add box can autocomplete offline."""
    if not names:
        return
    c = db.conn()
    with c:
        c.executemany(
            "INSERT INTO type_names(type_id, name) VALUES(?,?) "
            "ON CONFLICT(type_id) DO UPDATE SET name=excluded.name",
            list(names.items()))


def known_names(query: str = "", limit: int = 25) -> list[dict]:
    """Autocomplete source: type names this install has already seen.

    ESI has no public fuzzy type search, so suggestions come from names already
    encountered (his own orders, hangar, past adds) and anything else needs an
    exact name - which the game itself supplies via copy/paste.
    """
    q = (query or "").strip()
    if q:
        rows = db.conn().execute(
            "SELECT type_id, name FROM type_names WHERE name LIKE ? "
            "ORDER BY LENGTH(name), name LIMIT ?", (f"%{q}%", limit)).fetchall()
    else:
        rows = db.conn().execute(
            "SELECT type_id, name FROM type_names ORDER BY name LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]


def _resolve_character(character_id: int | None) -> int:
    """Which character a manual edit belongs to. With one logged in there is
    nothing to ask; with several the page must say."""
    chars = db.conn().execute(
        "SELECT character_id FROM characters").fetchall()
    if character_id is not None:
        return int(character_id)
    if not chars:
        raise ValueError("Log in with your EVE character first")
    if len(chars) > 1:
        raise ValueError("Several characters are logged in - say whose "
                         "list this item belongs to")
    return int(chars[0]["character_id"])


def add_item(*, name: str | None = None, type_id: int | None = None,
             target_qty: int = 0, character_id: int | None = None) -> dict:
    """Track a new item by name or id. Re-adding an existing one just unpauses
    it (and updates the target if one was given) rather than erroring."""
    cid = _resolve_character(character_id)
    resolved_name = (name or "").strip()
    if type_id is None:
        if not resolved_name:
            raise ValueError("Type an item name")
        hit = db.conn().execute(
            "SELECT type_id, name FROM type_names WHERE name = ? COLLATE NOCASE",
            (resolved_name,)).fetchone()
        if hit:
            type_id, resolved_name = hit["type_id"], hit["name"]
        else:
            found = universe.type_id_for_name(resolved_name)
            if not found:
                raise ValueError(
                    f'No item called "{resolved_name}". Copy the exact name '
                    f'from the game (right-click the item, Copy).')
            type_id, resolved_name = found
    if not resolved_name:
        resolved_name = universe.type_names([type_id]).get(
            type_id, f"Type {type_id}")

    remember_names({int(type_id): resolved_name})
    now = tsutil.now_str()
    c = db.conn()
    with c:
        existing = c.execute(
            "SELECT type_id FROM items WHERE character_id=? AND type_id=?",
            (cid, type_id)).fetchone()
        if existing:
            if target_qty > 0:
                c.execute("UPDATE items SET target_qty=?, active=1, name=?, "
                          "updated_at=? WHERE character_id=? AND type_id=?",
                          (int(target_qty), resolved_name, now, cid, type_id))
            else:
                c.execute("UPDATE items SET active=1, name=?, updated_at=? "
                          "WHERE character_id=? AND type_id=?",
                          (resolved_name, now, cid, type_id))
        else:
            c.execute(
                "INSERT INTO items(character_id, type_id, name, target_qty, "
                "active, source, created_at, updated_at) "
                "VALUES(?,?,?,?,1,'manual',?,?)",
                (cid, int(type_id), resolved_name, int(target_qty), now, now))
    return {"type_id": int(type_id), "name": resolved_name,
            "character_id": cid}


def update_item(character_id: int, type_id: int, *,
                target_qty: int | None = None,
                active: bool | None = None) -> None:
    sets, args = [], []
    if target_qty is not None:
        sets.append("target_qty=?")
        args.append(max(0, int(target_qty)))
    if active is not None:
        sets.append("active=?")
        args.append(1 if active else 0)
    if not sets:
        return
    sets.append("updated_at=?")
    args.extend([tsutil.now_str(), int(character_id), int(type_id)])
    c = db.conn()
    with c:
        c.execute(f"UPDATE items SET {', '.join(sets)} "
                  "WHERE character_id=? AND type_id=?", args)


def remove_item(character_id: int, type_id: int) -> None:
    c = db.conn()
    with c:
        c.execute("DELETE FROM items WHERE character_id=? AND type_id=?",
                  (int(character_id), int(type_id)))
        c.execute("DELETE FROM stock WHERE character_id=? AND type_id=?",
                  (int(character_id), int(type_id)))


# --------------------------------------------------------------------- refresh

def _require_characters() -> list[dict]:
    cfg = config.load()
    if not cfg.esi_client_id:
        raise SetupNeeded("no_client_id",
                          "No EVE application id configured yet.")
    chars = characters_all()
    if not chars:
        raise SetupNeeded("no_character", "Log in with your EVE character.")
    for ch in chars:
        if ch["missing_scopes"]:
            raise SetupNeeded(
                "missing_scopes",
                "This version needs extra EVE permissions - log in once more.",
                scopes=ch["missing_scopes"],
                character_id=ch["character_id"],
                character_name=ch["name"])
    return chars


def locations(character_id: int) -> list[dict]:
    """Markets one character sells at, named, for the picker."""
    cid = int(character_id)
    found = model.order_locations(esi_orders.open_orders(cid))
    for loc in found:
        loc["name"] = universe.location_name(loc["location_id"], cid)
    return found


def _refresh_character(ch: dict, cfg: config.Config,
                       have_names: dict[int, str]) -> dict:
    """One character's full update. Returns per-character summary bits."""
    cid = ch["character_id"]
    raw_orders = esi_orders.open_orders(cid)
    location_id = ch["location_id"]

    if location_id is None:
        found = model.order_locations(raw_orders)
        if len(found) == 1:
            location_id = found[0]["location_id"]
            set_character_location(cid, location_id)
            ch["location_id"] = location_id
            ch["location_name"] = db.conn().execute(
                "SELECT location_name FROM characters WHERE character_id=?",
                (cid,)).fetchone()["location_name"]
        elif len(found) > 1:
            for loc in found:
                loc["name"] = universe.location_name(loc["location_id"], cid)
            raise SetupNeeded(
                "choose_location",
                f"Choose which market {ch['name']} is seeding.",
                locations=found, character_id=cid, character_name=ch["name"])
        # No orders and no chosen market: nothing to place stock at - their
        # tracked items (if any) simply read as 0 listed, which is the truth.

    order_totals = model.sell_order_totals(raw_orders, location_id) \
        if location_id is not None else {}

    hangar: dict[int, int] = {}
    hangar_error = None
    if cfg.count_hangar and location_id is not None:
        try:
            hangar = model.hangar_stacks(esi_assets.fetch_assets(cid),
                                         location_id)
        except Exception as e:
            # Hangar stock is an accuracy refinement, not the feature. Losing
            # it over-buys slightly, which beats failing the whole refresh -
            # but say so rather than pretending the zero is real.
            log.warning("hangar read failed for %s: %s", ch["name"], e)
            hangar_error = str(e)

    known = {i["type_id"] for i in items_all(cid)}
    imported: dict[int, int] = {}
    if cfg.auto_import:
        imported = model.plan_import(order_totals, known)

    # Name everything we are about to store, in one batched call.
    need_names = set(imported) | (set(hangar) & known) | set(order_totals)
    missing = [t for t in need_names if t not in have_names]
    if missing:
        fetched = universe.type_names(missing)
        remember_names(fetched)
        have_names.update(fetched)

    now = tsutil.now_str()
    c = db.conn()
    with c:
        for tid, seed in imported.items():
            c.execute(
                "INSERT INTO items(character_id, type_id, name, target_qty, "
                "active, source, created_at, updated_at) "
                "VALUES(?,?,?,?,1,'import',?,?) "
                "ON CONFLICT(character_id, type_id) DO NOTHING",
                (cid, tid, have_names.get(tid, f"Type {tid}"), int(seed),
                 now, now))
        # Stock is rebuilt wholesale: an item whose orders are all gone must
        # read as 0 listed, not keep yesterday's number. An emptied state is
        # real data.
        c.execute("DELETE FROM stock WHERE character_id=?", (cid,))
        tracked = {i["type_id"] for i in items_all(cid)}
        for tid in tracked:
            t = order_totals.get(tid) or {}
            c.execute(
                "INSERT INTO stock(character_id, type_id, listed_qty, orders, "
                "hangar_qty, price, updated_at) VALUES(?,?,?,?,?,?,?)",
                (cid, tid, int(t.get("listed") or 0),
                 int(t.get("orders") or 0), int(hangar.get(tid) or 0),
                 t.get("price"), now))
        # Keep names fresh for items renamed by CCP or added before a lookup.
        for tid, nm in have_names.items():
            c.execute("UPDATE items SET name=? WHERE character_id=? AND "
                      "type_id=? AND name!=?", (nm, cid, tid, nm))

    rows = rows_for(cid)
    # A character with nothing tracked yet has no state worth freezing - an
    # all-zero snapshot per idle refresh would just be noise in the history.
    snapshot_id = record_snapshot(rows, len(imported), cid, location_id,
                                  ch["location_name"]) if rows else None
    return {"imported": len(imported), "hangar_error": hangar_error,
            "snapshot_id": snapshot_id, "orders": len(raw_orders)}


def refresh() -> dict:
    """One full update across every logged-in character: read their orders
    (and hangars), import anything new, recompute the buy lists, and record a
    snapshot per character.

    Raises SetupNeeded when a character's market hasn't been chosen yet -
    guessing which of several markets they mean would silently produce a
    wrong list.
    """
    chars = _require_characters()
    cfg = config.load()
    have_names = {r["type_id"]: r["name"] for r in known_names(limit=100000)}

    imported = 0
    orders_seen = 0
    hangar_errors: list[str] = []
    for ch in chars:
        part = _refresh_character(ch, cfg, have_names)
        imported += part["imported"]
        orders_seen += part["orders"]
        if part["hangar_error"]:
            hangar_errors.append(f"{ch['name']}: {part['hangar_error']}")

    if not orders_seen and not items_all():
        raise SetupNeeded(
            "no_orders",
            "No character has open sell orders, so there is nothing to "
            "import yet. Add items by hand, or place some orders first.")

    now = tsutil.now_str()
    db.set_state("last_refresh", now)
    db.set_state("last_error", "")

    rows = current_rows()
    summary = model.totals(rows)
    summary.update({
        "ts": now,
        "imported": imported,
        "characters": len(chars),
        "hangar_error": "; ".join(hangar_errors) or None,
    })
    return summary


def record_snapshot(rows: list[dict], imported: int, character_id: int,
                    location_id: int | None, location_name: str | None) -> int:
    """Persist this refresh - totals and every line, per character.

    History cannot be backfilled: a refresh that wasn't recorded is gone for
    good, and at this scale storage is free. `count_hangar` is frozen into the
    row so a later settings change can't rewrite what an old list meant.
    """
    cfg = config.load()
    t = model.totals(rows)
    c = db.conn()
    with c:
        cur = c.execute(
            "INSERT INTO snapshots(ts, character_id, location_id, "
            "location_name, tracked, buy_lines, buy_units, listed_units, "
            "hangar_units, imported, count_hangar) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (tsutil.now_str(), character_id, location_id,
             location_name, t["tracked"], t["buy_lines"], t["buy_units"],
             t["listed_units"], t["hangar_units"], imported,
             1 if cfg.count_hangar else 0))
        snapshot_id = int(cur.lastrowid)
        c.executemany(
            "INSERT INTO snapshot_lines(snapshot_id, type_id, name, target_qty,"
            " listed_qty, hangar_qty, buy_qty, price) VALUES(?,?,?,?,?,?,?,?)",
            [(snapshot_id, r["type_id"], r["name"], r["target_qty"],
              r["listed_qty"], r["hangar_qty"], r["buy_qty"], r["price"])
             for r in rows])
    return snapshot_id


def history(limit: int = 30) -> list[dict]:
    rows = db.conn().execute(
        "SELECT id, ts, character_id, tracked, buy_lines, buy_units, "
        "listed_units, hangar_units, imported FROM snapshots "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ----------------------------------------------------------------- page state

def page_state() -> dict:
    """Everything the page needs in one call."""
    cfg = config.load()
    by_char = rows_by_character()
    merged = model.merge_rows([ch["rows"] for ch in by_char])
    last = db.get_state("last_refresh")
    return {
        "rows": merged,
        "totals": model.totals(merged),
        "multibuy": model.multibuy_text(merged),
        "by_character": by_char,
        "last_refresh": last,
        "last_refresh_age": tsutil.age_seconds(last),
        "settings": {
            "count_hangar": cfg.count_hangar,
            "auto_import": cfg.auto_import,
            "buy_lot_size": cfg.buy_lot_size,
            "restock_threshold_pct": cfg.restock_threshold_pct,
            "merge_characters": cfg.merge_characters,
            "language": cfg.language,
            "has_client_id": bool(cfg.esi_client_id),
        },
    }
