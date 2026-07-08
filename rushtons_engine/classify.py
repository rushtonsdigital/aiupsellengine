"""Customer classification: venue type, activity status, size band, prestige.

Rules are deterministic and reverse-engineered from (and regression-tested
against) rushtons_customer_classification.csv:

  * excluded            internal/non-customer account (name keywords)
  * no orders in history: temporarily_closed if Fresho active=No, else long_lapsed
  * "regular pattern"   = >= REGULAR_MIN_ORDERS orders and median gap <= REGULAR_MAX_MEDIAN_GAP
      recency <= 7d -> active_regular | 8-21d -> lapsed | >21d -> long_lapsed
  * sporadic pattern
      recency <= 28d -> active_adhoc  | 29-56d -> lapsed | >56d -> long_lapsed
  * size band: percentile on total order lines (volume proxy — decision 1;
      interim until a price list allows spend-based banding)
  * prestige: seeded from the prior classification (hand-curated VIP groups),
      new accounts VIP when gold, Excluded when internal, else Standard.
"""

import csv
import logging
import statistics
from datetime import date
from pathlib import Path

import sqlalchemy as sa

import config
import db

log = logging.getLogger(__name__)

VENUE_TAG_TO_TYPE = {
    "restaurant": "Restaurants",
    "hotel": "Hotels",
    "pub": "Pubs",
    "bar": "Bars",
    "cafe": "Cafe",
    "event catering": "Event catering",
    "contract catering": "Contract catering",
    "retail": "Retail",
    "bakery": "Bakery",
    "members club": "Members Club",
    "manufacturing": "Manufacturing",
    "wholesale": "Wholesale",
}


def normalize_venue_type(tag_value: str | None) -> str:
    if not tag_value:
        return "Unknown"
    return VENUE_TAG_TO_TYPE.get(tag_value.strip().lower(),
                                 tag_value.strip().title())


def is_internal(customer_name: str) -> bool:
    name = (customer_name or "").lower()
    return any(kw in name for kw in config.INTERNAL_NAME_KEYWORDS)


def load_prestige_seed(path: Path | None = None) -> dict[str, str]:
    path = path or config.PRESTIGE_SEED_FILE
    if not path or not Path(path).exists():
        log.warning("prestige seed file missing (%s); falling back to gold->VIP rule", path)
        return {}
    seed = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for row in csv.DictReader(fh):
            if row.get("customer_code") and row.get("prestige"):
                seed[row["customer_code"].strip()] = row["prestige"].strip()
    return seed


def _activity_status(num_orders: int, median_gap: float | None,
                     days_since_last: int | None, fresho_active: bool) -> str:
    if days_since_last is None:  # never ordered in ingested history
        return "temporarily_closed" if not fresho_active else "long_lapsed"
    regular = (num_orders >= config.REGULAR_MIN_ORDERS
               and median_gap is not None
               and median_gap <= config.REGULAR_MAX_MEDIAN_GAP)
    if regular:
        if days_since_last <= config.LAPSED_DAYS:
            return "active_regular"
        if days_since_last <= config.LONG_LAPSED_DAYS:
            return "lapsed"
        return "long_lapsed"
    if days_since_last <= config.ADHOC_LAPSED_DAYS:
        return "active_adhoc"
    if days_since_last <= config.ADHOC_LONG_LAPSED_DAYS:
        return "lapsed"
    return "long_lapsed"


def customer_order_stats(conn) -> dict[str, dict]:
    """Per-customer aggregates over full ingested history (counted states only)."""
    rows = conn.execute(
        sa.select(
            db.orders.c.customer_code,
            db.orders.c.order_number,
            db.orders.c.delivery_date,
        ).where(db.orders.c.order_state.in_(config.COUNTED_ORDER_STATES))
    ).fetchall()

    per_cust: dict[str, dict] = {}
    for code, order_number, delivery_date in rows:
        c = per_cust.setdefault(code, {"orders": set(), "dates": set(), "lines": 0})
        c["orders"].add(order_number)
        c["dates"].add(delivery_date)
        c["lines"] += 1

    stats = {}
    for code, c in per_cust.items():
        dates = sorted(c["dates"])
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        stats[code] = {
            "num_orders": len(c["orders"]),
            "num_lines": c["lines"],
            "first_order": dates[0],
            "last_order": dates[-1],
            "median_gap": statistics.median(gaps) if gaps else None,
        }
    return stats


def classify_all(conn, as_of: date) -> None:
    """Recompute activity_status, size_band, prestige, last_order_date for
    every customer. Deterministic for a given orders table and as_of date."""
    stats = customer_order_stats(conn)
    seed = load_prestige_seed()
    custs = conn.execute(sa.select(db.customers)).fetchall()

    # size band: percentile rank on total lines, ties broken by customer_code
    # so banding is a total order (matches prior file: ~20% gold / 50% silver).
    banded = sorted(
        (c.customer_code for c in custs
         if c.customer_code in stats and not is_internal(c.customer_name)),
        key=lambda code: (-stats[code]["num_lines"], code),
    )
    bands = {}
    n = len(banded)
    for i, code in enumerate(banded):
        pct = (i + 1) / n if n else 1.0
        bands[code] = ("gold" if pct <= config.GOLD_PERCENTILE
                       else "silver" if pct <= config.SILVER_PERCENTILE
                       else "bronze")

    for c in custs:
        s = stats.get(c.customer_code)
        internal = is_internal(c.customer_name)
        days_since = (as_of - s["last_order"]).days if s else None
        if internal:
            status, venue = "excluded", "Internal/Non-customer"
        else:
            status = _activity_status(
                s["num_orders"] if s else 0,
                s["median_gap"] if s else None,
                days_since, bool(c.active))
            venue = c.venue_type or "Unknown"
        band = bands.get(c.customer_code) if not internal else None
        prestige = ("Excluded" if internal
                    else seed.get(c.customer_code)
                    or ("VIP" if band == "gold" else "Standard"))
        conn.execute(db.customers.update()
                     .where(db.customers.c.customer_code == c.customer_code)
                     .values(
                         venue_type=venue,
                         activity_status=status,
                         size_band=band,
                         prestige=prestige,
                         first_seen=s["first_order"] if s else c.first_seen,
                         last_order_date=s["last_order"] if s else None,
                         updated_at=db.now_utc(),
                     ))
    log.info("classified %d customers as of %s", len(custs), as_of)
