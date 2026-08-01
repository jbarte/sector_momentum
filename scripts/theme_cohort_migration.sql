-- Cohort unification — backfill historical theme rows into the shared tables.
--
-- Idempotent: the NOT EXISTS guards mean re-running is a no-op. Safe to apply
-- before or after the dual-write code ships; rows written by both paths are
-- deduplicated by the same guard.
--
-- Verify with the queries at the bottom. An empty result from each means pass.
--
-- Rollback: DELETE FROM scores WHERE region = 'THEME';  (and likewise for
-- signals / sentiment_signals). The theme_* tables are untouched by this
-- script and remain the source of truth until PR 3.

BEGIN;

INSERT INTO scores (scan_id, region, gics_sector, level_score, change_score,
                    data_score, sentiment_score, composite, rank)
SELECT ts.scan_id, 'THEME', ts.theme, ts.level_score, ts.change_score,
       ts.data_score, ts.sentiment_score, ts.composite, ts.rank
FROM theme_scores ts
WHERE NOT EXISTS (
    SELECT 1 FROM scores s
    WHERE s.scan_id = ts.scan_id
      AND s.region = 'THEME'
      AND s.gics_sector = ts.theme
);

INSERT INTO signals (scan_id, region, gics_sector, signal_name, raw_value, z_value)
SELECT tg.scan_id, 'THEME', tg.theme, tg.signal_name, tg.raw_value, tg.z_value
FROM theme_signals tg
WHERE NOT EXISTS (
    SELECT 1 FROM signals s
    WHERE s.scan_id = tg.scan_id
      AND s.region = 'THEME'
      AND s.gics_sector = tg.theme
      AND s.signal_name = tg.signal_name
);

INSERT INTO sentiment_signals (scan_id, region, gics_sector, signal_name,
                               value, text_value)
SELECT tss.scan_id, 'THEME', tss.theme, tss.signal_name,
       tss.value, tss.text_value
FROM theme_sentiment_signals tss
WHERE NOT EXISTS (
    SELECT 1 FROM sentiment_signals s
    WHERE s.scan_id = tss.scan_id
      AND s.region = 'THEME'
      AND s.gics_sector = tss.theme
      AND s.signal_name = tss.signal_name
);

COMMIT;

-- ---------------------------------------------------------------------------
-- Verification. Each query must return ZERO rows.
-- ---------------------------------------------------------------------------

-- 1. Per-scan score parity between legacy and shared tables.
SELECT s.scan_id, count(*) AS unified, ts.n AS legacy
FROM scores s
JOIN (SELECT scan_id, count(*) n FROM theme_scores GROUP BY scan_id) ts
  ON ts.scan_id = s.scan_id
WHERE s.region = 'THEME'
GROUP BY s.scan_id, ts.n
HAVING count(*) <> ts.n;

-- 2. Per-scan signal parity.
SELECT s.scan_id, count(*) AS unified, tg.n AS legacy
FROM signals s
JOIN (SELECT scan_id, count(*) n FROM theme_signals GROUP BY scan_id) tg
  ON tg.scan_id = s.scan_id
WHERE s.region = 'THEME'
GROUP BY s.scan_id, tg.n
HAVING count(*) <> tg.n;

-- 3. Value fidelity — no composite drifted during the copy.
SELECT s.scan_id, s.gics_sector, s.composite, ts.composite
FROM scores s
JOIN theme_scores ts
  ON ts.scan_id = s.scan_id AND ts.theme = s.gics_sector
WHERE s.region = 'THEME'
  AND s.composite IS DISTINCT FROM ts.composite;

-- 4. No sector row was touched.
SELECT region, count(*) FROM scores
WHERE region NOT IN ('US', 'EU', 'THEME')
GROUP BY region;
