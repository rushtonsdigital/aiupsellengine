"""The value layer: Fresho's 'Sales Totals and Gross Profit Margin' export, one
calendar month per file, and the per-account summary built from it."""

import datetime as dt
import json

import pytest
import sqlalchemy as sa

import db
import ingest
import reporting
from conftest import add_customer, add_order

HEADER = ("CustomerCode,CustomerName,Orders,Credits,CostOfGoodsSold,"
          "GrossProfit,GrossProfitMarginPercent\n")


def _export(tmp_path, start, end, rows):
    """Write a GP-margin export the way Fresho names and formats it."""
    p = tmp_path / f"rushtons-greengrocers_sales_gp_margin_{start}_{end}.csv"
    body = "".join(
        f'{code},{name},"£{sales:,.2f}","(£{credit:,.2f})","(£{cogs:,.2f})",'
        f'"£{sales - credit - cogs:,.2f}",x%\n'
        for code, name, sales, credit, cogs in rows)
    total = sum(r[2] for r in rows)
    p.write_text(HEADER + body + f',Total,"£{total:,.2f}",,,,\n', encoding="utf-8")
    return p


def test_money_parsing():
    assert ingest._money("£27,870.25") == 27870.25
    assert ingest._money("(£186.90)") == -186.90
    assert ingest._money("N/A") == 0.0 and ingest._money("") == 0.0


def test_only_single_month_files_accepted(tmp_path):
    ok = tmp_path / "x_sales_gp_margin_2026-09-01_2026-09-30.csv"
    mtd = tmp_path / "x_sales_gp_margin_2026-10-01_2026-10-17.csv"
    year = tmp_path / "x_sales_gp_margin_2026-01-01_2026-10-17.csv"
    assert ingest.value_file_period(ok) == (dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert ingest.value_file_period(mtd)[1] == dt.date(2026, 10, 17)
    with pytest.raises(ingest.NotAMonthError):
        ingest.value_file_period(year)


def test_duplicate_codes_summed_total_row_dropped(tmp_path):
    p = _export(tmp_path, "2026-09-01", "2026-09-30",
                [("R A", "Alpha", 1000, 10, 600), ("R A", "Alpha (old name)", 500, 0, 300),
                 ("R B", "Beta", 200, 0, 150)])
    rows = {r["customer_code"]: r for r in ingest.read_value_rows(p)}
    assert set(rows) == {"R A", "R B"}
    a = rows["R A"]
    assert a["gross_sales"] == 1500 and a["credits"] == -10
    assert a["net_sales"] == 1490 and a["cogs"] == 900
    assert a["gross_profit"] == pytest.approx(a["net_sales"] - a["cogs"])


def test_month_to_date_replaced_by_fuller_file_never_by_older(conn, tmp_path):
    early = _export(tmp_path, "2026-10-01", "2026-10-10", [("R A", "A", 100, 0, 60)])
    full = _export(tmp_path, "2026-10-01", "2026-10-17", [("R A", "A", 300, 0, 180)])
    ingest.ingest_values_file(conn, early)
    ingest.ingest_values_file(conn, full)
    assert ingest.ingest_values_file(conn, early) == 0       # stale: skipped
    row = conn.execute(sa.select(db.customer_month_values)).one()
    assert row.net_sales == 300 and row.period_end == dt.date(2026, 10, 17)
    ingest.ingest_values_file(conn, full)                    # re-run: idempotent
    assert conn.execute(sa.select(sa.func.count())
                        .select_from(db.customer_month_values)).scalar() == 1


def test_whole_period_total_skipped_by_batch_loader(conn, tmp_path):
    month = _export(tmp_path, "2026-09-01", "2026-09-30", [("R A", "A", 100, 0, 60)])
    year = _export(tmp_path, "2026-01-01", "2026-10-17", [("R A", "A", 9999, 0, 1)])
    assert ingest.ingest_values_files(conn, [year, month]) == 1
    assert conn.execute(sa.select(db.customer_month_values.c.net_sales)).scalar() == 100


def test_refresh_values_summary(conn, tmp_path):
    # Jun-Sep complete, Oct to date. GROW lifts in Sep; GONE buys nothing in Sep.
    months = [("2026-06-01", "2026-06-30"), ("2026-07-01", "2026-07-31"),
              ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]
    for start, end in months:
        rows = [("R GONE", "Gone", 1000, 0, 600)] if start != "2026-09-01" else []
        sep = start == "2026-09-01"
        rows.append(("R GROW", "Grow", 2000 if sep else 1000, 0, 1200 if sep else 600))
        ingest.ingest_values_file(conn, _export(tmp_path, start, end, rows))
    ingest.ingest_values_file(conn, _export(tmp_path, "2026-10-01", "2026-10-17",
                                            [("R GROW", "Grow", 700, 0, 420)]))
    # four September orders for GROW -> average order value £500
    add_customer(conn, "R GROW")
    for day in (2, 9, 16, 23):
        add_order(conn, "R GROW", "VEG-1", dt.date(2026, 9, day))
    add_order(conn, "R GROW", "VEG-1", dt.date(2026, 5, 30))  # history before Sep

    assert reporting.refresh_values(conn) == 2
    m = {r.customer_code: r for r in conn.execute(sa.select(db.customer_value_metrics))}
    grow, gone = m["R GROW"], m["R GONE"]
    assert grow.last_month == dt.date(2026, 9, 1)
    assert float(grow.sales_last_month) == 2000 and float(grow.sales_prev3_avg) == 1000
    assert float(grow.trend_pct) == 100.0
    assert float(grow.sales_mtd) == 700 and grow.mtd_through == dt.date(2026, 10, 17)
    assert float(grow.sales_12m) == 5700 and float(grow.gp_pct_12m) == 40.0
    assert grow.orders_last_month == 4 and float(grow.aov_last_month) == 500
    assert json.loads(grow.series)[0] == ["2026-06", 1000, 400]
    # no row in the last month is a real £0, not "its own last active month"
    assert float(gone.sales_last_month) == 0 and float(gone.trend_pct) == -100.0
    assert gone.aov_last_month is None
