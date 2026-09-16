"""The interactions ledger: every touch with a customer, from any source.

Sources and their idempotency keys (source_id):
  dashboard  one-tap logs + assignments saved into the published dashboard's
             state; harvested here each week ("l|<code>|<ts>" ids minted by the
             page's JS).
  outlook    read-only mailbox sync. A Claude session finds the messages via
             the Microsoft connector (Python cannot call MCP tools), writes
             them to a JSON file, and loads it here. source_id is the message's
             internetMessageId. Metadata only - direction, date, subject,
             counterparty - never bodies.
  system     derived outcomes: "ordered_again" when a customer places an order
             after the latest outreach ("oa|<code>|<date>").

Every loader is idempotent (insert-if-source_id-absent), so re-running a week
is always safe - same contract as the rest of the pipeline.

CLI (run with the venv python, from rushtons_engine/):
  python interactions.py harvest <state.json>   dashboard state -> ledger;
                                                writes <state>.harvested.json
                                                (log emptied) for the rebuild
  python interactions.py emails <events.json>   outlook sync results -> ledger
  python interactions.py contacts <file.csv>    customer_code,email[,label]
  python interactions.py outcomes               derive ordered_again rows
"""

import argparse
import csv
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import sqlalchemy as sa

import db

log = logging.getLogger(__name__)

OUTREACH_KINDS = ("whatsapp_sent", "email_sent", "email_out")


def _parse_at(value) -> datetime:
    """ISO timestamp (page JS emits '...Z') or date -> aware UTC datetime."""
    s = str(value).strip().replace("Z", "+00:00")
    if len(s) == 10:  # bare date
        s += "T00:00:00+00:00"
    dt = datetime.fromisoformat(s)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _existing_source_ids(conn, ids):
    if not ids:
        return set()
    rows = conn.execute(sa.select(db.interactions.c.source_id)
                        .where(db.interactions.c.source_id.in_(list(ids))))
    return {r.source_id for r in rows}


def _known_customers(conn):
    return {r.customer_code
            for r in conn.execute(sa.select(db.customers.c.customer_code))}


def _insert(conn, rows) -> int:
    """Insert rows whose source_id is not already present. Returns count."""
    rows = [r for r in rows if r.get("source_id")]
    seen = _existing_source_ids(conn, {r["source_id"] for r in rows})
    known = _known_customers(conn)
    fresh, dropped, batch_ids = [], 0, set()
    for r in rows:
        if r["source_id"] in seen or r["source_id"] in batch_ids:
            continue
        if r["customer_code"] not in known:
            dropped += 1
            continue
        batch_ids.add(r["source_id"])
        fresh.append(r)
    if fresh:
        conn.execute(db.interactions.insert(), [
            {**r, "created_at": db.now_utc()} for r in fresh])
    if dropped:
        log.warning("%d interaction(s) skipped (unknown customer code)", dropped)
    return len(fresh)


def harvest_state(conn, state: dict) -> int:
    """Dashboard state -> ledger. Consumes state['log']; assignments stay state."""
    rows = []
    for ev in state.get("log", []):
        kind = ev.get("kind")
        if kind not in ("whatsapp_sent", "email_sent", "note", "assigned"):
            log.warning("unknown dashboard event kind %r skipped", kind)
            continue
        rows.append({
            "customer_code": ev.get("code"),
            "occurred_at": _parse_at(ev.get("at")),
            "kind": kind,
            "summary": (ev.get("note") or "")[:500] or None,
            "actor": ev.get("actor") or None,
            "source": "dashboard",
            "source_id": ev.get("id"),
        })
    n = _insert(conn, rows)
    log.info("harvested %d dashboard event(s)", n)
    return n


def load_email_events(conn, events: list) -> int:
    """Outlook sync results -> ledger. Each event:
    {customer_code, direction: 'in'|'out', at, subject, counterparty, message_id}
    """
    rows = []
    for ev in events:
        direction = ev.get("direction")
        if direction not in ("in", "out"):
            log.warning("email event with direction %r skipped", direction)
            continue
        counterparty = (ev.get("counterparty") or "").strip()
        subject = (ev.get("subject") or "").strip()
        summary = subject[:300] or None
        if counterparty:
            summary = f"{subject[:260]} ({counterparty})" if subject else counterparty
        rows.append({
            "customer_code": ev.get("customer_code"),
            "occurred_at": _parse_at(ev.get("at")),
            "kind": "email_in" if direction == "in" else "email_out",
            "summary": summary,
            "actor": ev.get("actor") or None,
            "source": "outlook",
            "source_id": ev.get("message_id"),
        })
    n = _insert(conn, rows)
    log.info("loaded %d email event(s)", n)
    return n


def upsert_contact(conn, code, name=None, label=None, email=None, phone=None) -> bool:
    """Insert or update one contact person. Email is the natural key when
    present; a phone-only contact dedupes on (code, phone). Returns True if a
    row was written."""
    email = (email or "").strip().lower() or None
    phone = (phone or "").strip() or None
    name = (name or "").strip() or None
    label = (label or "").strip() or None
    if email and "@" not in email:
        return False
    if not email and not phone:
        return False
    cc = db.customer_contacts
    if email:
        conn.execute(cc.delete().where(cc.c.customer_code == code)
                     .where(cc.c.email == email))
    else:
        existing = conn.execute(sa.select(cc.c.id)
                                .where(cc.c.customer_code == code)
                                .where(cc.c.phone == phone)).first()
        if existing:
            conn.execute(cc.delete().where(cc.c.id == existing.id))
    conn.execute(cc.insert().values(customer_code=code, name=name, label=label,
                                    email=email, phone=phone))
    return True


def load_contacts_csv(conn, path: Path) -> int:
    """Upsert customer_code[,name][,label][,email][,phone] rows (email or
    phone required per row). Reports unknown codes."""
    known = _known_customers(conn)
    n, unknown = 0, []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = (row.get("customer_code") or "").strip()
            if not code:
                continue
            if code not in known:
                unknown.append(code)
                continue
            if upsert_contact(conn, code, name=row.get("name"),
                              label=row.get("label"), email=row.get("email"),
                              phone=row.get("phone")):
                n += 1
    if unknown:
        log.warning("contacts skipped for unknown customer codes: %s",
                    ", ".join(sorted(set(unknown))))
    log.info("loaded %d contact(s)", n)
    return n


def harvest_contacts(conn, state: dict) -> int:
    """Contacts added on the dashboard's Accounts section -> customer_contacts.
    Upserts, so re-harvesting the same page state is safe."""
    known = _known_customers(conn)
    n = 0
    for c in state.get("contacts", []):
        code = c.get("code")
        if code not in known:
            log.warning("contact for unknown customer %r skipped", code)
            continue
        if upsert_contact(conn, code, name=c.get("name"), label=c.get("label"),
                          email=c.get("email"), phone=c.get("phone")):
            n += 1
    if n:
        log.info("harvested %d contact(s) from the dashboard", n)
    return n


def derive_order_outcomes(conn) -> int:
    """Log 'ordered_again' the first time a customer orders after their latest
    outreach (whatsapp_sent / email_sent / email_out) with no outcome yet."""
    rows = conn.execute(sa.select(
        db.interactions.c.customer_code,
        db.interactions.c.kind,
        db.interactions.c.occurred_at,
    ).where(db.interactions.c.kind.in_(list(OUTREACH_KINDS) + ["ordered_again"]))
    ).fetchall()

    last_outreach, last_outcome = {}, {}
    for code, kind, at in rows:
        at = _parse_at(at)
        bucket = last_outcome if kind == "ordered_again" else last_outreach
        if code not in bucket or at > bucket[code]:
            bucket[code] = at

    pending = {code: at for code, at in last_outreach.items()
               if code not in last_outcome or last_outcome[code] < at}
    if not pending:
        return 0

    new = []
    for code, sent_at in pending.items():
        first = conn.execute(
            sa.select(sa.func.min(db.orders.c.delivery_date))
            .where(db.orders.c.customer_code == code)
            .where(db.orders.c.delivery_date > sent_at.date())
        ).scalar()
        if first is None:
            continue
        new.append({
            "customer_code": code,
            "occurred_at": _parse_at(str(first)),
            "kind": "ordered_again",
            "summary": f"first order after outreach on {sent_at.date()}",
            "actor": None,
            "source": "system",
            "source_id": f"oa|{code}|{first}",
        })
    n = _insert(conn, new)
    if n:
        log.info("derived %d ordered_again outcome(s)", n)
    return n


def timeline(conn, codes, per_account: int = 8) -> dict:
    """Recent ledger entries per account for the dashboard build:
    {code: [{at, kind, summary, actor}, ...]} newest first."""
    if not codes:
        return {}
    rows = conn.execute(
        sa.select(db.interactions.c.customer_code, db.interactions.c.occurred_at,
                  db.interactions.c.kind, db.interactions.c.summary,
                  db.interactions.c.actor)
        .where(db.interactions.c.customer_code.in_(list(codes)))
        .order_by(db.interactions.c.occurred_at.desc())
    ).fetchall()
    out = {}
    for code, at, kind, summary, actor in rows:
        lst = out.setdefault(code, [])
        if len(lst) < per_account:
            lst.append({"at": _parse_at(at).isoformat(), "kind": kind,
                        "summary": summary or "", "actor": actor or ""})
    return out


def main():
    parser = argparse.ArgumentParser(description="Rushton's interactions ledger")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("harvest").add_argument("state", type=Path)
    sub.add_parser("emails").add_argument("events", type=Path)
    sub.add_parser("contacts").add_argument("file", type=Path)
    sub.add_parser("outcomes")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")

    engine = db.init_db()
    with engine.begin() as conn:
        if args.cmd == "harvest":
            import leads as leads_mod
            state = json.loads(args.state.read_text(encoding="utf-8"))
            harvest_state(conn, state)
            harvest_contacts(conn, state)
            leads_mod.apply_manual_matches(conn, state.get("leadMatch", {}))
            cleaned = dict(state, log=[], contacts=[], leadMatch={})
            out = args.state.with_suffix(".harvested.json")
            out.write_text(json.dumps(cleaned), encoding="utf-8")
            print(f"harvested; rebuild the dashboard with state from {out}")
        elif args.cmd == "emails":
            events = json.loads(args.events.read_text(encoding="utf-8"))
            load_email_events(conn, events)
        elif args.cmd == "contacts":
            load_contacts_csv(conn, args.file)
        elif args.cmd == "outcomes":
            derive_order_outcomes(conn)


if __name__ == "__main__":
    main()
