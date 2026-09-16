"""Marketing leads: a weekly mirror of the Google Sheet the n8n flow feeds.

Division of truth (Hannah's brief, 2026-08-06):
- The SHEET stays the source of truth for lead fields — n8n writes it, the
  team edits Status there (the only hand-touched field). We never write back.
- THIS table adds what the sheet cannot: the link to the Fresho account once a
  lead is Acquired, and the ongoing order value that flows from it. Value is
  VOLUME (orders, order lines) — there is still no price data anywhere, so the
  "£ value" in the brief needs a price feed before it can be pounds.

Everything is idempotent: reloading the sheet upserts by source_id (SessionID
when present, else a hash of email+date) and PRESERVES existing match fields.

Cleanup rules (the brief's "test/personal rows skew reporting"):
  excluded_reason is set for test rows (the word 'test' in name/details/notes),
  internal addresses, and personal sample-box asks with no business name.
  Excluded leads stay visible but drop out of the KPIs.

Rating normalisation: 1-5 kept; 'Good' -> 4 (documented mapping); else null.

CLI (venv python, from rushtons_engine/):
  python leads.py sync <sheet.csv>    load + auto-match in one go
"""

import argparse
import hashlib
import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import csv

import sqlalchemy as sa

import db

log = logging.getLogger(__name__)

NAME_MATCH_THRESHOLD = 0.84
_STOP_TOKENS = {"ltd", "limited", "the", "co", "company", "uk", "london"}
_TEST_RE = re.compile(r"\btest\b", re.IGNORECASE)
_INTERNAL_EMAIL = re.compile(
    r"(goodbytesnetwork|goodbytes\.network|goodagents)", re.IGNORECASE)

SHEET_COLUMNS = {
    "SessionID": "session_id", "Name": "name", "Business": "business",
    "Email": "email", "Telephone": "phone", "Lead Source": "lead_source",
    "Analysis (AI/Human)": "analysis", "Notes": "notes", "Date": "date_raw",
    "Status": "status", "Rating": "rating_raw", "Type": "type",
    "Details": "details",
}


def _parse_date(value):
    s = (value or "").strip()
    for fmt in ("%B %d, %Y %I:%M %p", "%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def normalize_rating(raw):
    s = (raw or "").strip().lower()
    if s in ("1", "2", "3", "4", "5"):
        return int(s)
    if s == "good":        # legacy free-text value; documented mapping
        return 4
    return None


def exclusion_reason(row) -> str | None:
    blob = " ".join(str(row.get(k) or "") for k in ("name", "details", "notes"))
    if _TEST_RE.search(blob):
        return "test row"
    if _INTERNAL_EMAIL.search(row.get("email") or ""):
        return "internal email"
    if not (row.get("business") or "").strip() \
            and "sample box" in (row.get("lead_source") or "").lower():
        return "personal sample-box request"
    return None


def _source_id(row) -> str:
    sid = (row.get("session_id") or "").strip()
    if sid:
        return f"s|{sid}"
    key = f"{(row.get('email') or '').lower()}|{row.get('date_raw') or ''}"
    return "h|" + hashlib.sha1(key.encode()).hexdigest()[:16]


def _norm_name(s: str) -> str:
    tokens = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    return " ".join(t for t in tokens if t not in _STOP_TOKENS)


def load_csv(conn, path: Path) -> int:
    """Mirror the sheet into the leads table. Idempotent by source_id;
    preserves match fields across reloads (the sheet knows nothing of them)."""
    existing = {r.source_id: r for r in conn.execute(sa.select(
        db.leads.c.source_id, db.leads.c.matched_customer_code,
        db.leads.c.match_method, db.leads.c.match_score, db.leads.c.matched_at))}

    n, seen = 0, set()
    with open(path, encoding="utf-8-sig", newline="") as f:
        for raw in csv.DictReader(f):
            row = {ours: (raw.get(theirs) or "").strip()
                   for theirs, ours in SHEET_COLUMNS.items()}
            if not row["email"] and not row["business"] and not row["name"]:
                continue
            sid = _source_id(row)
            if sid in seen:      # duplicate sheet row; first one wins
                continue
            seen.add(sid)
            prev = existing.get(sid)
            values = {
                "source_id": sid,
                "session_id": row["session_id"] or None,
                "name": row["name"] or None,
                "business": row["business"] or None,
                "email": row["email"].lower() or None,
                "phone": row["phone"] or None,
                "lead_source": row["lead_source"] or None,
                "analysis": row["analysis"] or None,
                "notes": row["notes"] or None,
                "submitted_at": _parse_date(row["date_raw"]),
                "status": row["status"] or None,
                "rating_raw": row["rating_raw"] or None,
                "rating": normalize_rating(row["rating_raw"]),
                "type": row["type"] or None,
                "details": row["details"] or None,
                "excluded_reason": exclusion_reason(row),
                "matched_customer_code": prev.matched_customer_code if prev else None,
                "match_method": prev.match_method if prev else None,
                "match_score": prev.match_score if prev else None,
                "matched_at": prev.matched_at if prev else None,
                "updated_at": db.now_utc(),
            }
            conn.execute(db.leads.delete().where(db.leads.c.source_id == sid))
            conn.execute(db.leads.insert().values(**values))
            n += 1
    log.info("leads mirrored: %d rows from %s", n, path.name)
    return n


def auto_match(conn) -> int:
    """Link Acquired leads to their Fresho account automatically.

    Two signals, in order of trust: a contact email/phone already on file for
    an account (auto_contact), then business-name similarity against customer
    names (auto_name, NAME_MATCH_THRESHOLD). Manual matches never overwritten.
    """
    customers = conn.execute(sa.select(
        db.customers.c.customer_code, db.customers.c.customer_name)).fetchall()
    by_norm = [(c.customer_code, _norm_name(c.customer_name)) for c in customers
               if c.customer_name]
    contact_email, contact_phone = {}, {}
    for r in conn.execute(sa.select(db.customer_contacts.c.customer_code,
                                    db.customer_contacts.c.email,
                                    db.customer_contacts.c.phone)):
        if r.email:
            contact_email[r.email.lower()] = r.customer_code
        if r.phone:
            digits = re.sub(r"\D", "", r.phone)[-10:]
            if digits:
                contact_phone[digits] = r.customer_code

    pending = conn.execute(sa.select(db.leads).where(
        db.leads.c.status == "Acquired")
        .where(db.leads.c.matched_customer_code.is_(None))
        .where(db.leads.c.excluded_reason.is_(None))).mappings().all()

    n = 0
    for lead in pending:
        code, method, score = None, None, None
        email = (lead["email"] or "").lower()
        digits = re.sub(r"\D", "", lead["phone"] or "")[-10:]
        if email and email in contact_email:
            code, method, score = contact_email[email], "auto_contact", 1.0
        elif digits and digits in contact_phone:
            code, method, score = contact_phone[digits], "auto_contact", 1.0
        else:
            target = _norm_name(lead["business"] or lead["name"] or "")
            if target:
                best = max(((c, SequenceMatcher(None, target, nn).ratio())
                            for c, nn in by_norm), key=lambda x: x[1],
                           default=(None, 0))
                if best[1] >= NAME_MATCH_THRESHOLD:
                    code, method, score = best[0], "auto_name", round(best[1], 3)
        if code:
            conn.execute(db.leads.update()
                         .where(db.leads.c.source_id == lead["source_id"])
                         .values(matched_customer_code=code, match_method=method,
                                 match_score=score, matched_at=db.now_utc()))
            n += 1
            log.info("lead %s (%s) matched to %s via %s (%.2f)",
                     lead["source_id"], lead["business"] or lead["name"],
                     code, method, score)
    if not n:
        log.info("auto-match: nothing new to match")
    return n


def apply_manual_matches(conn, mapping: dict) -> int:
    """Dashboard overrides: {source_id: customer_code} links; empty string
    clears a match. Manual always wins over auto."""
    known = {r.customer_code for r in
             conn.execute(sa.select(db.customers.c.customer_code))}
    n = 0
    for sid, code in mapping.items():
        if code and code not in known:
            log.warning("manual lead match to unknown customer %r skipped", code)
            continue
        values = ({"matched_customer_code": code, "match_method": "manual",
                   "match_score": None, "matched_at": db.now_utc()}
                  if code else
                  {"matched_customer_code": None, "match_method": None,
                   "match_score": None, "matched_at": None})
        res = conn.execute(db.leads.update()
                           .where(db.leads.c.source_id == sid).values(**values))
        n += res.rowcount
    if n:
        log.info("applied %d manual lead match(es)", n)
    return n


def main():
    parser = argparse.ArgumentParser(description="Rushton's leads mirror")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync").add_argument("sheet", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    engine = db.init_db()
    with engine.begin() as conn:
        load_csv(conn, args.sheet)
        auto_match(conn)


if __name__ == "__main__":
    main()
