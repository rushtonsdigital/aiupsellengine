# Rushton's AI Upsell Engine — instructions for Claude Code

The engine lives in `rushtons_engine/` (Python venv at `rushtons_engine/.venv`).

The weekly run is four steps, and the split matters:

| # | Who  | What                                                    |
|---|------|---------------------------------------------------------|
| 1 | code | selects **who** to target — the weekly 10 (`selector.py`) |
| 2 | code | selects **what is eligible** — a product pool per account |
| 3 | AI   | picks the **final products** from that pool              |
| 4 | AI   | writes the **WhatsApp messages** around them             |

**Two boundaries, both enforced in `draft.py` — never work around them:**
- The selected 10 are final. Never re-rank, add, drop or second-guess them.
- The AI may only pitch products from that account's pool. Never invent a SKU.

Inside those boundaries the AI's judgement is the point, not a liability:
choosing the *right* product for a venue (and dropping a gap category when
nothing fits) is step 3's whole job. Ranking by popularity alone got this
wrong — see `feedback-log.md` in the comms skill for the client feedback
(2026-07-14) that drove the split.

## Weekly update procedure

When the user asks to "run the weekly update" (or similar):

1. Confirm the new Fresho exports are present (the 6 daily
   `*product_totals_by_customer_*.csv` files, plus optionally a fresh
   `*customers_*.csv` master) in `rushtons_engine/data/` — or wherever the
   user says they are.
2. Run selection (steps 1 and 2):
   ```
   rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --data-dir <folder>
   ```
   This ingests, classifies, selects the top 10, builds each account's
   eligible product pool, and writes
   `rushtons_engine/output/drafting_brief_<date>.json`.
3. Before drafting, load the `rushtons-comms` skill
   (`.claude/skills/rushtons-comms/SKILL.md`) — it holds the product-selection
   judgement for step 3, the real client tone guidelines, example messages,
   and a feedback log of corrections. Always check `feedback-log.md` in that
   folder too; it overrides `SKILL.md` where they conflict. Do not work from
   memory of past rules — guidance changes as the client gives feedback, and
   this skill is the only place that's kept current.
4. Read the brief. For each account: **first pick the products** from its
   `product_pool` (step 3 — look at who the customer actually is; search the
   web where the venue is identifiable and it sharpens the call; drop a gap
   category if nothing in it genuinely fits), **then write** the three
   WhatsApp messages around those picks (step 4). Save as
   `rushtons_engine/output/drafts_<date>.json` in the format the brief's
   `instructions` field specifies — every pick needs a one-line `why`.
5. Apply and finalise:
   ```
   rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --drafts rushtons_engine\output\drafts_<date>.json
   ```
6. Point the user at `rushtons_engine/output/rushtons_upsell_tracker_<date>.xlsx`
   (goes to the shared SharePoint folder; CS team reviews and sends manually).

Notes:
- Everything is idempotent; re-running a step replaces that week's outputs.
- If the tracker `.xlsx` is open in Excel when `export.py` tries to write it,
  it does NOT crash the run — it retries briefly, then falls back to a
  `..._UPDATED_<time>.xlsx` file and logs an error explaining why. The
  database is already fully updated by that point regardless; only the
  human-facing Excel copy is affected. Check the log/console output for the
  actual filename written.
- `apply_drafts` hard-fails if the drafts file adds, drops, or leaves empty
  any account, or picks a product outside that account's pool — fix the
  drafts file, never the selection or the pool.
- Noticed a product that looks mis-categorised in Fresho (a category mixing
  commodity and specialty lines, say)? Put it in `data_notes` on that account
  in the drafts file and mention it to the user — that's how the source data
  gets fixed rather than worked around.
- If ingest fails on an unknown product group, add it deliberately to
  `rushtons_engine/categories.py` (never bucket silently) and mention it to
  the user.
- Tests: `rushtons_engine\.venv\Scripts\python.exe -m pytest -q` (run from
  `rushtons_engine/`); regression suite needs `REGRESSION_DATA_DIR` set to the
  folder with the June exports.
