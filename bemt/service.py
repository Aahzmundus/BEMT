"""The I/O shell: fetch from ESI, persist, and answer the page's questions.

All the arithmetic lives in `model.py`; this module only moves data between ESI,
SQLite and the web layer.
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


# ------------------------------------------------------------------ item store

def items_all() -> list[dict]:
    rows = db.conn().execute(
        "SELECT type_id, name, target_qty, active, source FROM items"
    ).fetchall()
    return [dict(r) for r in rows]


def stock_all() -> dict[int, dict]:
    rows = db.conn().execute(
        "SELECT type_id, listed_qty, orders, hangar_qty, price FROM stock"
    ).fetchall()
    return {r["type_id"]: dict(r) for r in rows}


def current_rows() -> list[dict]:
    cfg = config.load()
    return model.build_rows(items_all(), stock_all(),
                            count_hangar=cfg.count_hangar,
                            lot_size=cfg.buy_lot_size)


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


def add_item(*, name: str | None = None, type_id: int | None = None,
             target_qty: int = 0) -> dict:
    """Track a new item by name or id. Re-adding an existing one just unpauses
    it (and updates the target if one was given) rather than erroring."""
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
        existing = c.execute("SELECT type_id FROM items WHERE type_id=?",
                             (type_id,)).fetchone()
        if existing:
            if target_qty > 0:
                c.execute("UPDATE items SET target_qty=?, active=1, name=?, "
                          "updated_at=? WHERE type_id=?",
                          (int(target_qty), resolved_name, now, type_id))
            else:
                c.execute("UPDATE items SET active=1, name=?, updated_at=? "
                          "WHERE type_id=?", (resolved_name, now, type_id))
        else:
            c.execute(
                "INSERT INTO items(type_id, name, target_qty, active, source, "
                "created_at, updated_at) VALUES(?,?,?,1,'manual',?,?)",
                (int(type_id), resolved_name, int(target_qty), now, now))
    return {"type_id": int(type_id), "name": resolved_name}


def update_item(type_id: int, *, target_qty: int | None = None,
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
    args.extend([tsutil.now_str(), int(type_id)])
    c = db.conn()
    with c:
        c.execute(f"UPDATE items SET {', '.join(sets)} WHERE type_id=?", args)


def remove_item(type_id: int) -> None:
    c = db.conn()
    with c:
        c.execute("DELETE FROM items WHERE type_id=?", (int(type_id),))
        c.execute("DELETE FROM stock WHERE type_id=?", (int(type_id),))


# --------------------------------------------------------------------- refresh

def _require_character() -> int:
    cfg = config.load()
    if not cfg.esi_client_id:
        raise SetupNeeded("no_client_id",
                          "No EVE application id configured yet.")
    if not cfg.character_id:
        raise SetupNeeded("no_character", "Log in with your EVE character.")
    missing = sso.missing_scopes(cfg.character_id)
    if missing:
        raise SetupNeeded(
            "missing_scopes",
            "This version needs extra EVE permissions - log in once more.",
            scopes=missing)
    return cfg.character_id


def locations(character_id: int | None = None) -> list[dict]:
    """Markets the character sells at, named, for the picker."""
    cid = character_id or _require_character()
    found = model.order_locations(esi_orders.open_orders(cid))
    for loc in found:
        loc["name"] = universe.location_name(loc["location_id"], cid)
    return found


def refresh() -> dict:
    """One full update: read the orders (and hangar), import anything new,
    recompute the buy list, and record a snapshot.

    Raises SetupNeeded when the market hasn't been chosen yet - guessing which
    of several markets he means would silently produce a wrong list.
    """
    cid = _require_character()
    cfg = config.load()

    raw_orders = esi_orders.open_orders(cid)
    if cfg.location_id is None:
        found = model.order_locations(raw_orders)
        if len(found) == 1:
            loc_id = found[0]["location_id"]
            cfg = config.update(
                location_id=loc_id,
                location_name=universe.location_name(loc_id, cid))
        elif not found:
            raise SetupNeeded(
                "no_orders",
                "This character has no open sell orders, so there is nothing "
                "to import yet. Add items by hand, or place some orders first.")
        else:
            for loc in found:
                loc["name"] = universe.location_name(loc["location_id"], cid)
            raise SetupNeeded("choose_location",
                              "Choose which market you are seeding.",
                              locations=found)

    order_totals = model.sell_order_totals(raw_orders, cfg.location_id)

    hangar: dict[int, int] = {}
    if cfg.count_hangar:
        try:
            hangar = model.hangar_stacks(esi_assets.fetch_assets(cid),
                                         cfg.location_id)
        except Exception as e:
            # Hangar stock is an accuracy refinement, not the feature. Losing it
            # over-buys slightly, which beats failing the whole refresh - but
            # say so rather than pretending the zero is real.
            log.warning("hangar read failed: %s", e)
            hangar = {}
            hangar_error = str(e)
        else:
            hangar_error = None
    else:
        hangar_error = None

    known = {int(i["type_id"]) for i in items_all()}
    imported: dict[int, int] = {}
    if cfg.auto_import:
        imported = model.plan_import(order_totals, known)

    # Name everything we are about to store, in one batched call.
    need_names = set(imported) | (set(hangar) & known) | set(order_totals)
    have_names = {r["type_id"]: r["name"] for r in known_names(limit=100000)}
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
                "INSERT INTO items(type_id, name, target_qty, active, source, "
                "created_at, updated_at) VALUES(?,?,?,1,'import',?,?) "
                "ON CONFLICT(type_id) DO NOTHING",
                (tid, have_names.get(tid, f"Type {tid}"), int(seed), now, now))
        # Stock is rebuilt wholesale: an item whose orders are all gone must
        # read as 0 listed, not keep yesterday's number. An emptied state is
        # real data.
        c.execute("DELETE FROM stock")
        tracked = {int(i["type_id"]) for i in items_all()}
        for tid in tracked:
            t = order_totals.get(tid) or {}
            c.execute(
                "INSERT INTO stock(type_id, listed_qty, orders, hangar_qty, "
                "price, updated_at) VALUES(?,?,?,?,?,?)",
                (tid, int(t.get("listed") or 0), int(t.get("orders") or 0),
                 int(hangar.get(tid) or 0), t.get("price"), now))
        # Keep names fresh for items renamed by CCP or added before a lookup.
        for tid, nm in have_names.items():
            c.execute("UPDATE items SET name=? WHERE type_id=? AND name!=?",
                      (nm, tid, nm))

    rows = current_rows()
    snapshot_id = record_snapshot(rows, len(imported))
    db.set_state("last_refresh", now)
    db.set_state("last_error", "")

    summary = model.totals(rows)
    summary.update({
        "ts": now,
        "imported": len(imported),
        "snapshot_id": snapshot_id,
        "location_id": cfg.location_id,
        "location_name": cfg.location_name,
        "hangar_error": hangar_error,
    })
    return summary


def record_snapshot(rows: list[dict], imported: int) -> int:
    """Persist this refresh - totals and every line.

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
            (tsutil.now_str(), cfg.character_id, cfg.location_id,
             cfg.location_name, t["tracked"], t["buy_lines"], t["buy_units"],
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
        "SELECT id, ts, tracked, buy_lines, buy_units, listed_units, "
        "hangar_units, imported FROM snapshots ORDER BY id DESC LIMIT ?",
        (limit,)).fetchall()
    return [dict(r) for r in reversed(rows)]


# ----------------------------------------------------------------- page state

def page_state() -> dict:
    """Everything the page needs in one call."""
    cfg = config.load()
    rows = current_rows()
    last = db.get_state("last_refresh")
    character = None
    if cfg.character_id:
        row = db.conn().execute(
            "SELECT character_id, name FROM characters WHERE character_id=?",
            (cfg.character_id,)).fetchone()
        if row:
            character = {"character_id": row["character_id"],
                         "name": row["name"],
                         "missing_scopes": sso.missing_scopes(row["character_id"])}
    return {
        "rows": rows,
        "totals": model.totals(rows),
        "multibuy": model.multibuy_text(rows),
        "last_refresh": last,
        "last_refresh_age": tsutil.age_seconds(last),
        "character": character,
        "settings": {
            "location_id": cfg.location_id,
            "location_name": cfg.location_name,
            "count_hangar": cfg.count_hangar,
            "auto_import": cfg.auto_import,
            "buy_lot_size": cfg.buy_lot_size,
            "language": cfg.language,
            "has_client_id": bool(cfg.esi_client_id),
        },
    }
