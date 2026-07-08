# Rushton's AI Upsell Engine — Phase 1

Deterministic Python selects the weekly 10 upsell targets; Claude (running in
Claude Code — no API key needed) only drafts the WhatsApp messages.
**Code picks, AI writes** — the LLM never ranks, filters or overrides the
selection, and `draft.apply_drafts` hard-fails on any attempt to.

## Weekly runbook (Monday, ~10 minutes, driven from Claude Code)

1. Download the 6 daily exports (Mon–Sat, `*product_totals_by_customer_*.csv`)
   and the customer master (`*customers_*.csv`) from Fresho into `data/`.
2. In Claude Code, ask: *"run the weekly upsell update"* — the procedure lives
   in the project-root `CLAUDE.md`. Under the hood:
   - `run_weekly.py` ingests, classifies, selects the 10, and writes
     `output/drafting_brief_<date>.json`;
   - Claude Code drafts the 3 messages per account into
     `output/drafts_<date>.json` (tone rules travel inside the brief);
   - `run_weekly.py --drafts output/drafts_<date>.json` validates, stores the
     comms, and regenerates the tracker.
3. Collect `output/rushtons_upsell_tracker_<date>.xlsx`, drop it in the shared
   SharePoint folder. CS team reviews drafts, fills in Contact / Approved /
   Sent / Outcome, and sends via WhatsApp manually.

Everything is idempotent — re-running a week (or re-ingesting the same file)
replaces rather than duplicates. The run date is derived from the data (latest
delivery date), never the wall clock, so identical inputs give identical output.

## Setup (one-time)

```
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install sqlalchemy psycopg2-binary openpyxl pytest python-dotenv
copy .env.example .env     # fill in DATABASE_URL (Supabase)
```

Without a `.env` the engine runs on local SQLite (`rushtons.db`) — useful for
dry runs. Supabase setup: create a free-tier
project in an **EU region**, run `schema.sql` in the SQL editor, put the
connection string in `.env`, and (once this folder is a GitHub repo) install
`keepalive.yml` so the free tier never auto-pauses.

## Bootstrapping history

Ingest any past exports the same way — e.g. the June 2026 set:

```
.venv\Scripts\python.exe run_weekly.py --data-dir "C:\Rushtons AI Upsell" --skip-drafts
```

## Module map

| File | Role |
|---|---|
| `config.py` | Every threshold and weight. Notably `LOW_ORDER_METRIC` (`sku` now, flip to `category` when the SKU pool thins out — expected within ~3 weeks) and `LOW_ORDER_MAX`. |
| `categories.py` | Explicit raw Fresho group → canonical category map (33 observed values). Unknown group = hard error, on purpose. |
| `ingest.py` | Raw daily exports + customer master. Delete-by-source-file idempotency; genuine duplicate order lines are preserved. |
| `classify.py` | Activity status, volume-proxy size bands (interim until a price list exists), prestige (seeded from the prior hand classification). |
| `metrics.py` | `customer_week_metrics` recompute. |
| `selector.py` | The deterministic heart: candidate filter → scored, tie-broken top 10 → gaps → in-season product suggestions. (Named `selector`, not `select`, to avoid shadowing the Python stdlib module.) |
| `draft.py` | Drafting bridge: exports the drafting brief for Claude Code, validates and applies the returned drafts. Rejects drafts that add/drop/skip accounts. Review-gated; never sends. |
| `export.py` | Excel tracker: Summary + one tab per account. |
| `run_weekly.py` | Orchestrator. |
| `schema.sql` | Canonical Postgres DDL for Supabase. |

## Tests

```
.venv\Scripts\python.exe -m pytest -q                      # unit (fast)
set REGRESSION_DATA_DIR=C:\Rushtons AI Upsell
.venv\Scripts\python.exe -m pytest -m regression -q        # against real June data
```

The regression suite pins: exact ingest row count (82,676), ≥90% agreement
with the prior hand classification on status and size band, all internal
accounts excluded, and a deterministic top 10.

## Phase 1 guardrails

- Every export row to date is `Invoiced`; any new `order_state` value is
  skipped with a loud warning until a human decides (config).
- An account recommended in the last 8 weeks is skipped (`COOLDOWN_WEEKS`).
- `Z888. Out of Season` products are never suggested.
- Segment→category pitch maps in `config.py` await Rushton's sign-off.
