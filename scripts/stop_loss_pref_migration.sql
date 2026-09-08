-- Per-user opt-in for trailing stop-loss alerts.
-- Idempotent. Run once via the Supabase SQL editor.
-- Prereq: public.alert_prefs exists (scripts/alert_prefs_migration.sql).

-- A nullable timestamp rather than a boolean: NULL = off, non-null = on AND
-- when it was switched on. One column, so there is no separate flag that can
-- desync from the timestamp.
--
-- The timestamp is also the day-one guard. Without it, the first scan after
-- this migration finds positions ALREADY more than 12% off peak and fires a
-- stack of push notifications about breaches from weeks ago. The scan only
-- notifies when stopped_on >= stop_loss_since; older breaches still get a
-- latch row, with notified = false, so the dashboard shows them and the
-- phone stays quiet. That rule is correct for every future opt-in too, not
-- just this migration.
alter table public.alert_prefs
  add column if not exists stop_loss_since timestamptz;

-- No grant change needed: alert_prefs already grants update to authenticated,
-- under the existing owner policy.
