-- Trailing stop-loss latch: one row per (user, item) that has breached.
-- Idempotent. Run once against the production DB via the Supabase SQL editor
-- (same operating model as positions_migration.sql).
-- Prereq: public.positions exists (scripts/positions_migration.sql).

-- The composite FK with ON DELETE CASCADE is load-bearing, not decoration:
-- unstarring a position deletes its latch automatically, so "unstar to
-- re-arm" needs no application code, and a latch can never outlive the
-- holding it describes. Re-starring creates a fresh positions row with a new
-- created_at, hence a new entry date and a new peak.
create table if not exists public.position_stops (
  user_id     uuid not null,
  item_type   text not null,
  region      text not null default '',
  name        text not null,
  stopped_on  date not null,          -- the close that breached
  peak_price  numeric not null,
  peak_on     date not null,
  drawdown    numeric not null,       -- e.g. -0.143 at the breach
  notified    boolean not null default false,
  created_at  timestamptz not null default now(),
  primary key (user_id, item_type, region, name),
  foreign key (user_id, item_type, region, name)
    references public.positions(user_id, item_type, region, name)
    on delete cascade
);

alter table public.position_stops enable row level security;

drop policy if exists position_stops_owner on public.position_stops;
create policy position_stops_owner on public.position_stops
  for select to authenticated
  using (auth.uid() = user_id);

-- SELECT only. The scan connects as the postgres role and bypasses RLS, and
-- is the sole writer; clearing happens through the already-granted
-- `delete on public.positions` via the cascade above. A client that could
-- INSERT here could fabricate a stop it was never sent.
grant select on public.position_stops to authenticated;

-- Verification:
--   as authenticated -> select returns only own rows; insert/update/delete denied
--   delete from positions -> the matching position_stops row disappears
