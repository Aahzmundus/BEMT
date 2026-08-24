"""The restock model. Pure functions - no I/O, no database, no ESI.

The whole tool is one idea:

    buy = target - (still listed on the market) - (sitting in the hangar)

`target` is the par level: how many of an item Benji wants on the market when
the shelf is full. It is seeded automatically from the size of his own sell
orders and is his to adjust afterwards.

Why par levels and not "what sold since last time": a par level is a statement
about the desired state, so it is self-correcting. Skip a week, check from a
different PC, restore from a backup - the answer is still right, because it is
recomputed from what is on the market *now* rather than accumulated from a
history that could have a hole in it.
"""

from __future__ import annotations

from collections import Counter

#: Item hangar. Anything in a *different* flag (HiSlot0, Cargo, DroneBay, ...)
#: is fitted to or loaded into something, i.e. in use rather than for sale.
HANGAR_FLAG = "Hangar"


# --------------------------------------------------------------- market orders

def sell_order_totals(orders: list[dict],
                      location_id: int | None = None) -> dict[int, dict]:
    """Aggregate open SELL orders per item.

    Returns ``type_id -> {listed, listed_total, orders, price}`` where

    - ``listed`` is `volume_remain`: units still on the market right now,
    - ``listed_total`` is `volume_total`: how big the orders were when placed,
      which is the best available evidence of the stock level he intended,
    - ``price`` is the lowest of his own sell prices for that item.

    Buy orders are ignored: a buy order is him acquiring stock, not seeding it.
    Passing ``location_id`` restricts the aggregate to one market.
    """
    out: dict[int, dict] = {}
    for o in orders or []:
        if o.get("is_buy_order"):
            continue
        if location_id is not None and o.get("location_id") != location_id:
            continue
        tid = int(o["type_id"])
        row = out.setdefault(
            tid, {"listed": 0, "listed_total": 0, "orders": 0, "price": None})
        row["listed"] += int(o.get("volume_remain") or 0)
        row["listed_total"] += int(o.get("volume_total") or 0)
        row["orders"] += 1
        price = o.get("price")
        if price is not None and (row["price"] is None or price < row["price"]):
            row["price"] = float(price)
    return out


def order_locations(orders: list[dict]) -> list[dict]:
    """Markets the character has sell orders at, busiest first.

    Feeds the "which market are you seeding?" picker, so the location is chosen
    from real data instead of being hardcoded or typed in by hand.
    """
    per: dict[int, dict] = {}
    for o in orders or []:
        if o.get("is_buy_order"):
            continue
        loc = o.get("location_id")
        if loc is None:
            continue
        row = per.setdefault(int(loc), {"location_id": int(loc), "orders": 0,
                                        "types": set()})
        row["orders"] += 1
        row["types"].add(int(o["type_id"]))
    result = [{"location_id": r["location_id"], "orders": r["orders"],
               "types": len(r["types"])} for r in per.values()]
    result.sort(key=lambda r: (-r["orders"], r["location_id"]))
    return result


# ---------------------------------------------------------------------- assets

def hangar_stacks(assets: list[dict], location_id: int) -> dict[int, int]:
    """``type_id -> quantity`` of sellable stock in one station's item hangar.

    Walks into station containers (their contents are also flagged Hangar) and
    counts the contents rather than the container. Deliberately excluded:

    - anything fitted to or loaded inside a ship (its flag is a slot/bay, not
      Hangar), so a fitted ship's modules are never mistaken for stock;
    - the ship or container holding those items, because a rigged, loaded hull
      is in use - counting it as shelf stock would suppress a real re-buy.

    An empty, packaged hull sitting in the hangar has no children at all and so
    does count, which is right: that is exactly a hull ready to be listed.
    """
    children: dict[int, list[dict]] = {}
    for a in assets or []:
        children.setdefault(a.get("location_id"), []).append(a)

    counts: Counter[int] = Counter()
    visited: set[int] = set()

    def walk(item: dict) -> None:
        item_id = item.get("item_id")
        if item_id in visited:  # cycle guard against corrupt parent chains
            return
        visited.add(item_id)
        kids = children.get(item_id) or []
        inner = [k for k in kids if k.get("location_flag") == HANGAR_FLAG]
        if inner:  # a container: count what is in it, not the can
            for k in inner:
                walk(k)
            return
        if kids:  # fitted / loaded: in use, not stock
            return
        counts[int(item["type_id"])] += int(item.get("quantity") or 0)

    for a in children.get(location_id, []):
        if a.get("location_flag") == HANGAR_FLAG:
            walk(a)
    return dict(counts)


# ------------------------------------------------------------------- par level

def buy_qty(target: int, listed: int, hangar: int = 0, *,
            count_hangar: bool = True, lot_size: int = 0,
            threshold_pct: int = 100) -> int:
    """How many units to buy. Never negative - overstock is not a shopping item.

    ``threshold_pct`` is the reorder point: nothing is bought until stock falls
    *below* that percentage of the target, and once it does the buy tops back
    up to the full target (classic par restock). 100 means "buy on any
    deficit", which is the pre-0.1.1 behaviour. A sold-out item (0 in stock)
    always triggers.

    ``lot_size`` rounds up to a whole lot for anyone who restocks in fixed
    batches; 0 or 1 means no rounding.
    """
    have = int(listed) + (int(hangar) if count_hangar else 0)
    need = int(target) - have
    if need <= 0:
        return 0
    # Integer comparison of have/target < pct/100, exact - no float edges.
    if have * 100 >= int(target) * int(threshold_pct):
        return 0
    if lot_size and lot_size > 1:
        need = -(-need // lot_size) * lot_size  # ceil to the next whole lot
    return need


def row_status(target: int, listed: int, buy: int, active: bool) -> str:
    """Bucket for the UI. ``sold_out`` earns its own colour: nothing of that
    item is on the market at all, which is the case a seeder most wants to see.
    """
    if not active:
        return "paused"
    if target <= 0:
        return "untracked"
    if listed <= 0:
        return "sold_out"
    if buy > 0:
        return "low"
    return "ok"


def build_rows(items: list[dict], stock: dict[int, dict], *,
               count_hangar: bool = True, lot_size: int = 0,
               threshold_pct: int = 100) -> list[dict]:
    """Join the tracked items with what the last refresh observed.

    Sorted the way the list gets used: things to buy first (most urgent, i.e.
    fully sold out, at the top), then everything healthy, then paused items.
    """
    rows: list[dict] = []
    for it in items:
        tid = int(it["type_id"])
        obs = stock.get(tid) or {}
        listed = int(obs.get("listed_qty") or 0)
        hangar = int(obs.get("hangar_qty") or 0)
        target = int(it.get("target_qty") or 0)
        active = bool(it.get("active", 1))
        buy = buy_qty(target, listed, hangar, count_hangar=count_hangar,
                      lot_size=lot_size,
                      threshold_pct=threshold_pct) if active else 0
        rows.append({
            "type_id": tid,
            "character_id": it.get("character_id"),
            "name": it.get("name") or f"Type {tid}",
            "target_qty": target,
            "listed_qty": listed,
            "hangar_qty": hangar,
            "orders": int(obs.get("orders") or 0),
            "price": obs.get("price"),
            "buy_qty": buy,
            "active": active,
            "source": it.get("source") or "manual",
            "status": row_status(target, listed, buy, active),
        })

    order = {"sold_out": 0, "low": 1, "ok": 2, "untracked": 3, "paused": 4}
    rows.sort(key=lambda r: (order.get(r["status"], 9), r["name"].lower()))
    return rows


def totals(rows: list[dict]) -> dict:
    """Headline numbers for the top of the page and for each snapshot."""
    buying = [r for r in rows if r["buy_qty"] > 0]
    return {
        "tracked": sum(1 for r in rows if r["active"]),
        "paused": sum(1 for r in rows if not r["active"]),
        "buy_lines": len(buying),
        "buy_units": sum(r["buy_qty"] for r in buying),
        "sold_out": sum(1 for r in rows if r["status"] == "sold_out"),
        "listed_units": sum(r["listed_qty"] for r in rows),
        "hangar_units": sum(r["hangar_qty"] for r in rows),
    }


# ------------------------------------------------------------ multi-character

def merge_rows(row_lists: list[list[dict]]) -> list[dict]:
    """Fold several characters' rows into one shopping list.

    The same item tracked by two characters becomes one line whose quantities
    are summed - each character still needs their share bought, so the total
    to shop for is the sum. Paused rows keep their per-character meaning: a
    row is active in the merge if ANY character tracks it actively, and only
    active rows contribute quantities.
    """
    merged: dict[int, dict] = {}
    for rows in row_lists:
        for r in rows:
            m = merged.setdefault(int(r["type_id"]), {
                "type_id": int(r["type_id"]), "character_id": None,
                "name": r["name"], "target_qty": 0, "listed_qty": 0,
                "hangar_qty": 0, "orders": 0, "price": None, "buy_qty": 0,
                "active": False, "source": r.get("source") or "manual",
            })
            if not r["active"]:
                continue
            m["active"] = True
            m["name"] = r["name"]
            m["target_qty"] += r["target_qty"]
            m["listed_qty"] += r["listed_qty"]
            m["hangar_qty"] += r["hangar_qty"]
            m["orders"] += r["orders"]
            m["buy_qty"] += r["buy_qty"]
            if r["price"] is not None and (m["price"] is None
                                           or r["price"] < m["price"]):
                m["price"] = r["price"]

    out = list(merged.values())
    for m in out:
        m["status"] = row_status(m["target_qty"], m["listed_qty"],
                                 m["buy_qty"], m["active"])
    order = {"sold_out": 0, "low": 1, "ok": 2, "untracked": 3, "paused": 4}
    out.sort(key=lambda r: (order.get(r["status"], 9), r["name"].lower()))
    return out


# ---------------------------------------------------------------- auto-import

def plan_import(order_totals: dict[int, dict],
                known_type_ids: set[int]) -> dict[int, int]:
    """New items to start tracking, with a seeded target.

    The target seed is `volume_total` - the size the order was placed at, not
    what is left of it - so importing a half-sold order still recovers the full
    intended stock level.

    Only ever *adds*. An item already tracked keeps its target, because that
    target may have been typed in by hand and an automatic import must not
    silently overwrite a human decision.
    """
    return {tid: int(t.get("listed_total") or t.get("listed") or 0)
            for tid, t in order_totals.items()
            if tid not in known_type_ids}


# ------------------------------------------------------------------- multibuy

def multibuy_text(rows: list[dict]) -> str:
    """The in-game multibuy payload: one ``Name<TAB>quantity`` line per item.

    Tab-separated name/quantity is the format EVE's multibuy window parses (the
    same shape as a copied cargo scan). Only active rows with something to buy
    appear - pasting a zero would be noise.
    """
    lines = [f"{r['name']}\t{r['buy_qty']}"
             for r in rows if r["active"] and r["buy_qty"] > 0]
    return "\n".join(lines)
