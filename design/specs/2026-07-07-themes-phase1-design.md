# Thematic ETF momentum — Phase 1 design (universe + score + leaderboard)

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/themes-phase1`

## Problem

The scanner is GICS-sector-only. A lot of rotation happens at the **theme** level
(defence, crypto, AI, uranium, clean energy) that doesn't map onto the 11 GICS
sectors. The momentum engine (`src/signals/*`, `src/scoring.py`) operates on any
price series — only the *universe* and *benchmark* differ — so a thematic ETF track
can be layered on the existing engine rather than rewritten, exactly as sentiment
was.

## Goal (Phase 1 only)

A read-only **Themes leaderboard**: a configurable thematic-ETF universe, scored by
the existing momentum pillars against one global benchmark, persisted to dedicated
theme tables, and rendered as a third dashboard segment (`Sectors | Themes |
Sentiment`).

**Explicitly deferred to later phases:** rank deltas, trajectory, RRG, history charts
(Phase 2); Trends sentiment for themes (Phase 3). No change to the sector track.

## Key decisions

1. **Universe:** a new `config/themes.yaml` — one ETF ticker per theme to start.
2. **Single global benchmark (`ACWI`, `SPY` fallback).** Themes have no region
   cohort, but RS/RS-momentum are the tool's core signals and drive the later RRG.
   Scoring each theme's relative strength against one broad global index (ACWI,
   all-country world) keeps **full signal parity** with the sector model at the cost
   of a single benchmark ticker. Breadth (`breadth_above_50dma`) is **N/A** for themes
   (no GICS constituent list) and stays NaN — the scorer already tolerates missing
   signals.
3. **Own z-score cohort.** Themes are scored in a **separate `score_all` pass** over
   a themes-only DataFrame, so their composite is a cross-sectional z within the theme
   universe, independent of the sector cohorts.
4. **Separate `theme_scores` / `theme_signals` tables** (not the sector tables), for
   clean isolation. Additive `CREATE TABLE IF NOT EXISTS` — no migration.
5. **Attached to the same `scan_id`** as the daily sector scan (one scan row, two
   scored tracks), so themes advance in lockstep with the daily run.
6. **Non-fatal.** A themes-pass failure logs a warning and leaves the sector scan and
   dashboard fully intact.

## Components

### 1. `config/themes.yaml` (new)

```yaml
benchmark: ACWI          # global RS benchmark (falls back to SPY if ACWI has no data)
themes:
  Artificial Intelligence & Robotics: BOTZ
  Semiconductors: SOXX
  Cybersecurity: CIBR
  Clean Energy: ICLN
  Defense: ITA
  Blockchain & Crypto: BLOK
  Uranium & Nuclear: URA
  Space: UFO
  Lithium & Battery: LIT
  Biotech: XBI
```

One ETF per theme (starter set; editable). `benchmark` is a single ticker.

### 2. `src/pipeline.py` — `build_theme_signals_rows`

A themes analog of `build_signals_rows`, reusing `compute_signals_for_sector`:

```python
def build_theme_signals_rows(themes_cfg: dict, prices: dict[str, pd.DataFrame]) -> list[dict]:
    """Compute signal rows for each theme ETF vs the global benchmark.

    themes_cfg = {"benchmark": <ticker>, "themes": {name: etf_ticker, ...}}.
    Each row: region="THEME", gics_sector=<theme name>, sector_key=f"THEME|{name}",
    + all SIGNAL_COLUMNS. breadth_above_50dma stays NaN (no constituent list).
    """
```

- `benchmark = themes_cfg.get("benchmark") or "ACWI"`; if the benchmark ticker has no
  price data, fall back to `"SPY"` (log the fallback).
- For each theme, call `compute_signals_for_sector(sector_key=f"THEME|{name}",
  region="THEME", gics_sector=name, sector_ticker=etf, benchmark_ticker=benchmark,
  prices=prices)`. Skip a theme whose ETF has no price data (log it).

Reuses the existing per-signal logic verbatim — no new signal math.

### 3. `src/state.py` — theme tables + save/load

New DDL (mirrors `scores`/`signals`, keyed by `theme` instead of region+sector):

```sql
CREATE TABLE IF NOT EXISTS theme_scores (
    scan_id      INTEGER NOT NULL REFERENCES scans(scan_id),
    theme        TEXT NOT NULL,
    level_score  REAL, change_score REAL, data_score REAL,
    sentiment_score REAL, composite REAL, rank REAL
);
CREATE TABLE IF NOT EXISTS theme_signals (
    scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
    theme       TEXT NOT NULL,
    signal_name TEXT NOT NULL,
    raw_value   REAL, z_value REAL
);
```

New functions:
- `save_theme_scan(conn, scan_id, theme_scores_df, theme_signals_df)` — insert rows
  for an existing `scan_id` (created by the sector `save_scan`). `sentiment_score`
  stored as NULL/NaN in Phase 1 (column present for Phase 3).
- `get_theme_scores_for_latest_scan(conn) -> DataFrame` (theme, scores…).
- `get_theme_signals_for_latest_scan(conn) -> DataFrame` (theme, signal_name, raw, z).

(Deltas/history loaders are Phase 2 — not built here.)

### 4. `scan.py` — themes pass

After the sector `save_scan(...)` returns `scan_id` (and before/around report/dashboard):

```python
try:
    themes_cfg = _load_yaml("config/themes.yaml")
    theme_tickers = list(themes_cfg["themes"].values()) + [themes_cfg.get("benchmark", "ACWI"), "SPY"]
    theme_prices = fetch_prices(tickers=sorted(set(theme_tickers)), start=str(start_date), end=str(end_date))
    theme_rows = build_theme_signals_rows(themes_cfg, theme_prices)
    theme_wide = pd.DataFrame(theme_rows).set_index("sector_key")[SIGNAL_COLUMNS]
    theme_scored = score_all(theme_wide, blend_sentiment=False)
    # shape into theme_scores_df / theme_signals_df keyed by theme name
    save_theme_scan(conn, scan_id, theme_scores_df, theme_signals_df)
    logger.info("Themes: scored %d themes", len(theme_scored))
except Exception as exc:
    logger.warning("Themes pass failed (%s) — sector scan unaffected", exc)
```

Guarded end-to-end: any failure leaves the sector scan/report/dashboard untouched.
Skipped under `--dry-run` alongside the other DB writes.

### 5. Dashboard — Themes leaderboard page

- `dashboard/build.py`: load the latest theme scores/signals; build theme leaderboard
  rows reusing the existing row + `_build_breakdown_html` rendering (composite, level,
  change, data, per-signal breakdown). Render a new `dashboard/templates/themes.html.j2`
  mirroring the leaderboard structure — read-only, **no** rank-Δ/trajectory columns
  (Phase 2).
- Add a **Themes** segment to the header toggle in `index.html.j2`, `themes.html.j2`,
  and `sentiment.html.j2` (`Sectors | Themes | Sentiment`), plus the SV label in
  `_i18n.html.j2` (`segment_themes`).
- If there are no theme rows (older scans), the page renders an empty-state note and
  the build does not fail.

## Data flow

```
config/themes.yaml ─┐
                    ├─ scan.py themes pass (same scan_id as sectors)
fetch_prices(themes+ACWI) ─ build_theme_signals_rows ─ score_all(blend_sentiment=False)
                    └─ save_theme_scan ─▶ theme_scores / theme_signals
dashboard/build.py ─ get_theme_*_for_latest_scan ─ theme leaderboard rows ─▶ themes.html
```

## Error handling / degradation

- Missing `config/themes.yaml` → themes pass skipped (logged); sector scan unaffected.
- Benchmark ACWI unavailable → fall back to SPY (logged); if both fail, RS signals are
  NaN for all themes (scorer tolerates — composite from the remaining signals).
- A theme ETF with no price data → that theme skipped (logged), others proceed.
- No theme rows for the latest scan → dashboard renders an empty Themes page.
- Whole themes pass wrapped in try/except → never breaks the daily sector run.

## Testing

- `build_theme_signals_rows`: given a fake `prices` dict, produces one row per theme
  with `region="THEME"`, `sector_key="THEME|<name>"`, RS computed vs the benchmark,
  and `breadth_above_50dma` NaN; a theme with no price data is skipped; missing-ACWI
  falls back to SPY.
- Own-cohort scoring: `score_all` over a themes-only frame yields composites that are
  z-scored within the themes (a sanity check that themes aren't mixed with sectors).
- `save_theme_scan` + `get_theme_scores_for_latest_scan` round-trip (DB smoke, in the
  psycopg2-gated suite).
- Theme leaderboard-row builder produces the expected rows/breakdown from a fake
  theme scores/signals frame; empty input → empty list (no crash).
- `themes.html.j2` renders with sample rows and in the empty state.
- Full suite + scan smoke stay green.

## Honest caveats / follow-ups

- **Starter universe** — the 10 themes/ETFs are a reasonable liquid set, not
  researched exhaustively; trivially edited in config.
- **US-listing skew** — most thematic ETFs are US-listed, so ACWI-relative RS carries
  a mild home-market tilt; acceptable for a global thematic read, revisit if it
  distorts.
- **Phase 2/3 deferred:** deltas, trajectory, RRG, history, and Trends sentiment for
  themes are separate specs.
