-- Cohort unification — backfill historical theme rows into the shared tables.
--
-- Idempotent: the NOT EXISTS guards mean re-running is a no-op.
--
-- PRECONDITION: run only after PR 1 is merged to `main` and deployed. The
-- regions=SECTOR_REGIONS scoping in `src/state.py` must be live first —
-- without it these rows leak into every sector reader and double every theme
-- alert.
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

-- 1. Per-scan score parity between legacy and shared tables. Legacy is the
--    LEFT side so a scan whose theme rows were missed entirely by the
--    unified copy still surfaces (COALESCE turns the missing right-side
--    count into 0 instead of dropping the row via an inner join).
SELECT ts.scan_id, COALESCE(s.n, 0) AS unified, ts.n AS legacy
FROM (SELECT scan_id, count(*) n FROM theme_scores GROUP BY scan_id) ts
LEFT JOIN (
    SELECT scan_id, count(*) n FROM scores WHERE region = 'THEME' GROUP BY scan_id
) s ON s.scan_id = ts.scan_id
WHERE COALESCE(s.n, 0) <> ts.n;

-- 2. Per-scan signal parity. Same left-join shape as (1).
SELECT tg.scan_id, COALESCE(s.n, 0) AS unified, tg.n AS legacy
FROM (SELECT scan_id, count(*) n FROM theme_signals GROUP BY scan_id) tg
LEFT JOIN (
    SELECT scan_id, count(*) n FROM signals WHERE region = 'THEME' GROUP BY scan_id
) s ON s.scan_id = tg.scan_id
WHERE COALESCE(s.n, 0) <> tg.n;

-- 3. Per-scan sentiment-signal parity. Same left-join shape as (1)/(2);
--    sentiment_signals is dual-written and backfilled but was previously
--    never verified.
SELECT tss.scan_id, COALESCE(s.n, 0) AS unified, tss.n AS legacy
FROM (SELECT scan_id, count(*) n FROM theme_sentiment_signals GROUP BY scan_id) tss
LEFT JOIN (
    SELECT scan_id, count(*) n FROM sentiment_signals WHERE region = 'THEME' GROUP BY scan_id
) s ON s.scan_id = tss.scan_id
WHERE COALESCE(s.n, 0) <> tss.n;

-- 4. Value fidelity — no composite drifted during the copy.
SELECT s.scan_id, s.gics_sector, s.composite, ts.composite
FROM scores s
JOIN theme_scores ts
  ON ts.scan_id = s.scan_id AND ts.theme = s.gics_sector
WHERE s.region = 'THEME'
  AND s.composite IS DISTINCT FROM ts.composite;

-- 5. No unexpected cohort discriminator appeared. This only enumerates
--    region values outside the known set — it does not (and cannot, via
--    this query) detect a US/EU row that was updated or deleted.
SELECT region, count(*) FROM scores
WHERE region NOT IN ('US', 'EU', 'THEME')
GROUP BY region;
