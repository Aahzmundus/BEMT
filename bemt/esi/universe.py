"""Name resolution: locations, and type names in both directions.

Station names are public. A player structure (citadel) is NOT: it needs the
authed /universe/structures/ endpoint and the read_structures scope, and it
403s if the character has no docking access there. Every lookup here degrades
to a readable placeholder instead of raising - a name is cosmetic, and losing
one must never break a refresh.
"""

from __future__ import annotations

import logging

from . import client

log = logging.getLogger(__name__)

#: EVE id ranges. Player structures live above 1e12; NPC stations in the 60-64M
#: band. Anything else (a solar system id, say) is not a dockable location.
STRUCTURE_ID_MIN = 1_000_000_000_000
STATION_ID_MIN = 60_000_000
STATION_ID_MAX = 64_000_000

_NAMES_BATCH = 1000  # ESI caps the /universe/names/ body at 1000 ids


def location_name(location_id: int, character_id: int | None = None) -> str:
    """Best-effort human name for a station or structure."""
    try:
        if location_id >= STRUCTURE_ID_MIN:
            if character_id is None:
                return f"Structure {location_id}"
            data = client.get_json(f"/universe/structures/{location_id}/",
                                   character_id=character_id)
            return data.get("name") or f"Structure {location_id}"
        if STATION_ID_MIN <= location_id < STATION_ID_MAX:
            data = client.get_json(f"/universe/stations/{location_id}/")
            return data.get("name") or f"Station {location_id}"
    except Exception as e:  # no docking access, expired structure, ESI down
        log.info("could not resolve location %s: %s", location_id, e)
        return f"Structure {location_id}" if location_id >= STRUCTURE_ID_MIN \
            else f"Station {location_id}"
    return f"Location {location_id}"


def type_names(type_ids: list[int]) -> dict[int, str]:
    """type_id -> name for many ids at once (public endpoint)."""
    out: dict[int, str] = {}
    ids = sorted({int(t) for t in type_ids})
    for i in range(0, len(ids), _NAMES_BATCH):
        batch = ids[i:i + _NAMES_BATCH]
        try:
            rows = client.post_json("/universe/names/", json_body=batch)
        except Exception as e:
            log.info("name lookup failed for %d ids: %s", len(batch), e)
            continue
        for r in rows or []:
            if r.get("category") == "inventory_type":
                out[int(r["id"])] = r["name"]
    return out


def type_id_for_name(name: str) -> tuple[int, str] | None:
    """Resolve one exact item name to (type_id, canonical_name).

    /universe/ids/ is an exact, case-insensitive match - there is no public
    fuzzy type search in ESI, which is why the UI autocompletes from names it
    has already seen and only falls back to this for genuinely new items.
    """
    text = (name or "").strip()
    if not text:
        return None
    try:
        data = client.post_json("/universe/ids/", json_body=[text])
    except Exception as e:
        log.info("id lookup failed for %r: %s", text, e)
        return None
    for row in (data or {}).get("inventory_types") or []:
        return int(row["id"]), row["name"]
    return None
