"""HTTP layer: the endpoints the page actually calls."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bemt import app as app_module
from bemt import config, db, service

from .conftest import sell_order

STATION = 60003760
OTHER = 60008494
CID = 90000001


@pytest.fixture
def client(env, monkeypatch):
    fake = {"orders": {}, "assets": {}}
    monkeypatch.setattr(service.esi_orders, "open_orders",
                        lambda cid: fake["orders"].get(cid, []))
    monkeypatch.setattr(service.esi_assets, "fetch_assets",
                        lambda cid: fake["assets"].get(cid, []))
    monkeypatch.setattr(service.universe, "type_names",
                        lambda ids: {t: {34: "Tritanium"}.get(t, f"Type {t}")
                                     for t in ids})
    monkeypatch.setattr(service.universe, "location_name",
                        lambda loc, cid=None: f"Station {loc}")
    monkeypatch.setattr(service.sso, "missing_scopes", lambda cid: [])

    config.update(esi_client_id="test-client", restock_threshold_pct=100)
    db.conn().execute(
        "INSERT INTO characters(character_id, name, refresh_token, scopes) "
        "VALUES(?,'Benji','rt','')", (CID,))
    db.conn().commit()

    with TestClient(app_module.app) as c:
        c.fake = fake
        yield c


def test_the_page_loads(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "BEMT" in resp.text


def test_static_files_are_served(client):
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/i18n.js").status_code == 200


def test_static_path_traversal_is_refused(client):
    assert client.get("/static/..%2f..%2fconfig.json").status_code == 404


def test_state_endpoint_reports_never_refreshed(client):
    body = client.get("/api/state").json()
    assert body["last_refresh"] is None
    assert body["rows"] == []
    assert body["by_character"][0]["name"] == "Benji"


def test_refresh_then_multibuy(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    body = client.post("/api/refresh").json()
    assert body["summary"]["buy_units"] == 750
    assert client.get("/api/multibuy").text == "Tritanium\t750"


def test_a_setup_gap_is_a_409_not_a_500(client):
    """The page turns this into a question, so it must be distinguishable."""
    client.fake["orders"][CID] = [sell_order(34, 1, 1),
                                  sell_order(35, 1, 1, location_id=OTHER)]
    resp = client.post("/api/refresh")
    assert resp.status_code == 409
    body = resp.json()
    assert body["reason"] == "choose_location"
    assert body["character_id"] == CID


def test_choosing_a_market_sticks(client):
    resp = client.post(f"/api/characters/{CID}/location",
                       json={"location_id": STATION})
    assert resp.status_code == 200
    ch = resp.json()["by_character"][0]
    assert ch["location_id"] == STATION
    assert ch["location_name"] == f"Station {STATION}"


def test_editing_a_target_returns_the_recomputed_page(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    client.post("/api/refresh")
    body = client.patch(f"/api/items/{CID}/34",
                        json={"target_qty": 2000}).json()
    assert body["rows"][0]["buy_qty"] == 1750


def test_pausing_an_item_removes_it_from_the_multibuy(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    client.post("/api/refresh")
    body = client.patch(f"/api/items/{CID}/34", json={"active": False}).json()
    assert body["multibuy"] == ""


def test_deleting_an_item(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    client.post("/api/refresh")
    assert client.delete(f"/api/items/{CID}/34").json()["rows"] == []


def test_adding_an_unknown_item_is_a_400_with_a_readable_message(
        client, monkeypatch):
    monkeypatch.setattr(service.universe, "type_id_for_name", lambda n: None)
    resp = client.post("/api/items", json={"name": "Nonsenseium"})
    assert resp.status_code == 400
    assert "No item called" in resp.json()["detail"]


def test_suggestions_come_from_names_already_seen(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    client.post("/api/refresh")
    matches = client.get("/api/suggest?q=trit").json()["matches"]
    assert matches[0]["name"] == "Tritanium"


def test_history_endpoint(client):
    client.fake["orders"][CID] = [sell_order(34, 250, 1000)]
    client.post("/api/refresh")
    assert len(client.get("/api/history").json()["snapshots"]) == 1


def test_threshold_setting_is_clamped_to_a_sane_range(client):
    body = client.post("/api/settings",
                       json={"restock_threshold_pct": 0}).json()
    assert body["settings"]["restock_threshold_pct"] == 1
    body = client.post("/api/settings",
                       json={"restock_threshold_pct": 250}).json()
    assert body["settings"]["restock_threshold_pct"] == 100


def test_merge_setting_round_trips(client):
    body = client.post("/api/settings",
                       json={"merge_characters": False}).json()
    assert body["settings"]["merge_characters"] is False


def test_removing_a_character_logs_them_out(client):
    body = client.delete(f"/api/characters/{CID}").json()
    assert body["by_character"] == []
    assert db.conn().execute("SELECT COUNT(*) AS n FROM characters"
                             ).fetchone()["n"] == 0


def test_update_endpoint_never_breaks_the_page(client, monkeypatch):
    """GitHub being unreachable must read as "no update", not as an error."""
    def boom(*a, **k):
        raise RuntimeError("offline")
    monkeypatch.setattr(app_module.update.httpx, "get", boom)
    body = client.get("/api/update").json()
    assert body["update_available"] is False
    assert body["current"]
