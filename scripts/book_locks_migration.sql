-- Position lock: "freeze my book until the next review date".
-- One row per user -- there is one book. Idempotent. Run once against the
-- production DB via the Supabase SQL editor (same operating model as
-- scripts/positions_migration.sql).
-- Prereq: auth enabled; RLS already in use (scripts/enable_rls.sql, 2026-07-18).
--
-- A separate table rather than a column on public.positions: the lock is a
-- property of the BOOK, not of any individual position.

create table if not exists public.book_locks (
  user_id      uuid primary key default auth.uid() references auth.users(id) on delete cascade,
  horizon_key  text not null,                  -- which preset's calendar the lock was taken against
  locked_until date not null,                  -- the review date it releases on
  locked_at    timestamptz not null default now(),
  unlocked_at  timestamptz                     -- set when the reader overrides; the audit
                                               -- value has to survive the bypass
);

alter table public.book_locks enable row level security;

drop policy if exists book_locks_owner on public.book_locks;
create policy book_locks_owner on public.book_locks
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Note this grants UPDATE, which public.positions deliberately does not need
-- (that table is insert/delete only). A lock is toggled in place.
grant select, insert, update, delete on public.book_locks to authenticated;

-- Verification:
--   as anon          -> select/insert denied (no policy for anon)
--   as authenticated -> select returns only own row;
--                       insert with user_id != auth.uid() rejected by with check.
