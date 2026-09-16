"""leads.py mirrors Hannah's sheet and links Acquired leads to Fresho accounts.
Idempotency and match preservation are the load-bearing behaviours."""

import datetime as dt

import sqlalchemy as sa

import db
import leads
from conftest import add_customer

SHEET_HEADER = ("SessionID,Name,Business,Email,Telephone,Lead Source,"
                "Analysis (AI/Human),Notes,Date,Status,Rating,Type,Details\n")


def _write(tmp_path, body):
    f = tmp_path / "sheet.csv"
    f.write_text(SHEET_HEADER + body, encoding="utf-8")
    return f


def _rows(conn):
    return conn.execute(sa.select(db.leads)).mappings().all()


def test_load_normalises_and_flags(conn, tmp_path):
    f = _write(tmp_path,
        '101,Ana,The Green Fork,ana@greenfork.co.uk,07700 1,Contact Page Form,'
        'AI summary,,"May 8, 2026",New,Good,Restaurant,\n'
        '102,Jose,GoodBytes.network Ltd,goodbytesnetwork@gmail.com,077,'
        'Contact Page Form,,,"July 7, 2026",New,1,Other,Test\n'
        ',Sam,,sam@gmail.com,,Sample Box,,,"January 2, 2026 11:57 AM",Discarded,,,\n')
    assert leads.load_csv(conn, f) == 3
    rows = {r["session_id"] or r["email"]: r for r in _rows(conn)}
    assert rows["101"]["rating"] == 4                    # 'Good' -> 4
    assert rows["101"]["submitted_at"] == dt.date(2026, 5, 8)
    assert rows["101"]["excluded_reason"] is None
    assert rows["102"]["excluded_reason"] == "test row"
    assert rows["sam@gmail.com"]["excluded_reason"] == "personal sample-box request"
    assert rows["sam@gmail.com"]["submitted_at"] == dt.date(2026, 1, 2)


def test_reload_preserves_matches(conn, tmp_path):
    add_customer(conn, "C1", name="The Green Fork")
    f = _write(tmp_path,
        '101,Ana,The Green Fork,ana@greenfork.co.uk,07700 1,Contact Page Form,'
        ',,"May 8, 2026",Acquired,5,Restaurant,\n')
    leads.load_csv(conn, f)
    assert leads.auto_match(conn) == 1
    row = _rows(conn)[0]
    assert row["matched_customer_code"] == "C1"
    assert row["match_method"] == "auto_name"
    # sheet reloads weekly: the match must survive
    leads.load_csv(conn, f)
    row = _rows(conn)[0]
    assert row["matched_customer_code"] == "C1"
    assert leads.auto_match(conn) == 0                   # nothing left to match


def test_auto_match_via_contact_beats_name(conn, tmp_path):
    add_customer(conn, "C1", name="Totally Different Name")
    conn.execute(db.customer_contacts.insert().values(
        customer_code="C1", email="chef@somewhere.co.uk", phone=None))
    f = _write(tmp_path,
        '201,Ana,Some Brand New Venue,chef@somewhere.co.uk,,Email,'
        ',,"June 1, 2026",Acquired,4,Restaurant,\n')
    leads.load_csv(conn, f)
    assert leads.auto_match(conn) == 1
    row = _rows(conn)[0]
    assert row["matched_customer_code"] == "C1"
    assert row["match_method"] == "auto_contact"


def test_only_acquired_and_included_leads_match(conn, tmp_path):
    add_customer(conn, "C1", name="The Green Fork")
    f = _write(tmp_path,
        '301,Ana,The Green Fork,a@x.co,,Email,,,"June 1, 2026",New,3,Restaurant,\n'
        '302,Bo,The Green Fork,b@x.co,,Email,,,"June 1, 2026",Acquired,3,Restaurant,Test\n')
    leads.load_csv(conn, f)
    assert leads.auto_match(conn) == 0    # 301 not Acquired; 302 excluded (test)


def test_manual_match_and_clear(conn, tmp_path):
    add_customer(conn, "C1")
    f = _write(tmp_path,
        '401,Ana,Mystery Venue,a@x.co,,Email,,,"June 1, 2026",Acquired,2,,\n')
    leads.load_csv(conn, f)
    assert leads.apply_manual_matches(conn, {"s|401": "C1"}) == 1
    assert _rows(conn)[0]["match_method"] == "manual"
    assert leads.apply_manual_matches(conn, {"s|401": ""}) == 1
    assert _rows(conn)[0]["matched_customer_code"] is None
    assert leads.apply_manual_matches(conn, {"s|401": "NOPE"}) == 0
