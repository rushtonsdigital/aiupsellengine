# Self-hosted Metabase for Rushton's BI

The **free, open-source** Metabase — not Metabase Cloud ($100/mo). Same software,
$0 licence. You only pay for a box to run it on (or nothing).

Metabase reads your data from Supabase through the read-only `rushtons_readonly`
role, over the reporting views/tables the engine builds. It never writes.

## 1. Pick where to run it

Any Docker host works. Cheapest options, best first for leadership access:

- **~$5/mo VPS** (Hetzner ~€4, DigitalOcean/Linode/Vultr ~$5–6) — always on,
  reachable from anywhere. Recommended.
- **Free tiers** — Fly.io small VM or Oracle Cloud Always Free. Zero cost, a bit
  more fiddly; watch that free tiers don't sleep.
- **Your own PC** — £0, but only reachable while that machine is on and only on
  your network unless you expose it. Fine for evaluating.

## 2. Start Metabase

On the host, from this folder:

```bash
cp .env.example .env
# edit .env: set METABASE_APP_DB_PASSWORD to anything strong
docker compose up -d
```

Open `http://<host>:3000` (give it ~1–2 min on first boot), and create the admin
account. That admin login is yours; add ops/leadership as users later under
**Admin → People**.

> The `metabase-db` container stores Metabase's own dashboards/users — not your
> data. Your data lives in Supabase and is added in the next step.

## 3. Create the read-only role (once)

If you haven't already, run [`rushtons_readonly.sql`](rushtons_readonly.sql) in the
Supabase **SQL Editor**. Read the ordering note at the top — the grants only cover
objects that already exist, so run the engine against Supabase first (or re-run the
grant line afterwards) so the `v_*` views are included.

## 4. Connect Supabase as a data source

In Metabase: **Admin → Databases → Add database → PostgreSQL**, then:

| Field | Value |
|---|---|
| Display name | `Rushton's (Supabase)` |
| Host | your Supabase **session pooler** host, e.g. `aws-1-eu-west-2.pooler.supabase.com` |
| Port | `5432` |
| Database name | `postgres` |
| Username | `rushtons_readonly` — or `rushtons_readonly.<project-ref>` if the pooler requires the `role.ref` form (check Supabase → Connect) |
| Password | the role's password |
| Use a secure connection (SSL) | **on** |

Save. Metabase syncs the schema and you'll see the `v_*` views and summary tables.

## 5. Build the starter dashboards

Base each question on a view (not raw tables). Suggested first four:

- **Volume overview** — `v_monthly_customer_sales` (trend), `v_order_lines` (drill-down)
- **Account health & lapsing** — `v_customer_health`, `v_lapsing_accounts`
- **Category penetration & gaps** — `v_category_penetration`, `v_account_gaps`
- **Upsell funnel** — `v_recommendation_funnel`, plus `recommendation_category_facts`
  for conversion by category

> Remember: every measure is **volume, not revenue** (there's no price data), and
> never sum `quantity` across products (mixed `qty_type`). Headline on order and
> line counts.

## Updating / backups

- Update Metabase: `docker compose pull && docker compose up -d`.
- Your dashboards live in the `metabase-db-data` volume — back that up if the
  dashboards become valuable (`docker compose exec metabase-db pg_dump ...`).
