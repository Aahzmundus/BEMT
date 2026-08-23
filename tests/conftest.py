"""Test fixtures: a throwaway data dir per test, so nothing touches a real install."""

from __future__ import annotations

import pytest

from bemt import config, db, paths


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the DB and config at a temp dir and start from an empty schema.

    Note which name gets patched: `config.py` does `from .paths import
    CONFIG_PATH`, so the binding that matters lives on the CONSUMING module
    (`bemt.config.CONFIG_PATH`), not on `bemt.paths`. Patching only the source
    module would leave the real config.json in play.
    """
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path)
    monkeypatch.setattr(paths, "DB_PATH", tmp_path / "bemt.db")
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(config, "BACKUP_PATH", tmp_path / "config.json.bak")

    db.reset_connection()
    config.reset_cache()
    db.init()
    yield tmp_path
    db.reset_connection()
    config.reset_cache()


def sell_order(type_id, remain, total, *, location_id=60003760, price=100.0,
               order_id=None):
    return {
        "order_id": order_id or type_id * 10,
        "type_id": type_id,
        "location_id": location_id,
        "is_buy_order": False,
        "price": price,
        "volume_remain": remain,
        "volume_total": total,
    }


def asset(item_id, type_id, quantity, location_id, flag="Hangar"):
    return {
        "item_id": item_id,
        "type_id": type_id,
        "quantity": quantity,
        "location_id": location_id,
        "location_flag": flag,
    }
