"""Deterministic weekly selection: code picks, AI writes.

Same orders table in -> same ranked ten out, every run. No LLM anywhere in
this module. Every rule and weight lives in config.py.

Pipeline: candidate filter -> gap detection -> score -> total-order tie-break
-> top N -> deterministic in-season product suggestions per gap.
"""

import logging
from datetime import date, timedelta

import sqlalchemy as sa

import classify
import config
import db

log = logging.getLogger(__name__)

ACTIVE_STATUSES = {"active_regular", "active_adhoc"}


def as_of_date(conn) -> date:
    """The engine's clock is the data, not the wall clock: run date = latest
    delivery date ingested. Keeps every run reproducible."""
    value = conn.execute(sa.select(sa.func.max(db.orders.c.delivery_date))).scalar()
    if value is None:
        raise RuntimeError("orders table is empty — ingest data first")
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _customer_categories(conn, lookback_days: int | None, as_of: date) -> dict[str, set]:
    q = (sa.select(db.orders.c.customer_code, db.products.c.category)
         .select_from(db.orders.join(
             db.products, db.orders.c.product_code == db.products.c.product_code))
         .where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
         .distinct())
    if lookback_days:
        q = q.where(db.orders.c.delivery_date >= as_of - timedelta(days=lookback_days))
    out: dict[str, set] = {}
    for code, cat in conn.execute(q):
        out.setdefault(code, set()).add(cat)
    return out


def _customer_skus(conn) -> dict[str, set]:
    out: dict[str, set] = {}
    q = (sa.select(db.orders.c.customer_code, db.orders.c.product_code)
         .where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
         .distinct())
    for code, sku in conn.execute(q):
        out.setdefault(code, set()).add(sku)
    return out


def _recent_recommendations(conn, run_date: date) -> set[str]:
    cutoff = run_date - timedelta(weeks=config.COOLDOWN_WEEKS)
    q = (sa.select(db.recommendations.c.customer_code)
         .where(db.recommendations.c.run_date >= cutoff)
         .where(db.recommendations.c.run_date < run_date))
    return {r.customer_code for r in conn.execute(q)}


def detect_gaps(bought: set[str], venue_type: str) -> tuple[list[str], list[str]]:
    """Returns (all_targetable_gaps, focused_gaps). Focused = intersected with
    the segment focus map (meeting-PDF priorities), capped, deterministic order:
    segment-focus list order first, remaining gaps alphabetically."""
    gaps = [c for c in config.TARGETABLE_CATEGORIES if c not in bought]
    focus = config.SEGMENT_FOCUS_CATEGORIES.get(venue_type, [])
    focused = [c for c in focus if c in gaps]
    if not focused:
        focused = sorted(gaps)
    else:
        focused += [c for c in sorted(gaps) if c not in focused]
    return gaps, focused[:config.MAX_GAPS_PER_ACCOUNT]


def score_candidate(orders_per_week: float, gap_count: int,
                    venue_type: str, avg_lines_per_order: float) -> float:
    engagement = min(orders_per_week, config.ENGAGEMENT_CAP_ORDERS_PER_WEEK) \
        / config.ENGAGEMENT_CAP_ORDERS_PER_WEEK
    headroom = gap_count / len(config.TARGETABLE_CATEGORIES)
    segment = config.SEGMENT_PRIORITY_BONUS.get(venue_type,
                                                config.DEFAULT_SEGMENT_BONUS)
    volume = min(avg_lines_per_order, config.VOLUME_CAP_LINES_PER_ORDER) \
        / config.VOLUME_CAP_LINES_PER_ORDER
    return round(
        config.WEIGHT_ENGAGEMENT * engagement
        + config.WEIGHT_HEADROOM * headroom
        + config.WEIGHT_SEGMENT * segment
        + config.WEIGHT_VOLUME * volume, 6)


def suggest_products(conn, category: str, as_of: date,
                     exclude_skus: set[str]) -> list[dict]:
    """Top products in a category by distinct recent buyers — popular right now
    means in season and low-risk to recommend. Deterministic tie-breaks."""
    window_start = as_of - timedelta(days=config.SUGGESTION_WINDOW_DAYS)

    def _query(since: date | None):
        q = (sa.select(
                db.orders.c.product_code,
                db.products.c.product_name,
                sa.func.count(sa.distinct(db.orders.c.customer_code)).label("buyers"))
             .select_from(db.orders.join(
                 db.products, db.orders.c.product_code == db.products.c.product_code))
             .where(db.products.c.category == category)
             .where(db.products.c.out_of_season.is_(False))
             .where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
             .group_by(db.orders.c.product_code, db.products.c.product_name))
        if since:
            q = q.where(db.orders.c.delivery_date >= since)
        return conn.execute(q).fetchall()

    rows = _query(window_start) or _query(None)
    ranked = sorted(rows, key=lambda r: (-r.buyers, r.product_code))
    out = []
    for r in ranked:
        if r.product_code in exclude_skus:
            continue
        out.append({"code": r.product_code, "name": r.product_name,
                    "buyers_14d": int(r.buyers)})
        if len(out) >= config.SUGGESTIONS_PER_GAP:
            break
    return out


def select_top(conn, run_date: date | None = None) -> list[dict]:
    """Rank candidates and persist the top N to recommendations.
    Re-running for the same run_date replaces that run's rows (idempotent)."""
    as_of = run_date or as_of_date(conn)
    stats = classify.customer_order_stats(conn)
    cats = _customer_categories(conn, config.GAP_LOOKBACK_DAYS, as_of)
    skus = _customer_skus(conn)
    cooldown = _recent_recommendations(conn, as_of)
    custs = {c.customer_code: c for c in conn.execute(sa.select(db.customers))}

    span_days = (as_of - min(s["first_order"] for s in stats.values())).days + 1
    span_weeks = max(span_days / 7.0, 1.0)

    candidates = []
    for code, s in stats.items():
        c = custs.get(code)
        if c is None or c.activity_status not in ACTIVE_STATUSES:
            continue
        if c.prestige == "Excluded" or c.venue_type in config.EXCLUDED_VENUE_TYPES:
            continue
        if s["num_orders"] < config.MIN_ORDERS_EVER:
            continue
        breadth = (len(skus.get(code, ())) if config.LOW_ORDER_METRIC == "sku"
                   else len(cats.get(code, ())))
        if breadth > config.LOW_ORDER_MAX:
            continue
        if code in cooldown:
            continue
        gaps, focused = detect_gaps(cats.get(code, set()), c.venue_type)
        if not focused:
            continue  # nothing to pitch
        orders_per_week = s["num_orders"] / span_weeks
        avg_lines = s["num_lines"] / s["num_orders"]
        score = score_candidate(orders_per_week, len(gaps), c.venue_type, avg_lines)
        candidates.append({
            "customer_code": code, "customer": c, "stats": s,
            "gaps": gaps, "focused": focused, "score": score,
            "total_qty_rank": s["num_lines"],
        })

    # Total order: score desc -> order lines desc -> customer_code asc.
    candidates.sort(key=lambda x: (-x["score"], -x["total_qty_rank"],
                                   x["customer_code"]))
    top = candidates[:config.TOP_N]

    conn.execute(db.recommendations.delete()
                 .where(db.recommendations.c.run_date == as_of))
    results = []
    for rank, cand in enumerate(top, start=1):
        c, s = cand["customer"], cand["stats"]
        suggestions = {
            gap: suggest_products(conn, gap, as_of, skus.get(cand["customer_code"], set()))
            for gap in cand["focused"]
        }
        rationale = (
            f"{c.venue_type}; {c.activity_status}; {s['num_orders']} orders "
            f"({s['num_orders'] / span_weeks:.1f}/wk), "
            f"{len(skus.get(cand['customer_code'], ()))} SKUs across "
            f"{len(cats.get(cand['customer_code'], ()))} categories; "
            f"missing {len(cand['gaps'])}/{len(config.TARGETABLE_CATEGORIES)} "
            f"targetable categories. Pitch: {', '.join(cand['focused'])}."
        )
        rec_id = conn.execute(db.recommendations.insert().values(
            run_date=as_of,
            customer_code=cand["customer_code"],
            rank=rank,
            score=cand["score"],
            gap_categories=cand["focused"],
            suggested_products=suggestions,
            rationale=rationale,
            status="proposed",
            created_at=db.now_utc(),
        )).inserted_primary_key[0]
        results.append({
            "recommendation_id": rec_id, "rank": rank,
            "customer_code": cand["customer_code"],
            "customer_name": c.customer_name, "venue_type": c.venue_type,
            "sales_rep": c.sales_rep, "score": cand["score"],
            "activity_status": c.activity_status, "size_band": c.size_band,
            "num_orders": s["num_orders"],
            "num_skus": len(skus.get(cand["customer_code"], ())),
            "num_cats": len(cats.get(cand["customer_code"], ())),
            "bought_categories": sorted(cats.get(cand["customer_code"], ())),
            "gap_categories": cand["focused"],
            "all_gaps": cand["gaps"],
            "suggested_products": suggestions,
            "rationale": rationale,
        })
    log.info("selected %d of %d candidates for %s",
             len(results), len(candidates), as_of)
    return results
