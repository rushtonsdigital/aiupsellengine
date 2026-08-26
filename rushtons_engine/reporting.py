"""Reporting / BI summary tables — recomputed each weekly run.

Same contract as metrics.recompute: full-scan the raw tables, aggregate in
Python, DELETE then bulk INSERT. Cheap at this data volume and guarantees the
summaries always agree with ingested history. Metabase and the read-only chat
path (query.py) read these tables and the reporting.sql views, never the raw
orders table live, so reports stay instant as history grows.

Every measure here is a VOLUME measure — order counts, line counts, quantity.
There is no price/revenue anywhere in the source data. total_qty mixes qty_type
(Each/kg/box) and is only meaningful per product, never summed across types.
"""

import logging
from datetime import timedelta

import sqlalchemy as sa

import config
import db

log = logging.getLogger(__name__)

BUYERS_RECENT_DAYS = config.SUGGESTION_WINDOW_DAYS  # 14 — "buying it now"
BUYERS_TREND_DAYS = 90                              # longer trend window


def refresh_sales(conn, as_of) -> tuple[int, int]:
    """Rebuild customer_category_metrics and product_metrics from orders.

    One scan of invoiced order lines (joined to products for the category)
    feeds both tables. Returns (customer_category_rows, product_rows).
    """
    recent_start = as_of - timedelta(days=BUYERS_RECENT_DAYS)
    trend_start = as_of - timedelta(days=BUYERS_TREND_DAYS)

    rows = conn.execute(
        sa.select(
            db.orders.c.customer_code,
            db.orders.c.order_number,
            db.orders.c.product_code,
            db.orders.c.delivery_date,
            db.orders.c.quantity,
            db.products.c.category,
        ).select_from(
            db.orders.join(db.products,
                           db.orders.c.product_code == db.products.c.product_code)
        ).where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
    ).fetchall()

    cat: dict[tuple, dict] = {}
    prod: dict[str, dict] = {}
    for code, order_number, product_code, ddate, qty, category in rows:
        q = float(qty or 0)

        c = cat.setdefault((code, category), {
            "lines": 0, "orders": set(), "qty": 0.0,
            "first": ddate, "last": ddate})
        c["lines"] += 1
        c["orders"].add(order_number)
        c["qty"] += q
        c["first"] = min(c["first"], ddate)
        c["last"] = max(c["last"], ddate)

        p = prod.setdefault(product_code, {
            "buyers": set(), "lines": 0, "qty": 0.0,
            "buyers_14d": set(), "buyers_90d": set(),
            "first": ddate, "last": ddate})
        p["buyers"].add(code)
        p["lines"] += 1
        p["qty"] += q
        p["first"] = min(p["first"], ddate)
        p["last"] = max(p["last"], ddate)
        if ddate >= recent_start:
            p["buyers_14d"].add(code)
        if ddate >= trend_start:
            p["buyers_90d"].add(code)

    conn.execute(db.customer_category_metrics.delete())
    if cat:
        conn.execute(db.customer_category_metrics.insert(), [{
            "customer_code": code, "category": category,
            "line_count": a["lines"], "order_count": len(a["orders"]),
            "total_qty": a["qty"], "first_bought": a["first"],
            "last_bought": a["last"],
        } for (code, category), a in sorted(cat.items())])

    conn.execute(db.product_metrics.delete())
    if prod:
        conn.execute(db.product_metrics.insert(), [{
            "product_code": pc, "distinct_buyers": len(a["buyers"]),
            "line_count": a["lines"], "total_qty": a["qty"],
            "buyers_14d": len(a["buyers_14d"]), "buyers_90d": len(a["buyers_90d"]),
            "first_sold": a["first"], "last_sold": a["last"],
        } for pc, a in sorted(prod.items())])

    log.info("reporting sales refreshed: %d category rows, %d product rows",
             len(cat), len(prod))
    return len(cat), len(prod)


def refresh_funnel(conn) -> int:
    """Rebuild recommendation_category_facts by flattening every recommendation's
    gap_categories / chosen_products JSON in Python — one row per
    (run_date, customer_code, category).

    Rebuilds across ALL runs (history is tiny), so the funnel can be charted
    over time. Reading the JSON here means no consumer touches jsonb/json_each,
    which diverges between Postgres and SQLite.
    """
    recs = conn.execute(sa.select(
        db.recommendations.c.run_date,
        db.recommendations.c.customer_code,
        db.recommendations.c.gap_categories,
        db.recommendations.c.chosen_products,
        db.recommendations.c.status,
    )).fetchall()

    facts: dict[tuple, dict] = {}
    for run_date, customer_code, gap_categories, chosen_products, status in recs:
        offered = set(gap_categories or [])
        chosen = {item.get("category") for item in (chosen_products or [])
                  if item.get("category")}
        for category in offered | chosen:
            facts[(run_date, customer_code, category)] = {
                "run_date": run_date, "customer_code": customer_code,
                "category": category, "offered": category in offered,
                "chosen": category in chosen, "rec_status": status,
            }

    conn.execute(db.recommendation_category_facts.delete())
    if facts:
        conn.execute(db.recommendation_category_facts.insert(),
                     [facts[k] for k in sorted(facts)])
    log.info("reporting funnel refreshed: %d category facts", len(facts))
    return len(facts)
