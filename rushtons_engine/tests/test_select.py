"""select.py is the module the whole engine's credibility rests on:
deterministic, auditable, reproducible. These tests pin its behaviour."""

import datetime as dt

import sqlalchemy as sa

import classify
import config
import db
import selector as sel
from conftest import add_customer, add_product, add_order, weekly_orders

AS_OF = dt.date(2026, 6, 30)
START = AS_OF - dt.timedelta(days=28)


def _seed_catalogue(conn):
    add_product(conn, "VEG-1", category="Vegetables")
    add_product(conn, "VEG-2", category="Vegetables")
    add_product(conn, "FRU-1", category="Fruits")
    add_product(conn, "DAI-1", name="Cheese Burrata", category="Dairy and Chilled")
    add_product(conn, "DAI-2", name="Milk Whole", category="Dairy and Chilled")
    add_product(conn, "DAI-3", name="Yoghurt Greek", category="Dairy and Chilled",
                out_of_season=True)
    # delisted line: keeps its real category but must never be pitched
    add_product(conn, "DAI-4", name="Cream Sour (discontinued)",
                category="Dairy and Chilled", delisted=True)
    # The specialty line nobody has bought recently — the whole reason the pool
    # is sourced from the catalogue rather than from recent orders.
    add_product(conn, "DAI-9", name="Cheese Baron Bigod", category="Dairy and Chilled")
    # background buyers so suggestions have popularity signal:
    add_customer(conn, "BG1")
    add_customer(conn, "BG2")
    for c in ("BG1", "BG2"):
        add_order(conn, c, "DAI-1", AS_OF - dt.timedelta(days=3))
    add_order(conn, "BG1", "DAI-2", AS_OF - dt.timedelta(days=3))


def _classify_and_select(conn):
    classify.classify_all(conn, AS_OF)
    return sel.select_top(conn, run_date=AS_OF)


def test_customers_needing_review(conn):
    _seed_catalogue(conn)
    add_customer(conn, "TYPED", venue_type="Restaurants")       # known type
    weekly_orders(conn, "TYPED", ["VEG-1"], START, weeks=2)
    add_customer(conn, "UNTYPED", venue_type=None)              # unknown type
    weekly_orders(conn, "UNTYPED", ["VEG-1"], START, weeks=2)
    add_customer(conn, "NOORDERS", venue_type=None)            # never ordered
    add_customer(conn, "STAFF ACCOUNT", name="Staff Account",  # internal
                 venue_type=None)
    weekly_orders(conn, "STAFF ACCOUNT", ["VEG-1"], START, weeks=2)
    classify.classify_all(conn, AS_OF)
    review = {r["customer_code"] for r in sel.customers_needing_review(conn)}
    assert "UNTYPED" in review           # trades, no known type -> review
    assert "TYPED" not in review         # already typed
    assert "NOORDERS" not in review      # never traded
    assert "STAFF ACCOUNT" not in review  # internal / excluded


def test_narrow_engaged_account_is_selected_wide_account_is_not(conn):
    _seed_catalogue(conn)
    add_customer(conn, "NARROW")   # 2 SKUs, weekly cadence -> candidate
    weekly_orders(conn, "NARROW", ["VEG-1", "FRU-1"], START, weeks=5)
    add_customer(conn, "WIDE")     # >4 SKUs -> filtered
    weekly_orders(conn, "WIDE", ["VEG-1", "VEG-2", "FRU-1", "DAI-1", "DAI-2"],
                  START, weeks=5)
    results = _classify_and_select(conn)
    codes = [r["customer_code"] for r in results]
    assert "NARROW" in codes
    assert "WIDE" not in codes


def test_filters(conn):
    _seed_catalogue(conn)
    add_customer(conn, "OK")
    weekly_orders(conn, "OK", ["VEG-1"], START, weeks=5)
    add_customer(conn, "ONEOFF")           # a single order ever -> out
    add_order(conn, "ONEOFF", "VEG-1", AS_OF - dt.timedelta(days=2))
    add_customer(conn, "STALE")            # regular pattern, stopped 10d ago -> lapsed
    weekly_orders(conn, "STALE", ["VEG-1"], START - dt.timedelta(days=21), weeks=6)
    add_customer(conn, "INTERNAL", name="Cash Sales")
    weekly_orders(conn, "INTERNAL", ["VEG-1"], START, weeks=5)
    add_customer(conn, "FACTORY", venue_type="Manufacturing")
    weekly_orders(conn, "FACTORY", ["VEG-1"], START, weeks=5)

    codes = [r["customer_code"] for r in _classify_and_select(conn)]
    assert codes.count("OK") == 1
    for bad in ("ONEOFF", "STALE", "INTERNAL", "FACTORY"):
        assert bad not in codes


def test_cooldown_excludes_recently_recommended(conn):
    _seed_catalogue(conn)
    add_customer(conn, "REPEAT")
    weekly_orders(conn, "REPEAT", ["VEG-1"], START, weeks=5)
    conn.execute(db.recommendations.insert().values(
        run_date=AS_OF - dt.timedelta(weeks=2), customer_code="REPEAT",
        rank=1, status="sent", created_at=db.now_utc()))
    codes = [r["customer_code"] for r in _classify_and_select(conn)]
    assert "REPEAT" not in codes


def test_cooldown_does_not_block_same_day_rerun(conn):
    _seed_catalogue(conn)
    add_customer(conn, "TODAY")
    weekly_orders(conn, "TODAY", ["VEG-1"], START, weeks=5)
    first = _classify_and_select(conn)
    second = sel.select_top(conn, run_date=AS_OF)   # re-run, same run_date
    assert [r["customer_code"] for r in first] == \
           [r["customer_code"] for r in second]


def test_deterministic_and_tie_broken_by_code(conn):
    _seed_catalogue(conn)
    # identical twins except code: tie must fall to code asc
    for code in ("TWIN-B", "TWIN-A"):
        add_customer(conn, code)
        weekly_orders(conn, code, ["VEG-1"], START, weeks=5)
    r1 = _classify_and_select(conn)
    r2 = sel.select_top(conn, run_date=AS_OF)
    assert [x["customer_code"] for x in r1] == [x["customer_code"] for x in r2]
    twins = [x["customer_code"] for x in r1 if x["customer_code"].startswith("TWIN")]
    assert twins == ["TWIN-A", "TWIN-B"]


def test_golden_ranking(conn):
    """Frozen expected output for a fixed scenario. If a config weight changes,
    this test changing is the review signal."""
    _seed_catalogue(conn)
    # engaged restaurant, 5 orders/4wks, 1 SKU -> top
    add_customer(conn, "R-HOT", venue_type="Restaurants")
    weekly_orders(conn, "R-HOT", ["VEG-1"], START, weeks=5)
    # same cadence but pub (lower segment bonus)
    add_customer(conn, "P-MID", venue_type="Pubs")
    weekly_orders(conn, "P-MID", ["VEG-1"], START, weeks=5)
    # restaurant but only 2 orders (low engagement)
    add_customer(conn, "R-LOW", venue_type="Restaurants")
    weekly_orders(conn, "R-LOW", ["VEG-1"], AS_OF - dt.timedelta(days=8), weeks=2)

    results = _classify_and_select(conn)
    ranked = [r["customer_code"] for r in results if not r["customer_code"].startswith("BG")]
    # R-LOW outranks P-MID: at low cadences (both far below the 6/wk engagement
    # cap) the Restaurants-vs-Pubs segment bonus (1.0 vs 0.5) dominates the
    # small engagement difference. Real regulars order 3-6x/week, where
    # engagement (weight 3.0) dominates instead.
    assert ranked == ["R-HOT", "R-LOW", "P-MID"]
    assert [r["rank"] for r in results] == list(range(1, len(results) + 1))


def test_gaps_follow_segment_focus_and_pool_is_in_season(conn):
    _seed_catalogue(conn)
    add_customer(conn, "REST", venue_type="Restaurants")
    weekly_orders(conn, "REST", ["VEG-1"], START, weeks=5)
    results = _classify_and_select(conn)
    rec = next(r for r in results if r["customer_code"] == "REST")
    # Restaurants focus leads with Dairy and Chilled (meeting-PDF priority)
    assert rec["gap_categories"][0] == "Dairy and Chilled"
    assert len(rec["gap_categories"]) <= config.MAX_GAP_CATEGORIES_OFFERED
    dairy = rec["product_pool"]["Dairy and Chilled"]
    codes = [p["code"] for p in dairy]
    assert "DAI-1" in codes          # most-bought recent product leads
    assert "DAI-3" not in codes      # out_of_season never enters the pool
    assert "DAI-4" not in codes      # delisted never enters the pool
    assert len(dairy) <= config.POOL_PER_GAP
    assert codes == sorted(codes, key=lambda c: (-next(p["buyers_14d"] for p in dairy if p["code"] == c), c))
    # the drafter needs Fresho's own grouping to spot mislabelled categories
    assert all("product_group" in p for p in dairy)


def test_pool_surfaces_specialty_lines_nobody_bought_recently(conn):
    """The bug that produced the Baby Cucumber / Baby Corn pitch: a pool built
    from recent orders can only ever contain what's already selling, so the
    low-volume specialty lines Rushton's most wants to push are invisible."""
    _seed_catalogue(conn)
    add_customer(conn, "REST", venue_type="Restaurants")
    weekly_orders(conn, "REST", ["VEG-1"], START, weeks=5)
    rec = next(r for r in _classify_and_select(conn) if r["customer_code"] == "REST")

    dairy = rec["product_pool"]["Dairy and Chilled"]
    baron = next(p for p in dairy if p["code"] == "DAI-9")
    assert baron["buyers_14d"] == 0        # zero recent sales, still eligible
    assert baron["name"] == "Cheese Baron Bigod"


def test_pool_collapses_pack_formats_of_the_same_product(conn):
    """1613-EA / 1613-CS-8 / 1613-CS-12 are one beetroot in three pack sizes —
    the pitch is the product, not the pack."""
    _seed_catalogue(conn)
    add_product(conn, "5028-EA", name="Butter Unsalted", category="Dairy and Chilled")
    add_product(conn, "5028-CS-8", name="Butter Unsalted", category="Dairy and Chilled")
    add_customer(conn, "REST", venue_type="Restaurants")
    weekly_orders(conn, "REST", ["VEG-1"], START, weeks=5)
    rec = next(r for r in _classify_and_select(conn) if r["customer_code"] == "REST")

    butter = [p for p in rec["product_pool"]["Dairy and Chilled"]
              if p["name"] == "Butter Unsalted"]
    assert len(butter) == 1


def test_account_buying_one_pack_format_is_not_pitched_another(conn):
    """Buying 1613-CS-8 means you buy that beetroot — don't pitch 1613-EA."""
    _seed_catalogue(conn)
    add_product(conn, "5138-EA", name="Cream Double", category="Dairy and Chilled")
    add_product(conn, "5138-CS-8", name="Cream Double", category="Dairy and Chilled")
    add_customer(conn, "REST", venue_type="Restaurants")
    weekly_orders(conn, "REST", ["VEG-1"], START, weeks=5)
    add_order(conn, "REST", "5138-CS-8", AS_OF - dt.timedelta(days=3))
    rec = next(r for r in _classify_and_select(conn) if r["customer_code"] == "REST")

    codes = [p["code"] for p in rec["product_pool"].get("Dairy and Chilled", [])]
    assert "5138-EA" not in codes


def test_base_code_leaves_non_fresho_codes_alone():
    """Collapsing two genuinely different products would hide one from the pool."""
    assert sel.base_code("1613-EA") == "1613"
    assert sel.base_code("1613-CS-12") == "1613"
    assert sel.base_code("1613") == "1613"
    assert sel.base_code("DAI-1") == "DAI-1"      # not a Fresho sku — untouched


def test_recommendations_persisted_idempotently(conn):
    _seed_catalogue(conn)
    add_customer(conn, "SAVED")
    weekly_orders(conn, "SAVED", ["VEG-1"], START, weeks=5)
    _classify_and_select(conn)
    sel.select_top(conn, run_date=AS_OF)
    n = conn.execute(sa.select(sa.func.count()).select_from(db.recommendations)
                     .where(db.recommendations.c.run_date == AS_OF)).scalar()
    stored = conn.execute(sa.select(db.recommendations)).fetchall()
    assert n == len({r.customer_code for r in stored if r.run_date == AS_OF})

