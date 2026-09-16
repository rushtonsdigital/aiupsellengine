"""interactions.py is the CRM ledger: every loader must be idempotent, and
ordered_again derivation must fire once per outreach, not per run."""

import datetime as dt

import sqlalchemy as sa

import db
import interactions
from conftest import add_customer, add_product, add_order

AS_OF = dt.date(2026, 8, 24)


def _rows(conn):
    return conn.execute(sa.select(db.interactions)
                        .order_by(db.interactions.c.occurred_at)).mappings().all()


def _state(*events):
    return {"assign": {}, "actions": {}, "log": list(events)}


def _ev(id, code, kind, at, note=""):
    return {"id": id, "code": code, "kind": kind, "at": at, "note": note}


def test_harvest_is_idempotent(conn):
    add_customer(conn, "C1")
    state = _state(
        _ev("l|C1|a", "C1", "whatsapp_sent", "2026-08-20T10:00:00Z"),
        _ev("l|C1|b", "C1", "note", "2026-08-21T09:00:00Z", note="spoke to chef"),
    )
    assert interactions.harvest_state(conn, state) == 2
    assert interactions.harvest_state(conn, state) == 0   # re-run: no dupes
    rows = _rows(conn)
    assert len(rows) == 2
    assert rows[1]["kind"] == "note"
    assert rows[1]["summary"] == "spoke to chef"
    assert rows[0]["source"] == "dashboard"


def test_harvest_skips_unknown_kind_and_customer(conn):
    add_customer(conn, "C1")
    state = _state(
        _ev("l|C1|a", "C1", "carrier_pigeon", "2026-08-20T10:00:00Z"),
        _ev("l|ZZ|b", "ZZ", "note", "2026-08-20T10:00:00Z", note="ghost"),
        _ev("l|C1|c", "C1", "email_sent", "2026-08-20T11:00:00Z"),
    )
    assert interactions.harvest_state(conn, state) == 1
    assert _rows(conn)[0]["kind"] == "email_sent"


def test_email_events_idempotent_by_message_id(conn):
    add_customer(conn, "C1")
    events = [
        {"customer_code": "C1", "direction": "in", "at": "2026-08-19T08:00:00Z",
         "subject": "Order query", "counterparty": "chef@venue.co.uk",
         "message_id": "<m1@x>"},
        {"customer_code": "C1", "direction": "out", "at": "2026-08-19T09:00:00Z",
         "subject": "Re: Order query", "counterparty": "chef@venue.co.uk",
         "message_id": "<m2@x>"},
        {"customer_code": "C1", "direction": "sideways", "at": "2026-08-19T10:00:00Z",
         "subject": "bad", "counterparty": "", "message_id": "<m3@x>"},
    ]
    assert interactions.load_email_events(conn, events) == 2
    assert interactions.load_email_events(conn, events) == 0
    kinds = [r["kind"] for r in _rows(conn)]
    assert kinds == ["email_in", "email_out"]
    assert "chef@venue.co.uk" in _rows(conn)[0]["summary"]


def test_contacts_csv_upsert(conn, tmp_path):
    add_customer(conn, "C1")
    f = tmp_path / "contacts.csv"
    f.write_text("customer_code,name,label,email,phone\n"
                 "C1,Ana,chef,Chef@Venue.CO.UK,\n"
                 "C1,Ana,kitchen,chef@venue.co.uk,+44 7700 900001\n"  # re-labelled
                 "C1,Bo,orders,,+44 7700 900002\n"    # phone-only contact
                 "ZZ,,x,ghost@nowhere.com,\n",        # unknown code skipped
                 encoding="utf-8")
    interactions.load_contacts_csv(conn, f)
    rows = conn.execute(sa.select(db.customer_contacts)
                        .order_by(db.customer_contacts.c.id)).mappings().all()
    assert len(rows) == 2
    assert rows[0]["email"] == "chef@venue.co.uk"
    assert rows[0]["label"] == "kitchen"          # second row won the upsert
    assert rows[0]["phone"] == "+44 7700 900001"
    assert rows[1]["email"] is None
    assert rows[1]["phone"] == "+44 7700 900002"


def test_harvest_contacts_from_dashboard_state(conn):
    add_customer(conn, "C1")
    state = {"contacts": [
        {"code": "C1", "name": "Ana", "label": "chef",
         "email": "ANA@venue.co.uk", "phone": ""},
        {"code": "C1", "name": "Ana", "label": "head chef",
         "email": "ana@venue.co.uk", "phone": "07700 900003"},  # upsert
        {"code": "ZZ", "name": "Ghost", "email": "g@x.com"},    # unknown skipped
        {"code": "C1", "name": "NoWay", "email": "", "phone": ""},  # invalid
    ]}
    interactions.harvest_contacts(conn, state)
    interactions.harvest_contacts(conn, state)   # idempotent re-run
    rows = conn.execute(sa.select(db.customer_contacts)).mappings().all()
    assert len(rows) == 1
    assert rows[0]["label"] == "head chef"
    assert rows[0]["phone"] == "07700 900003"


def test_derive_order_outcomes_once_per_outreach(conn):
    add_customer(conn, "C1")
    add_product(conn, "P1")
    interactions.harvest_state(conn, _state(
        _ev("l|C1|a", "C1", "whatsapp_sent", "2026-08-10T10:00:00Z")))
    # no order yet -> nothing derived
    assert interactions.derive_order_outcomes(conn) == 0
    # order lands after the outreach -> one outcome, once
    add_order(conn, "C1", "P1", dt.date(2026, 8, 14))
    assert interactions.derive_order_outcomes(conn) == 1
    assert interactions.derive_order_outcomes(conn) == 0
    outcome = [r for r in _rows(conn) if r["kind"] == "ordered_again"]
    assert len(outcome) == 1
    assert outcome[0]["source"] == "system"
    # a NEW outreach after that outcome starts a fresh cycle
    interactions.harvest_state(conn, _state(
        _ev("l|C1|b", "C1", "email_sent", "2026-08-18T10:00:00Z")))
    assert interactions.derive_order_outcomes(conn) == 0   # no order after 18th yet
    add_order(conn, "C1", "P1", dt.date(2026, 8, 21))
    assert interactions.derive_order_outcomes(conn) == 1


def test_timeline_newest_first_and_capped(conn):
    add_customer(conn, "C1")
    events = [_ev(f"l|C1|{i}", "C1", "note", f"2026-08-{10+i:02d}T10:00:00Z",
                  note=f"n{i}") for i in range(10)]
    interactions.harvest_state(conn, _state(*events))
    tl = interactions.timeline(conn, ["C1"], per_account=8)
    assert len(tl["C1"]) == 8
    assert tl["C1"][0]["summary"] == "n9"     # newest first
    assert tl["C1"][-1]["summary"] == "n2"    # capped at 8
