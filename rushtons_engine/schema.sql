-- Rushton's AI Upsell Engine — canonical Postgres schema (Supabase)
-- Local/dev runs use the same structure created via SQLAlchemy metadata (db.py).
-- NOTE: orders has NO unique (order_number, product_code) constraint on purpose:
-- real exports contain identical duplicate lines within one file (172 pairs in June 2026).
-- Idempotency is delete-where-source_file-then-insert.

create table if not exists customers (
  customer_code    text primary key,
  customer_name    text,
  venue_type       text,            -- tag position 1
  group_size_band  text,            -- tag position 2
  group_affiliation text,           -- tag position 3 (or 5 when 3 empty)
  account_stage    text,            -- tag position 4
  order_channel    text,            -- tag position 5
  sales_rep        text,
  active           boolean,         -- Fresho active flag
  prestige         text,            -- 'VIP' | 'Standard' | 'Excluded'
  raw_tags         text,
  first_seen       date,
  last_order_date  date,
  activity_status  text,            -- active_regular|active_adhoc|lapsed|long_lapsed|temporarily_closed|excluded
  size_band        text,            -- gold|silver|bronze (volume proxy — see decision 1)
  updated_at       timestamptz default now()
);

create table if not exists products (
  product_code       text primary key,
  product_name       text,
  raw_product_group  text,          -- latest non-Z888 group seen
  category           text,          -- canonical, BULK/SPLIT collapsed
  out_of_season      boolean default false,  -- latest group was Z888
  delisted           boolean default false,  -- latest group was Z999
  first_seen         date,
  last_seen          date,
  updated_at         timestamptz default now()
);

create table if not exists orders (
  id            bigserial primary key,
  order_number  bigint,
  customer_code text references customers(customer_code),
  product_code  text references products(product_code),
  delivery_date date,
  quantity      numeric,
  qty_type      text,
  order_state   text,
  delivery_run  text,
  source_file   text not null,
  ingested_at   timestamptz default now()
);
create index if not exists idx_orders_source_file on orders(source_file);
create index if not exists idx_orders_customer on orders(customer_code);
create index if not exists idx_orders_delivery_date on orders(delivery_date);
create index if not exists idx_orders_product_code on orders(product_code);

create table if not exists customer_week_metrics (
  customer_code   text references customers(customer_code),
  week_start      date,             -- Monday
  order_count     int,
  distinct_lines  int,              -- distinct SKUs that week
  distinct_cats   int,              -- distinct canonical categories that week
  total_qty       numeric,
  primary key (customer_code, week_start)
);

create table if not exists recommendations (
  id                 bigserial primary key,
  run_date           date not null,
  customer_code      text references customers(customer_code),
  rank               int,
  score              numeric,
  gap_categories     jsonb,         -- list of canonical category names
  -- step 2 (code): everything eligible to pitch, per gap category
  product_pool       jsonb,         -- {category: [{code, name, product_group, buyers_14d}]}
  -- step 3 (drafter): the few actually pitched, chosen from product_pool.
  -- null until drafts are applied; every code here must exist in product_pool.
  chosen_products    jsonb,         -- [{code, name, category, why}]
  rationale          text,
  status             text default 'proposed',  -- proposed|approved|rejected|sent|converted
  created_at         timestamptz default now(),
  unique (run_date, customer_code)
);

create table if not exists comms (
  id                bigserial primary key,
  recommendation_id bigint references recommendations(id),
  stage             text,           -- announcement|followup|postbox
  channel           text default 'whatsapp',
  draft_body        text,
  approved_by       text,
  sent_at           timestamptz,
  created_at        timestamptz default now()
);

-- --- reporting / BI layer ----------------------------------------------------
-- Pre-aggregated summary tables, fully recomputed each weekly run by
-- reporting.py (same delete-then-bulk-insert pattern as customer_week_metrics).
-- Metabase and the read-only chat path read these + the reporting.sql views,
-- never the raw orders table live, so reports stay instant as history grows.
-- Every measure is VOLUME (order/line counts, quantity) — there is no
-- price/revenue in the source. total_qty mixes qty_type; meaningful only per
-- product, never summed across types.

create table if not exists customer_category_metrics (
  customer_code text references customers(customer_code),
  category      text,
  line_count    int,
  order_count   int,
  total_qty     numeric,
  first_bought  date,
  last_bought   date,
  primary key (customer_code, category)
);

create table if not exists product_metrics (
  product_code    text primary key references products(product_code),
  distinct_buyers int,
  line_count      int,
  total_qty       numeric,
  buyers_14d      int,             -- distinct buyers in the last 14 days
  buyers_90d      int,             -- distinct buyers in the last 90 days
  first_sold      date,
  last_sold       date
);

-- gap_categories / chosen_products JSON on recommendations, flattened in Python
-- (reporting.refresh_funnel) so conversion-by-category is a plain group-by.
create table if not exists recommendation_category_facts (
  run_date      date,
  customer_code text,
  category      text,
  offered       boolean,
  chosen        boolean,
  rec_status    text,
  primary key (run_date, customer_code, category)
);
