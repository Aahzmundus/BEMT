"""The character's own open market orders."""

from __future__ import annotations

from . import client


def open_orders(character_id: int) -> list[dict]:
    """Currently open orders (single page; ESI does not page this endpoint).

    Each row carries `type_id`, `location_id`, `is_buy_order`, `price`,
    `volume_remain` (what is still on the market) and `volume_total` (how big
    the order was when it was placed) - that last one is what seeds a target.
    """
    return client.get_json(f"/characters/{character_id}/orders/",
                           character_id=character_id)
