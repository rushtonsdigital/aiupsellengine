"""Sales report (hidden #reporting section): period arithmetic, the any-range
GP export store, and the week/month comparisons by customer and manager."""

import datetime as dt

import pytest
import sqlalchemy as sa

import db
import ingest
import query
import sales_report
from conftest import add_customer
from test_values import _export

D = dt.date


def test_week_is_last_complete_monday_to_sunday():
    assert sales_report.week_bounds(D(2026, 11, 9)) == (D(2026, 11, 2), D(2026, 11, 8))
    assert sales_report.week_bounds(D(2026, 11, 11)) == (D(2026, 11, 2), D(2026, 11, 8))
    assert sales_report.week_bounds(D(2026, 11, 8)) == (D(2026, 10, 26), D(2026, 11, 1))


def test_periods_line_up_weekdays_and_dates():
    p = sales_report.periods(D(2026, 11, 9))
    assert p["w"]["prev"] == (D(2026, 10, 26), D(2026, 11, 1))
    ly = p["w"]["ly"]
    assert ly == (D(2025, 11, 3), D(2025, 11, 9)) and ly[0].weekday() == 0
    assert p["mtd"]["cur"] == (D(2026, 11, 1), D(2026, 11, 8))
    assert p["mtd"]["prev"] == (D(2026, 10, 1), D(2026, 10, 8))
    assert p["mtd"]["ly"] == (D(2025, 11, 1), D(2025, 11, 8))
    assert p["m"]["cur"] == (D(2026, 10, 1), D(2026, 10, 31))
    assert p["m"]["prev"] == (D(2026, 9, 1), D(2026, 9, 30))
    assert p["m"]["ly"] == (D(2025, 10, 1), D(2025, 10, 31))
    # month to date clipped to a shorter previous month (31 Mar -> 28 Feb)
    p = sales_report.periods(D(2026, 4, 6))       # week ends Sun 5 Apr
    assert p["mtd"]["prev"] == (D(2026, 3, 1), D(2026, 3, 5))
    assert sales_report._prev_month_same_days(D(2026, 3, 1), D(2026, 3, 31)) == \
        (D(2026, 2, 1), D(2026, 2, 28))


def test_delivery_days_skip_sundays():
    assert sales_report.delivery_days(D(2026, 11, 1), D(2026, 11, 8)) == 6
    assert sales_report.delivery_days(D(2025, 11, 1), D(2025, 11, 8)) == 7


def test_any_range_export_lands_in_period_table_only(conn, tmp_path):
    week = _export(tmp_path, "2026-11-02", "2026-11-08", [("R A", "A", 500, 0, 300)])
    mtd = _export(tmp_path, "2026-11-01", "2026-11-08", [("R A", "A", 600, 0, 360)])
    assert ingest.ingest_values_files(conn, [week, mtd]) == 1   # only the MTD is a month
    periods = {(r.period_start, r.period_end): float(r.net_sales)
               for r in conn.execute(sa.select(db.customer_period_values))}
    assert periods == {(D(2026, 11, 2), D(2026, 11, 8)): 500,
                       (D(2026, 11, 1), D(2026, 11, 8)): 600}
    assert conn.execute(sa.select(db.customer_month_values.c.net_sales)).scalar() == 600
    ingest.ingest_values_files(conn, [week])                     # idempotent per range
    assert conn.execute(sa.select(sa.func.count())
                        .select_from(db.customer_period_values)).scalar() == 2


@pytest.fixture()
def on_conn(conn, monkeypatch):
    """Point the read-only query layer at the test database."""
    def run_sql(sql, limit=1000):
        res = conn.exec_driver_sql(sql)
        cols = list(res.keys())
        rows = [dict(zip(cols, r)) for r in res.fetchall()]
        return {"columns": cols, "rows": rows[:limit],
                "truncated": len(rows) > limit, "rowcount": len(rows[:limit])}
    monkeypatch.setattr(query, "run_sql", run_sql)
    return conn


def test_build_compares_by_customer_and_current_manager(on_conn, tmp_path):
    conn = on_conn
    add_customer(conn, "R UP", "Up", sales_rep="Harry (Rushtons)")
    add_customer(conn, "R NEW", "Newbie", sales_rep="Harry (Rushtons)")
    add_customer(conn, "R GONE", "Gone", sales_rep="Rob (Rushtons)")
    files = [
        _export(tmp_path, "2026-11-02", "2026-11-08",
                [("R UP", "Up", 1500, 0, 900), ("R NEW", "Newbie", 200, 0, 100)]),
        _export(tmp_path, "2026-10-26", "2026-11-01",
                [("R UP", "Up", 1000, 0, 600), ("R GONE", "Gone", 400, 0, 200)]),
        _export(tmp_path, "2025-11-03", "2025-11-09", [("R UP", "Up", 800, 0, 500)]),
        _export(tmp_path, "2026-10-01", "2026-10-31", [("R UP", "Up", 4000, 0, 2400)]),
        _export(tmp_path, "2026-09-01", "2026-09-30", [("R UP", "Up", 3000, 0, 1800)]),
    ]
    ingest.ingest_values_files(conn, files)
    rep = sales_report.build(D(2026, 11, 9))
    rows = {r["c"]: r for r in rep["rows"]}
    assert set(rows) == {"R UP", "R NEW", "R GONE"}
    assert rows["R UP"]["p"]["w"][:3] == [1500, 1000, 800]
    assert rows["R UP"]["p"]["w"][3] == 600                    # cash margin this week
    assert rows["R NEW"]["p"]["w"][:3] == [200, 0, 0]          # loaded period, no sales = 0
    assert rows["R GONE"]["r"] == "Rob (Rushtons)"
    assert rows["R UP"]["p"]["m"][:2] == [4000, 3000]
    assert rows["R UP"]["p"]["m"][2] is None                   # Oct 2025 not loaded
    assert rows["R UP"]["p"]["mtd"][0] is None                 # MTD export not loaded
    w = rep["periods"]["w"]
    assert w["cur"]["have"] and w["ly"]["have"] and not w["ly"]["partial"]
    assert rep["trend"]["cur"][-1] == 1700 and rep["trend"]["ly"][-1] == 800
    assert rep["trend"]["byRep"]["Harry (Rushtons)"]["cur"][-1] == 1700
    assert ["2026-11-01", "2026-11-08"] in rep["missing"]


def test_needed_exports_lists_only_what_is_missing(on_conn, tmp_path):
    conn = on_conn
    ingest.ingest_values_files(conn, [_export(tmp_path, "2026-11-02", "2026-11-08",
                                              [("R A", "A", 1, 0, 1)])])
    need = sales_report.needed_exports(D(2026, 11, 9))
    assert (D(2026, 11, 2), D(2026, 11, 8)) not in need
    assert (D(2025, 11, 3), D(2025, 11, 9)) in need
    assert (D(2026, 11, 1), D(2026, 11, 8)) in need
    assert all(b >= D(2025, 10, 1) for _, b in need)           # nothing before Fresho
