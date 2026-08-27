-- Read-only Supabase role for Metabase AND the Claude Code chat path (query.py).
-- Run in the Supabase dashboard → SQL Editor (runs as the postgres owner).
--
-- ORDER MATTERS: `grant select on all tables` only covers tables/views that
-- ALREADY EXIST. Run the engine against Supabase at least once first (that
-- applies the reporting views via init_db), OR re-run the two grant lines below
-- after the views exist. The `alter default privileges` line auto-covers
-- anything created LATER, but not objects that already existed when it ran.

create role rushtons_readonly login password 'CHANGE-ME';   -- then: alter role ... with password '...'
grant connect on database postgres to rushtons_readonly;
grant usage on schema public to rushtons_readonly;
grant select on all tables in schema public to rushtons_readonly;   -- includes views
alter default privileges in schema public grant select on tables to rushtons_readonly;
alter role rushtons_readonly set default_transaction_read_only = on;  -- writes error server-side
alter role rushtons_readonly set statement_timeout = '10s';           -- protects the free tier

-- To change the password later without recreating the role:
--   alter role rushtons_readonly with password 'NEW-STRONG-PASSWORD';
--
-- To re-grant after new reporting views are created:
--   grant select on all tables in schema public to rushtons_readonly;
--
-- Sanity check:
--   select rolname, rolcanlogin, rolconnlimit from pg_roles where rolname = 'rushtons_readonly';
