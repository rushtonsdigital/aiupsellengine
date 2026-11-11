"""Fresho's GP-margin export for any date range (customer_period_values) and
the daily Sales Line Items load behind the sales explorer (sales_daily)."""

import datetime as dt

import pytest
import sqlalchemy as sa

import db
import ingest
import query
import sales_explorer
from conftest import add_customer, add_product
from test_values import _export

D = dt.date


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


LI_HEADER = ("Product Group,Product Code,Product Name,Qty Type,Quantity,Customer Notes,"
             "Supplier Notes,Product Status,Delivery Run,Customer Name,Customer Code,"
             "Delivery Address,Order Number,Delivery Date,Order State,Additional Notes,"
             "Purchase Order Number,Unit Price,Cost Price,Discount,Discounted Line Total,"
             "Tax (on line total),Rebate %,Submitted At,Placed By,Locked At,Picker,Checker\n")


def _line_items(tmp_path, start, end, lines):
    """lines: (group, product, qty, customer, day, state, cost, total, rebate%)"""
    p = tmp_path / f"rushton_s_sales_line_items_{start}_to_{end}.csv"
    body = "".join(
        f'"{g}",\'{prod}\',Name,Each,{q},,,supplied,ROUTE 1,Cust,\'{c}\',"1 St, London",'
        f'F1,{day},{st},,,0,{cost},0.00,{tot},0.00,{reb},,,,,\n'
        for g, prod, q, c, day, st, cost, tot, reb in lines)
    p.write_text(LI_HEADER + body, encoding="utf-8")
    return p


def test_line_items_aggregate_by_customer_day_category(conn, tmp_path):
    add_product(conn, "9-EA", category="Herbs", raw_group="S100. Herbs - SPLIT")
    f = _line_items(tmp_path, "2026-11-02", "2026-11-03", [
        ("S010. Vegetables - SPLIT", "1-EA", 2, "R A", "2026-11-02", "invoiced", 1.5, 5.0, 5.0),
        ("B010. Vegetables - BULK", "2-KG", 1, "R A", "2026-11-02", "invoiced", 2.0, 3.0, 5.0),
        ("Z999. Delisted", "9-EA", 1, "R A", "2026-11-02", "invoiced", 1.0, 4.0, 0),
        ("S050. Fruits - SPLIT", "3-EA", 1, "R B", "2026-11-03", "cancelled", 1, 9, 0),
    ])
    assert ingest.ingest_line_items_file(conn, f) == 2
    rows = {r.category: r for r in conn.execute(sa.select(db.sales_daily))}
    veg = rows["Vegetables"]
    assert float(veg.net_sales) == 8.0 and float(veg.cost) == 5.0 and veg.lines == 2
    assert float(veg.rebate) == pytest.approx(0.4)
    assert "Herbs" in rows                          # delisted -> real category
    ingest.ingest_line_items_file(conn, f)          # idempotent per day range
    assert conn.execute(sa.select(sa.func.count())
                        .select_from(db.sales_daily)).scalar() == 2


def test_unknown_product_group_fails_loudly(conn, tmp_path):
    f = _line_items(tmp_path, "2026-11-02", "2026-11-02", [
        ("X999. Mystery", "5-EA", 1, "R A", "2026-11-02", "invoiced", 1, 2, 0)])
    with pytest.raises(ValueError):
        ingest.ingest_line_items_file(conn, f)


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


def test_explorer_build_compact_tables(on_conn, tmp_path):
    conn = on_conn
    add_customer(conn, "R A", "Alpha", sales_rep="Harry (Rushtons)")
    f = _line_items(tmp_path, "2026-11-02", "2026-11-04", [
        ("S010. Vegetables - SPLIT", "1-EA", 1, "R A", "2026-11-02", "invoiced", 6, 10, 0),
        ("S050. Fruits - SPLIT", "3-EA", 1, "R A", "2026-11-04", "invoiced", 1, 4, 0),
        ("S050. Fruits - SPLIT", "3-EA", 1, "R Z", "2026-11-04", "invoiced", 1, 2, 0),
    ])
    ingest.ingest_line_items_file(conn, f)
    x = sales_explorer.build()
    assert x["first"] == "2026-11-02" and x["days"] == 3
    names = {c[0]: c for c in x["customers"]}
    assert names["R A"][1] == "Alpha" and x["reps"][names["R A"][2]] == "Harry (Rushtons)"
    assert x["reps"][names["R Z"][2]] == sales_explorer.NO_REP     # not in customers
    a = [r for r in x["cd"] if x["customers"][r[1]][0] == "R A"]
    assert [r[0] for r in a] == [0, 2] and a[0][2] == 10 and a[0][3] == 4
    fruit = [r for r in x["kd"] if x["cats"][r[1]] == "Fruits"]
    assert sum(r[3] for r in fruit) == 6 and sum(r[7] for r in fruit) == 2


def test_missing_days_skip_sundays(on_conn, tmp_path):
    conn = on_conn
    ingest.ingest_line_items_file(conn, _line_items(tmp_path, "2026-11-02", "2026-11-03", [
        ("S010. Vegetables - SPLIT", "1-EA", 1, "R A", "2026-11-02", "invoiced", 1, 2, 0)]))
    # 2 Nov loaded; 3-10 Nov missing - one export, the Sunday in it is harmless
    assert sales_explorer.missing_days(D(2026, 11, 10)) == [(D(2026, 11, 3), D(2026, 11, 10))]
    assert sales_explorer.missing_days(D(2026, 11, 2)) == []
