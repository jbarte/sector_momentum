-- Phase-2 personalized alerts: one ntfy topic per user.
-- Idempotent. Run once against the production DB via the Supabase SQL editor
-- (same operating model as positions_migration.sql).
-- Prereq: auth enabled; RLS already in use (scripts/enable_rls.sql, 2026-07-18).

-- The CHECK below enforces the client-side entropy convention (newTopic() in
-- alert-prefs.js: "sm-" + 32 lowercase hex chars, i.e. 128 bits from the
-- CSPRNG) at the DB level, so a short or arbitrary topic can't be inserted by
-- any authenticated client — the topic is the only thing protecting a user's
-- alert feed.
create table if not exists public.alert_prefs (
  user_id    uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  ntfy_topic text not null unique check (ntfy_topic ~ '^sm-[0-9a-f]{32}$'),
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
