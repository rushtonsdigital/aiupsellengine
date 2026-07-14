"""Steps 1 and 2 of the weekly pipeline — code's half of the work.

    1. code selects WHO to target      (select_top — deterministic, final)
    2. code selects WHAT IS ELIGIBLE   (build_product_pool — deterministic)
    3. AI picks the final products      (drafting session — see draft.py)
    4. AI writes the messages           (drafting session — see draft.py)

Same orders table in -> same ranked ten and same pools out, every run. No LLM
anywhere in this module. Every rule and weight lives in config.py.

The account selection in step 1 is final and is never revisited downstream:
apply_drafts refuses any account outside it. Step 2 is different — it produces
a *pool* of eligible products, not a verdict. Ranking by recent-buyer count
alone kept surfacing commodity staples over the specialty lines Rushton's
actually wants to pitch (client feedback 2026-07-14), so the final pick moved
to step 3, where the customer can be taken into account. The pool is still the
hard boundary: step 3 may only choose from it, never invent a product.
"""

import logging
import re
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


_BASE_CODE = re.compile(r"^(\d+)(?:-|$)")


def base_code(product_code: str) -> str:
    """'1613-EA', '1613-CS-8', '1613-CS-12' are pack formats of one beetroot.
    The pitch is the product, not the pack — collapse them to '1613'.

    Fresho codes are <sku>-<pack format>, sku always numeric. Anything that
    doesn't match that shape is left alone rather than guessed at: collapsing
    two genuinely different products would silently hide one from the pool.
    """
    m = _BASE_CODE.match(product_code)
    return m.group(1) if m else product_code


def build_product_pool(conn, category: str, as_of: date,
                       exclude_skus: set[str]) -> list[dict]:
    """Step 2: the *eligible* products for this gap — a pool, not a final pick.

    Sourced from the CATALOGUE, not from what's selling. That distinction is
    the whole point: the specialty lines Rushton's most wants to pitch (baby
    candy beetroot, Yukon baby fennel, heritage carrots) are low-volume *by
    definition*, so a pool built from recent orders can never surface them —
    which is exactly how three commodity staples ended up as the entire Baby
    Vegetables pitch (client feedback 2026-07-14).

    So: every in-season product in the category the account doesn't already
    buy, deduplicated across pack formats, annotated with the trade signals the
    drafter needs to judge —

      buyers_14d  how many other customers bought it in the window. 0 does NOT
                  mean unavailable; for a specialty line it's normal, and often
                  the reason it's worth a tip-off. Never a reason to pick.
      last_sold   when it last moved at all. Old + zero buyers = check it's
                  really still stocked before pitching it.
      product_group  Fresho's own grouping, carried through so mislabelled
                  products are visible to the drafter rather than invisible.
    """
    window_start = as_of - timedelta(days=config.SUGGESTION_WINDOW_DAYS)

    recent = (sa.select(
                db.orders.c.product_code,
                sa.func.count(sa.distinct(db.orders.c.customer_code)).label("buyers"))
              .where(db.orders.c.delivery_date >= window_start)
              .where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
              .group_by(db.orders.c.product_code)).subquery()

    q = (sa.select(
            db.products.c.product_code,
            db.products.c.product_name,
            db.products.c.raw_product_group,
            db.products.c.last_seen,
            sa.func.coalesce(recent.c.buyers, 0).label("buyers"))
         .select_from(db.products.outerjoin(
             recent, db.products.c.product_code == recent.c.product_code))
         .where(db.products.c.category == category)
         .where(db.products.c.out_of_season.is_(False)))

    # An account that buys any pack format of a product buys the product.
    excluded_bases = {base_code(s) for s in exclude_skus}

    best: dict[str, dict] = {}
    for r in conn.execute(q):
        base = base_code(r.product_code)
        if base in excluded_bases:
            continue
        item = {"code": r.product_code, "name": r.product_name,
                "product_group": r.raw_product_group,
                "buyers_14d": int(r.buyers),
                "last_sold": str(r.last_seen) if r.last_seen else None}
        # One row per product: the format that actually moves, else the
        # simplest code — deterministic either way.
        current = best.get(base)
        if current is None or (item["buyers_14d"], current["code"]) > \
                (current["buyers_14d"], item["code"]):
            best[base] = item

    ranked = sorted(best.values(), key=lambda p: (-p["buyers_14d"], p["code"]))
    return ranked[:config.POOL_PER_GAP]


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

    # Re-running select_top for a run_date that already has locked recommendations
    # must not disturb rows that comms already reference (FK) or reset a status a
    # human has already progressed (approved/sent/converted). So: update existing
    # (run_date, customer_code) rows in place (same id, same status); only insert
    # rows that are genuinely new; only delete stale rows with no comms attached.
    new_codes = {cand["customer_code"] for cand in top}
    existing_ids = {r.customer_code: r.id for r in conn.execute(
        sa.select(db.recommendations.c.id, db.recommendations.c.customer_code)
        .where(db.recommendations.c.run_date == as_of))}
    stale_codes = set(existing_ids) - new_codes
    if stale_codes:
        stale_ids = [existing_ids[c] for c in stale_codes]
        referenced = {r.recommendation_id for r in conn.execute(
            sa.select(db.comms.c.recommendation_id)
            .where(db.comms.c.recommendation_id.in_(stale_ids)))}
        removable = [rid for rid in stale_ids if rid not in referenced]
        if removable:
            conn.execute(db.recommendations.delete()
                         .where(db.recommendations.c.id.in_(removable)))
        kept = [c for c in stale_codes if existing_ids[c] in referenced]
        if kept:
            log.warning("keeping %d stale recommendation(s) for %s still "
                        "referenced by comms: %s", len(kept), as_of, kept)

    results = []
    for rank, cand in enumerate(top, start=1):
        c, s = cand["customer"], cand["stats"]
        code = cand["customer_code"]
        pool = {
            gap: build_product_pool(conn, gap, as_of, skus.get(code, set()))
            for gap in cand["focused"]
        }
        rationale = (
            f"{c.venue_type}; {c.activity_status}; {s['num_orders']} orders "
            f"({s['num_orders'] / span_weeks:.1f}/wk), "
            f"{len(skus.get(code, ()))} SKUs across "
            f"{len(cats.get(code, ()))} categories; "
            f"missing {len(cand['gaps'])}/{len(config.TARGETABLE_CATEGORIES)} "
            f"targetable categories. Pitch: {', '.join(cand['focused'])}."
        )
        # product_pool is code's output (step 2). chosen_products is the
        # drafter's pick from it (step 3) and stays None until apply_drafts.
        values = dict(run_date=as_of, customer_code=code, rank=rank,
                     score=cand["score"], gap_categories=cand["focused"],
                     product_pool=pool, rationale=rationale)
        if code in existing_ids and code not in stale_codes:
            rec_id = existing_ids[code]
            conn.execute(db.recommendations.update()
                         .where(db.recommendations.c.id == rec_id).values(**values))
        else:
            rec_id = conn.execute(db.recommendations.insert().values(
                **values, status="proposed", created_at=db.now_utc(),
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
            "product_pool": pool,
            "rationale": rationale,
        })
    log.info("selected %d of %d candidates for %s",
             len(results), len(candidates), as_of)
    return results
