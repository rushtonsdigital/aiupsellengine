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
    # background buyers so suggestions have popularity signal:
    add_customer(conn, "BG1")
    add_customer(conn, "BG2")
    for c in ("BG1", "BG2"):
        add_order(conn, c, "DAI-1", AS_OF - dt.timedelta(days=3))
    add_order(conn, "BG1", "DAI-2", AS_OF - dt.timedelta(days=3))


def _classify_and_select(conn):
    classify.classify_all(conn, AS_OF)
    return sel.select_top(conn, run_date=AS_OF)


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


def test_gaps_follow_segment_focus_and_suggestions_are_in_season(conn):
    _seed_catalogue(conn)
    add_customer(conn, "REST", venue_type="Restaurants")
    weekly_orders(conn, "REST", ["VEG-1"], START, weeks=5)
    results = _classify_and_select(conn)
    rec = next(r for r in results if r["customer_code"] == "REST")
    # Restaurants focus leads with Dairy and Chilled (meeting-PDF priority)
    assert rec["gap_categories"][0] == "Dairy and Chilled"
    assert len(rec["gap_categories"]) <= config.MAX_GAPS_PER_ACCOUNT
    dairy = rec["suggested_products"]["Dairy and Chilled"]
    codes = [p["code"] for p in dairy]
    assert "DAI-1" in codes          # most-bought recent product leads
    assert "DAI-3" not in codes      # out_of_season never suggested
    assert codes == sorted(codes, key=lambda c: (-next(p["buyers_14d"] for p in dairy if p["code"] == c), c))


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

