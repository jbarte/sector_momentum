-- Content gating: expose the latest scan's leaderboard to authenticated users.
--
-- Run once against the production DB post-merge (Supabase SQL editor).
-- Idempotent. The pipeline's postgres-role connection bypasses RLS and is
-- unaffected; anon is blocked (no policy); authenticated can SELECT via the view.
-- Prereq: RLS already enabled on scores/scans (scripts/enable_rls.sql, 2026-07-18).

-- security_invoker so the view runs with the caller's RLS context (PG15+).
create or replace view public.v_latest_scores
  with (security_invoker = true) as
select sc.run_at, s.region, s.gics_sector,
       s.level_score, s.change_score, s.data_score,
       s.sentiment_score, s.composite, s.rank
from public.scores s
join public.scans sc on sc.scan_id = s.scan_id
where sc.scan_id = (select max(scan_id) from public.scans);

-- Authenticated read policies on the underlying tables (idempotent via drop/create).
drop policy if exists authenticated_read_scores on public.scores;
create policy authenticated_read_scores
  on public.scores for select to authenticated using (true);

drop policy if exists authenticated_read_scans on public.scans;
create policy authenticated_read_scans
  on public.scans for select to authenticated using (true);

-- Let the API roles reach the view (RLS on the base tables still gates rows).
grant select on public.v_latest_scores to authenticated;

-- Verification: as anon -> 0 rows; as authenticated -> latest scan rows.
-- select count(*) from public.v_latest_scores;

-- Recent scans for authenticated users, so the client can recompute rank-delta,
-- Trend and the entry/exit band for the live leaderboard (web auth.js/rescore.js,
-- and the iOS app). The window MUST equal dashboard/rows.py HISTORY_SCANS, the
-- window the baked board is built from -- pinned by tests/test_history_window.py.
-- It was 6 until 2026-09-28: the daily cron replays Friday's close on Sat/Sun/Mon,
-- so 6 raw scans held only 4 distinct ones Mon-Thu, and signed-in Trend was
-- fitted over 4 scans where Python fits 5.
create or replace view public.v_recent_scores
  with (security_invoker = true) as
select sc.scan_id, sc.run_at, s.region, s.gics_sector,
       s.level_score, s.change_score, s.data_score,
       s.sentiment_score, s.composite, s.rank
from public.scores s
join public.scans sc on sc.scan_id = s.scan_id
where sc.scan_id in (
    select scan_id from public.scans order by scan_id desc limit 20
);

grant select on public.v_recent_scores to authenticated;

-- Verification: as anon -> 0 rows; as authenticated -> up to 20 scans of rows.
-- select scan_id, count(*) from public.v_recent_scores group by scan_id order by scan_id;
