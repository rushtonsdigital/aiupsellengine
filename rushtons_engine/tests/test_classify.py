import datetime as dt

import sqlalchemy as sa

import classify
import db
from conftest import add_customer, add_product, weekly_orders

AS_OF = dt.date(2026, 6, 30)


def _setup_product(conn):
    add_product(conn, "P1")


def _status_of(conn, code):
    return conn.execute(sa.select(db.customers.c.activity_status)
                        .where(db.customers.c.customer_code == code)).scalar()


def test_status_rules(conn):
    _setup_product(conn)
    # regular cadence, ordered yesterday -> active_regular
    add_customer(conn, "REG")
    weekly_orders(conn, "REG", ["P1"], AS_OF - dt.timedelta(days=29), weeks=5)
    # regular cadence that stopped 10 days ago -> lapsed
    add_customer(conn, "LAP")
    weekly_orders(conn, "LAP", ["P1"], AS_OF - dt.timedelta(days=45), weeks=6)
    # sporadic buyer, 2 orders, last one 20 days ago -> active_adhoc
    add_customer(conn, "ADH")
    weekly_orders(conn, "ADH", ["P1"], AS_OF - dt.timedelta(days=27), weeks=2)
    # never ordered, Fresho-active -> long_lapsed
    add_customer(conn, "GONE")
    # never ordered, Fresho-inactive -> temporarily_closed
    add_customer(conn, "SHUT", active=False)
    # internal account -> excluded
    add_customer(conn, "INT", name="Staff Account - Rushton's Greengrocers")
    weekly_orders(conn, "INT", ["P1"], AS_OF - dt.timedelta(days=7), weeks=1)

    classify.classify_all(conn, AS_OF)

    assert _status_of(conn, "REG") == "active_regular"
    assert _status_of(conn, "LAP") == "lapsed"
    assert _status_of(conn, "ADH") == "active_adhoc"
    assert _status_of(conn, "GONE") == "long_lapsed"
    assert _status_of(conn, "SHUT") == "temporarily_closed"
    assert _status_of(conn, "INT") == "excluded"


def test_size_bands_are_percentile_on_lines(conn):
    _setup_product(conn)
    # ten customers with strictly decreasing volume
    for i in range(10):
        code = f"C{i:02d}"
        add_customer(conn, code)
        for _ in range(10 - i):
            weekly_orders(conn, code, ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
    classify.classify_all(conn, AS_OF)
    bands = {r.customer_code: r.size_band
             for r in conn.execute(sa.select(db.customers)).fetchall()}
    assert bands["C00"] == "gold"          # top 20% of 10 = 2 golds
    assert bands["C01"] == "gold"
    assert bands["C02"] == "silver"        # next 50% = 5 silvers
    assert bands["C06"] == "silver"
    assert bands["C07"] == "bronze"        # bottom 30%
    assert bands["C09"] == "bronze"


def test_prestige_rules(conn, monkeypatch):
    monkeypatch.setattr(classify, "load_prestige_seed",
                        lambda path=None: {"SEEDED": "VIP"})
    _setup_product(conn)
    add_customer(conn, "SEEDED")
    add_customer(conn, "PLAIN")
    add_customer(conn, "INT", name="Waste Account - Rushton's Greengrocers")
    for code in ("SEEDED", "PLAIN", "INT"):
        weekly_orders(conn, code, ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
    classify.classify_all(conn, AS_OF)
    prestige = {r.customer_code: r.prestige
                for r in conn.execute(sa.select(db.customers)).fetchall()}
    assert prestige["SEEDED"] == "VIP"     # hand-curated seed wins
    assert prestige["INT"] == "Excluded"
    assert prestige["PLAIN"] in ("Standard", "VIP")  # VIP only if banded gold


def test_excluded_customer_codes_are_excluded(conn, monkeypatch):
    """Hand-listed trade counterparties (wholesalers) get prestige='Excluded'
    even though they buy real produce and look like normal accounts."""
    monkeypatch.setattr("config.EXCLUDED_CUSTOMER_CODES", {"WHOLESALE"})
    _setup_product(conn)
    add_customer(conn, "WHOLESALE")
    weekly_orders(conn, "WHOLESALE", ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
    classify.classify_all(conn, AS_OF)
    p = conn.execute(sa.select(db.customers.c.prestige)
                     .where(db.customers.c.customer_code == "WHOLESALE")).scalar()
    assert p == "Excluded"


def test_market_customer_stage_is_excluded(conn):
    """Accounts tagged with a trade account-stage ('market customer') are
    excluded systematically, without needing a per-code entry."""
    _setup_product(conn)
    add_customer(conn, "TRADE")
    conn.execute(db.customers.update()
                 .where(db.customers.c.customer_code == "TRADE")
                 .values(account_stage="market customer"))
    weekly_orders(conn, "TRADE", ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
    classify.classify_all(conn, AS_OF)
    p = conn.execute(sa.select(db.customers.c.prestige)
                     .where(db.customers.c.customer_code == "TRADE")).scalar()
    assert p == "Excluded"


def test_venue_normalisation():
    assert classify.normalize_venue_type("restaurant") == "Restaurants"
    assert classify.normalize_venue_type("members club") == "Members Club"
    assert classify.normalize_venue_type(None) == "Unknown"
    assert classify.normalize_venue_type("food truck") == "Food Truck"  # passthrough


def _month_value(conn, code, month, net):
    """One complete month of £ for an account (as ingest.ingest_values_file stores it)."""
    import calendar
    end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
    conn.execute(db.customer_month_values.insert().values(
        customer_code=code, month=month, period_end=end, net_sales=net,
        gross_sales=net, credits=0, cogs=0, gross_profit=0))


def test_size_bands_rank_on_recent_value_and_skip_excluded(conn, monkeypatch):
    """With £ loaded, bands follow the last 3 complete months of sales (not
    lifetime lines); rule-excluded accounts don't take gold slots but still get
    a band from the cut-offs; TIER_OVERRIDES win."""
    import config
    _setup_product(conn)
    for i in range(10):
        code = f"C{i:02d}"
        add_customer(conn, code)
        weekly_orders(conn, code, ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
        # value runs opposite to code order: C09 is the biggest spender
        for m in (dt.date(2026, 4, 1), dt.date(2026, 5, 1), dt.date(2026, 6, 1)):
            _month_value(conn, code, m, 1000 * (i + 1))
    _month_value(conn, "C00", dt.date(2026, 1, 1), 10**7)   # outside the 3-month window
    add_customer(conn, "URBAN")                              # excluded group, big spender
    conn.execute(db.customers.update().where(db.customers.c.customer_code == "URBAN")
                 .values(group_affiliation="Urban Pubs & Bars"))
    weekly_orders(conn, "URBAN", ["P1"], AS_OF - dt.timedelta(days=6), weeks=1)
    for m in (dt.date(2026, 4, 1), dt.date(2026, 5, 1), dt.date(2026, 6, 1)):
        _month_value(conn, "URBAN", m, 99999)
    bands_of = lambda: {r.customer_code: r.size_band
                        for r in conn.execute(sa.select(db.customers)).fetchall()}

    classify.classify_all(conn, AS_OF)
    bands = bands_of()
    assert bands["C09"] == "gold" and bands["C08"] == "gold"   # top 2 of 10 by £
    assert bands["C00"] == "bronze"          # big January is outside the window
    assert bands["URBAN"] == "gold"          # banded by cut-off, not ranked -
    assert bands["C07"] == "silver"          # so it didn't push C08 out of gold

    monkeypatch.setattr(config, "TIER_OVERRIDES", {"C08": "silver"})
    classify.classify_all(conn, AS_OF)
    assert bands_of()["C08"] == "silver"     # a confirmed hand rating wins
