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
wrong — see `feedback-log.md` in the `rushtons-product-selection` skill for
the client feedback (2026-07-14) that drove the split.

## Working style — do the work, don't hand back instructions

The user is **not very technical** and is time-poor. Default to **completing
every step you are able to complete yourself**, end to end, rather than
explaining how to do it. This includes running commands, git/GitHub operations
via the `gh` CLI (create/merge PRs, branches), editing files, running the
pipeline, and any diagnostics. Do these directly — don't write out a to-do list
for the user to execute.

Only hand a step back to the user when it **genuinely cannot be done from here**,
and when you do, give the shortest possible click-by-click steps. Things that
truly require the user:
- entering a **password / secret** anywhere (never handle these — e.g. the DB
  password in `.env`, GitHub Actions secrets);
- actions needing a login/scope this session lacks (e.g. pushing files under
  `.github/workflows/` needs a `workflow`-scoped token — do it via the GitHub
  web UI instead, or have the user do it);
- a **decision** only they can make (which Supabase project, whether to send
  client-facing messages, etc.).

Still confirm before **irreversible or outward-facing** actions (deleting data,
pushing to `main`, sending anything to a client) — but once confirmed, carry the
whole thing through yourself. When you finish, report what you did and what (if
anything) is left for them, not a list of what they should go and do.

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
3. Two skills, one per AI step — load each (with its `feedback-log.md`, which
   overrides its `SKILL.md`) at the point you need it, never from memory:
   - Step 3, picking: `rushtons-product-selection`
     (`.claude/skills/rushtons-product-selection/SKILL.md`) — the product-
     selection judgement, and the **mandatory live web search** of every venue.
   - Step 4, writing: `rushtons-comms`
     (`.claude/skills/rushtons-comms/SKILL.md`) — the real client tone
     guidelines, example messages, and tone feedback log.
4. Read the brief. For each account: **first pick the products** from its
   `product_pool` (step 3 — run a live web search of the venue first, then pick
   what genuinely fits and drop any gap category that doesn't; record the
   search in `customer_review`), **then write** the three WhatsApp messages
   around those picks (step 4). Save as
   `rushtons_engine/output/drafts_<date>.json` in the format the brief's
   `instructions` field specifies — every pick needs a one-line `why`, and
   every account needs a `customer_review` (apply_drafts rejects a missing one).
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

## Business intelligence / data questions

Separate from the weekly upsell run, the engine has a read-only **reporting
layer** for BI reports and ad-hoc data questions:

- `reporting.py` refreshes three summary tables each weekly run
  (`customer_category_metrics`, `product_metrics`,
  `recommendation_category_facts`) in the same style as `metrics.recompute`.
- `reporting.sql` defines Postgres **views** (`v_order_lines`,
  `v_monthly_customer_sales`, `v_category_penetration`, `v_account_gaps`,
  `v_customer_health`, `v_lapsing_accounts`, `v_recommendation_funnel`,
  `v_product_performance`), applied by `db.apply_reporting_views` — **Postgres
  only** (guarded in `init_db`; the SQLite dev/test fallback skips them).
- **Predefined reports** are served by Metabase (off-the-shelf) connected to
  Supabase via a read-only `rushtons_readonly` role — an operational setup,
  not code. See the plan and the `rushtons-analytics` skill for the role SQL.
- **Ad-hoc data questions** (a chat with the data) are answered from a Claude
  Code session: load the **`rushtons-analytics`** skill and query through
  `rushtons_engine/query.py::run_sql` (read-only; SELECT-only; auto-LIMIT).
- Two hard caveats the skill enforces: every measure is **volume, not revenue**
  (there is no price data), and `quantity` must **never be summed across
  products** (mixed `qty_type`). Headline on order/line counts.
- New env var `REPORTING_DATABASE_URL` (in `.env`) holds the read-only role's
  connection string; it falls back to `DATABASE_URL` on local SQLite.
