-- Cohort unification — drop the retired theme_* schema.
--
-- PRECONDITION: run only AFTER cohort-unification PR 3 is merged to `main` and
-- deployed. src/state.py's init_db() runs CREATE TABLE IF NOT EXISTS for every
-- statement in _DDL_STATEMENTS on every connection, so dropping these while
-- code that still declares them is deployed simply recreates them empty on the
-- next scan. PR 3 removes those declarations; this script removes the tables.
--
-- These tables have been write-only since PR 2 (2026-08-02) and unwritten since
-- PR 3. Historical rows (through scan 149) were copied into
-- scores/signals/sentiment_signals with region='THEME' by
-- scripts/theme_cohort_migration.sql's backfill, verified by that script's
-- four parity queries at the time. Rows from scan 150 onward were written
-- directly to the shared tables by save_theme_scan's dual-write — those were
-- never in scope for the migration's verification. The pre-drop parity check
-- below covers the full current state regardless of which path wrote which
-- rows.
--
-- Rollback: there is none in SQL — this deletes data. The shared tables
-- (scores/signals/sentiment_signals with region='THEME') hold the same data
-- and are unaffected by this script, so a rollback is only needed if the
-- parity assumption above turns out wrong. If so, restore the legacy tables
-- from backup_2026-08-03T10-57-27Z.zip in the private db-backups bucket
-- (`python restore.py`) — the last pre-deploy archive, containing
-- theme_scores=275, theme_signals=3441, theme_sentiment_signals=704. Any
-- later archive will NOT work for this: once this PR deploys, write_backup
-- only emits the four shared-table CSVs (_ARCHIVE_MEMBERS derives from the
-- shrunk _COLUMNS), so every post-deploy backup contains scans/scores/signals/
-- sentiment_signals only — no theme_*.csv members to restore from.
--
-- Verify with the queries at the bottom. An empty result from each means pass.

BEGIN;

-- Pre-drop parity assertion. This is the one irreversible step in the
-- series, so it self-guards here rather than trusting a remembered manual
-- check (e.g. that the migration's parity queries are still valid for rows
-- written after 2026-08-02): if the legacy and shared counts don't match,
-- abort the whole transaction and drop nothing.
DO $$
DECLARE
    legacy_scores  bigint;
    shared_scores  bigint;
    legacy_signals bigint;
    shared_signals bigint;
BEGIN
    SELECT count(*) INTO legacy_scores  FROM theme_scores;
    SELECT count(*) INTO shared_scores  FROM scores  WHERE region = 'THEME';
    SELECT count(*) INTO legacy_signals FROM theme_signals;
    SELECT count(*) INTO shared_signals FROM signals WHERE region = 'THEME';

    IF legacy_scores <> shared_scores OR legacy_signals <> shared_signals THEN
        RAISE EXCEPTION
            'ABORT — parity check failed, not dropping. '
            'theme_scores=% vs scores(THEME)=%; theme_signals=% vs signals(THEME)=%',
            legacy_scores, shared_scores, legacy_signals, shared_signals;
    END IF;
END $$;

DROP TABLE IF EXISTS theme_sentiment_signals;
DROP TABLE IF EXISTS theme_signals;
DROP TABLE IF EXISTS theme_scores;

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification. Each query must return ZERO rows.
-- ---------------------------------------------------------------------------

-- 1. The tables are gone.
SELECT c.relname
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'public'
  AND c.relname IN ('theme_scores', 'theme_signals', 'theme_sentiment_signals');

-- 2. The THEME cohort survived in the shared tables.
--    (Returns a row only if theme data is MISSING — that would be the disaster.)
SELECT 'no theme rows in scores' AS problem
WHERE NOT EXISTS (SELECT 1 FROM scores WHERE region = 'THEME');

-- 3. Sector rows untouched.
SELECT 'sector rows missing' AS problem
WHERE NOT EXISTS (SELECT 1 FROM scores WHERE region IN ('US', 'EU'));
