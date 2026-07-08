import datetime as dt

import pytest
import sqlalchemy as sa

import db


@pytest.fixture()
def conn():
    """Fresh in-memory database per test."""
    engine = sa.create_engine("sqlite://")
    db.metadata.create_all(engine)
    with engine.begin() as connection:
        yield connection


def add_customer(conn, code, name=None, venue_type="Restaurants", active=True,
                 prestige="Standard", activity_status=None, sales_rep="Ben (Rushtons)"):
    conn.execute(db.customers.insert().values(
        customer_code=code, customer_name=name or f"Venue {code}",
        venue_type=venue_type, active=active, prestige=prestige,
        activity_status=activity_status, sales_rep=sales_rep,
        updated_at=db.now_utc()))


def add_product(conn, code, name=None, category="Vegetables",
                raw_group="S010. Vegetables - SPLIT", out_of_season=False):
    conn.execute(db.products.insert().values(
        product_code=code, product_name=name or f"Product {code}",
        raw_product_group=raw_group, category=category,
        out_of_season=out_of_season, updated_at=db.now_utc()))


_order_seq = {"n": 90000000}


def add_order(conn, customer, product, delivery_date, qty=1.0,
              order_number=None, state="Invoiced", source_file="test.csv"):
    if order_number is None:
        _order_seq["n"] += 1
        order_number = _order_seq["n"]
    conn.execute(db.orders.insert().values(
        order_number=order_number, customer_code=customer, product_code=product,
        delivery_date=delivery_date, quantity=qty, qty_type="Each",
        order_state=state, delivery_run="R01", source_file=source_file,
        ingested_at=db.now_utc()))
    return order_number


def weekly_orders(conn, customer, products, start, weeks, per_week=1):
    """Regular cadence helper: `per_week` orders each week for `weeks` weeks,
    every order containing all `products`."""
    for w in range(weeks):
        for i in range(per_week):
            day = start + dt.timedelta(weeks=w, days=i)
            n = add_order(conn, customer, products[0], day)
            for p in products[1:]:
                add_order(conn, customer, p, day, order_number=n)
