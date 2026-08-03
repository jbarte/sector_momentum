-- Cohort unification — drop the retired theme_* schema.
--
-- PRECONDITION: run only AFTER cohort-unification PR 3 is merged to `main` and
-- deployed. src/state.py's init_db() runs CREATE TABLE IF NOT EXISTS for every
-- statement in _DDL_STATEMENTS on every connection, so dropping these while
-- code that still declares them is deployed simply recreates them empty on the
-- next scan. PR 3 removes those declarations; this script removes the tables.
--
-- These tables have been write-only since PR 2 (2026-08-02) and unwritten since
-- PR 3. Every row they hold was copied into scores/signals/sentiment_signals
-- with region='THEME' by scripts/theme_cohort_migration.sql, verified by that
-- script's four parity queries.
--
-- Rollback: there is none in SQL — this deletes data. Restore from the most
-- recent backup_<UTC>.zip in the private db-backups bucket (`python restore.py`),
-- which is taken before every scan.
--
-- Verify with the queries at the bottom. An empty result from each means pass.

BEGIN;

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
