---
name: rushtons-weekly-update
description: The end-to-end Monday run for Rushton's — review team feedback, download the Fresho exports in the browser, load them, run selection, pick products and write messages, apply, refresh the dashboard, and hand over the tracker and a summary email. Load this whenever the user asks to "run the weekly update", "do this week's pull", or similar. Follow every step in order; it orchestrates the other rushtons-* skills.
---

# Rushton's Weekly Update

The weekly run, done the same way every time. It ends with: the tracker for the CS
team, the client dashboard republished, a drafted email to the team, and a short
report to Jose of what changed and what's waiting on whom.

Run it on (or after) the Monday it's for. **Run date = that Monday** (YYYY-MM-DD); every
command below takes `--run-date <Monday>`.

Read `checklist.md` in this folder for the one-page tick list. This file explains each step.

---

## 0. Pre-flight (2 minutes)

1. `git checkout main && git pull` in `C:\Rushtons AI Upsell`; working tree clean apart from
   the two known untracked files.
2. **Database is up.** From `rushtons_engine/`:
   ```
   .venv\Scripts\python.exe -c "from query import run_sql; print(run_sql('select (select max(delivery_date) from orders) o, (select max(run_date) from recommendations) r, (select max(period_end) from customer_month_values) v')['rows'])"
   ```
   `tenant/user not found` or a DNS error = the Supabase project has paused (free-tier limit).
   Ask Jose to restore it in the Supabase dashboard (project `bhlgphaokfbhcetzersv`) — you
   can't. Note `o` (last order date loaded): the new order export must start the day after.
3. Tests: `.venv\Scripts\python.exe -m pytest -q` — must pass before you change anything.

## 1. Review the team's feedback since the last run

Do this **first** — it changes the rules for this week's selection. Use a background
general-purpose agent (read-only) so the Fresho download can run in parallel. Sources:

- **Gmail** (goodbytesnetwork@gmail.com): everything from/to `@rushtonsgreengrocer.co.uk`
  after the last run date — Hannah Turner, Laurence Copeman, Harry Pattison (and Ben, Lucie).
  Start with the reply thread on last week's pull email.
- **Fireflies**: any Rushton's meeting since the last run (titles like "Hannah + Jose",
  "Rushtons + Goodbytes"). Read the transcript, not just the summary.
- **Google Drive**: Meet transcripts / notes docs from the same dates.

Ask the agent for: exclusions/holds/un-holds (verbatim, who, date), product feedback
(out of season, wrong fit), tone feedback and any **original-vs-rewritten message pairs**,
results (who replied yes/no, boxes sent, reorders), dashboard requests, decisions/approvals,
data the team sent (produce list, contacts, Fresho fixes), unanswered questions to Jose.

Then **act on it** before step 3:
- Exclusions / holds → `rushtons_engine/config.py` (selection-only holds:
  `NO_SAMPLE_BOX_NAME_PATTERNS`, `HOLD_CUSTOMER_CODES`, `PARKED_VENUE_TYPES`,
  `GROUP_HOLD_UNTIL`, `NEW_ACCOUNT_GRACE_DAYS`; permanent: `EXCLUDED_*`). Add a dated comment
  quoting who asked. Add/adjust a test in `tests/test_select.py`; run the suite.
- Product feedback → a dated entry in `.claude/skills/rushtons-product-selection/feedback-log.md`;
  out-of-season lines → this month's `rushtons_engine/seasonal/seasonal_YYYY-MM.csv` (`out`).
- Tone feedback / rewrite pairs → `.claude/skills/rushtons-comms/feedback-log.md` and the best
  pairs into `examples.md`.
- Confirmed hand tiers → `config.TIER_OVERRIDES`.
- **Record last week's results** (every week — easy to miss): for the previous run date,
  set `recommendations.status` to `sent` / `rejected`, and add ledger rows with
  `interactions._insert` (idempotent `source_id` like `<source>|<code>|sent`):
  `whatsapp_sent` for each message sent (note which option, A or B), a `note` with the
  reason for each account not sent, and a `note` for every reply (yes/no, boxes sent).
  Republish the dashboard afterwards so the account timelines show them.
- Then **check every item in the team's latest emails off** against what's now in place,
  and list any you didn't action (and why) in the report.
- Anything ambiguous (e.g. "all caterers" — event only, or corporate too?) → don't guess;
  list it in the report as a question for Jose.

## 2. Download the Fresho exports (browser)

Use **Claude in Chrome** (the user's Chrome, already logged in to Fresho as Hannah Turner).
If no Chrome window/tab group is available, or Fresho shows a login page, **stop and ask the
user** to open Chrome and log in — never enter credentials yourself.

Page: `https://app.fresho.com/supplier/reports?company_id=30e1d749-b73a-4ecb-8e83-acf0aace02cc&mode=sell`
(company **Rushton's**; if the switcher shows "S. Thorogood & Sons HQ Account" that's the
buyer side — switch to Rushton's). Files land in `C:\Users\goodb\Downloads`.

**a. Orders — "Generate Operational Report" (top of the page)**
- Start date = day after the last loaded order date (pre-flight `o`); end date = the Sunday
  before the run date. The date inputs are native: click the day segment and type `DDMMYYYY`.
- Report Type **Product Totals By Customer** (default), Variant Standard, Order States All.
- Format: the select is inside a frame the page reader can't see — click it, press
  **Down** then **Enter** → CSV. Verify it reads CSV.
- **Generate Report**, scroll up to the "Generating Operational Report" dialog, wait for
  "Done.", click the `rushton_s_product_totals_by_customer_<timestamp>.csv` link, close.

**b. £ sales — "Sales Totals and Gross Profit Margin"**
- One **calendar month per file** (never a multi-month range — the loader skips it):
  current month **1st → run date**. In the first run of a new month, also export the
  **previous month in full**.
- Use `find` to get the Start/End/Format refs; `form_input` the dates (YYYY-MM-DD) and CSV,
  press Tab, then **take a screenshot to locate the Export button before clicking** — the
  page scrolls when the date fields take focus, so fixed coordinates miss. Click Export →
  "Download Report" → Close.
- File: `rushtons-greengrocers_sales_gp_margin_<start>_<end>.csv`.

**c. Customer master (when asked, or monthly)** — Fresho → Customers → Export. Fixes missing
account managers / venue types on new accounts (stub accounts are `account_stage = 'new (auto)'`).
Only ever load the newest master.

**Verify every download** with `ls -lt ~/Downloads | head` and, for orders, count lines per
`Delivery Date` and check every `Order State` is invoiced and the first date is after `o`
(overlapping dates DUPLICATE — trim if needed). A 125-byte GP file = headers only (no data).

## 3. Load + select (steps 1–2 of the engine)

Copy this week's files into a fresh scratchpad folder `run_<Monday>/` (orders CSV, GP CSV(s),
customer master if any), then from the repo root:
```
rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --data-dir <scratch>\run_<Monday> --run-date <Monday>
```
Check the log: lines ingested, GP months loaded, "selected 15 of N", brief + tracker written.
**Never clear the recommendations table** (the 8-week cooldown depends on it). The selection
is final — don't re-rank, add or drop accounts.

## 4. Pick products and write messages (steps 3–4)

Load **`rushtons-product-selection`** then **`rushtons-comms`** (each with its feedback-log —
never from memory). Read `output/drafting_brief_<Monday>.json` (write a compact pool summary to
the scratchpad to read it; pools are large).

- **Live web search of every venue** (15 searches, run in parallel) → `customer_review`.
- Pick up to 5 products from the account's pool; prefer `"season": "peak"` lines; drop gap
  categories that don't fit; one-line `why` each. Bars get bar-relevant lines only.
- Two message options (A with one dish idea, B simple, season-led). Follow-up is the
  definite send: "we're sending a few bits over tomorrow for you to try - x, y, z".
- `data_notes` for mis-typed venues, missing reps, groups, anything to fix at source.
- Save `output/drafts_<Monday>.json`, then apply:
```
rushtons_engine\.venv\Scripts\python.exe rushtons_engine\run_weekly.py --drafts rushtons_engine\output\drafts_<Monday>.json --run-date <Monday>
```
It hard-fails on any code outside a pool — fix the drafts, never the pool.

## 5. Dashboard: outreach drafts + state-preserving republish

1. **Outreach drafts** (`output/outreach_drafts.json`, keyed by customer code): rewrite for
   every account now on the Protect/Recover boards (top 12 of each), seasonal for this month,
   per `rushtons-comms`. Silent-decline drafts never mention the drop. Accounts on the
   no-sample-box list or parked segments get a tip-off without a box offer. Python never writes
   these words.
2. **Harvest the live page state** — `Artifact` read
   `https://claude.ai/artifact/FawjU1zri4r2CBfEViBbTW` with `path: index.html`, extract
   `<script id="dash-state">` to `state.json`, then
   `python interactions.py harvest state.json` (this also folds in lead Status changes and
   lead-to-account links made on the page).
   **Leads** need no download: the website forms post straight into the `leads` table via the
   `lead-intake` Edge Function (AI-rated, sales team emailed). Run
   `python rushtons_engine\leads.py retry` to finish any web lead whose AI write-up or email
   didn't complete, and mention in the report any lead from the week still unrated. The Google
   Sheet and n8n are retired — never re-import the sheet.
3. Rebuild with that state: `dashboard.write(date(<Monday>), state=json.load(open('state.harvested.json')))`
   (`run_weekly` rebuilds without state — never publish that file as is; it wipes assignments).
4. Publish the file to the same URL with `capabilities: {"artifact": {}}`.

## 6. Hand-over

- **Send Jose** (SendUserFile): `output/rushtons_upsell_tracker_<Monday>.xlsx` and any report
  the team asked for.
- **Draft (don't send) the weekly email** in Gmail to Hannah + Laurence, cc Harry, subject
  "AI Upsell – week of <date> (15 accounts)": what changed from their feedback, notes on this
  week's list (from `data_notes`/`customer_review`), the open asks per person, and a reminder
  to fill the tracker columns. Say in the report that the tracker must be attached by hand.
  Sending is Jose's call — never send without an explicit yes.
- **Memory**: update `rushtons-mvp-completion` / `rushtons-deployment-ops-state` with the run
  date, data dates, rule changes and open items.
- **Commit** config/test/skill changes on a branch, PR, merge after tests pass.

## 7. Report to Jose (short)

Data loaded (dates, lines, £ month), the 15 accounts (one line each, with anything notable),
rule changes made from feedback (and who asked), what's published, what's drafted, and
**waiting on**: per person (Jose / Hannah / Laurence / Harry). Flag anything you decided not
to change because it was ambiguous.

## Monthly extras (first run of each month)

- New `seasonal/seasonal_YYYY-MM.csv` from that month's market report (Hannah emails it, ~8th)
  plus last month's corrections — no file = no season flags.
- Previous month's full GP export (step 2b).
- Fresh customer master (step 2c).
- Refresh the tier-review CSV for Hannah if tiers moved a lot.
