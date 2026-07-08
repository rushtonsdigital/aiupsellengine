# Rushton's AI Upsell Engine — instructions for Claude Code

The engine lives in `rushtons_engine/` (Python venv at `rushtons_engine/.venv`).
Rule of the project: **code picks, AI writes.** Selection of the weekly 10 is
deterministic Python (`selector.py`) — never re-rank, add, drop, or second-guess
the selected accounts. Claude Code's job in the weekly run is drafting the
WhatsApp messages only.

## Weekly update procedure

When the user asks to "run the weekly update" (or similar):

1. Confirm the new Fresho exports are present (the 6 daily
   `*product_totals_by_customer_*.csv` files, plus optionally a fresh
   `*customers_*.csv` master) in `rushtons_engine/data/` — or wherever the
   user says they are.
2. Run selection:
   ```
   rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --data-dir <folder>
   ```
   This ingests, classifies, selects the top 10, and writes
   `rushtons_engine/output/drafting_brief_<date>.json`.
3. Read the brief. For each account, write the three WhatsApp messages
   (announcement / followup / postbox) following the `tone_rules` in the brief
   exactly. Save as `rushtons_engine/output/drafts_<date>.json` in the format
   the brief's `instructions` field specifies.
4. Apply and finalise:
   ```
   rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --drafts rushtons_engine\output\drafts_<date>.json
   ```
5. Point the user at `rushtons_engine/output/rushtons_upsell_tracker_<date>.xlsx`
   (goes to the shared SharePoint folder; CS team reviews and sends manually).

Notes:
- Everything is idempotent; re-running a step replaces that week's outputs.
- `apply_drafts` hard-fails if the drafts file adds, drops, or leaves empty
  any account — fix the drafts file, never the selection.
- If ingest fails on an unknown product group, add it deliberately to
  `rushtons_engine/categories.py` (never bucket silently) and mention it to
  the user.
- Tests: `rushtons_engine\.venv\Scripts\python.exe -m pytest -q` (run from
  `rushtons_engine/`); regression suite needs `REGRESSION_DATA_DIR` set to the
  folder with the June exports.
