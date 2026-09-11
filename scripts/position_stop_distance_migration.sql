-- scripts/position_stop_distance_migration.sql
-- Live "how close to the stop" reading: one row per (user, item) currently
-- starred by a stop-loss opted-in user and NOT YET breached. Idempotent.
-- Run once against the production DB via the Supabase SQL editor (same
-- operating model as position_stops_migration.sql).
-- Prereq: public.positions exists (scripts/positions_migration.sql).

-- Unlike public.position_stops (a latch: written once on breach, never
-- updated again), this table is UPSERTED every scan -- it is a live
-- reading, not a permanent record of an event. The composite FK with
-- ON DELETE CASCADE is load-bearing for the same reason position_stops has
-- one: unstarring a position deletes its reading automatically, so
-- "unstar to re-arm" needs no application code, and a reading can never
-- outlive the holding it describes.
create table if not exists public.position_stop_distance (
  user_id      uuid not null,
  item_type    text not null,
  region       text not null default '',
  name         text not null,
  as_of        date not null,          -- the close this reading is from
  peak_price   numeric not null,
  peak_on      date not null,
  latest_price numeric not null,
  drawdown     numeric not null,       -- current, e.g. -0.07 (not yet breached)
  updated_at   timestamptz not null default now(),
  primary key (user_id, item_type, region, name),
  foreign key (user_id, item_type, region, name)
    references public.positions(user_id, item_type, region, name)
    on delete cascade
);

alter table public.position_stop_distance enable row level security;

drop policy if exists position_stop_distance_owner on public.position_stop_distance;
create policy position_stop_distance_owner on public.position_stop_distance
  for select to authenticated
  using (auth.uid() = user_id);

-- SELECT only. The scan connects as the postgres role and bypasses RLS, and
-- is the sole writer; clearing happens through the already-granted
-- `delete on public.positions` via the cascade above. A client that could
-- write here could fabricate a reading it was never sent.
grant select on public.position_stop_distance to authenticated;

-- Verification:
--   as authenticated -> select returns only own rows; insert/update/delete denied
--   delete from positions -> the matching position_stop_distance row disappears
--   run the scan twice for the same still-unbreached position -> one row,
--     updated in place (not duplicated)
