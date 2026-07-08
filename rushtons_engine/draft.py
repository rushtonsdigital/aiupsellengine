"""Comms drafting bridge — Claude Code writes, this module moves the data.

Phase 1 drafting runs inside Claude Code (no API key, no SDK): the engine
exports a *drafting brief* (selection context + tone rules per account), the
Claude Code session writes the three WhatsApp messages per account into a
drafts JSON, and this module validates and applies them to the comms table.

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

# Tone rules from "Rushton's AI Upsell Engine Meeting Notes & Plan July 26".
# Embedded in every brief so the drafting session needs no other context.
TONE_RULES = """\
You draft WhatsApp messages for Rushton's Greengrocers, a London wholesale
fresh-produce supplier, to existing trade customers (chefs, owners, bar
managers). Write as the named account manager (sign_off field).

Rules (agreed with the client, non-negotiable):
- Warm and expert: the voice of a knowledgeable account manager, never a
  marketing email.
- Short and specific: WhatsApp-length. Reference the customer by name and
  their actual ordering behaviour.
- Seasonal and produce-led: lead with the specific products and what's good
  about them right now.
- Never generic: never write "we noticed you don't order dairy". Pitch the
  specific product instead ("we've just taken on a brilliant burrata that
  would work beautifully alongside your tomatoes").
- Flag the category gap naturally through the products, not as data.
- Offer a free sample box themed around the gap, no strings attached.
- Sign off with the account manager's first name only.
- No emojis unless natural, no exclamation-mark pileups, no corporate speak.

Three messages per account:
1. announcement — "been meaning to mention something..." opener; reference
   what they currently order; lead with 2-3 specific seasonal products from
   the gap; offer the sample box.
2. followup — short nudge a few days later if no reply; one or two lines;
   acknowledge no rush; keep it human.
3. postbox — sent the day the sample box lands: hope it arrived safely, how
   did they find it; soft invite to add items to the next order.
"""


def _first_name(sales_rep: str | None) -> str:
    return (sales_rep or "the Rushton's team").split(" ")[0]


def export_brief(recommendations: list[dict], run_date, out_dir: Path | None = None) -> Path:
    """Write the drafting brief for the Claude Code session."""
    out_dir = Path(out_dir or config.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"drafting_brief_{run_date}.json"
    brief = {
        "run_date": str(run_date),
        "instructions": (
            "Write three WhatsApp messages (announcement, followup, postbox) "
            f"for each account below, following tone_rules. Save them to "
            f"drafts_{run_date}.json in this folder as "
            '{"<customer_code>": {"announcement": "...", "followup": "...", '
            '"postbox": "..."}, ...} covering every account exactly once. '
            "Do not add, drop or reorder accounts."
        ),
        "tone_rules": TONE_RULES,
        "accounts": [{
            "customer_code": r["customer_code"],
            "customer_name": r["customer_name"],
            "venue_type": r["venue_type"],
            "sign_off": _first_name(r["sales_rep"]),
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
