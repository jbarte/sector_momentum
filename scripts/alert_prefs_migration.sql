-- Phase-2 personalized alerts: one ntfy topic per user.
-- Idempotent. Run once against the production DB via the Supabase SQL editor
-- (same operating model as positions_migration.sql).
-- Prereq: auth enabled; RLS already in use (scripts/enable_rls.sql, 2026-07-18).

create table if not exists public.alert_prefs (
  user_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  ntfy_topic text not null unique,
  enabled    boolean not null default true,
  created_at timestamptz not null default now()
);

alter table public.alert_prefs enable row level security;

drop policy if exists alert_prefs_owner on public.alert_prefs;
create policy alert_prefs_owner on public.alert_prefs
  for all to authenticated
  using (auth.uid() = user_id) with check (auth.uid() = user_id);

grant select, insert, update, delete on public.alert_prefs to authenticated;

-- Verification:
--   as anon          -> select/insert denied (no policy for anon)
--   as authenticated -> select returns only own row;
--                       insert with user_id != auth.uid() rejected by with check.
-- ntfy_topic is UNIQUE: two users must never share a topic, or one would
-- receive the other's holdings.
