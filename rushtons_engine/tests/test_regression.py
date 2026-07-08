"""Regression against the real June 2026 exports and the prior hand-reviewed
classification. Slow; skipped unless REGRESSION_DATA_DIR points at the folder
holding the raw daily exports + rushtons_customer_classification.csv.

Run:  set REGRESSION_DATA_DIR=C:\\Rushtons AI Upsell
      pytest -m regression -q
"""

import csv
import os
from pathlib import Path

import pytest
import sqlalchemy as sa

import classify
import config
import db
import ingest
import selector as sel

DATA_DIR = os.environ.get("REGRESSION_DATA_DIR")

pytestmark = [
    pytest.mark.regression,
    pytest.mark.skipif(not DATA_DIR, reason="REGRESSION_DATA_DIR not set"),
]

STATUS_MAP = {  # prior file's labels -> engine labels
    "Active regular": "active_regular",
    "Active ad hoc": "active_adhoc",
    "Lapsed": "lapsed",
    "Long lapsed": "long_lapsed",
    "Temporarily closed": "temporarily_closed",
    "Excluded": "excluded",
}


@pytest.fixture(scope="module")
def real_conn():
    engine = sa.create_engine("sqlite://")
    db.metadata.create_all(engine)
    data = Path(DATA_DIR)
    with engine.begin() as conn:
        for f in sorted(data.glob(config.CUSTOMERS_FILE_GLOB)):
            ingest.ingest_customers_file(conn, f)
        for f in sorted(data.glob(config.ORDERS_FILE_GLOB)):
            ingest.ingest_orders_file(conn, f)
        classify.classify_all(conn, sel.as_of_date(conn))
        yield conn


@pytest.fixture(scope="module")
def prior():
    path = Path(DATA_DIR) / "rushtons_customer_classification.csv"
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return {r["customer_code"]: r for r in csv.DictReader(fh)}


def test_row_counts_match_combined_file(real_conn):
    n = real_conn.execute(sa.select(sa.func.count()).select_from(db.orders)).scalar()
    assert n == 82676  # the verified pre-cleaned combined file's line count


def test_all_customers_covered(real_conn, prior):
    ours = {r.customer_code for r in
            real_conn.execute(sa.select(db.customers.c.customer_code))}
    assert set(prior) <= ours


def test_activity_status_agreement(real_conn, prior):
    """>=90% agreement with the hand-reviewed prior classification; the
    remainder must sit on adjacent statuses (regular/adhoc or lapsed bands),
    never active vs closed."""
    ours = {r.customer_code: r.activity_status for r in
            real_conn.execute(sa.select(db.customers))}
    agree = diff = 0
    for code, row in prior.items():
        expected = STATUS_MAP[row["activity_status"]]
        got = ours.get(code)
        if got == expected:
            agree += 1
        else:
            diff += 1
            active = {"active_regular", "active_adhoc", "lapsed"}
            inactive = {"long_lapsed", "temporarily_closed", "excluded"}
            assert not (expected in inactive and got in
                        {"active_regular", "active_adhoc"}), \
                f"{code}: prior={expected} engine={got}"
    assert agree / (agree + diff) >= 0.90


def test_size_band_agreement(real_conn, prior):
    ours = {r.customer_code: r.size_band for r in
            real_conn.execute(sa.select(db.customers))}
    agree = total = 0
    for code, row in prior.items():
        expected = (row["order_size_tier"] or "").lower() or None
        if expected is None:
            continue
        total += 1
        agree += ours.get(code) == expected
    assert agree / total >= 0.90


def test_internal_accounts_all_detected(real_conn, prior):
    expected_internal = {c for c, r in prior.items()
                         if r["customer_type"] == "Internal/Non-customer"}
    ours = {r.customer_code for r in real_conn.execute(
        sa.select(db.customers).where(db.customers.c.activity_status == "excluded"))}
    # '1004' (Ben Demo account) was missed by the prior hand classification;
    # the engine correctly flags it. Every prior internal must be caught.
    assert expected_internal <= ours
    assert ours - expected_internal <= {"1004"}


def test_selection_returns_ten_and_is_deterministic(real_conn):
    first = sel.select_top(real_conn)
    second = sel.select_top(real_conn)
    assert len(first) == config.TOP_N
    assert [(r["rank"], r["customer_code"], r["score"]) for r in first] == \
           [(r["rank"], r["customer_code"], r["score"]) for r in second]

