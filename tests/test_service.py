"""The I/O shell: refresh, import, item edits, snapshots. ESI is faked."""

from __future__ import annotations

import pytest

from bemt import config, db, service

from .conftest import asset, sell_order

STATION = 60003760
OTHER = 60008494
NAMES = {34: "Tritanium", 35: "Pyerite", 36: "Mexallon"}


@pytest.fixture
def esi(monkeypatch):
    """Stand in for every ESI call the service makes.

    Patched on the module objects `service` holds, which is the binding that
    actually gets called.
    """
    fake = {"orders": [], "assets": []}

    monkeypatch.setattr(service.esi_orders, "open_orders",
                        lambda cid: fake["orders"])
    monkeypatch.setattr(service.esi_assets, "fetch_assets",
                        lambda cid: fake["assets"])
    monkeypatch.setattr(service.universe, "type_names",
                        lambda ids: {t: NAMES.get(t, f"Type {t}") for t in ids})
    monkeypatch.setattr(service.universe, "location_name",
                        lambda loc, cid=None: f"Station {loc}")
    monkeypatch.setattr(service.sso, "missing_scopes", lambda cid: [])
    return fake


@pytest.fixture
def logged_in(env, esi):
    config.update(esi_client_id="test-client", character_id=90000001,
                  character_name="Benji")
    db.conn().execute(
        "INSERT INTO characters(character_id, name, refresh_token, scopes) "
        "VALUES(90000001,'Benji','rt','')")
    db.conn().commit()
    return esi


# ----------------------------------------------------------------- setup gaps

def test_refresh_without_a_login_asks_for_one(env, esi):
    config.update(esi_client_id="test-client")
    with pytest.raises(service.SetupNeeded) as exc:
        service.refresh()
    assert exc.value.reason == "no_character"


def test_one_market_is_chosen_automatically(logged_in):
    logged_in["orders"] = [sell_order(34, 100, 200)]
    service.refresh()
    assert config.load().location_id == STATION


def test_several_markets_are_never_guessed(logged_in):
    """Picking one would silently produce a wrong list for the other."""
    logged_in["orders"] = [sell_order(34, 100, 200),
                           sell_order(35, 5, 5, location_id=OTHER)]
    with pytest.raises(service.SetupNeeded) as exc:
        service.refresh()
    assert exc.value.reason == "choose_location"
    assert len(exc.value.extra["locations"]) == 2
    assert config.load().location_id is None


def test_no_orders_at_all_is_reported_not_crashed(logged_in):
    with pytest.raises(service.SetupNeeded) as exc:
        service.refresh()
    assert exc.value.reason == "no_orders"


# -------------------------------------------------------------------- refresh

def test_refresh_imports_orders_and_computes_the_buy_list(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000), sell_order(35, 40, 40)]
    summary = service.refresh()

    assert summary["imported"] == 2
    rows = {r["name"]: r for r in service.current_rows()}
    assert rows["Tritanium"]["target_qty"] == 1000   # seeded from volume_total
    assert rows["Tritanium"]["listed_qty"] == 250
    assert rows["Tritanium"]["buy_qty"] == 750
    assert rows["Pyerite"]["buy_qty"] == 0           # full order still up


def test_hangar_stock_reduces_what_must_be_bought(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    logged_in["assets"] = [asset(1, 34, 300, STATION)]
    service.refresh()
    row = service.current_rows()[0]
    assert row["hangar_qty"] == 300
    assert row["buy_qty"] == 450


def test_hangar_can_be_switched_off(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    logged_in["assets"] = [asset(1, 34, 300, STATION)]
    config.update(count_hangar=False)
    service.refresh()
    assert service.current_rows()[0]["buy_qty"] == 750


def test_a_failed_hangar_read_degrades_instead_of_failing_the_refresh(
        logged_in, monkeypatch):
    """Over-buying slightly beats no list at all - but the page is told."""
    logged_in["orders"] = [sell_order(34, 250, 1000)]

    def boom(cid):
        raise RuntimeError("ESI 500")
    monkeypatch.setattr(service.esi_assets, "fetch_assets", boom)

    summary = service.refresh()
    assert summary["hangar_error"]
    assert service.current_rows()[0]["buy_qty"] == 750


def test_a_sold_out_order_reads_as_zero_not_as_last_weeks_number(logged_in):
    """An emptied state is real data. Keeping the old number would hide the
    single most important case: the order is gone, re-buy the whole lot."""
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    assert service.current_rows()[0]["listed_qty"] == 250

    logged_in["orders"] = []           # it all sold
    config.update(location_id=STATION)  # market already chosen
    service.refresh()
    row = service.current_rows()[0]
    assert row["listed_qty"] == 0
    assert row["buy_qty"] == 1000
    assert row["status"] == "sold_out"


def test_a_hand_edited_target_survives_later_imports(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    service.update_item(34, target_qty=5000)

    summary = service.refresh()
    assert summary["imported"] == 0
    assert service.current_rows()[0]["target_qty"] == 5000


def test_auto_import_can_be_switched_off(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    config.update(auto_import=False, location_id=STATION)
    service.refresh()
    assert service.current_rows() == []


def test_orders_at_other_markets_do_not_leak_in(logged_in):
    logged_in["orders"] = [sell_order(34, 100, 100),
                           sell_order(35, 7, 7, location_id=OTHER)]
    config.update(location_id=STATION)
    service.refresh()
    assert [r["type_id"] for r in service.current_rows()] == [34]


# ---------------------------------------------------------------- item edits

def test_add_item_by_known_name(logged_in):
    service.remember_names({36: "Mexallon"})
    service.add_item(name="mexallon", target_qty=250)   # case-insensitive
    row = service.current_rows()[0]
    assert row["type_id"] == 36 and row["target_qty"] == 250
    assert row["buy_qty"] == 250


def test_add_item_falls_back_to_an_esi_name_lookup(logged_in, monkeypatch):
    monkeypatch.setattr(service.universe, "type_id_for_name",
                        lambda n: (35, "Pyerite"))
    service.add_item(name="pyerite", target_qty=10)
    assert service.current_rows()[0]["name"] == "Pyerite"


def test_add_item_with_an_unknown_name_explains_itself(logged_in, monkeypatch):
    monkeypatch.setattr(service.universe, "type_id_for_name", lambda n: None)
    with pytest.raises(ValueError, match="No item called"):
        service.add_item(name="Tritaniumm")


def test_re_adding_an_item_unpauses_it_rather_than_erroring(logged_in):
    service.remember_names({36: "Mexallon"})
    service.add_item(name="Mexallon", target_qty=100)
    service.update_item(36, active=False)
    service.add_item(name="Mexallon")
    assert service.current_rows()[0]["active"] is True


def test_removing_an_item_drops_its_stock_row_too(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    service.remove_item(34)
    assert service.current_rows() == []
    assert service.stock_all() == {}


def test_a_removed_item_comes_back_on_the_next_import(logged_in):
    """It is still on his market, so it is still part of the shelf."""
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    service.remove_item(34)
    assert service.refresh()["imported"] == 1


# ----------------------------------------------------------------- snapshots

def test_every_refresh_records_a_snapshot_with_its_lines(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000), sell_order(35, 40, 40)]
    service.refresh()

    snaps = service.history()
    assert len(snaps) == 1
    assert snaps[0]["buy_lines"] == 1 and snaps[0]["buy_units"] == 750
    lines = db.conn().execute(
        "SELECT * FROM snapshot_lines WHERE snapshot_id=?",
        (snaps[0]["id"],)).fetchall()
    assert len(lines) == 2


def test_a_snapshot_freezes_the_setting_it_was_computed_under(logged_in):
    """A later settings change must not rewrite what an old list meant."""
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    config.update(count_hangar=False)
    service.refresh()
    assert db.conn().execute(
        "SELECT count_hangar FROM snapshots").fetchone()["count_hangar"] == 0


def test_history_accumulates_in_time_order(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    service.refresh()
    snaps = service.history()
    assert [s["id"] for s in snaps] == sorted(s["id"] for s in snaps)


# --------------------------------------------------------------- page state

def test_page_state_is_idle_before_the_first_refresh(env, esi):
    """No data means "never checked", never a clean bill of health."""
    state = service.page_state()
    assert state["last_refresh"] is None
    assert state["rows"] == []
    assert state["multibuy"] == ""


def test_page_state_carries_the_multibuy_payload(logged_in):
    logged_in["orders"] = [sell_order(34, 250, 1000)]
    service.refresh()
    assert service.page_state()["multibuy"] == "Tritanium\t750"
