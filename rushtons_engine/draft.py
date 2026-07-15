"""Steps 3 and 4 of the weekly pipeline — the AI's half of the work.

    1. code selects WHO to target      (selector.select_top — final)
    2. code selects WHAT IS ELIGIBLE   (selector.build_product_pool)
    3. AI picks the final products      <- this module carries the brief
    4. AI writes the messages           <- ... and validates what comes back

Both AI steps run inside Claude Code (Phase 1 has no API key and no SDK): the
engine exports a *drafting brief*, the Claude Code session loads the
`rushtons-comms` skill for product-selection judgement, tone, examples and
accumulated client feedback, and writes its picks + messages into a drafts
JSON. This module validates that file and applies it.

Why step 3 exists at all: ranking the pool by recent-buyer count alone kept
surfacing commodity staples over the specialty lines Rushton's actually wants
to pitch, and had no idea a cocktail bar wants mint rather than lemongrass, or
that panko crumbs make no sense for a British steak house. That is a judgement
call about a specific customer, so a human-grade step makes it — but inside
hard boundaries.

Two boundaries survive from "code picks, AI writes", and both are enforced
here, not trusted:

  * the drafter can never add, drop or re-rank ACCOUNTS  — the ten are locked
  * the drafter can never invent a PRODUCT               — every chosen code
    must exist in that account's pool from step 2

Everything inside those boundaries — which products actually fit this venue,
which gap to drop entirely, how to say it — is the drafter's call.

Tone, voice, real examples and client feedback live in
`.claude/skills/rushtons-comms/` (SKILL.md + examples.md + feedback-log.md),
not here. That's deliberate: guidance changes as the client gives feedback and
should be editable as plain markdown. See rushtons-tone-guidelines-source in
project memory.
"""

import json
import logging
from pathlib import Path

import config
import db

log = logging.getLogger(__name__)

STAGES = ("announcement", "followup", "postbox")

INSTRUCTIONS = (
    "Two jobs here, in order, each with its own skill. Do not work from memory "
    "of past rules — load the skill and its feedback-log.md each time; guidance "
    "changes as the client gives feedback.\n\n"
    "STEP 3 — choose the products. Load the rushtons-product-selection skill "
    "(.claude/skills/rushtons-product-selection/SKILL.md) and its feedback-log. "
    "Each account below has a `product_pool`: everything eligible to pitch, per "
    "gap category, already filtered to in-season lines they don't currently "
    "buy. `buyers_14d` is a popularity signal, NOT a recommendation — it "
    "favours commodity staples over the specialty lines worth pitching. "
    "REQUIRED: run a live web search of every venue before picking its products "
    "— you cannot judge fit from a customer code, and the venue_type is often "
    "thin or 'Unknown'. Record what you found in `customer_review` (say so "
    "explicitly if a venue can't be found). Then pick the products that "
    f"genuinely fit that kitchen — at most {config.MAX_CHOSEN_PRODUCTS} per "
    "account, a one-line `why` for each. You may only choose codes in that "
    "account's product_pool — never invent one. You MAY drop a whole gap "
    "category if nothing in its pool honestly fits. Note any product-labelling "
    "problem in `data_notes`.\n\n"
    "STEP 4 — write the messages. Load the rushtons-comms skill "
    "(.claude/skills/rushtons-comms/SKILL.md) and its feedback-log, then write "
    "the three WhatsApp messages (announcement, followup, postbox) around the "
    "products you chose, per its tone guidance.\n\n"
    "Save to drafts_{run_date}.json in this folder:\n"
    '{"<customer_code>": {\n'
    '   "customer_review": "what your web search found — venue, what they cook",\n'
    '   "chosen_products": [{"code": "...", "name": "...", "category": "...",\n'
    '                        "why": "why this fits this kitchen"}],\n'
    '   "data_notes": "optional — labelling/categorisation problems spotted",\n'
    '   "messages": {"announcement": "...", "followup": "...", "postbox": "..."}\n'
    "}, ...}\n\n"
    "Cover every account below exactly once. Do not add, drop or reorder "
    "accounts — the selection is code's call, not yours."
)


def export_brief(recommendations: list[dict], run_date, out_dir: Path | None = None) -> Path:
    """Write the drafting brief: the locked ten, plus everything the drafter
    needs to judge products (step 3) and write messages (step 4)."""
    out_dir = Path(out_dir or config.EXPORT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"drafting_brief_{run_date}.json"
    brief = {
        "run_date": str(run_date),
        "max_products_per_account": config.MAX_CHOSEN_PRODUCTS,
        "instructions": INSTRUCTIONS.replace("{run_date}", str(run_date)),
        "accounts": [{
            "customer_code": r["customer_code"],
            "customer_name": r["customer_name"],
            "venue_type": r["venue_type"],
            "account_manager": r["sales_rep"],
            "size_band": r.get("size_band"),
            "currently_buys": r["bought_categories"],
            "orders_last_month": r["num_orders"],
            "distinct_products": r["num_skus"],
            "gap_categories": r["gap_categories"],
            "product_pool": r["product_pool"],
            "why_selected": r["rationale"],
        } for r in recommendations],
    }
    path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    log.info("drafting brief written: %s (%d accounts)", path, len(recommendations))
    return path


def pending_placeholder() -> dict:
    return {stage: "[drafts pending — see the drafting brief in output/]"
            for stage in STAGES}


def _validate(drafts: dict, recommendations: list[dict]) -> None:
    """Enforce the two boundaries: the locked accounts, and the product pool.
    Raises ValueError with an actionable message — never silently repairs."""
    selected = {r["customer_code"] for r in recommendations}
    extra = set(drafts) - selected
    missing = selected - set(drafts)
    if extra:
        raise ValueError(f"drafts file contains accounts outside the locked "
                         f"selection: {sorted(extra)} — the selection is code's "
                         "call, not the drafter's")
    if missing:
        raise ValueError(f"drafts missing for selected accounts: {sorted(missing)}")

    pools = {r["customer_code"]: r["product_pool"] for r in recommendations}
    for code, entry in drafts.items():
        if not isinstance(entry, dict) or "messages" not in entry:
            raise ValueError(
                f"{code}: expected the step-3/step-4 draft format "
                '{"chosen_products": [...], "messages": {...}} — got a bare '
                "message block. Re-draft from the current brief; the drafter "
                "now picks the products too.")

        if not (entry.get("customer_review") or "").strip():
            raise ValueError(
                f"{code}: no customer_review — step 3 requires a live web "
                "search of the venue before picking. Record what it found here "
                "(or say the venue couldn't be found).")

        chosen = entry.get("chosen_products") or []
        if not chosen:
            raise ValueError(f"{code}: no chosen_products — step 3 must pick at "
                             "least one product from the pool")
        if len(chosen) > config.MAX_CHOSEN_PRODUCTS:
            raise ValueError(
                f"{code}: {len(chosen)} products chosen, max is "
                f"{config.MAX_CHOSEN_PRODUCTS} — a sample box is themed, not a "
                "catalogue")

        eligible = {p["code"] for items in pools[code].values() for p in items}
        for p in chosen:
            if not p.get("code"):
                raise ValueError(f"{code}: a chosen product has no code")
            if p["code"] not in eligible:
                raise ValueError(
                    f"{code}: product {p['code']} ({p.get('name', '?')}) is not "
                    f"in this account's pool — the drafter may only choose from "
                    f"the pool, never invent a product. Eligible: "
                    f"{sorted(eligible)}")
            if not (p.get("why") or "").strip():
                raise ValueError(f"{code}: product {p['code']} has no `why` — "
                                 "every pick needs a one-line justification")

        empty = [s for s in STAGES if not (entry["messages"].get(s) or "").strip()]
        if empty:
            raise ValueError(f"{code}: empty or missing stages {empty}")


def apply_drafts(conn, drafts_path: Path, recommendations: list[dict]) -> int:
    """Validate a drafts JSON against the locked selection and the product
    pools, then persist picks + messages. Idempotent: existing drafts for these
    recommendations are replaced."""
    drafts = json.loads(Path(drafts_path).read_text(encoding="utf-8"))
    _validate(drafts, recommendations)

    written = 0
    for rec in recommendations:
        entry = drafts[rec["customer_code"]]
        msgs = entry["messages"]
        chosen = entry["chosen_products"]

        conn.execute(db.recommendations.update()
                     .where(db.recommendations.c.id == rec["recommendation_id"])
                     .values(chosen_products=chosen))
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

        rec["chosen_products"] = chosen
        rec["customer_review"] = (entry.get("customer_review") or "").strip()
        rec["data_notes"] = (entry.get("data_notes") or "").strip()
        rec["drafts"] = {s: msgs[s].strip() for s in STAGES}

    notes = {r["customer_code"]: r["data_notes"]
             for r in recommendations if r.get("data_notes")}
    if notes:
        log.warning("drafter flagged product-data problems: %s", notes)
    log.info("applied %d drafts for %d accounts from %s",
             written, len(recommendations), drafts_path)
    return written
