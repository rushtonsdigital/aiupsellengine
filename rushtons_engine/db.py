"""Database layer: one SQLAlchemy Core metadata shared by Supabase Postgres and
local SQLite. schema.sql is the canonical Postgres DDL; this metadata mirrors it
so tests and pre-Supabase runs work identically.
"""

from datetime import datetime, timezone

import sqlalchemy as sa

import config

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
    sa.Column("suggested_products", sa.JSON),
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

_engine = None


def get_engine(url: str | None = None) -> sa.Engine:
    global _engine
    if _engine is None or url is not None:
        _engine = sa.create_engine(url or config.DATABASE_URL)
    return _engine


def init_db(engine: sa.Engine | None = None) -> sa.Engine:
    """Create all tables if missing. Safe to call every run."""
    engine = engine or get_engine()
    metadata.create_all(engine)
    return engine


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
