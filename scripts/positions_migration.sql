-- Phase-1 position tracking: one row per (user, item) == "I hold this".
-- Idempotent. Run once against the production DB via the Supabase SQL editor
-- (same operating model as content_gating_migration.sql).
-- Prereq: auth enabled; RLS already in use (scripts/enable_rls.sql, 2026-07-18).

create table if not exists public.positions (
  user_id     uuid not null default auth.uid() references auth.users(id) on delete cascade,
  item_type   text not null check (item_type in ('sector','theme')),
  region      text not null default '',        -- 'US'/'EU' for sectors, '' for themes
  name        text not null,                    -- sector name or theme name
  created_at  timestamptz not null default now(),
  primary key (user_id, item_type, region, name)
);

alter table public.positions enable row level security;

drop policy if exists positions_owner on public.positions;
create policy positions_owner on public.positions
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

grant select, insert, delete on public.positions to authenticated;

-- Verification:
--   as anon          -> select/insert denied (no policy for anon)
--   as authenticated -> select returns only own rows;
--                       insert with user_id != auth.uid() rejected by with check.
