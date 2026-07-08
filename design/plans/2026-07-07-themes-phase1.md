# Thematic ETF momentum — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only Themes leaderboard: a thematic-ETF universe scored by the existing momentum pillars against a global benchmark (ACWI), persisted to dedicated theme tables, and shown as a third dashboard segment.

**Architecture:** Reuse the sector pipeline end-to-end. A `build_theme_signals_rows` helper feeds theme ETF rows (region="THEME", RS vs ACWI) through the *existing* `zscore_cross_section`, `score_all`, `_build_scored_df_for_db`, and `_build_long_signals_df`. Themes score in their own `score_all` pass (own cohort), save to new `theme_scores`/`theme_signals` tables under the daily `scan_id`, and render via a themes leaderboard page reusing `_build_breakdown_html`.

**Tech Stack:** Python 3.13, pandas, PyYAML, yfinance (via `src/data/prices.py`), Jinja2, pytest.

## Global Constraints

- Phase 1 only: **no** rank-deltas, trajectory, RRG, history, or sentiment for themes (later phases). No change to the sector track.
- **Single global benchmark** `ACWI` (fall back to `SPY` if ACWI has no price data). Themes score in a **separate `score_all(blend_sentiment=False)` pass** → own z-score cohort.
- Theme rows use `region="THEME"`, `gics_sector=<theme name>`, `sector_key=f"THEME|{name}"`, and all `SIGNAL_COLUMNS`; `breadth_above_50dma` stays NaN (no constituent list).
- New tables `theme_scores` / `theme_signals` are **additive** (`CREATE TABLE IF NOT EXISTS`); themes attach to the **existing `scan_id`** from the sector `save_scan`.
- The themes pass in `scan.py` is **fully guarded** (try/except, non-fatal) and skipped under `--dry-run`.
- Do **not** `git add docs/`. Use `python3` for pytest. Conventional commits, subject < 72 chars, footer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Record the branch test baseline with `python3 -m pytest -q` before Task 1 (6 skips are the psycopg2-less DB modules; install `psycopg2-binary` to run them).
- Spec: `design/specs/2026-07-07-themes-phase1-design.md`.

## File Structure

- `config/themes.yaml` — new: `benchmark` + `themes` map.
- `src/pipeline.py` — add `build_theme_signals_rows`.
- `src/state.py` — `theme_scores`/`theme_signals` DDL; `save_theme_scan`; `get_theme_scores_for_latest_scan`; `get_theme_signals_for_latest_scan`.
- `scan.py` — guarded themes pass after the sector `save_scan`.
- `dashboard/build.py` — extend `_build_breakdown_html` for `THEME`; add `_build_theme_leaderboard_rows`; render `themes.html`.
- `dashboard/templates/themes.html.j2` — new page; segment toggle edits in `index.html.j2` + `sentiment.html.j2`; `segment_themes` in `_i18n.html.j2`.
- `tests/test_theme_pipeline.py`, `tests/test_theme_state.py`, `tests/test_theme_dashboard.py` — new.
- `BACKLOG.md` — Phase 1 Done entry.

---

### Task 1: Themes universe + `build_theme_signals_rows`

**Files:**
- Create: `config/themes.yaml`
- Modify: `src/pipeline.py` (add `build_theme_signals_rows` after `build_signals_rows`)
- Test: `tests/test_theme_pipeline.py` (create)

**Interfaces:**
- Consumes: `compute_signals_for_sector(sector_key, region, gics_sector, sector_ticker, benchmark_ticker, prices)` and `SIGNAL_COLUMNS` (existing in `src/pipeline.py`).
- Produces: `build_theme_signals_rows(themes_cfg: dict, prices: dict[str, pd.DataFrame]) -> list[dict]` — one row per theme whose ETF has price data; each row has `region="THEME"`, `gics_sector=<name>`, `sector_key=f"THEME|{name}"`, and all `SIGNAL_COLUMNS`. Benchmark = `themes_cfg["benchmark"]`, falling back to `"SPY"` when that ticker is absent from `prices`.

- [ ] **Step 1: Create the config**

`config/themes.yaml`:

```yaml
# Thematic / genre ETF universe (Phase 1). One ETF per theme.
# benchmark: single global index for relative strength (SPY fallback if absent).
benchmark: ACWI
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

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_theme_pipeline.py
import numpy as np
import pandas as pd
from src.pipeline import build_theme_signals_rows, SIGNAL_COLUMNS


def _ramp_prices(n=260, start=100.0, step=0.5):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series([start + step * i for i in range(n)], index=idx)
    return pd.DataFrame({"Close": close, "Volume": [1_000_000] * n}, index=idx)


def test_build_theme_rows_shape_and_keys():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Semiconductors": "SOXX"}}
    prices = {"UFO": _ramp_prices(), "SOXX": _ramp_prices(step=0.7), "ACWI": _ramp_prices(step=0.2)}
    rows = build_theme_signals_rows(cfg, prices)
    assert len(rows) == 2
    r = next(r for r in rows if r["gics_sector"] == "Space")
    assert r["region"] == "THEME"
    assert r["sector_key"] == "THEME|Space"
    assert set(SIGNAL_COLUMNS).issubset(r.keys())
    assert np.isnan(r["breadth_above_50dma"])          # breadth N/A for themes
    assert not np.isnan(r["rs_ratio"])                 # RS computed vs ACWI


def test_build_theme_rows_skips_missing_etf():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Ghost": "ZZZZ"}}
    prices = {"UFO": _ramp_prices(), "ACWI": _ramp_prices(step=0.2)}
    rows = build_theme_signals_rows(cfg, prices)
    assert [r["gics_sector"] for r in rows] == ["Space"]   # ZZZZ (no data) skipped


def test_build_theme_rows_benchmark_fallback_to_spy():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO"}}
    prices = {"UFO": _ramp_prices(), "SPY": _ramp_prices(step=0.2)}   # no ACWI
    rows = build_theme_signals_rows(cfg, prices)
    assert len(rows) == 1
    assert not np.isnan(rows[0]["rs_ratio"])           # RS computed vs SPY fallback
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_theme_pipeline.py -v`
Expected: FAIL with `ImportError: cannot import name 'build_theme_signals_rows'`

- [ ] **Step 4: Implement**

Add to `src/pipeline.py` after `build_signals_rows`:

```python
def build_theme_signals_rows(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
) -> list[dict]:
    """Compute signal rows for each theme ETF vs one global benchmark.

    themes_cfg = {"benchmark": <ticker>, "themes": {name: etf_ticker, ...}}.
    Rows use region="THEME", gics_sector=<name>, sector_key="THEME|<name>", and all
    SIGNAL_COLUMNS. breadth_above_50dma stays NaN (themes have no constituent list).
    A theme whose ETF has no price data is skipped. The benchmark falls back to "SPY"
    when the configured benchmark ticker is absent from `prices`.
    """
    benchmark = themes_cfg.get("benchmark") or "ACWI"
    if benchmark not in prices and "SPY" in prices:
        logger.warning("Themes benchmark %s unavailable — falling back to SPY", benchmark)
        benchmark = "SPY"

    rows: list[dict] = []
    for name, ticker in themes_cfg.get("themes", {}).items():
        if ticker not in prices:
            logger.warning("Theme %s: ETF %s has no price data — skipping", name, ticker)
            continue
        sector_key = f"THEME|{name}"
        sig = compute_signals_for_sector(
            sector_key=sector_key,
            region="THEME",
            gics_sector=name,
            sector_ticker=ticker,
            benchmark_ticker=benchmark,
            prices=prices,
        )
        if sig is not None:
            rows.append(sig)
    return rows
```

Confirm `logger` exists at the top of `src/pipeline.py`; if not, add `import logging` + `logger = logging.getLogger(__name__)` near the other module setup (check first — do not duplicate).

- [ ] **Step 5: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_theme_pipeline.py -v`
Expected: PASS (3 tests)

- [ ] **Step 6: Commit**

```bash
git add config/themes.yaml src/pipeline.py tests/test_theme_pipeline.py
git commit -m "feat: themes universe config and signal-row builder" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Theme tables + save/load in `src/state.py`

**Files:**
- Modify: `src/state.py` (DDL list; add `save_theme_scan`, `get_theme_scores_for_latest_scan`, `get_theme_signals_for_latest_scan`)
- Test: `tests/test_theme_state.py` (create)

**Interfaces:**
- Consumes: an open psycopg2 connection; a `scan_id` from `save_scan`.
- Produces:
  - `save_theme_scan(conn, scan_id: int, scores_df: pd.DataFrame, signals_df: pd.DataFrame) -> None` — `scores_df` has columns `region, gics_sector, level_score, change_score, data_score, sentiment_score, composite, rank` (region all `"THEME"`); `signals_df` has `region, gics_sector, signal_name, raw_value, z_value`. Stores `theme = gics_sector` in the theme tables.
  - `get_theme_scores_for_latest_scan(conn) -> pd.DataFrame` (columns `theme, level_score, change_score, data_score, sentiment_score, composite, rank`).
  - `get_theme_signals_for_latest_scan(conn) -> pd.DataFrame` (columns `theme, signal_name, raw_value, z_value`).

- [ ] **Step 1: Write the failing test**

`save_theme_scan` is tested with a **fake connection/cursor** that records the
`executemany` parameter tuples — this verifies the `theme = gics_sector` shaping and
column order without a live DB (and avoids the `db_conn` fixture's teardown, which
wipes `scans` but not theme tables — a DB-backed theme insert would FK-fail on
teardown). `psycopg2` is only needed for the type annotations to import, so
`import src.state` must still work with it installed (it already is via the DB
modules; if not, `pip3 install psycopg2-binary`).

```python
# tests/test_theme_state.py
import pandas as pd
from src.state import save_theme_scan


class _FakeCursor:
    def __init__(self):
        self.executemany_calls = []            # list of (sql, rows)
    def execute(self, sql, params=None):
        pass
    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


class _FakeConn:
    def __init__(self):
        self._cur = _FakeCursor()
    def cursor(self):
        return self._cur
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _scores_df():
    return pd.DataFrame([
        {"region": "THEME", "gics_sector": "Space", "level_score": 1.0, "change_score": 0.5,
         "data_score": 0.8, "sentiment_score": None, "composite": 0.8, "rank": 1.0},
        {"region": "THEME", "gics_sector": "Semiconductors", "level_score": -0.5, "change_score": 0.2,
         "data_score": -0.1, "sentiment_score": None, "composite": -0.1, "rank": 2.0},
    ])


def _signals_df():
    return pd.DataFrame([
        {"region": "THEME", "gics_sector": "Space", "signal_name": "rs_ratio",
         "raw_value": 101.2, "z_value": 1.3},
    ])


def test_save_theme_scan_shapes_rows_with_theme_from_gics_sector():
    conn = _FakeConn()
    save_theme_scan(conn, 7, _scores_df(), _signals_df())
    calls = conn._cur.executemany_calls
    score_call = next(c for c in calls if "theme_scores" in c[0])
    sig_call = next(c for c in calls if "theme_signals" in c[0])
    # scores: (scan_id, theme, level, change, data, sentiment, composite, rank)
    assert score_call[1][0][0] == 7                       # scan_id
    assert score_call[1][0][1] == "Space"                 # theme == gics_sector
    assert score_call[1][0][6] == 0.8                     # composite
    # signals: (scan_id, theme, signal_name, raw_value, z_value)
    assert sig_call[1][0] == (7, "Space", "rs_ratio", 101.2, 1.3)


def test_save_theme_scan_empty_frames_no_insert():
    conn = _FakeConn()
    save_theme_scan(conn, 7, pd.DataFrame(), pd.DataFrame())
    assert conn._cur.executemany_calls == []              # nothing inserted
```

(The `get_theme_*_for_latest_scan` loaders are thin `pd.read_sql_query` wrappers mirroring the existing `get_signals_for_latest_scan`; they're exercised by the dashboard build against the real DB and need no separate unit test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_theme_state.py -v`
Expected: FAIL with `ImportError: cannot import name 'save_theme_scan'`

- [ ] **Step 3: Add the DDL**

In `src/state.py`, append to the `_DDL_STATEMENTS` list (after the `sentiment_signals` block):

```python
    """
    CREATE TABLE IF NOT EXISTS theme_scores (
        scan_id      INTEGER NOT NULL REFERENCES scans(scan_id),
        theme        TEXT NOT NULL,
        level_score  REAL,
        change_score REAL,
        data_score   REAL,
        sentiment_score REAL,
        composite    REAL,
        rank         REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS theme_signals (
        scan_id     INTEGER NOT NULL REFERENCES scans(scan_id),
        theme       TEXT NOT NULL,
        signal_name TEXT NOT NULL,
        raw_value   REAL,
        z_value     REAL
    )
    """,
```

- [ ] **Step 4: Implement save/load**

Add to `src/state.py` (near `save_scan` / the getters):

```python
def save_theme_scan(
    conn: psycopg2.extensions.connection,
    scan_id: int,
    scores_df: pd.DataFrame,
    signals_df: pd.DataFrame,
) -> None:
    """Insert theme scores/signals for an existing scan_id (theme = gics_sector)."""
    score_cols = ["level_score", "change_score", "data_score",
                  "sentiment_score", "composite", "rank"]
    with conn:
        with conn.cursor() as cur:
            if not scores_df.empty:
                rows = [
                    (scan_id, row["gics_sector"],
                     *(_to_float_or_none(row.get(c)) for c in score_cols))
                    for _, row in scores_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO theme_scores "
                    "(scan_id, theme, level_score, change_score, data_score, "
                    "sentiment_score, composite, rank) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
            if not signals_df.empty:
                srows = [
                    (scan_id, row["gics_sector"], row["signal_name"],
                     _to_float_or_none(row.get("raw_value")),
                     _to_float_or_none(row.get("z_value")))
                    for _, row in signals_df.iterrows()
                ]
                cur.executemany(
                    "INSERT INTO theme_signals "
                    "(scan_id, theme, signal_name, raw_value, z_value) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    srows,
                )
    logger.info("Saved %d theme scores for scan_id=%d", len(scores_df), scan_id)


def get_theme_scores_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme score rows for the most recent scan. Empty DataFrame if none."""
    return pd.read_sql_query(
        """
        SELECT ts.theme, ts.level_score, ts.change_score, ts.data_score,
               ts.sentiment_score, ts.composite, ts.rank
        FROM theme_scores ts
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON ts.scan_id = m.max_id
        """,
        conn,
    )


def get_theme_signals_for_latest_scan(conn: psycopg2.extensions.connection) -> pd.DataFrame:
    """Theme signal rows for the most recent scan. Empty DataFrame if none."""
    return pd.read_sql_query(
        """
        SELECT tsg.theme, tsg.signal_name, tsg.raw_value, tsg.z_value
        FROM theme_signals tsg
        JOIN (SELECT MAX(scan_id) AS max_id FROM scans) m ON tsg.scan_id = m.max_id
        """,
        conn,
    )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_theme_state.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Commit**

```bash
git add src/state.py tests/test_theme_state.py
git commit -m "feat: theme_scores/theme_signals tables with save+load" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Themes pass in `scan.py`

**Files:**
- Modify: `scan.py` (imports; guarded themes pass after `save_scan`)

**Interfaces:**
- Consumes: `build_theme_signals_rows` (Task 1); `save_theme_scan` (Task 2); existing `fetch_prices`, `SIGNAL_COLUMNS`, `score_all`, `zscore_cross_section`, `_build_scored_df_for_db`, `_build_long_signals_df`.

- [ ] **Step 1: Extend imports**

Ensure `scan.py` imports `build_theme_signals_rows` from `src.pipeline` (extend the existing `from src.pipeline import SIGNAL_COLUMNS, build_signals_rows` line) and `save_theme_scan` from `src.state` (extend the existing state import inside `run()`).

- [ ] **Step 2: Add the guarded themes pass**

In `scan.py`, inside the `else` branch that persists the scan (right after the sector `scan_id = save_scan(...)` call and its log line, still inside `if not args.dry_run:`), add:

```python
        # Themes track (Phase 1): score a thematic-ETF universe vs a global
        # benchmark and persist to theme tables under the same scan_id. Fully
        # non-fatal — a themes failure must not affect the sector scan.
        try:
            with open("config/themes.yaml", "r") as _fh:
                _themes_cfg = yaml.safe_load(_fh) or {}
            _theme_tickers = sorted({
                *_themes_cfg.get("themes", {}).values(),
                _themes_cfg.get("benchmark", "ACWI"), "SPY",
            })
            _theme_prices = fetch_prices(
                tickers=_theme_tickers, start=str(start_date), end=str(end_date),
            )
            _theme_rows = build_theme_signals_rows(_themes_cfg, _theme_prices)
            if _theme_rows:
                _theme_wide = pd.DataFrame(_theme_rows).set_index("sector_key")[SIGNAL_COLUMNS]
                _theme_scored = score_all(_theme_wide, blend_sentiment=False)
                _theme_scores_df = _build_scored_df_for_db(_theme_scored)
                _theme_z = zscore_cross_section(_theme_wide)
                _theme_signals_df = _build_long_signals_df(_theme_rows, z_wide_df=_theme_z)
                save_theme_scan(conn, scan_id, _theme_scores_df, _theme_signals_df)
                logger.info("Themes: scored and saved %d themes", len(_theme_rows))
            else:
                logger.warning("Themes: no themes with price data — skipping")
        except FileNotFoundError:
            logger.info("Themes: config/themes.yaml not found — skipping themes track")
        except Exception as exc:  # non-fatal
            logger.warning("Themes pass failed (%s) — sector scan unaffected", exc)
```

(`yaml`, `fetch_prices`, `pd`, `score_all`, `zscore_cross_section` are already imported in `scan.py`/`run()`; `_build_scored_df_for_db` and `_build_long_signals_df` are module-level in `scan.py`.)

- [ ] **Step 3: Verify scan.py parses**

Run: `python3 -c "import ast; ast.parse(open('scan.py').read()); print('scan.py parses')"`
Expected: `scan.py parses`

- [ ] **Step 4: Run the scan smoke suite**

Run: `python3 -m pytest tests/test_scan_smoke.py -v`
Expected: PASS (install `psycopg2-binary` first if needed). The themes pass is inside the persist branch; smoke tests that mock the DB should be unaffected. If a smoke test drives the full persist path and now calls `fetch_prices` for theme tickers, confirm it still passes (the pass is guarded — a fetch failure logs and continues). If any smoke test fails, STOP and report.

- [ ] **Step 5: Commit**

```bash
git add scan.py
git commit -m "feat: score and persist thematic ETF universe in the scan" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Themes leaderboard page

**Files:**
- Modify: `dashboard/build.py` (extend `_build_breakdown_html` for `THEME`; add `_build_theme_leaderboard_rows`; load theme data + render `themes.html` in `main`/`build`)
- Create: `dashboard/templates/themes.html.j2`
- Modify: `dashboard/templates/index.html.j2`, `dashboard/templates/sentiment.html.j2` (add Themes segment link), `dashboard/templates/_i18n.html.j2` (`segment_themes`)
- Test: `tests/test_theme_dashboard.py` (create)

**Interfaces:**
- Consumes: `get_theme_scores_for_latest_scan`, `get_theme_signals_for_latest_scan` (Task 2); existing `_build_breakdown_html`, `_render`, `_safe_float`.
- Produces: `_build_theme_leaderboard_rows(scores_df, signals_df, themes_cfg, weights) -> list[dict]` — rows sorted by `rank` with keys `rank, theme, sector_id, composite, level_score, change_score, data_score, breakdown_html`.

- [ ] **Step 1: Extend `_build_breakdown_html` for themes**

In `dashboard/build.py`, add an optional `themes_cfg: dict | None = None` param to `_build_breakdown_html`, and handle the `THEME` region in the ticker/benchmark lookup block (the current `if region == "US": ... else: ...`). Replace with:

```python
    if region == "THEME":
        ticker = (themes_cfg or {}).get("themes", {}).get(sector_name, "—")
        benchmark = (themes_cfg or {}).get("benchmark", "ACWI")
    elif region == "US":
        ticker = universe.get("us_sectors", {}).get(sector_name, "—")
        benchmark = universe.get("us_benchmark", "RSP")
    else:
        ticker = universe.get("eu_sectors", {}).get(sector_name, "—")
        if isinstance(ticker, list):
            ticker = " + ".join(ticker)
        benchmark = universe.get("eu_benchmark", "EXSA.DE")
```

All existing callers pass no `themes_cfg` (defaults to `None`) → US/EU behavior unchanged.

- [ ] **Step 2: Write the failing test**

```python
# tests/test_theme_dashboard.py
import pandas as pd
from dashboard.build import _build_theme_leaderboard_rows


def _scores():
    return pd.DataFrame([
        {"theme": "Space", "level_score": 1.0, "change_score": 0.5, "data_score": 0.8,
         "sentiment_score": None, "composite": 0.8, "rank": 2.0},
        {"theme": "Semiconductors", "level_score": 1.5, "change_score": 0.9, "data_score": 1.2,
         "sentiment_score": None, "composite": 1.2, "rank": 1.0},
    ])


def _signals():
    return pd.DataFrame([
        {"theme": "Space", "signal_name": "rs_ratio", "raw_value": 101.0, "z_value": 0.4},
        {"theme": "Semiconductors", "signal_name": "rs_ratio", "raw_value": 104.0, "z_value": 1.1},
    ])


def test_theme_rows_sorted_by_rank_with_breakdown():
    cfg = {"benchmark": "ACWI", "themes": {"Space": "UFO", "Semiconductors": "SOXX"}}
    rows = _build_theme_leaderboard_rows(_scores(), _signals(), cfg, weights={})
    assert [r["theme"] for r in rows] == ["Semiconductors", "Space"]   # rank 1 first
    assert rows[0]["rank"] == 1
    assert "SOXX" in rows[0]["breakdown_html"]      # theme ETF surfaced in breakdown
    assert rows[0]["sector_id"] == "THEME-Semiconductors"


def test_theme_rows_empty_input():
    assert _build_theme_leaderboard_rows(pd.DataFrame(), pd.DataFrame(), {}, {}) == []
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python3 -m pytest tests/test_theme_dashboard.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_theme_leaderboard_rows'`

- [ ] **Step 4: Implement `_build_theme_leaderboard_rows`**

In `dashboard/build.py`:

```python
def _build_theme_leaderboard_rows(scores_df, signals_df, themes_cfg: dict, weights: dict) -> list[dict]:
    """Read-only leaderboard rows for the themes track, sorted by rank."""
    if scores_df is None or scores_df.empty:
        return []
    rows = []
    for _, s in scores_df.sort_values("rank").iterrows():
        theme = s["theme"]
        key = f"THEME|{theme}"
        # _build_breakdown_html reads signal rows only via {s["signal_name"]: s},
        # needing signal_name/raw_value/z_value — exactly what get_theme_signals
        # returns. It takes the theme name from the sector_key ("THEME|<name>"), so no
        # extra keys are needed on the signal rows.
        row_signals = (
            signals_df[signals_df["theme"] == theme].to_dict("records")
            if not signals_df.empty else []
        )
        score_row = s.to_dict()
        breakdown = _build_breakdown_html(
            key, score_row, row_signals, universe={}, weights=weights,
            sector_etfs=None, themes_cfg=themes_cfg,
        )
        rows.append({
            "rank": int(s["rank"]) if pd.notna(s["rank"]) else "—",
            "theme": theme,
            "sector_id": key.replace("|", "-").replace(" ", "_"),
            "composite": f"{_safe_float(s['composite']):.3f}" if _safe_float(s["composite"]) is not None else "—",
            "level_score": f"{_safe_float(s['level_score']):.3f}" if _safe_float(s["level_score"]) is not None else "—",
            "change_score": f"{_safe_float(s['change_score']):.3f}" if _safe_float(s["change_score"]) is not None else "—",
            "data_score": f"{_safe_float(s['data_score']):.3f}" if _safe_float(s["data_score"]) is not None else "—",
            "breakdown_html": breakdown,
        })
    return rows
```

`_build_breakdown_html`'s footer shows `ETF: {ticker} · Benchmark: {benchmark}` — with the `THEME` branch from Step 1 those come from `themes_cfg`, so the panel renders the theme's ETF + ACWI and the same per-signal z-bar table the sector rows use.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/test_theme_dashboard.py -v`
Expected: PASS (2 tests)

- [ ] **Step 6: Create `themes.html.j2`**

`dashboard/templates/themes.html.j2` — mirror the sentiment page's shell (head, header, segment toggle, `_i18n` include) with a read-only leaderboard table (no sentiment/Δ/trend columns):

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>Sector Momentum — Themes</title>
<style>
{% include "_style.html.j2" %}
</style>
</head>
<body>
<header>
  <h1 data-i18n="title">Sector momentum</h1>
  <span class="scan-date"><span data-i18n="lastScan">Last scan:</span> {% if active_scan_id %}#{{ active_scan_id }} · {% endif %}{{ scan_date }}</span>
  <div class="segment-toggle" role="tablist" aria-label="Dashboard segment">
    <a class="segment-btn" href="index.html" data-i18n="segment_sectors">Sectors</a>
    <span class="segment-btn active" aria-current="page" data-i18n="segment_themes">Themes</span>
    <a class="segment-btn" href="sentiment.html" data-i18n="segment_sentiment">Sentiment</a>
  </div>
  <span class="disclaimer" data-i18n="disclaimer">Analytical tooling, not investment advice.</span>
  <button id="lang-toggle" class="lang-toggle" type="button" onclick="toggleLang()" aria-label="Switch language">SV</button>
</header>

<section class="tab-panel active">
  <p class="tab-note" data-i18n="note_themes">Thematic ETFs ranked by the same momentum pillars, vs a global benchmark (ACWI). Information-only; separate from the sector leaderboard.</p>
  <div class="table-wrap">
    <table id="themes-table">
      <thead>
        <tr>
          <th data-i18n="col_rank">#</th>
          <th data-i18n="col_theme">Theme</th>
          <th data-i18n="col_composite">Composite</th>
          <th data-i18n="col_level">Level</th>
          <th data-i18n="col_change">Change</th>
          <th data-i18n="col_data">Data</th>
        </tr>
      </thead>
      <tbody>
        {% for row in theme_rows %}
        <tr class="leaderboard-row" data-sector-id="{{ row.sector_id }}" onclick="toggleBreakdown('{{ row.sector_id }}')">
          <td class="rank-cell">
            <span class="rank-badge{% if row.rank is number and row.rank <= 3 %} top3{% endif %}">{{ row.rank }}</span>
            <span class="chevron" id="chev-{{ row.sector_id }}">▶</span>
          </td>
          <td>{{ row.theme }}</td>
          <td class="composite-cell">{{ row.composite }}</td>
          <td>{{ row.level_score }}</td>
          <td>{{ row.change_score }}</td>
          <td>{{ row.data_score }}</td>
        </tr>
        <tr class="breakdown-row" id="bd-{{ row.sector_id }}">
          <td colspan="6">{{ row.breakdown_html | safe }}</td>
        </tr>
        {% else %}
        <tr><td colspan="6" style="text-align:center;padding:24px;color:var(--fg4)" data-i18n="themes_empty">No theme data yet — run a scan.</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</section>

<script>
function toggleBreakdown(id) {
  var bd = document.getElementById("bd-" + id);
  var chev = document.getElementById("chev-" + id);
  if (!bd) return;
  var open = bd.classList.toggle("open");
  if (chev) chev.textContent = open ? "▼" : "▶";
}
{% include "_i18n.html.j2" %}
</script>
</body>
</html>
```

- [ ] **Step 7: Add the Themes segment link to the other two pages + SV label**

In `index.html.j2` and `sentiment.html.j2`, add the Themes link inside the existing `.segment-toggle` block (between Sectors and Sentiment):

```html
    <a class="segment-btn" href="themes.html" data-i18n="segment_themes">Themes</a>
```

In `_i18n.html.j2`, add to the SV text map (next to `segment_sentiment`):

```javascript
    segment_themes: "Teman",
    col_theme: "Tema",
    note_themes: "Tematiska ETF:er rankade med samma momentumpelare, mot ett globalt jämförelseindex (ACWI). Endast information; separat från sektortopplistan.",
    themes_empty: "Ingen temadata än — kör en skanning.",
```

- [ ] **Step 8: Wire theme rendering into the dashboard build**

In `dashboard/build.py`'s `main`/`build` (where `sentiment.html` is rendered), load theme data and render `themes.html`:

```python
    from src.state import get_theme_scores_for_latest_scan, get_theme_signals_for_latest_scan
    theme_scores_df = get_theme_scores_for_latest_scan(conn)
    theme_signals_df = get_theme_signals_for_latest_scan(conn)
    import yaml as _yaml
    _themes_cfg = {}
    _themes_path = project_root / "config/themes.yaml"
    if _themes_path.exists():
        _themes_cfg = _yaml.safe_load(_themes_path.read_text()) or {}
    theme_rows = _build_theme_leaderboard_rows(theme_scores_df, theme_signals_df, _themes_cfg, _weights)
```

(Load these alongside the other `get_*_for_latest_scan` calls, before `conn.close()`.) Then render:

```python
    _render(
        template_path=Path(__file__).parent / "templates" / "themes.html.j2",
        out_path=out_dir / "themes.html",
        context=dict(
            scan_date=scan_date,
            active_scan_id=active_scan_id,
            theme_rows=theme_rows,
            plotly_bundle=plotly_bundle_rel,
        ),
    )
```

- [ ] **Step 9: Verify templates render + build helper**

Run:
```bash
python3 -m pytest tests/test_theme_dashboard.py -v
python3 -c "
from jinja2 import Environment, FileSystemLoader
env = Environment(loader=FileSystemLoader('dashboard/templates'))
rows=[{'rank':1,'theme':'Semiconductors','sector_id':'THEME-Semiconductors','composite':'1.200','level_score':'1.500','change_score':'0.900','data_score':'1.200','breakdown_html':'<div>SOXX</div>'}]
html=env.get_template('themes.html.j2').render(scan_date='x', active_scan_id=1, theme_rows=rows, plotly_bundle='x')
assert 'Semiconductors' in html and 'segment_themes' in html
empty=env.get_template('themes.html.j2').render(scan_date='x', active_scan_id=1, theme_rows=[], plotly_bundle='x')
assert 'No theme data yet' in empty
print('themes.html.j2 renders (rows + empty)')
"
```
Expected: tests PASS; `themes.html.j2 renders (rows + empty)`.

- [ ] **Step 10: Commit**

```bash
git add dashboard/build.py dashboard/templates/themes.html.j2 dashboard/templates/index.html.j2 dashboard/templates/sentiment.html.j2 dashboard/templates/_i18n.html.j2 tests/test_theme_dashboard.py
git commit -m "feat: themes leaderboard page and segment toggle" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Backlog hygiene

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Add a Done entry** at the top of `## Done` (the "Thematic / genre ETF momentum" queued item stays — Phases 2/3 remain):

```markdown
- ~~Thematic ETF momentum — Phase 1 (universe + score + leaderboard)~~ — a thematic
  ETF universe (`config/themes.yaml`, one ETF per theme) is scored by the existing
  momentum pillars vs a single global benchmark (ACWI, SPY fallback) in its own
  z-score cohort (`build_theme_signals_rows` + `score_all`), persisted to new
  `theme_scores`/`theme_signals` tables under the daily `scan_id`, and shown as a
  read-only **Themes** leaderboard (third header segment, reusing the breakdown panel).
  Breadth is N/A for themes; the themes pass is fully non-fatal. Phases 2 (deltas /
  trajectory / RRG / history) and 3 (Trends sentiment for themes) remain queued above.
  *(2026-07-07)*
```

- [ ] **Step 2: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: mark thematic ETF momentum Phase 1 done" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Final Verification

- [ ] **Full suite green:** `python3 -m pytest -q` → branch baseline + new theme tests, 6 skipped (or DB modules run with `psycopg2-binary`). No regressions.
- [ ] **No `docs/` staged:** `git status --porcelain docs/` → empty.
- [ ] **Diff source-only:** `git diff --stat main...HEAD` touches only `config/`, `src/`, `scan.py`, `dashboard/`, `tests/`, `BACKLOG.md`, `design/`.
- [ ] **Local dashboard build sanity (optional):** if a test DB is available, `python3 dashboard/build.py` and confirm `docs/themes.html` is generated with the segment toggle — but do **not** commit `docs/`.
- [ ] Final whole-branch review, address findings, then `git push -u origin feature/themes-phase1` and open a PR with `gh pr create` (per CLAUDE.md — Claude opens the PR; Jonas merges). **Do not merge.**
