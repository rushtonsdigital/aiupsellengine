---
name: rushtons-analytics
description: Answering ad-hoc business-intelligence questions about Rushton's data (sales/volume, accounts, categories, products, the upsell funnel) from a Claude Code session. Load this whenever the user asks a question of the data rather than running the weekly pipeline. Queries a read-only reporting layer — never writes.
---

# Rushton's Analytics (data chat)

Use this to answer questions about Rushton's data — accounts, products, categories, ordering
patterns, and the upsell funnel — without running the weekly pipeline. It is **read-only**: you
query a curated reporting layer and report back. You never write, and you never run the selection
or drafting steps from here.

Predefined dashboards live in Metabase (for the ops team and leadership). This skill is the
ad-hoc, conversational path: the user asks in plain English, you translate to SQL against the
reporting views, run it safely, and answer.

## Three hard truths about this data — say them when they matter

1. **Volume, not revenue.** There is **no price or spend anywhere** in the source. Every measure
   is a *volume* measure: number of orders, number of order lines, and `quantity`. If someone
   asks "how much has X spent / what's our revenue", you cannot answer in money — answer in
   orders/lines and say so plainly.
2. **Never sum `quantity` across products.** `qty_type` is mixed (Each / kg / box), so a single
   summed `total_qty` is meaningless. Only ever use quantity **per product** (or grouped by
   `qty_type`). For "how much did they buy", prefer **distinct orders** or **line counts**.
3. **Invoiced only.** The reporting layer already filters to invoiced lines; you don't add that
   filter, but know that quotes/uninvoiced orders are not in these numbers.

## How to run a query

Always go through the read-only helper — do not open your own DB connection or use the writer
`DATABASE_URL`:

```python
import sys; sys.path.insert(0, "rushtons_engine")   # if not already importable
import query
query.describe()                    # {view/table: [columns]} — orient yourself first
query.run_sql("select ... ")        # -> {columns, rows, truncated, rowcount}
```

Run it with the engine's venv:
`rushtons_engine\.venv\Scripts\python.exe`.

`run_sql` enforces read-only (single SELECT/WITH, forbidden-keyword block, auto `LIMIT`, a
`rushtons_readonly` role in production). If it raises, fix the SQL — never try to bypass it.
Put literal values **inline** in the SQL (there is no bound-parameter path). If a result comes
back `truncated: true`, tighten the query or raise the `limit=` argument deliberately.

## The reporting layer (query these, not the raw tables)

Prefer these pre-labelled views/tables over raw `orders`/`customers`/`products` — they are clean,
fast, and already carry human labels.

**Views** (Postgres/Supabase):
- `v_order_lines` — one row per invoiced order line, fully labelled (customer name/venue/size/
  status, product name/category, quantity, qty_type, `month_start`). The base for drill-down.
- `v_monthly_customer_sales` — monthly volume per customer: `orders`, `lines`, `qty` (volume).
- `v_category_penetration` — per targetable category: `buying_accounts`, `active_accounts`,
  `penetration` (0–1).
- `v_account_gaps` — one row per active account × targetable category it does **not** buy. This
  answers "what is <account> missing?" and "who doesn't buy <category>?".
- `v_customer_health` — one row per customer: `activity_status`, `size_band`, `prestige`,
  `last_order_date`, `days_since_last_order`.
- `v_lapsing_accounts` — at-risk accounts (lapsed/long_lapsed or overdue), most overdue first.
- `v_recommendation_funnel` — one row per recommendation: `rank`, `score`, `status`, `has_comms`,
  `sent`, `converted`, plus venue/size. Conversion by rank/segment/status.
- `v_product_performance` — per product: `distinct_buyers`, `line_count`, `total_qty`,
  `buyers_14d`, `buyers_90d`, `trend` (up/steady/down).

**Summary tables** (also present on SQLite dev/test):
- `customer_category_metrics` — per (customer, category): `line_count`, `order_count`,
  `total_qty`, `first_bought`, `last_bought`.
- `product_metrics` — the raw numbers behind `v_product_performance`.
- `recommendation_category_facts` — per (run_date, customer, category): `offered`, `chosen`,
  `rec_status`. Use for **conversion by category**.
- `customer_week_metrics` — per (customer, week_start): weekly `order_count`, `distinct_lines`,
  `distinct_cats`, `total_qty`.

Key dimensions: `size_band` (gold/silver/bronze), `activity_status`
(active_regular/active_adhoc/lapsed/long_lapsed/temporarily_closed/excluded), `venue_type`,
`prestige` (VIP/Standard/Excluded), `category`.

## Worked examples

- *"Which Gold restaurants aren't buying Mushroom?"*
  ```sql
  select customer_name from v_account_gaps
  where size_band = 'gold' and venue_type = 'Restaurants' and category = 'Mushroom'
  order by customer_name
  ```
- *"Top 20 products by number of buyers"*
  ```sql
  select product_name, category, distinct_buyers, trend
  from v_product_performance order by distinct_buyers desc limit 20
  ```
- *"Upsell conversion rate by rank"*
  ```sql
  select rank,
         round(avg(case when converted then 1.0 else 0.0 end), 3) as conversion,
         count(*) as recommendations
  from v_recommendation_funnel group by rank order by rank
  ```
- *"Which categories convert best when we pitch them?"*
  ```sql
  select category,
         sum(case when chosen then 1 else 0 end) as pitched,
         sum(case when chosen and rec_status = 'converted' then 1 else 0 end) as converted
  from recommendation_category_facts group by category order by pitched desc
  ```
- *"Accounts most overdue an order"*
  ```sql
  select customer_name, activity_status, days_since_last_order
  from v_lapsing_accounts limit 25
  ```

## When you answer

- Lead with the answer in plain English; show a small table only if it helps.
- If the number is a volume (orders/lines), say so — don't let it read as revenue.
- If a question needs money, or menu-matching, or anything not in this data, say what's missing
  rather than inventing it (a price feed is future scope, noted in the BI plan).
- If asked to change data, decline and point to the weekly pipeline — this path is read-only.
