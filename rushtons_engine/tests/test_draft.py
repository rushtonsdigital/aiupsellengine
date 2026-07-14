import datetime as dt
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
        "num_orders": 5, "num_skus": 2, "size_band": "Silver",
        "gap_categories": ["Dairy and Chilled"],
        "product_pool": {"Dairy and Chilled": [
            {"code": "DAI-1", "name": "Burrata", "product_group": "D010. Dairy",
             "buyers_14d": 9},
            {"code": "DAI-2", "name": "Clarence Court eggs",
             "product_group": "D010. Dairy", "buyers_14d": 4}]},
        "rationale": "test",
    }


def _write_drafts(tmp_path, payload):
    path = tmp_path / "drafts.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


MESSAGES = {"announcement": "Hey chef...", "followup": "No rush...",
            "postbox": "Box landed ok?"}


def _entry(**overrides):
    entry = {
        "customer_review": "Neighbourhood bistro, British menu.",
        "chosen_products": [
            {"code": "DAI-2", "name": "Clarence Court eggs",
             "category": "Dairy and Chilled", "why": "richer yolk for the brunch menu"}],
        "messages": dict(MESSAGES),
    }
    entry.update(overrides)
    return entry


def test_brief_carries_pool_and_apply_persists_picks(conn, tmp_path):
    recs = [_rec("C ONE")]
    conn.execute(db.recommendations.insert().values(
        id=1, run_date=dt.date(2026, 6, 30), customer_code="C ONE", rank=1))

    brief_path = draft.export_brief(recs, "2026-06-30", out_dir=tmp_path)
    brief = json.loads(brief_path.read_text(encoding="utf-8"))
    account = brief["accounts"][0]
    assert account["account_manager"] == "Ben (Rushtons)"
    assert account["product_pool"]["Dairy and Chilled"][0]["code"] == "DAI-1"
    assert "rushtons-comms" in brief["instructions"]

    drafts = _write_drafts(tmp_path, {"C ONE": _entry()})
    assert draft.apply_drafts(conn, drafts, recs) == 3

    rows = conn.execute(sa.select(db.comms)).fetchall()
    assert {r.stage for r in rows} == set(draft.STAGES)
    assert recs[0]["drafts"]["announcement"] == "Hey chef..."
    # the drafter's pick is persisted alongside the pool, not instead of it
    chosen = conn.execute(sa.select(db.recommendations.c.chosen_products)
                          .where(db.recommendations.c.id == 1)).scalar()
    assert [p["code"] for p in chosen] == ["DAI-2"]

    # idempotent re-apply replaces, not duplicates
    draft.apply_drafts(conn, drafts, recs)
    assert conn.execute(sa.select(sa.func.count()).select_from(db.comms)).scalar() == 3


def test_apply_rejects_product_outside_the_pool(conn, tmp_path):
    """The core step-3 guardrail: the drafter chooses from the pool, never invents."""
    bad = _entry(chosen_products=[
        {"code": "GHOST-1", "name": "Wagyu", "category": "Dairy and Chilled",
         "why": "sounds premium"}])
    drafts = _write_drafts(tmp_path, {"C ONE": bad})
    with pytest.raises(ValueError, match="not in this account's pool"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_pick_without_justification(conn, tmp_path):
    bad = _entry(chosen_products=[
        {"code": "DAI-1", "name": "Burrata", "category": "Dairy and Chilled",
         "why": "  "}])
    drafts = _write_drafts(tmp_path, {"C ONE": bad})
    with pytest.raises(ValueError, match="no `why`"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_no_products_chosen(conn, tmp_path):
    drafts = _write_drafts(tmp_path, {"C ONE": _entry(chosen_products=[])})
    with pytest.raises(ValueError, match="no chosen_products"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_too_many_products(conn, tmp_path):
    import config
    picks = [{"code": "DAI-1", "name": "Burrata", "category": "Dairy and Chilled",
              "why": "ok"}] * (config.MAX_CHOSEN_PRODUCTS + 1)
    drafts = _write_drafts(tmp_path, {"C ONE": _entry(chosen_products=picks)})
    with pytest.raises(ValueError, match="max is"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_legacy_bare_message_format(conn, tmp_path):
    """Pre-4-step drafts had no product picks — fail loudly rather than half-apply."""
    drafts = _write_drafts(tmp_path, {"C ONE": dict(MESSAGES)})
    with pytest.raises(ValueError, match="step-3/step-4 draft format"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_accounts_outside_selection(conn, tmp_path):
    drafts = _write_drafts(tmp_path, {"C ONE": _entry(), "C SNEAKY": _entry()})
    with pytest.raises(ValueError, match="outside the locked selection"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])


def test_apply_rejects_missing_accounts(conn, tmp_path):
    drafts = _write_drafts(tmp_path, {"C ONE": _entry()})
    with pytest.raises(ValueError, match="missing"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE", 1), _rec("C TWO", 2)])


def test_apply_rejects_empty_stage(conn, tmp_path):
    bad = _entry(messages=dict(MESSAGES, followup="   "))
    drafts = _write_drafts(tmp_path, {"C ONE": bad})
    with pytest.raises(ValueError, match="empty or missing"):
        draft.apply_drafts(conn, drafts, [_rec("C ONE")])
