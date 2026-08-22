-- Rushton's AI Upsell Engine — reporting VIEWS (Postgres / Supabase only)
--
-- Applied by db.apply_reporting_views(), called from init_db() on every run,
-- but ONLY on Postgres — these use date_trunc / cross join / ::date casts and
-- date arithmetic that the SQLite test/dev fallback lacks. Metabase and the
-- read-only chat path (query.py) read these views + the summary tables, never
-- the raw orders table live.
--
-- `create or replace view` keeps this file the single source of truth: it is
-- idempotent and re-applied each run. NOTE: replacing a view whose COLUMN set
-- changed needs a manual `drop view ... cascade` first (Postgres won't rename
-- view columns in place) — rare, and a deliberate step when it happens.
--
-- Every measure is a VOLUME measure (order counts, line counts, quantity).
-- There is no price/revenue in the source. `qty` mixes qty_type (Each/kg/box)
-- and is meaningful only grouped by product — never a single summed total.

-- The categories worth pitching a sample box around. MUST mirror
-- config.TARGETABLE_CATEGORIES — kept here as a view so the penetration and gap
-- views below share one definition. Update both together if the list changes.
create or replace view v_targetable_categories (category) as
values
  ('Vegetables'), ('Potatoes'), ('Salads'), ('Tomatoes'), ('Fruits'),
  ('Italian'), ('Baby Vegetables'), ('Exotic Fruit & Veg'), ('Mushroom'),
  ('Herbs'), ('Micros, Leaves & Flowers'), ('Dry Stores & Non Food'),
  ('Prep Fruit & Juices'), ('Prep Vegetables'), ('Frozen Produce'),
  ('Dairy and Chilled');

-- Base fact for drill-down and ad-hoc chat: one row per invoiced order line,
-- fully labelled so no consumer has to join.
create or replace view v_order_lines as
select o.id, o.order_number, o.delivery_date,
       date_trunc('month', o.delivery_date)::date as month_start,
       o.customer_code, c.customer_name, c.venue_type, c.size_band,
       c.activity_status, c.prestige, c.sales_rep,
       o.product_code, p.product_name, p.category,
       p.out_of_season, p.delisted,
       o.quantity, o.qty_type
from orders o
join customers c on c.customer_code = o.customer_code
join products  p on p.product_code  = o.product_code
where o.order_state = 'Invoiced';

-- Monthly volume per customer, aggregated over the small weekly pre-agg
-- (customer_week_metrics.week_start is already Monday-truncated), so date_trunc
-- never touches the 1M-row orders table.
create or replace view v_monthly_customer_sales as
select m.customer_code, c.customer_name, c.venue_type, c.size_band,
       date_trunc('month', m.week_start)::date as month_start,
       sum(m.order_count)    as orders,
       sum(m.distinct_lines) as lines,
       sum(m.total_qty)      as qty      -- volume, mixed qty_type
from customer_week_metrics m
join customers c on c.customer_code = m.customer_code
group by m.customer_code, c.customer_name, c.venue_type, c.size_band,
         date_trunc('month', m.week_start)::date;

-- Portfolio view: for each targetable category, how many active accounts buy it
-- vs total active accounts, and the penetration ratio.
create or replace view v_category_penetration as
with active as (
  select customer_code from customers
  where activity_status in ('active_regular', 'active_adhoc')
)
select t.category,
       count(distinct ccm.customer_code)                        as buying_accounts,
       (select count(*) from active)                            as active_accounts,
       round(count(distinct ccm.customer_code)::numeric
             / nullif((select count(*) from active), 0), 3)     as penetration
from v_targetable_categories t
left join customer_category_metrics ccm
       on ccm.category = t.category
      and ccm.customer_code in (select customer_code from active)
group by t.category;

-- Account-level drill-down: which targetable categories an active account does
-- NOT buy — the gap list. Also answers "what is <account> missing?" in chat.
create or replace view v_account_gaps as
select c.customer_code, c.customer_name, c.venue_type, c.size_band,
       c.activity_status, t.category
from customers c
cross join v_targetable_categories t
left join customer_category_metrics ccm
       on ccm.customer_code = c.customer_code
      and ccm.category = t.category
where c.activity_status in ('active_regular', 'active_adhoc')
  and ccm.customer_code is null;

-- One row per customer for the activity-status distribution and at-a-glance
-- health. days_since_last_order uses the latest delivery date in the data as
-- "today" (the engine's clock is the data, not the wall clock).
create or replace view v_customer_health as
select c.customer_code, c.customer_name, c.venue_type, c.size_band,
       c.prestige, c.activity_status, c.sales_rep,
       c.first_seen, c.last_order_date,
       (select max(delivery_date) from orders) - c.last_order_date
         as days_since_last_order
from customers c;

-- At-risk accounts: lapsed/long_lapsed, or simply overdue, excluding accounts
-- that are deliberately excluded or temporarily closed.
create or replace view v_lapsing_accounts as
select *
from v_customer_health
where coalesce(activity_status, '') not in ('excluded', 'temporarily_closed')
  and (activity_status in ('lapsed', 'long_lapsed')
       or (days_since_last_order is not null and days_since_last_order > 21))
order by days_since_last_order desc nulls last;

-- The upsell funnel: one row per recommendation, with delivery signals.
-- Conversion by rank / segment / status. Conversion BY CATEGORY comes from the
-- recommendation_category_facts summary table (JSON flattened in Python).
create or replace view v_recommendation_funnel as
select r.run_date, r.customer_code, c.customer_name, c.venue_type, c.size_band,
       r.rank, r.score, r.status,
       count(co.id) > 0       as has_comms,
       count(co.sent_at) > 0  as sent,
       (r.status = 'converted') as converted
from recommendations r
join customers c on c.customer_code = r.customer_code
left join comms co on co.recommendation_id = r.id
group by r.run_date, r.customer_code, c.customer_name, c.venue_type,
         c.size_band, r.rank, r.score, r.status;

-- Product performance with labels and a coarse trend from the recent vs longer
-- buyer windows (90 days spans ~6.4 x 14 days, so buyers_90d/6 is the rough
-- 14-day-equivalent baseline).
create or replace view v_product_performance as
select p.product_code, p.product_name, p.category, p.raw_product_group,
       p.out_of_season, p.delisted,
       pm.distinct_buyers, pm.line_count, pm.total_qty,
       pm.buyers_14d, pm.buyers_90d, pm.first_sold, pm.last_sold,
       case
         when pm.buyers_14d > pm.buyers_90d / 6.0  then 'up'
         when pm.buyers_14d < pm.buyers_90d / 12.0 then 'down'
         else 'steady'
       end as trend
from product_metrics pm
join products p on p.product_code = pm.product_code;
