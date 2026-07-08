"""customer_week_metrics: one row per customer per ISO week (Monday start).

Fully recomputed from the orders table each run — cheap at this data volume
and guarantees the table always agrees with ingested history.
"""

import logging
from datetime import timedelta

import sqlalchemy as sa

import config
import db

log = logging.getLogger(__name__)


def week_start(d):
    return d - timedelta(days=d.weekday())


def recompute(conn) -> int:
    rows = conn.execute(
        sa.select(
            db.orders.c.customer_code,
            db.orders.c.order_number,
            db.orders.c.delivery_date,
            db.orders.c.product_code,
            db.orders.c.quantity,
            db.products.c.category,
        ).select_from(
            db.orders.join(db.products,
                           db.orders.c.product_code == db.products.c.product_code)
        ).where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
    ).fetchall()

    agg: dict[tuple, dict] = {}
    for code, order_number, delivery_date, product_code, qty, category in rows:
        key = (code, week_start(delivery_date))
        a = agg.setdefault(key, {"orders": set(), "skus": set(),
                                 "cats": set(), "qty": 0.0})
        a["orders"].add(order_number)
        a["skus"].add(product_code)
        a["cats"].add(category)
        a["qty"] += float(qty or 0)

    conn.execute(db.customer_week_metrics.delete())
    if agg:
        conn.execute(db.customer_week_metrics.insert(), [{
            "customer_code": code,
            "week_start": wk,
            "order_count": len(a["orders"]),
            "distinct_lines": len(a["skus"]),
            "distinct_cats": len(a["cats"]),
            "total_qty": a["qty"],
        } for (code, wk), a in sorted(agg.items())])
    log.info("customer_week_metrics recomputed: %d rows", len(agg))
    return len(agg)
