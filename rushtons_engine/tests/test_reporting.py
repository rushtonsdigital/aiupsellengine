"""reporting.py builds the BI summary tables the Metabase/chat layer reads.
These pin the aggregation and (dialect-risky) JSON-flatten logic on SQLite."""

import datetime as dt
import os

import pytest
import sqlalchemy as sa

import db
import reporting
from conftest import add_customer, add_product, add_order

AS_OF = dt.date(2026, 6, 30)


def _rows(conn, table):
    return conn.execute(sa.select(table)).mappings().all()


def test_refresh_sales_category_metrics(conn):
    add_customer(conn, "C1")
    add_product(conn, "VEG-1", category="Vegetables")
    add_product(conn, "VEG-2", category="Vegetables")
    add_product(conn, "FRU-1", category="Fruits")
    # one order (shared order_number) with two Vegetable lines + one Fruit line
    n = add_order(conn, "C1", "VEG-1", AS_OF, qty=2.0)
    add_order(conn, "C1", "VEG-2", AS_OF, qty=3.0, order_number=n)
    # a second, separate Vegetable order a week earlier
    add_order(conn, "C1", "VEG-1", AS_OF - dt.timedelta(days=7), qty=1.0)
    add_order(conn, "C1", "FRU-1", AS_OF, qty=5.0, order_number=n)

    reporting.refresh_sales(conn, AS_OF)
    by_cat = {r["category"]: r for r in _rows(conn, db.customer_category_metrics)}

    veg = by_cat["Vegetables"]
    assert veg["line_count"] == 3            # VEG-1 x2 (two orders) + VEG-2 x1
    assert veg["order_count"] == 2           # two distinct order numbers
    assert float(veg["total_qty"]) == 6.0    # 2 + 3 + 1
    assert veg["first_bought"] == AS_OF - dt.timedelta(days=7)
    assert veg["last_bought"] == AS_OF

    fru = by_cat["Fruits"]
    assert fru["line_count"] == 1
    assert fru["order_count"] == 1


def test_refresh_sales_product_buyer_windows(conn):
    add_product(conn, "P1", category="Vegetables")
    for code in ("A", "B", "C", "D", "E"):
        add_customer(conn, code)
    add_order(conn, "A", "P1", AS_OF)                          # today
    add_order(conn, "B", "P1", AS_OF - dt.timedelta(days=14))  # 14d boundary (in)
    add_order(conn, "C", "P1", AS_OF - dt.timedelta(days=15))  # out of 14d
    add_order(conn, "D", "P1", AS_OF - dt.timedelta(days=90))  # 90d boundary (in)
    add_order(conn, "E", "P1", AS_OF - dt.timedelta(days=91))  # out of 90d

    reporting.refresh_sales(conn, AS_OF)
    p = _rows(conn, db.product_metrics)[0]

    assert p["distinct_buyers"] == 5
    assert p["buyers_14d"] == 2   # A, B
    assert p["buyers_90d"] == 4   # A, B, C, D
    assert p["first_sold"] == AS_OF - dt.timedelta(days=91)
    assert p["last_sold"] == AS_OF


def test_refresh_sales_ignores_non_invoiced(conn):
    add_customer(conn, "C1")
    add_product(conn, "P1", category="Vegetables")
    add_order(conn, "C1", "P1", AS_OF, state="Invoiced")
    add_order(conn, "C1", "P1", AS_OF, state="Accepted", order_number=91234567)

    reporting.refresh_sales(conn, AS_OF)
    assert _rows(conn, db.product_metrics)[0]["line_count"] == 1


def test_refresh_funnel_flattens_json(conn):
    d1 = AS_OF
    conn.execute(db.recommendations.insert(), [
        {"run_date": d1, "customer_code": "X", "rank": 1, "score": 5,
         "gap_categories": ["Mushroom", "Herbs", "Fruits"],
         "chosen_products": [
             {"code": "1", "name": "n", "category": "Mushroom", "why": "w"},
             {"code": "2", "name": "n", "category": "Tomatoes", "why": "w"}],
         "status": "converted", "created_at": db.now_utc()},
        {"run_date": d1, "customer_code": "Y", "rank": 2, "score": 4,
         "gap_categories": ["Salads"], "chosen_products": None,
         "status": "proposed", "created_at": db.now_utc()},
    ])

    reporting.refresh_funnel(conn)
    facts = {(r["customer_code"], r["category"]): r
             for r in _rows(conn, db.recommendation_category_facts)}

    assert facts[("X", "Mushroom")]["offered"] is True
    assert facts[("X", "Mushroom")]["chosen"] is True
    assert facts[("X", "Mushroom")]["rec_status"] == "converted"
    assert facts[("X", "Herbs")]["offered"] is True
    assert facts[("X", "Herbs")]["chosen"] is False
    # chosen but not offered (a category the drafter added outside the gaps)
    assert facts[("X", "Tomatoes")]["offered"] is False
    assert facts[("X", "Tomatoes")]["chosen"] is True
    # second recommendation, no chosen products
    assert facts[("Y", "Salads")]["offered"] is True
    assert facts[("Y", "Salads")]["chosen"] is False


@pytest.mark.skipif(not os.environ.get("REPORTING_TEST_DATABASE_URL"),
                    reason="set REPORTING_TEST_DATABASE_URL to a throwaway "
                           "Postgres to validate reporting.sql")
def test_reporting_views_apply_on_postgres():
    """Catch reporting.sql syntax / column drift against a real Postgres.
    The views are Postgres-only (skipped on SQLite in init_db), so this is the
    only place they get executed by CI. Uses a throwaway DB, never production."""
    engine = sa.create_engine(os.environ["REPORTING_TEST_DATABASE_URL"])
    db.init_db(engine)                    # creates tables + applies the views
    db.apply_reporting_views(engine)      # idempotent re-apply must also succeed
    insp = sa.inspect(engine)
    views = set(insp.get_view_names())
    for v in ("v_order_lines", "v_monthly_customer_sales", "v_category_penetration",
              "v_account_gaps", "v_customer_health", "v_lapsing_accounts",
              "v_recommendation_funnel", "v_product_performance"):
        assert v in views, v
    # every view must be selectable (validates column references resolve)
    with engine.connect() as conn:
        for v in views:
            if v.startswith("v_"):
                conn.exec_driver_sql(f"select * from {v} limit 1")
