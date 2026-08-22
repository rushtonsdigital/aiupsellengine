"""Database layer: one SQLAlchemy Core metadata shared by Supabase Postgres and
local SQLite. schema.sql is the canonical Postgres DDL; this metadata mirrors it
so tests and pre-Supabase runs work identically.
"""

from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

import config

_REPORTING_SQL = Path(__file__).resolve().parent / "reporting.sql"

metadata = sa.MetaData()

customers = sa.Table(
    "customers", metadata,
    sa.Column("customer_code", sa.Text, primary_key=True),
    sa.Column("customer_name", sa.Text),
    sa.Column("venue_type", sa.Text),
    sa.Column("group_size_band", sa.Text),
    sa.Column("group_affiliation", sa.Text),
    sa.Column("account_stage", sa.Text),
    sa.Column("order_channel", sa.Text),
    sa.Column("sales_rep", sa.Text),
    sa.Column("active", sa.Boolean),
    sa.Column("prestige", sa.Text),
    sa.Column("raw_tags", sa.Text),
    sa.Column("first_seen", sa.Date),
    sa.Column("last_order_date", sa.Date),
    sa.Column("activity_status", sa.Text),
    sa.Column("size_band", sa.Text),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

products = sa.Table(
    "products", metadata,
    sa.Column("product_code", sa.Text, primary_key=True),
    sa.Column("product_name", sa.Text),
    sa.Column("raw_product_group", sa.Text),
    sa.Column("category", sa.Text),
    sa.Column("out_of_season", sa.Boolean, default=False),
    sa.Column("delisted", sa.Boolean, default=False),
    sa.Column("first_seen", sa.Date),
    sa.Column("last_seen", sa.Date),
    sa.Column("updated_at", sa.DateTime(timezone=True)),
)

orders = sa.Table(
    "orders", metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
              primary_key=True, autoincrement=True),
    sa.Column("order_number", sa.BigInteger),
    sa.Column("customer_code", sa.Text, sa.ForeignKey("customers.customer_code")),
    sa.Column("product_code", sa.Text, sa.ForeignKey("products.product_code")),
    sa.Column("delivery_date", sa.Date),
    sa.Column("quantity", sa.Numeric),
    sa.Column("qty_type", sa.Text),
    sa.Column("order_state", sa.Text),
    sa.Column("delivery_run", sa.Text),
    sa.Column("source_file", sa.Text, nullable=False, index=True),
    sa.Column("ingested_at", sa.DateTime(timezone=True)),
)
sa.Index("idx_orders_customer", orders.c.customer_code)
sa.Index("idx_orders_delivery_date", orders.c.delivery_date)
sa.Index("idx_orders_product_code", orders.c.product_code)

customer_week_metrics = sa.Table(
    "customer_week_metrics", metadata,
    sa.Column("customer_code", sa.Text, sa.ForeignKey("customers.customer_code"),
              primary_key=True),
    sa.Column("week_start", sa.Date, primary_key=True),
    sa.Column("order_count", sa.Integer),
    sa.Column("distinct_lines", sa.Integer),
    sa.Column("distinct_cats", sa.Integer),
    sa.Column("total_qty", sa.Numeric),
)

recommendations = sa.Table(
    "recommendations", metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
              primary_key=True, autoincrement=True),
    sa.Column("run_date", sa.Date, nullable=False),
    sa.Column("customer_code", sa.Text, sa.ForeignKey("customers.customer_code")),
    sa.Column("rank", sa.Integer),
    sa.Column("score", sa.Numeric),
    sa.Column("gap_categories", sa.JSON),
    # Step 2 (code): every product eligible to pitch, per gap category.
    sa.Column("product_pool", sa.JSON),
    # Step 3 (drafter): the few actually pitched, chosen from product_pool
    # with the customer in view. Null until drafts are applied.
    sa.Column("chosen_products", sa.JSON),
    sa.Column("rationale", sa.Text),
    sa.Column("status", sa.Text, default="proposed"),
    sa.Column("created_at", sa.DateTime(timezone=True)),
    sa.UniqueConstraint("run_date", "customer_code", name="uq_rec_run_customer"),
)

comms = sa.Table(
    "comms", metadata,
    sa.Column("id", sa.BigInteger().with_variant(sa.Integer, "sqlite"),
              primary_key=True, autoincrement=True),
    sa.Column("recommendation_id", sa.BigInteger,
              sa.ForeignKey("recommendations.id")),
    sa.Column("stage", sa.Text),
    sa.Column("channel", sa.Text, default="whatsapp"),
    sa.Column("draft_body", sa.Text),
    sa.Column("approved_by", sa.Text),
    sa.Column("sent_at", sa.DateTime(timezone=True)),
    sa.Column("created_at", sa.DateTime(timezone=True)),
)

# --- reporting / BI layer ----------------------------------------------------
# Pre-aggregated summary tables, refreshed each weekly run by reporting.py in
# the same delete-then-bulk-insert style as customer_week_metrics (metrics.py).
# They exist so Metabase and the read-only chat path read cheap, pre-labelled
# datasets instead of scanning the raw orders table live. Defined in metadata
# (not just schema.sql) so create_all() builds them on both Postgres and SQLite
# and the pytest fixtures can exercise the aggregation logic.
#
# NB: every measure here is a VOLUME measure (order counts, line counts,
# quantity) — there is no price/revenue data anywhere in the source. total_qty
# mixes qty_type (Each/kg/box) and is only meaningful grouped by product.

customer_category_metrics = sa.Table(
    "customer_category_metrics", metadata,
    sa.Column("customer_code", sa.Text, sa.ForeignKey("customers.customer_code"),
              primary_key=True),
    sa.Column("category", sa.Text, primary_key=True),
    sa.Column("line_count", sa.Integer),
    sa.Column("order_count", sa.Integer),
    sa.Column("total_qty", sa.Numeric),
    sa.Column("first_bought", sa.Date),
    sa.Column("last_bought", sa.Date),
)

product_metrics = sa.Table(
    "product_metrics", metadata,
    sa.Column("product_code", sa.Text, sa.ForeignKey("products.product_code"),
              primary_key=True),
    sa.Column("distinct_buyers", sa.Integer),
    sa.Column("line_count", sa.Integer),
    sa.Column("total_qty", sa.Numeric),
    sa.Column("buyers_14d", sa.Integer),
    sa.Column("buyers_90d", sa.Integer),
    sa.Column("first_sold", sa.Date),
    sa.Column("last_sold", sa.Date),
)

# One row per (run_date, customer_code, category): the gap_categories /
# chosen_products JSON on recommendations, flattened in Python by
# reporting.refresh_funnel so "conversion by category" is a plain group-by and
# no consumer has to parse JSON (which diverges between Postgres and SQLite).
recommendation_category_facts = sa.Table(
    "recommendation_category_facts", metadata,
    sa.Column("run_date", sa.Date, primary_key=True),
    sa.Column("customer_code", sa.Text, primary_key=True),
    sa.Column("category", sa.Text, primary_key=True),
    sa.Column("offered", sa.Boolean),
    sa.Column("chosen", sa.Boolean),
    sa.Column("rec_status", sa.Text),
)

_engine = None


def get_engine(url: str | None = None) -> sa.Engine:
    global _engine
    if _engine is None or url is not None:
        _engine = sa.create_engine(url or config.DATABASE_URL)
    return _engine


def init_db(engine: sa.Engine | None = None) -> sa.Engine:
    """Create all tables if missing, then apply in-place column migrations.
    Safe to call every run."""
    engine = engine or get_engine()
    metadata.create_all(engine)
    _migrate_recommendations(engine)
    _migrate_products(engine)
    if engine.dialect.name == "postgresql":
        apply_reporting_views(engine)
    return engine


def apply_reporting_views(engine: sa.Engine) -> None:
    """(Re)create the Postgres reporting views from reporting.sql.

    Metabase and the read-only chat path (query.py) read these human-labelled
    views, never the raw normalised tables. Postgres-only on purpose: the views
    use date_trunc / unnest / ::date casts SQLite lacks, and SQLite is only ever
    the local/test fallback that Metabase never connects to (init_db guards the
    call). Uses `create or replace view`, so it is idempotent and re-applied
    every run — the checked-in reporting.sql stays the source of truth.
    """
    sql = _REPORTING_SQL.read_text(encoding="utf-8")
    # exec_driver_sql passes the script straight to psycopg2 (which runs the
    # whole ;-separated batch in one call) without SQLAlchemy's colon parsing —
    # essential because the views use `::date` casts that text() would mistake
    # for bind parameters.
    with engine.begin() as conn:
        conn.exec_driver_sql(sql)


def _migrate_products(engine: sa.Engine) -> None:
    """Add columns to an existing products table that create_all() won't touch.

    `delisted` arrived with Fresho's Z999. Delisted group (Aug 2026); a database
    first written before then needs it added by hand. Idempotent: no-op once the
    column exists. Supported by SQLite 3.25+ and Postgres.
    """
    inspector = sa.inspect(engine)
    if not inspector.has_table("products"):
        return
    cols = {c["name"] for c in inspector.get_columns("products")}
    if "delisted" not in cols:
        with engine.begin() as conn:
            conn.execute(sa.text(
                "alter table products add column delisted boolean default false"))


def _migrate_recommendations(engine: sa.Engine) -> None:
    """Bring an existing recommendations table up to the current schema.

    create_all() only creates missing tables — it will not add columns to one
    that already exists, so a database written before the 4-step split (pool
    vs chosen products) needs these applied by hand. Both statements are
    supported by SQLite 3.25+ and Postgres, and both are no-ops once applied.
    """
    inspector = sa.inspect(engine)
    if not inspector.has_table("recommendations"):
        return
    cols = {c["name"] for c in inspector.get_columns("recommendations")}
    statements = []
    if "suggested_products" in cols and "product_pool" not in cols:
        statements.append("alter table recommendations "
                          "rename column suggested_products to product_pool")
    if "chosen_products" not in cols:
        statements.append("alter table recommendations "
                          "add column chosen_products json")
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(sa.text(stmt))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
