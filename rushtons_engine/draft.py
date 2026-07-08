"""Comms drafting bridge — Claude Code writes, this module moves the data.

Phase 1 drafting runs inside Claude Code (no API key, no SDK): the engine
exports a *drafting brief* (selection context per account), the Claude Code
session loads the `rushtons-comms` skill for tone/voice/examples/feedback and
writes the three WhatsApp messages per account into a drafts JSON, and this
module validates and applies them to the comms table.

Tone, voice, real examples and accumulated client feedback live in
`.claude/skills/rushtons-comms/` (SKILL.md + examples.md + feedback-log.md) —
not here. That's a deliberate separation: tone guidance changes based on
client feedback and should be editable as plain markdown, without touching
this file. See rushtons-tone-guidelines-source in project memory for why.

"Code picks, AI writes" still holds: the brief is generated AFTER the ten are
locked, and apply_drafts refuses drafts for accounts outside the selection —
the drafting step can never add, drop or re-rank accounts.
"""

import json
import logging
from pathlib import Path

import config
import db

log = logging.getLogger(__name__)

STAGES = ("announcement", "followup", "postbox")


def export_brief(recommendations: list[dict], run_date, out_dir: Path | None = None) -> Path:
    """Write the drafting brief for the Claude Code session."""
    out_dir = Path(out_dir or config.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"drafting_brief_{run_date}.json"
    brief = {
        "run_date": str(run_date),
        "instructions": (
            "Before drafting, load the rushtons-comms skill "
            "(.claude/skills/rushtons-comms/SKILL.md) for tone, voice, real "
            "examples, and the latest client feedback — do not draft from "
            "memory of past tone rules. Write three WhatsApp messages "
            "(announcement, followup, postbox) for each account below. Save "
            f"them to drafts_{run_date}.json in this folder as "
            '{"<customer_code>": {"announcement": "...", "followup": "...", '
            '"postbox": "..."}, ...} covering every account exactly once. '
            "Do not add, drop or reorder accounts."
        ),
        "accounts": [{
            "customer_code": r["customer_code"],
            "customer_name": r["customer_name"],
            "venue_type": r["venue_type"],
            "account_manager": r["sales_rep"],
            "currently_buys": r["bought_categories"],
            "orders_last_month": r["num_orders"],
            "distinct_products": r["num_skus"],
            "gap_categories": r["gap_categories"],
            "suggested_products": r["suggested_products"],
            "why_selected": r["rationale"],
        } for r in recommendations],
    }
    path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    log.info("drafting brief written: %s (%d accounts)", path, len(recommendations))
    return path


def pending_placeholder() -> dict:
    return {stage: "[drafts pending — see the drafting brief in output/]"
            for stage in STAGES}


def apply_drafts(conn, drafts_path: Path, recommendations: list[dict]) -> int:
    """Validate a drafts JSON against the locked selection and persist to comms.
    Idempotent: existing drafts for these recommendations are replaced."""
    drafts = json.loads(Path(drafts_path).read_text(encoding="utf-8"))

    selected = {r["customer_code"] for r in recommendations}
    extra = set(drafts) - selected
    missing = selected - set(drafts)
    if extra:
        raise ValueError(f"drafts file contains accounts outside the locked "
                         f"selection: {sorted(extra)} — the selection is code's "
                         "call, not the drafter's")
    if missing:
        raise ValueError(f"drafts missing for selected accounts: {sorted(missing)}")
    for code, msgs in drafts.items():
        empty = [s for s in STAGES if not (msgs.get(s) or "").strip()]
        if empty:
            raise ValueError(f"{code}: empty or missing stages {empty}")

    written = 0
    for rec in recommendations:
        msgs = drafts[rec["customer_code"]]
        conn.execute(db.comms.delete().where(
            db.comms.c.recommendation_id == rec["recommendation_id"]))
        for stage in STAGES:
            conn.execute(db.comms.insert().values(
                recommendation_id=rec["recommendation_id"],
                stage=stage,
                channel="whatsapp",
                draft_body=msgs[stage].strip(),
                created_at=db.now_utc(),
            ))
            written += 1
        rec["drafts"] = {s: msgs[s].strip() for s in STAGES}
    log.info("applied %d drafts for %d accounts from %s",
             written, len(recommendations), drafts_path)
    return written
