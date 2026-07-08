import json

import pytest
import sqlalchemy as sa

import db
import draft


def _rec(code, rec_id=1):
    return {
        "recommendation_id": rec_id, "rank": 1, "customer_code": code,
        "customer_name": f"Venue {code}", "venue_type": "Restaurants",
        "sales_rep": "Ben (Rushtons)", "bought_categories": ["Vegetables"],
        "num_orders": 5, "num_skus": 2, "gap_categories": ["Dairy and Chilled"],
        "suggested_products": {"Dairy and Chilled": [
            {"code": "DAI-1", "name": "Burrata", "buyers_14d": 9}]},
        "rationale": "test",
    }


def _write_drafts(tmp_path, payload):
    path = tmp_path / "drafts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


GOOD = {"announcement": "Hey chef...", "followup": "No rush...", "postbox": "Box landed ok?"}


def test_brief_roundtrip_and_apply(conn, tmp_path):
    recs = [_rec("C ONE")]
    brief_path = draft.export_brief(recs, "2026-06-30", out_dir=tmp_path)
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    assert brief["accounts"][0]["account_manager"] == "Ben (Rushtons)"
    assert "rushtons-comms" in brief["instructions"]

    drafts = _write_drafts(tmp_path, {"C ONE": GOOD})
    assert draft.apply_drafts(conn, drafts, recs) == 3
    rows = conn.execute(sa.select(db.comms)).fetchall()
    assert {r.stage for r in rows} == set(draft.STAGES)
    assert recs[0]["drafts"]["announcement"] == "Hey chef..."
    # idempotent re-apply replaces, not duplicates
    draft.apply_drafts(conn, drafts, recs)
    assert conn.execute(sa.select(sa.func.count()).select_from(db.comms)).scalar() == 3


def test_apply_rejects_accounts_outside_selection(conn, tmp_path):
    drafts = _write_drafts(tmp_path, {"C ONE": GOOD, "C SNEAKY": GOOD})
    with pytest.raises(ValueError, match="outside the locked selection"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_missing_accounts(conn, tmp_path):
    drafts = _write_drafts(tmp_path, {"C ONE": GOOD})
    with pytest.raises(ValueError, match="missing"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE", 1), _rec("C TWO", 2)])


def test_apply_rejects_empty_stage(conn, tmp_path):
    bad = dict(GOOD, followup="   ")
    drafts = _write_drafts(tmp_path, {"C ONE": bad})
    with pytest.raises(ValueError, match="empty or missing"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])
