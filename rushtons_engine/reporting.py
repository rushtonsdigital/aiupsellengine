"""Reporting / BI summary tables — recomputed each weekly run.

Same contract as metrics.recompute: full-scan the raw tables, aggregate in
Python, DELETE then bulk INSERT. Cheap at this data volume and guarantees the
summaries always agree with ingested history. Metabase and the read-only chat
path (query.py) read these tables and the reporting.sql views, never the raw
orders table live, so reports stay instant as history grows.

refresh_sales / refresh_funnel are VOLUME measures — order counts, line counts,
quantity. total_qty mixes qty_type (Each/kg/box) and is only meaningful per
product, never summed across types. £ value comes only from refresh_values:
per account per month (Fresho's GP-margin report has no product split).
"""

import calendar
import json
import logging
from datetime import date, timedelta

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
    cprod: dict[tuple, list] = {}           # (customer, product) -> [lines, first, last]
    orders: dict[str, dict] = {}            # customer -> {order_number: (date, lines)}
    for code, order_number, product_code, ddate, qty, category in rows:
        q = float(qty or 0)

        cp = cprod.setdefault((code, product_code), [0, ddate, ddate])
        cp[0] += 1
        cp[1], cp[2] = min(cp[1], ddate), max(cp[2], ddate)
        o = orders.setdefault(code, {})
        d, n = o.get(order_number, (ddate, 0))
        o[order_number] = (d, n + 1)

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

    conn.execute(db.customer_product_metrics.delete())
    if cprod:
        conn.execute(db.customer_product_metrics.insert(), [{
            "customer_code": code, "product_code": pc, "line_count": v[0],
            "first_bought": v[1], "last_bought": v[2],
        } for (code, pc), v in sorted(cprod.items())])

    conn.execute(db.customer_order_profile.delete())
    profile = []
    for code, o in sorted(orders.items()):
        dated = sorted(o.values())
        last_date = dated[-1][0]
        days = [0] * 7
        for d, _ in dated:
            days[d.weekday()] += 1
        profile.append({
            "customer_code": code,
            "orders_7d": sum(1 for d, _ in dated if d > as_of - timedelta(days=7)),
            "orders_28d": sum(1 for d, _ in dated if d > as_of - timedelta(days=28)),
            "orders_total": len(dated), "last_order_date": last_date,
            "last_order_lines": sum(n for d, n in dated if d == last_date),
            "weekday_orders": json.dumps(days),
        })
    if profile:
        conn.execute(db.customer_order_profile.insert(), profile)

    log.info("reporting sales refreshed: %d category rows, %d product rows, "
             "%d account-product rows", len(cat), len(prod), len(cprod))
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


def _month_end(m: date) -> date:
    return m.replace(day=calendar.monthrange(m.year, m.month)[1])


def _add_months(m: date, n: int) -> date:
    y, mo = divmod(m.month - 1 + n, 12)
    return date(m.year + y, mo + 1, 1)


def refresh_values(conn) -> int:
    """Rebuild customer_value_metrics from customer_month_values.

    Months are compared globally, not per account, so "last month" means the
    same calendar month for everyone and an account with no sales that month
    shows £0 (a real drop) rather than its own last active month. Returns rows.
    """
    t = db.customer_month_values
    rows = conn.execute(sa.select(t.c.customer_code, t.c.month, t.c.period_end,
                                  t.c.net_sales, t.c.gross_profit,
                                  t.c.credits)).fetchall()
    conn.execute(db.customer_value_metrics.delete())
    if not rows:
        log.info("reporting values: no GP-margin data loaded")
        return 0

    complete = sorted({r.month for r in rows if r.period_end == _month_end(r.month)})
    latest = max(r.month for r in rows)
    mtd_month = latest if latest not in complete else None
    mtd_through = max((r.period_end for r in rows if r.month == mtd_month), default=None)
    last = complete[-1] if complete else None
    prev3 = [m for m in complete if last and _add_months(last, -3) <= m < last]
    window_start = _add_months(latest, -11)

    # Orders per account in the last complete month, for average order value -
    # only when our order history covers that whole month.
    orders_n: dict[str, int] = {}
    first_order = conn.execute(sa.select(sa.func.min(db.orders.c.delivery_date))).scalar()
    if last and first_order and first_order <= last:
        o = db.orders.c
        orders_n = dict(conn.execute(
            sa.select(o.customer_code, sa.func.count(sa.distinct(o.order_number)))
            .where(o.order_state.in_(config.COUNTED_ORDER_STATES),
                   o.delivery_date >= last, o.delivery_date <= _month_end(last))
            .group_by(o.customer_code)).fetchall())

    by_cust: dict[str, dict[date, tuple[float, float]]] = {}
    for r in rows:
        if r.month >= window_start:
            by_cust.setdefault(r.customer_code, {})[r.month] = (
                float(r.net_sales or 0), float(r.gross_profit or 0),
                -float(r.credits or 0))

    pct = lambda num, den: round(100.0 * num / den, 1) if den else None
    out = []
    for code, months in sorted(by_cust.items()):
        sales = sum(v[0] for v in months.values())
        gp = sum(v[1] for v in months.values())
        s_last, g_last = months.get(last, (0.0, 0.0, 0.0))[:2] if last else (0.0, 0.0)
        prev_avg = (sum(months.get(m, (0.0, 0.0, 0.0))[0] for m in prev3) / len(prev3)
                    if prev3 else None)
        n_orders = orders_n.get(code)
        out.append({
            "customer_code": code, "months": len(months),
            "sales_12m": round(sales, 2), "gp_12m": round(gp, 2),
            "gp_pct_12m": pct(gp, sales),
            "last_month": last, "sales_last_month": round(s_last, 2),
            "gp_pct_last_month": pct(g_last, s_last),
            "sales_prev3_avg": round(prev_avg, 2) if prev_avg is not None else None,
            "trend_pct": pct(s_last - prev_avg, prev_avg) if prev_avg else None,
            "sales_mtd": round(months.get(mtd_month, (0.0,))[0], 2) if mtd_month else None,
            "mtd_through": mtd_through,
            "orders_last_month": n_orders,
            "aov_last_month": round(s_last / n_orders, 2) if n_orders else None,
            "series": json.dumps([[m.strftime("%Y-%m"), round(v[0], 2), round(v[1], 2),
                                   round(v[2], 2)] for m, v in sorted(months.items())]),
        })
    conn.execute(db.customer_value_metrics.insert(), out)
    log.info("reporting values refreshed: %d accounts, last complete month %s",
             len(out), last)
    return len(out)
