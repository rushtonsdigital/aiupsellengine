# Rushton's BI — Looker Studio guide (free, no server)

Looker Studio is Google's free dashboard tool. It connects straight to your
Supabase database and reads the reporting data the engine built. Nothing to
install, nothing to pay for, nothing to keep running.

You do this **once**. After that, you and leadership just open the dashboard link.

---

## Before you start

Have ready: the **password you set for the `rushtons_readonly` login** in Supabase.
(That's the read-only login — it can look at data but can never change anything.)

## Your connection details (copy these exactly)

| Field | Value |
|---|---|
| **Host / Server** | `aws-1-eu-west-2.pooler.supabase.com` |
| **Port** | `5432` |
| **Database** | `postgres` |
| **Username** | `rushtons_readonly.bhlgphaokfbhcetzersv` |
| **Password** | *(the read-only password you set)* |
| **Enable SSL** | **Yes / ticked** |

> If the username is rejected, try plain `rushtons_readonly`. The exact format is
> shown in Supabase → **Connect** → *Session pooler* if you ever need to check.

---

## Step by step

1. Go to **https://lookerstudio.google.com** and sign in with your Google account.
2. Click **Create** (top-left) → **Data source**.
3. In the connector list, search for and pick **PostgreSQL** (by Google).
4. Fill in the form using the table above. Tick **Enable SSL**. Click **Authenticate**.
5. It now asks which table to use. Choose a **view** from the list — start with
   `v_customer_health`. Click **Connect** (top-right), then **Create Report** →
   **Add to report**.
6. You now have a blank dashboard with that data. Add a chart: **Add a chart** →
   pick e.g. a bar chart, and drag in fields. Save happens automatically.

To add more data (the other views), use **Resource → Manage added data sources →
Add a data source** and repeat step 3–5 for each view you want.

---

## Which view for which chart

Use the **summary views** for charts (they're small and fast). Use `v_order_lines`
only for a detail table with filters — it's the big raw one.

| You want to show… | Use this view | Good chart |
|---|---|---|
| Orders per month, per customer | `v_monthly_customer_sales` | Time-series / bar |
| How many accounts buy each category | `v_category_penetration` | Bar (sort by penetration) |
| Which accounts miss which category | `v_account_gaps` | Table, filter by category |
| Account health mix | `v_customer_health` | Pie/bar on `activity_status` |
| Who's overdue an order | `v_lapsing_accounts` | Table, sort by `days_since_last_order` |
| Are our upsell messages working | `v_recommendation_funnel` | Scorecards: sent / converted |
| Best / worst performing products | `v_product_performance` | Table, sort by `distinct_buyers` |
| Line-level detail (drill-down) | `v_order_lines` | Table with date & customer filters |

**A good first dashboard (30 mins):** one page with — a bar of
`v_category_penetration`, a pie of `v_customer_health` by `activity_status`, a
table from `v_lapsing_accounts`, and two scorecards from `v_recommendation_funnel`
(sent, converted). That already tells leadership a lot.

---

## Sharing with leadership

Top-right **Share** → add their email, or **Share → Manage access → Anyone with the
link → Viewer**. They just open the link — no login/setup needed for viewers.

## Two things to remember about the numbers

- Every measure is **volume, not revenue** — order counts, line counts, quantity.
  There's no price data, so nothing here is in £.
- **Don't sum `quantity` across different products** — the units are mixed
  (each / kg / box). Count orders or lines instead.

## If something doesn't connect

- **SSL error:** make sure *Enable SSL* is ticked. If it still refuses, download
  Supabase's certificate (Supabase → Settings → Database → SSL) and upload it in
  the connector's SSL section.
- **Username rejected:** try `rushtons_readonly` without the `.bhlg…` suffix.
- **Password:** it's the `rushtons_readonly` one, not your main Supabase password.
  You can reset it in Supabase SQL Editor:
  `alter role rushtons_readonly with password 'NEW-PASSWORD';`
