-- Leads app (leads_app/): the team's live leads CRM, signed in with Supabase Auth.
--
-- Who may use it: public.app_users (an allowlist). A sign-in for any other
-- email is refused when Supabase tries to create the account, so nobody else
-- can even hold a login. Signed-in team members can READ leads and their
-- activity; every change goes through log_lead_activity / link_lead_account,
-- which record who did it (their app_users name) and apply it to the lead —
-- the same rules as leads.apply_activity_log in Python.
--
-- Applied by db.apply_leads_app_sql on every init (Postgres only); idempotent.
-- Add a person:  python leads.py adduser <email> "<Name>"

create table if not exists public.app_users (
    email      text primary key check (email = lower(email)),
    name       text not null,
    role       text not null default 'team' check (role in ('team', 'admin')),
    active     boolean not null default true,
    created_at timestamptz not null default now()
);
alter table public.app_users enable row level security;
revoke all on public.app_users from anon, authenticated;

-- The signed-in person's name, or null when they are not on the allowlist.
create or replace function public.app_user_name() returns text
language sql stable security definer set search_path = public as $$
    select name from public.app_users
    where active and email = lower(coalesce(auth.jwt() ->> 'email', ''))
$$;
revoke all on function public.app_user_name() from public, anon;
grant execute on function public.app_user_name() to authenticated;

-- Refuse to create a login for anyone not on the allowlist.
create or replace function public.app_users_only() returns trigger
language plpgsql security definer set search_path = public as $$
begin
    if not exists (select 1 from public.app_users
                   where active and email = lower(coalesce(new.email, ''))) then
        raise exception 'This email is not on the Rushton''s leads app list';
    end if;
    return new;
end $$;
drop trigger if exists app_users_only on auth.users;
create trigger app_users_only before insert on auth.users
    for each row execute function public.app_users_only();

-- Read access for the team; no direct writes (the functions below do those).
alter table public.leads enable row level security;
alter table public.lead_activities enable row level security;
drop policy if exists leads_app_read on public.leads;
create policy leads_app_read on public.leads for select to authenticated
    using (public.app_user_name() is not null);
drop policy if exists leads_app_read on public.lead_activities;
create policy leads_app_read on public.lead_activities for select to authenticated
    using (public.app_user_name() is not null);
grant select on public.leads, public.lead_activities to authenticated;

-- Live updates in the app (Supabase Realtime honours the policies above).
do $$
begin
    if exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
        if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime'
                       and schemaname = 'public' and tablename = 'leads') then
            alter publication supabase_realtime add table public.leads;
        end if;
        if not exists (select 1 from pg_publication_tables where pubname = 'supabase_realtime'
                       and schemaname = 'public' and tablename = 'lead_activities') then
            alter publication supabase_realtime add table public.lead_activities;
        end if;
    end if;
end $$;

-- Who is signed in, the owner list and the Fresho accounts for linking.
-- Team = everyone with Fresho accounts (first names, as dashboard._lead_team)
-- plus the app's own users; config.LEAD_TEAM_NOT_PEOPLE are left out.
create or replace function public.leads_app_lookups() returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare
    me text := public.app_user_name();
begin
    if me is null then
        raise exception 'not allowed' using errcode = '42501';
    end if;
    return jsonb_build_object(
        'me', me,
        'team', (select coalesce(jsonb_agg(n order by n), '[]'::jsonb) from (
            select btrim(regexp_replace(sales_rep, '\s*\(.*\)\s*$', '')) as n
            from public.customers where sales_rep is not null
            union
            select name from public.app_users where active
        ) t where n <> '' and n not in ('Market Customer', 'Customer Service')),
        'accounts', (select coalesce(jsonb_agg(jsonb_build_array(
                customer_code, customer_name, activity_status, last_order_date)
                order by customer_name), '[]'::jsonb)
            from public.customers where customer_name is not null)
    );
end $$;
revoke all on function public.leads_app_lookups() from public, anon;
grant execute on function public.leads_app_lookups() to authenticated;

-- One team action on a lead: logged with the person's name, then applied.
create or replace function public.log_lead_activity(
    p_sid text, p_kind text, p_stage text default null,
    p_note text default null, p_due date default null) returns void
language plpgsql security definer set search_path = public as $$
declare
    me text := public.app_user_name();
    n  text := nullif(btrim(coalesce(p_note, '')), '');
begin
    if me is null then
        raise exception 'not allowed' using errcode = '42501';
    end if;
    if not exists (select 1 from public.leads where source_id = p_sid) then
        raise exception 'unknown lead';
    end if;
    if p_kind not in ('stage', 'note', 'follow_up', 'owner', 'verdict',
                      'call', 'email', 'whatsapp', 'visit', 'sample') then
        raise exception using message = 'unknown action: ' || p_kind;
    end if;
    if p_kind = 'stage' and coalesce(p_stage, '') not in
            ('New', 'Contacted', 'Qualified', 'Trial', 'Acquired', 'Discarded') then
        raise exception using message = 'unknown stage: ' || coalesce(p_stage, '');
    end if;
    if p_kind = 'verdict' and coalesce(n, '') not in
            ('Pursue with urgency', 'Pursue', 'Nurture', 'Pass to rep', 'Reject') then
        raise exception using message = 'unknown verdict: ' || coalesce(n, '');
    end if;

    insert into public.lead_activities
        (event_id, source_id, occurred_at, kind, stage, note, follow_up_on, actor, source)
    values ('app|' || gen_random_uuid(), p_sid, now(), p_kind,
            case when p_kind = 'stage' then p_stage end, left(n, 1000), p_due,
            me, 'app');

    update public.leads set
        updated_at        = now(),
        status            = case when p_kind = 'stage' then p_stage else status end,
        status_changed_at = case when p_kind = 'stage' then now() else status_changed_at end,
        lost_reason       = case when p_kind <> 'stage' then lost_reason
                                 when p_stage = 'Discarded' then n end,
        -- a stage move or follow-up sets (or clears) the hand-set date;
        -- any other action only sets it when it carries one
        next_follow_up    = case when p_kind in ('stage', 'follow_up') then p_due
                                 else coalesce(p_due, next_follow_up) end,
        owner             = case when p_kind = 'owner' then n else owner end,
        verdict           = case when p_kind = 'verdict' then n else verdict end,
        verdict_source    = case when p_kind = 'verdict' then 'team' else verdict_source end
    where source_id = p_sid;
end $$;
revoke all on function public.log_lead_activity(text, text, text, text, date) from public, anon;
grant execute on function public.log_lead_activity(text, text, text, text, date) to authenticated;

-- Link a lead to its Fresho account ('' or null clears the link).
create or replace function public.link_lead_account(p_sid text, p_code text) returns void
language plpgsql security definer set search_path = public as $$
declare
    me text := public.app_user_name();
    c  text := nullif(btrim(coalesce(p_code, '')), '');
begin
    if me is null then
        raise exception 'not allowed' using errcode = '42501';
    end if;
    if c is not null and not exists (select 1 from public.customers where customer_code = c) then
        raise exception 'unknown account';
    end if;
    update public.leads set
        matched_customer_code = c,
        match_method = case when c is null then null else 'manual' end,
        match_score  = null,
        matched_at   = case when c is null then null else now() end,
        updated_at   = now()
    where source_id = p_sid;
    insert into public.lead_activities
        (event_id, source_id, occurred_at, kind, note, actor, source)
    values ('app|' || gen_random_uuid(), p_sid, now(), 'note',
            case when c is null then 'Fresho account link cleared'
                 else 'Linked to Fresho account ' || c end, me, 'app');
end $$;
revoke all on function public.link_lead_account(text, text) from public, anon;
grant execute on function public.link_lead_account(text, text) to authenticated;

notify pgrst, 'reload schema';
