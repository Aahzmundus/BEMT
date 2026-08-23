"""Character assets, used to count stock already sitting in the station hangar."""

from __future__ import annotations

from . import client


def fetch_assets(character_id: int) -> list[dict]:
    return client.get_paged(f"/characters/{character_id}/assets/",
                            character_id=character_id)
