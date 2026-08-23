"""The restock arithmetic. Pure functions, hand-written fixtures."""

from __future__ import annotations

from bemt import model

from .conftest import asset, sell_order

STATION = 60003760
OTHER = 60008494


# --------------------------------------------------------------- sell orders

def test_sell_orders_aggregate_per_type():
    orders = [
        sell_order(34, remain=400, total=1000, price=6.0, order_id=1),
        sell_order(34, remain=250, total=500, price=5.5, order_id=2),
        sell_order(35, remain=10, total=10, price=9.0),
    ]
    totals = model.sell_order_totals(orders, STATION)
    assert totals[34] == {"listed": 650, "listed_total": 1500, "orders": 2,
                          "price": 5.5}  # price is the lowest of his own
    assert totals[35]["orders"] == 1


def test_buy_orders_are_not_stock():
    """A buy order is him acquiring stock, not seeding it."""
    orders = [
        sell_order(34, 100, 100),
        {**sell_order(35, 999, 999), "is_buy_order": True},
    ]
    totals = model.sell_order_totals(orders, STATION)
    assert 35 not in totals


def test_orders_at_other_markets_are_excluded():
    orders = [sell_order(34, 100, 100), sell_order(34, 50, 50, location_id=OTHER)]
    assert model.sell_order_totals(orders, STATION)[34]["listed"] == 100
    assert model.sell_order_totals(orders, OTHER)[34]["listed"] == 50
    assert model.sell_order_totals(orders)[34]["listed"] == 150  # no filter


def test_order_locations_ranked_by_order_count():
    orders = [sell_order(34, 1, 1), sell_order(35, 1, 1),
              sell_order(36, 1, 1, location_id=OTHER)]
    locs = model.order_locations(orders)
    assert [loc["location_id"] for loc in locs] == [STATION, OTHER]
    assert locs[0] == {"location_id": STATION, "orders": 2, "types": 2}


# ------------------------------------------------------------- hangar stacks

def test_hangar_counts_loose_stacks_at_the_station_only():
    assets = [
        asset(1, 34, 500, STATION),
        asset(2, 35, 200, STATION),
        asset(3, 34, 900, OTHER),          # a different station
    ]
    assert model.hangar_stacks(assets, STATION) == {34: 500, 35: 200}


def test_hangar_walks_into_containers_and_ignores_the_can():
    """Contents of a station container are stock; the can itself is not."""
    assets = [
        asset(10, 3465, 1, STATION),        # a Large Standard Container
        asset(11, 34, 1000, 10),            # inside it
        asset(12, 35, 5, 10),
    ]
    assert model.hangar_stacks(assets, STATION) == {34: 1000, 35: 5}


def test_fitted_ship_and_its_modules_are_not_stock():
    """A rigged, loaded hull is in use. Counting it would suppress a re-buy."""
    assets = [
        asset(20, 621, 1, STATION),                       # an assembled ship
        asset(21, 2048, 1, 20, flag="LoSlot0"),           # fitted module
        asset(22, 34, 5000, 20, flag="Cargo"),            # cargo, not shelf stock
        asset(23, 34, 100, STATION),                      # loose in the hangar
    ]
    assert model.hangar_stacks(assets, STATION) == {34: 100}


def test_packaged_hull_in_the_hangar_does_count():
    assets = [asset(30, 621, 3, STATION)]
    assert model.hangar_stacks(assets, STATION) == {621: 3}


def test_hangar_ignores_non_hangar_flags_at_the_station():
    assets = [asset(40, 34, 100, STATION, flag="Deliveries")]
    assert model.hangar_stacks(assets, STATION) == {}


def test_hangar_survives_a_cyclic_parent_chain():
    """Corrupt data must not hang the refresh."""
    assets = [asset(50, 34, 1, 51), asset(51, 35, 1, 50)]
    model.hangar_stacks(assets, 50)  # no infinite recursion


# ------------------------------------------------------------------ par math

def test_buy_is_the_shortfall():
    assert model.buy_qty(target=100, listed=40, hangar=10) == 50


def test_overstock_never_becomes_a_negative_shopping_item():
    assert model.buy_qty(target=100, listed=120, hangar=50) == 0


def test_hangar_can_be_ignored():
    assert model.buy_qty(100, 40, 30, count_hangar=False) == 60


def test_lot_size_rounds_up_never_down():
    """Rounding a shortfall down would leave the shelf short."""
    assert model.buy_qty(100, 45, 0, lot_size=20) == 60   # 55 -> 60
    assert model.buy_qty(100, 40, 0, lot_size=20) == 60   # exact multiple
    assert model.buy_qty(100, 45, 0, lot_size=1) == 55    # 1 is a no-op
    assert model.buy_qty(100, 45, 0, lot_size=0) == 55


def test_sold_out_is_its_own_status():
    assert model.row_status(target=100, listed=0, buy=100, active=True) == "sold_out"
    assert model.row_status(100, 40, 60, True) == "low"
    assert model.row_status(100, 100, 0, True) == "ok"
    assert model.row_status(100, 0, 0, False) == "paused"


# ----------------------------------------------------------------- the table

def _items(*specs):
    return [{"type_id": t, "name": n, "target_qty": q, "active": a,
             "source": "import"} for t, n, q, a in specs]


def test_rows_join_items_with_observed_stock():
    items = _items((34, "Tritanium", 1000, 1))
    stock = {34: {"listed_qty": 400, "hangar_qty": 100, "orders": 1, "price": 6.0}}
    row = model.build_rows(items, stock)[0]
    assert row["buy_qty"] == 500
    assert row["status"] == "low"
    assert row["price"] == 6.0


def test_a_tracked_item_with_no_orders_left_reads_as_sold_out():
    """The case the whole tool exists for: the order is gone, re-buy the lot."""
    rows = model.build_rows(_items((34, "Tritanium", 1000, 1)), {})
    assert rows[0]["listed_qty"] == 0
    assert rows[0]["buy_qty"] == 1000
    assert rows[0]["status"] == "sold_out"


def test_paused_items_never_generate_a_buy():
    rows = model.build_rows(_items((34, "Tritanium", 1000, 0)), {})
    assert rows[0]["buy_qty"] == 0
    assert rows[0]["status"] == "paused"


def test_rows_sort_urgent_first_then_alphabetical():
    items = _items(
        (34, "Zydrine", 10, 1),      # ok
        (35, "Alpha", 10, 1),        # sold out
        (36, "Beta", 10, 1),         # sold out
        (37, "Gamma", 10, 0),        # paused
        (38, "Delta", 10, 1),        # low
    )
    stock = {34: {"listed_qty": 10}, 38: {"listed_qty": 4}}
    names = [r["name"] for r in model.build_rows(items, stock)]
    assert names == ["Alpha", "Beta", "Delta", "Zydrine", "Gamma"]


def test_totals_summarise_the_page():
    items = _items((34, "A", 100, 1), (35, "B", 50, 1), (36, "C", 10, 0))
    stock = {34: {"listed_qty": 100}, 35: {"listed_qty": 10, "hangar_qty": 5}}
    t = model.totals(model.build_rows(items, stock))
    assert t["tracked"] == 2 and t["paused"] == 1
    assert t["buy_lines"] == 1 and t["buy_units"] == 35
    assert t["listed_units"] == 110 and t["hangar_units"] == 5


# ----------------------------------------------------------------- importing

def test_import_seeds_the_target_from_the_original_order_size():
    """volume_total, not volume_remain: a half-sold order still recovers the
    full stock level he originally listed."""
    totals = model.sell_order_totals([sell_order(34, remain=250, total=1000)])
    assert model.plan_import(totals, known_type_ids=set()) == {34: 1000}


def test_import_never_touches_an_item_already_tracked():
    """A target may have been typed by hand; an import must not overwrite it."""
    totals = model.sell_order_totals([sell_order(34, 250, 1000),
                                      sell_order(35, 10, 10)])
    assert model.plan_import(totals, known_type_ids={34}) == {35: 10}


# ------------------------------------------------------------------ multibuy

def test_multibuy_is_tab_separated_name_and_quantity():
    rows = model.build_rows(_items((34, "Tritanium", 1000, 1),
                                   (35, "Pyerite", 500, 1)),
                            {35: {"listed_qty": 500}})
    assert model.multibuy_text(rows) == "Tritanium\t1000"


def test_multibuy_skips_paused_and_fully_stocked_items():
    rows = model.build_rows(_items((34, "A", 10, 0), (35, "B", 10, 1)),
                            {35: {"listed_qty": 10}})
    assert model.multibuy_text(rows) == ""
