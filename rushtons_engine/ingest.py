"""Ingest raw Fresho exports.

Two file shapes are understood:
  * daily order exports  (`*product_totals_by_customer_YYYY-MM-DD.csv`)
  * the customer master  (`*customers_*.csv`)

Order-line idempotency is delete-where-source_file-then-insert. There is
deliberately NO unique (order_number, product_code) key: real exports contain
identical duplicate lines within a single file and both lines are genuine.
"""

import csv
import logging
from datetime import date, datetime
from pathlib import Path

import sqlalchemy as sa

import categories
import config
import db

log = logging.getLogger(__name__)

ORDER_COLUMNS = {  # raw daily export header -> orders column
    "Product Group": "product_group",
    "Product Code": "product_code",
    "Product Name": "product_name",
    "Qty Type": "qty_type",
    "Quantity": "quantity",
    "Delivery Run": "delivery_run",
    "Customer Name": "customer_name",
    "Customer Code": "customer_code",
    "Order Number": "order_number",
    "Delivery Date": "delivery_date",
    "Order State": "order_state",
}


def _clean_code(value: str) -> str:
    """Fresho quotes codes against Excel mangling: `'R NOTTINGH'` -> `R NOTTINGH`."""
    return (value or "").strip().strip("'").strip()


def _parse_date(value: str) -> date:
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def read_order_rows(path: Path) -> tuple[list[dict], int]:
    """Parse a daily export. Returns (rows, skipped_state_count).
    Raises UnknownProductGroupError on any unmapped product group."""
    rows, skipped = [], 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        missing = set(ORDER_COLUMNS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}: not a daily order export, "
                             f"missing columns {sorted(missing)}")
        for raw in reader:
            state = config.canonical_order_state(raw["Order State"])
            if state is None:
                skipped += 1
                continue
            group = (raw["Product Group"] or "").strip()
            categories.to_category(group)  # strict validation, raises on unknown
            rows.append({
                "order_number": int(raw["Order Number"].strip()),
                "customer_code": _clean_code(raw["Customer Code"]),
                "customer_name": (raw["Customer Name"] or "").strip(),
                "product_code": _clean_code(raw["Product Code"]),
                "product_name": (raw["Product Name"] or "").strip(),
                "product_group": group,
                "delivery_date": _parse_date(raw["Delivery Date"]),
                "quantity": float(raw["Quantity"] or 0),
                "qty_type": (raw["Qty Type"] or "").strip(),
                "order_state": state,
                "delivery_run": (raw["Delivery Run"] or "").strip(),
            })
    return rows, skipped


def _upsert_products(conn, rows: list[dict]) -> None:
    """Product master: latest informative (non-blank, non-Z-status) group wins
    the category; out_of_season / delisted reflect the latest group of any kind."""
    existing = {
        r.product_code: r
        for r in conn.execute(sa.select(db.products)).fetchall()
    }
    by_code: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["delivery_date"]):
        p = by_code.setdefault(row["product_code"], {
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "raw_product_group": None,
            "out_of_season": False,
            "delisted": False,
            "first_seen": row["delivery_date"],
            "last_seen": row["delivery_date"],
        })
        p["product_name"] = row["product_name"]
        p["last_seen"] = max(p["last_seen"], row["delivery_date"])
        p["first_seen"] = min(p["first_seen"], row["delivery_date"])
        if categories.is_informative(row["product_group"]):
            p["raw_product_group"] = row["product_group"]
        group = (row["product_group"] or "").strip()
        p["out_of_season"] = group == categories.OUT_OF_SEASON_GROUP
        p["delisted"] = group == categories.DELISTED_GROUP

    for code, p in by_code.items():
        prev = existing.get(code)
        raw_group = p["raw_product_group"] or (prev.raw_product_group if prev else None)
        values = {
            "product_name": p["product_name"],
            "raw_product_group": raw_group,
            "category": categories.to_category(raw_group or ""),
            "out_of_season": p["out_of_season"],
            "delisted": p["delisted"],
            "last_seen": max(p["last_seen"], prev.last_seen) if prev else p["last_seen"],
            "first_seen": min(p["first_seen"], prev.first_seen) if prev else p["first_seen"],
            "updated_at": db.now_utc(),
        }
        if prev:
            conn.execute(db.products.update()
                         .where(db.products.c.product_code == code).values(**values))
        else:
            conn.execute(db.products.insert().values(product_code=code, **values))


def _ensure_customer_stubs(conn, rows: list[dict]) -> None:
    """Order lines may reference customers absent from the master export."""
    known = {r.customer_code for r in
             conn.execute(sa.select(db.customers.c.customer_code)).fetchall()}
    stubs = {}
    for row in rows:
        if row["customer_code"] not in known:
            stubs[row["customer_code"]] = row["customer_name"]
    for code, name in stubs.items():
        log.warning("customer %s (%s) in orders but not in customer master; "
                    "stub created, flagged 'new (auto)' for venue-type review", code, name)
        conn.execute(db.customers.insert().values(
            customer_code=code, customer_name=name,
            account_stage="new (auto)", updated_at=db.now_utc()))


def ingest_orders_file(conn, path: Path) -> int:
    """Idempotently (re-)ingest one daily export. Returns inserted line count."""
    rows, skipped = read_order_rows(path)
    if skipped:
        log.warning("%s: skipped %d lines with order_state outside %s",
                    path.name, skipped, sorted(config.COUNTED_ORDER_STATES))
    _ensure_customer_stubs(conn, rows)
    _upsert_products(conn, rows)
    conn.execute(db.orders.delete().where(db.orders.c.source_file == path.name))
    if rows:
        conn.execute(db.orders.insert(), [{
            "order_number": r["order_number"],
            "customer_code": r["customer_code"],
            "product_code": r["product_code"],
            "delivery_date": r["delivery_date"],
            "quantity": r["quantity"],
            "qty_type": r["qty_type"],
            "order_state": r["order_state"],
            "delivery_run": r["delivery_run"],
            "source_file": path.name,
            "ingested_at": db.now_utc(),
        } for r in rows])
    log.info("%s: %d lines ingested", path.name, len(rows))
    return len(rows)


# --- customer master -----------------------------------------------------------

def _parse_tags(raw_tags: str) -> dict:
    """'1. restaurant | 2. group <5 | 3. | 4. new 26 | 5. forth hospitality'
    -> positional dict; empty positions become None."""
    out = {1: None, 2: None, 3: None, 4: None, 5: None}
    for part in (raw_tags or "").split("|"):
        part = part.strip()
        if len(part) >= 2 and part[0].isdigit() and part[1] == ".":
            pos = int(part[0])
            value = part[2:].strip()
            if pos in out:
                out[pos] = value or None
    return out


def ingest_customers_file(conn, path: Path) -> int:
    """Upsert the Fresho customer master. Leaves computed fields
    (activity_status, size_band, prestige...) untouched — classify.py owns those."""
    import classify  # late import: classify imports db but not ingest

    count = 0
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        reader = csv.DictReader(fh)
        cols = {c.split(" (")[0].strip(): c for c in (reader.fieldnames or [])}
        needed = ["customer_name", "customer_code", "active", "tags", "sales_rep"]
        missing = [n for n in needed if n not in cols]
        if missing:
            raise ValueError(f"{path.name}: not a customer master export, "
                             f"missing {missing}")
        known = {r.customer_code for r in
                 conn.execute(sa.select(db.customers.c.customer_code)).fetchall()}
        for raw in reader:
            code = _clean_code(raw[cols["customer_code"]])
            if not code:
                continue
            tags = _parse_tags(raw[cols["tags"]])
            values = {
                "customer_name": (raw[cols["customer_name"]] or "").strip(),
                "venue_type": classify.normalize_venue_type(tags[1]),
                "group_size_band": tags[2],
                "group_affiliation": tags[3] or tags[5],
                "account_stage": tags[4],
                "order_channel": tags[5],
                "sales_rep": (raw[cols["sales_rep"]] or "").strip() or None,
                "active": (raw[cols["active"]] or "").strip().lower() == "yes",
                "raw_tags": (raw[cols["tags"]] or "").strip(),
                "updated_at": db.now_utc(),
            }
            if code in known:
                conn.execute(db.customers.update()
                             .where(db.customers.c.customer_code == code)
                             .values(**values))
            else:
                conn.execute(db.customers.insert().values(customer_code=code, **values))
            count += 1
    log.info("%s: %d customers upserted", path.name, count)
    return count
