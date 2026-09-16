---
name: rushtons-commercial-plays
description: Rushton's commercial playbook — four named plays for growing value from existing customers (protect, recover, grow, manage), each a ready-made report with live data and a chart. Load this when the user asks how to grow existing accounts, wants a commercial/board report, or names one of the plays (silent decline, win-back, peer gaps, rep scorecard).
---

# Rushton's Commercial Plays

Four repeatable plays for **growing value from the customers Rushton's already
has**. Each is a named report: live query → table → chart → a specific action.
Run one on request; run all four for a board/commercial review.

They map to the four things a Commercial Director can actually do with an
existing book:

| # | Play | Lever | The question it answers |
|---|------|-------|-------------------------|
| 1 | **Silent decline** | Protect | Who is quietly slipping *while still looking healthy*? |
| 2 | **Win-back** | Recover | Which good customers have stopped, and who's worth chasing first? |
| 3 | **Peer gaps** | Grow | Who buys from us but sources a staple elsewhere? |
| 4 | **Rep scorecard** | Manage | Where does the team need help or coaching? |

Run queries through `rushtons_engine/query.py::run_sql` (see the
`rushtons-analytics` skill for mechanics and the data caveats). Charts go
through the visualize `show_widget` tool.

**The one rule that governs every number here: volume, not revenue.** There is
no price data. Measures are order counts and order lines. Never present these as
£, and never sum `quantity` across products.

**A data trap these queries already handle:** the most recent `week_start` is
usually a *partial* week (the data ends mid-week), which fakes a decline for
everyone. Plays 1 and 4 exclude it by only comparing **complete** weeks. Never
drop that guard.

---

## Play 1 — Silent decline (PROTECT) ⭐

**Thesis.** The accounts that hurt most aren't the ones that stopped — those are
obvious. They're the ones still ordering every week, still "active" on every
report, quietly ordering half as much. Nobody calls them, because nothing looks
wrong. This play finds them while the relationship is still warm.

**Cohort.** Gold/silver, still active, ≥6 orders in the prior 4 weeks
(materiality), orders down ≥35% in the last 4 complete weeks.

```sql
with bounds as (
  select max(week_start) - interval '7 day' as last_complete from customer_week_metrics),
recent as (
  select customer_code, sum(order_count) o
  from customer_week_metrics, bounds
  where week_start > (select last_complete from bounds) - interval '28 day'
    and week_start <= (select last_complete from bounds) group by 1),
prior as (
  select customer_code, sum(order_count) o
  from customer_week_metrics, bounds
  where week_start > (select last_complete from bounds) - interval '56 day'
    and week_start <= (select last_complete from bounds) - interval '28 day' group by 1)
select h.customer_name, h.venue_type, h.size_band, h.sales_rep,
       p.o as prior_orders, r.o as recent_orders,
       round(100.0*(r.o - p.o)/nullif(p.o,0)) as pct_change
from prior p
join recent r on r.customer_code = p.customer_code
join v_customer_health h on h.customer_code = p.customer_code
where h.activity_status in ('active_regular','active_adhoc')
  and h.size_band in ('gold','silver')
  and p.o >= 6
  and (r.o - p.o)/nullif(p.o,0)::numeric <= -0.35
order by (p.o - r.o) desc
```

**Present as** a **dumbbell / slope chart** — one row per venue, two dots joined
by a line: prior-4-weeks orders → recent-4-weeks orders. The longer the line, the bigger
the fall. Sort by absolute orders lost (not %), so a 35→16 outranks a 10→2.
Colour the line by severity (amber ≥35%, red ≥60%).

**The line to say:** "These accounts still look fine on every report — they're
ordering this week. But they've halved. This is the cheapest revenue you'll ever
protect, because they haven't left yet."

**Action.** Rep phones within the week. The question is diagnostic, not salesy:
menu changed? seasonal? someone else quoting them? Feed anything learned back to
the account notes.

---

## Play 2 — Win-back priority (RECOVER)

**Thesis.** Once an account stops, value decays with time. Chase by *value ×
urgency*, not alphabetically or by whoever shouted last.

**Cohort.** Gold/silver, lapsed or long-lapsed, ranked by historical volume.

```sql
with vol as (
  select customer_code, sum(line_count) as total_lines
  from customer_category_metrics group by customer_code)
select h.customer_name, h.venue_type, h.size_band, h.prestige,
       h.activity_status, h.days_since_last_order,
       coalesce(v.total_lines, 0) as total_lines
from v_customer_health h
left join vol v on v.customer_code = h.customer_code
where h.activity_status in ('lapsed','long_lapsed')
  and h.size_band in ('gold','silver')
  and coalesce(h.prestige,'') <> 'Excluded'
order by (h.size_band = 'gold') desc, v.total_lines desc nulls last
limit 12
```

**Present as** a **scatter**: x = `days_since_last_order` ("more urgent →"),
y = `total_lines` ("more valuable →"), coloured by tier (gold `#c98500`,
silver `#2a78d6`), each point labelled with a short venue name.

**The line to say:** "Top-right is where you start — biggest and longest gone."
Name the 2–3 venues sitting there.

**Action.** Warm "we've missed you" WhatsApp for the top few — offer to draft
them with the `rushtons-comms` skill.

---

## Play 3 — Peer gaps / competitor displacement (GROW) ⭐

**Thesis.** When ~95% of comparable venues buy a category and this one doesn't,
that is almost never "they don't use it" — a pub not buying fruit still serves
garnish. It means **someone else is delivering it**. Each row is a share-of-
wallet loss with a named competitor behind it, which is a far stronger sales
conversation than a generic "here's a category you're missing".

**Cohort.** Active gold/silver accounts in a venue type with ≥10 peers, missing
a category ≥70% of their peers buy.

```sql
with active as (
  select customer_code, customer_name, venue_type, size_band, sales_rep
  from v_customer_health
  where activity_status in ('active_regular','active_adhoc')
    and venue_type is not null
    and venue_type not in ('Unknown','Manufacturing','Internal/Non-customer')),
peers as (select venue_type, count(*) n from active group by 1 having count(*) >= 10),
buys as (
  select a.venue_type, ccm.category, count(distinct a.customer_code) buyers
  from active a
  join customer_category_metrics ccm on ccm.customer_code = a.customer_code
  group by 1,2),
norm as (
  select p.venue_type, b.category, p.n, round(100.0*b.buyers/p.n) pct
  from peers p join buys b on b.venue_type = p.venue_type
  where b.category in (select category from v_targetable_categories))
select a.customer_name, a.venue_type, a.size_band, a.sales_rep,
       n.category, n.pct as peer_pct, n.n as peer_n
from norm n
join active a on a.venue_type = n.venue_type
left join customer_category_metrics c
       on c.customer_code = a.customer_code and c.category = n.category
where c.customer_code is null
  and n.pct >= 70
  and a.size_band in ('gold','silver')
order by n.pct desc, a.customer_name
```

**Present as** two things:
1. The named list — *"Hagen & Hyde (pub) doesn't buy Fruits. 97% of 75 pubs do."*
2. A **penetration heatmap**, venue type × category, % of active accounts buying
   (sequential single-hue blue, light = whitespace). This is the portfolio view
   that shows where whole segments are unsold — e.g. Prep Fruit & Juices sits at
   3% of pubs vs 26% of restaurants.

Heatmap query:
```sql
with active as (
  select customer_code, venue_type from v_customer_health
  where activity_status in ('active_regular','active_adhoc')
    and venue_type in ('Restaurants','Pubs','Bars','Hotels','Corporate Catering')),
tot as (select venue_type, count(*) n from active group by 1)
select t.venue_type, c.category, t.n,
       round(100.0*count(distinct a.customer_code)/t.n) pct
from tot t
cross join v_targetable_categories c
left join active a on a.venue_type = t.venue_type
left join customer_category_metrics m
       on m.customer_code = a.customer_code and m.category = c.category
where m.customer_code is not null
group by t.venue_type, c.category, t.n
order by t.venue_type, pct desc
```

**Action.** Two speeds. Named rows → rep asks "who looks after your fruit at the
moment?". Heatmap whitespace → a segment campaign (e.g. prep lines to pubs),
which is also the input the weekly upsell engine acts on.

**Caveat to state honestly:** a high-penetration gap is *strong evidence* of
displacement, not proof — a few venues genuinely don't use a category. Treat it
as the best call list available, not a certainty.

---

## Play 4 — Rep portfolio scorecard (MANAGE)

**Thesis.** Retention is a team behaviour. Comparing portfolios shows where to
put coaching and support — and often where someone is simply carrying too much.

```sql
select coalesce(h.sales_rep,'(unassigned)') as rep,
       count(*) as accounts,
       sum(case when h.activity_status in ('lapsed','long_lapsed') then 1 else 0 end) as lapsed,
       round(100.0*sum(case when h.activity_status in ('lapsed','long_lapsed') then 1 else 0 end)
             / nullif(count(*),0)) as lapsed_pct,
       round(avg(g.gaps),1) as avg_open_gaps
from v_customer_health h
left join (select customer_code, count(*) gaps from v_account_gaps group by 1) g
       on g.customer_code = h.customer_code
where coalesce(h.size_band,'') in ('gold','silver')
group by 1
having count(*) >= 5
order by accounts desc
```

**Present as** a **grouped bar** — accounts held vs lapsed % per rep — plus
`avg_open_gaps` as the untapped-opportunity measure.

**Action.** Rebalance load, or pair the rep with the highest lapse rate with
whoever has the lowest for a fortnight.

**Handle this play carefully — it names individuals.** Always state the
confound: a higher lapse rate may reflect *portfolio mix* (more small/volatile
venues, newer accounts, a tougher patch), not effort or skill. Present it as
"where does the team need support", never as a league table, and check account
counts before drawing any conclusion — a rep with 89 accounts and one with 69
are not doing the same job. If asked to rank people on it, push back and
reframe.

---

## The client dashboard

All four plays are also rendered as a shareable, interactive page by
`rushtons_engine/dashboard.py`, rebuilt automatically at the end of each
`run_weekly.py` run to `output/commercial_dashboard.html`. Accounts in plays 1
and 2 expand to a drill-down — 13-week sparkline (zero-filled, so a week with no
orders shows as a red dot rather than vanishing), categories they have stopped
buying, never-bought gaps, copy-ready outreach drafts, and an assign-to control
that saves for everyone via the `artifact` capability.

To refresh the client's link after a run, **republish that same file path to the
existing Artifact URL** (pass the artifact `url` if this session didn't publish
it) with `capabilities: {"artifact": {}}` so assignment keeps working. Same
link, new numbers — that is the intended weekly rhythm.

It cannot query Supabase live (the Artifact sandbox blocks external
connections), which costs nothing because the data only changes on the weekly
ingest.

### The suggested-actions pane and interactions timeline

The dashboard opens with **Suggested actions** — one deduplicated card per
account across the plays, priority transparent (gold first; decliners by orders
lost, then win-backs by history, then peer-gap upsells), capped at 18 with a
"+N more" note. Done/Dismiss and assignments persist for every viewer via the
page's saved state.

Each account drill-down carries a **timeline** fed by the `interactions` table
plus any not-yet-harvested page events, and one-tap buttons ("Mark WhatsApp
sent", "Mark email sent", notes). `run_weekly.py` derives `ordered_again`
outcomes automatically.

**Weekly republish procedure (order matters — a plain rebuild resets state):**
1. WebFetch the published artifact, extract the `dash-state` JSON to a file.
2. `python interactions.py harvest state.json` → writes `state.harvested.json`
   (page log moved into the ledger, assignments/actions kept).
3. Optional Outlook sync: query `customer_contacts` for addresses, search the
   connected mailbox via the Microsoft MCP connector (Claude does this — Python
   cannot call MCP), write `[{customer_code, direction, at, subject,
   counterparty, message_id}]` to a JSON file, then
   `python interactions.py emails events.json`. Metadata only, never bodies.
4. Run the weekly pipeline, regenerate `output/outreach_drafts.json` for the new
   board (see below).
5. Rebuild with state and republish to the same URL:
   `dashboard.write(state=json.load(open("state.harvested.json")))`.

### Weekly: regenerate the outreach drafts

`output/outreach_drafts.json` is keyed by `customer_code` and holds the
`whatsapp` / `email_subject` / `email_body` the page shows. **Write it yourself
against the `rushtons-comms` skill** (load it, plus its `feedback-log.md`) —
Python must never invent customer-facing words. Accounts without a draft render
as "draft pending", so a partial file is safe.

Two rules that matter for the decline drafts:
- **Never mention the decline.** "We noticed your orders have dropped" reads as
  surveillance and puts the customer on the back foot. The drop is why the *rep*
  is calling; the *message* leads with produce, per the tone guide.
- Win-back drafts use the lapsed re-engagement shape from `examples.md`
  ("it's been a little while and we've missed you") — that one is client-approved.

The client tone doc specifies **WhatsApp** as the channel; the email variants are
an extension and need sign-off before anyone sends them.

**Speed note.** Every play reads pre-aggregated summary tables (~100ms; the full
dashboard builds in ~1s). Never aggregate over `v_order_lines` — it scans all
order lines and takes ~9s. It is for drill-down only.

## Running the full review

For a board or commercial meeting, run all four in order — protect, recover,
grow, manage — and open with one paragraph: how many accounts are silently
declining, how many are recoverable, how many peer gaps are open, and the single
biggest segment whitespace. Then let each chart carry its own play.

### Dashboard layout & email notes (2026-08-28)

- The page is sectioned: **This week / Protect / Recover / Grow / Manage** — a
  left rail on desktop, horizontal tab bar on phones; KPI tiles jump to their
  section; `#protect`-style hashes deep-link.
- Suggested-action cards **expand inline** (sparkline, timeline, drafts,
  buttons) — no scrolling to the plays to act on a card.
- **Send email** button: a `mailto:` link prefilled with the draft and the
  account's first `customer_contacts` address; clicking also logs `email_sent`
  ("opened in mail app") to the timeline. Opening the mail app is not proof of
  sending — the Outlook sync is the ground truth.
- ⚠️ **Test contact in place**: `rushtonsdigital@gmail.com` is loaded as the
  contact for the 24 board accounts (label `test`) so sendouts/receipts can be
  tested. With one shared address, sync attribution by address is ambiguous —
  attribute by venue name in the subject during tests. **Replace with real
  contact data before the CS team uses the email button in anger** (delete
  where label='test', then load the real sheet).
- The published page republishes itself on every viewer action, so **expect a
  409 conflict when publishing after people have used the page**: WebFetch the
  artifact, extract `dash-state`, harvest, rebuild with that state, republish.

### Mini-CRM layer (2026-08-28, second pass)

- **Accounts section** (nav "Accounts"): every non-excluded customer (583) with
  search + status/tier filters. Each account expands to facts, **contacts**,
  timeline, mark/note/assign. "Add contact" on the page saves into state and is
  harvested into `customer_contacts` weekly — this is how contact data can
  accumulate organically from the CS team.
- **Contacts are CRM-shaped**: `customer_contacts(id, customer_code, name,
  label/role, email, phone)` — email or phone required. Email powers mailto +
  Outlook sync; phone powers `wa.me` links ("Open in WhatsApp" with the draft
  prefilled appears on board cards once a phone exists).
- **Communications area**: each board account's drill-down has WhatsApp | Email
  channel tabs; copy / open / send / mark-sent live inside their channel tab.
- **Status chip** everywhere (cards, play rows, accounts list): derived from the
  latest ledger/page event — "Email sent · date", "WhatsApp sent · date",
  "Ordered ✓", "Replied" — so follow-up is visible at a glance. It is derived,
  not manually set; logging the event IS setting the status.
- Harvest now also consumes `state.contacts` (idempotent upserts) and empties
  it in `<state>.harvested.json` alongside `log`.

### CRM v2: account pages + leads pipeline (2026-08-28, third pass)

- **Accounts are cards → dedicated pages** (hash route `#acct/<code>`, back
  button, shareable). The page shows the full Fresho CRM detail (legal entity,
  delivery/billing address, delivery run, payment terms, pricing level,
  internal notes — captured by `ingest_customers_file` from the full master;
  backfilled once from the June 2026 master for the 7 CRM columns only, never
  the live Fresho-owned fields), a JS-drawn 13-week sparkline, top categories,
  never-bought gaps, contacts, comms, timeline.
- **Leads pipeline** (`leads.py` + `leads` table): weekly mirror of the
  marketing Google Sheet (n8n feeds it; the SHEET stays source of truth — we
  never write back; Status is edited there). `python leads.py sync <csv>`
  loads idempotently (source_id = SessionID or email+date hash, match fields
  preserved across reloads) and auto-links Acquired leads to Fresho accounts
  (contact email/phone first, then name similarity ≥0.84). Manual links from
  the page land in `state.leadMatch`, harvested by `interactions.py harvest`.
  Rating normalised 1-5 ('Good'→4); test/personal/internal rows get
  `excluded_reason` and drop out of KPIs. Hannah's brief (Gmail,
  2026-08-06) is the spec; **her "£ value" needs a price feed — everything is
  volume until then, and the page says so.**
- Weekly procedure gains one step: re-download the sheet
  (`https://docs.google.com/spreadsheets/d/1DmIupaaEzIQ8qQDadpwq1HJob0_X2fIwbq0PdXX5SDM/export?format=csv`)
  and run `leads.py sync` before the dashboard rebuild.
