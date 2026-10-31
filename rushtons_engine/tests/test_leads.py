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


def _ev(i, kind, at, **kw):
    return dict(id=f"ll|{i}", sid="s|101", kind=kind, at=at, actor="Laurence", **kw)


def test_activity_log_moves_the_lead_and_survives_a_sheet_reload(conn, tmp_path):
    f = _write(tmp_path,
        '101,Ana,The Green Fork,ana@greenfork.co.uk,07700 1,Contact Page Form,'
        ',,"May 8, 2026",New,4,Restaurant,\n')
    leads.load_csv(conn, f)
    events = [
        _ev(3, "follow_up", "2026-10-21T09:00:00Z", due="2026-10-24"),
        _ev(1, "call", "2026-10-20T09:00:00Z", note="spoke to chef"),
        _ev(2, "stage", "2026-10-20T09:01:00Z", stage="Contacted"),
        _ev(4, "owner", "2026-10-21T10:00:00Z", owner="Laurence"),
        _ev(5, "stage", "2026-10-21T11:00:00Z", stage="Bogus"),     # skipped
        dict(id="ll|6", sid="s|999", kind="note", at="2026-10-21T11:00:00Z"),
    ]
    assert leads.apply_activity_log(conn, events) == 4
    row = _rows(conn)[0]
    assert row["status"] == "Contacted"
    assert row["owner"] == "Laurence"
    assert row["next_follow_up"] == dt.date(2026, 10, 24)
    acts = conn.execute(sa.select(db.lead_activities)
                        .order_by(db.lead_activities.c.occurred_at)).mappings().all()
    assert [a["kind"] for a in acts] == ["call", "stage", "follow_up", "owner"]
    assert acts[0]["note"] == "spoke to chef" and acts[0]["actor"] == "Laurence"
    # harvesting the same state again changes nothing
    assert leads.apply_activity_log(conn, events) == 0
    # the legacy sheet (still saying New) must not undo the dashboard's stage
    leads.load_csv(conn, f)
    assert _rows(conn)[0]["status"] == "Contacted"


def test_lost_records_reason_and_a_stage_move_clears_the_follow_up(conn, tmp_path):
    f = _write(tmp_path,
        '101,Ana,The Green Fork,ana@greenfork.co.uk,07700 1,Contact Page Form,'
        ',,"May 8, 2026",New,4,Restaurant,\n')
    leads.load_csv(conn, f)
    leads.apply_activity_log(conn, [
        _ev(1, "note", "2026-10-20T09:00:00Z", note="call back", due="2026-10-30"),
        _ev(2, "stage", "2026-10-22T09:00:00Z", stage="Discarded",
            reason="Outside delivery area"),
    ])
    row = _rows(conn)[0]
    assert row["status"] == "Discarded"
    assert row["lost_reason"] == "Outside delivery area"
    assert row["next_follow_up"] is None


def test_migrate_adds_intake_columns_to_an_old_leads_table(tmp_path):
    eng = sa.create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with eng.begin() as c:
        c.execute(sa.text("create table leads (id integer primary key, "
                          "source_id text unique not null, status text)"))
        c.execute(sa.text("insert into leads (source_id, status) "
                          "values ('s|1', 'New')"))
    db._migrate_leads(eng)
    cols = {c["name"] for c in sa.inspect(eng).get_columns("leads")}
    assert set(db._LEAD_INTAKE_COLS) <= cols
    with eng.connect() as c:
        assert c.execute(sa.text("select intake from leads")).scalar() == "sheet"
    db._migrate_leads(eng)          # idempotent


def test_team_can_override_the_ai_verdict(conn, tmp_path):
    f = _write(tmp_path,
        '101,Ana,The Green Fork,ana@greenfork.co.uk,07700 1,Contact Page Form,'
        ',,"May 8, 2026",New,4,Restaurant,\n')
    leads.load_csv(conn, f)
    conn.execute(db.leads.update().values(verdict="Reject", verdict_source="ai"))
    assert leads.apply_activity_log(conn, [
        _ev(1, "verdict", "2026-10-31T09:00:00Z", note="Pursue"),
        _ev(2, "verdict", "2026-10-31T09:01:00Z", note="Maybe"),   # not a verdict
    ]) == 1
    row = _rows(conn)[0]
    assert row["verdict"] == "Pursue" and row["verdict_source"] == "team"
