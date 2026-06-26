# Backtest (Phase 1 — edge) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a strategy backtest that ranks sectors by the scanner's composite and holds the top 5 monthly, for US and EU as two independent tracks, then persists results and renders them in a dashboard "Backtest" tab.

**Architecture:** A new `src/backtest/` package of pure, testable modules (replay → strategy → metrics → engine → results) driven by a top-level `backtest.py` CLI that fetches prices and writes a committed `backtests/` artifact. `dashboard/build.py` reads that artifact and renders equity-curve charts + a metrics table. The scoring pipeline is reused unchanged via a small refactor that extracts the signal-orchestration helpers from `scan.py` into `src/pipeline.py`.

**Tech Stack:** Python 3, pandas, numpy, PyYAML, yfinance/stooq (existing `fetch_prices`), Plotly (existing dashboard charting), pytest.

## Global Constraints

- **No look-ahead:** at month-end date D, the composite is computed from prices `≤ D`; the selected sleeve earns the **forward** return D → next month-end. Verified by a dedicated test.
- **Per-region scoring:** each track scores only its own region's sectors (`score_all` z-scores over the rows passed). This differs from the live scan's pooled-22 scoring; it is intentional (independent tracks).
- **Price-based pillars only:** the backtest excludes constituent breadth and sentiment (cannot be reconstructed historically). Always call `score_all(..., sentiment_score=None, blend_sentiment=False)`.
- **Backtest cache isolation:** fetch prices with `cache_dir="data/backtest_cache"`. The default `data/cache` is keyed by ticker and treats any file reaching "yesterday" as fresh regardless of start date, so it would shadow a long-history request.
- **Strategy rule:** monthly rebalance (last trading day of month), hold **top 5** by composite, equal-weighted, long-only, no transaction costs (turnover reported).
- **Benchmarks:** US track vs `RSP`, EU track vs `EXSA.DE` (from `config/universe.yaml`).
- **Sector key format:** `"<REGION>|<gics_sector>"`, e.g. `"US|Technology"`.
- **Commit style:** conventional commits; subject < 72 chars. End commit messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- **Results artifact lives in `backtests/`** (committed, mirrors the existing `backups/` pattern).

---

### Task 1: Extract signal pipeline into `src/pipeline.py`

Pure refactor, no behaviour change. Move the signal-orchestration helpers out of `scan.py` so the backtest can reuse the exact same signal code.

**Files:**
- Create: `src/pipeline.py`
- Modify: `scan.py:55-228` (remove the moved defs; import them instead)
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Produces:
  - `SIGNAL_COLUMNS: list[str]`
  - `compute_signals_for_sector(sector_key: str, region: str, gics_sector: str, sector_ticker: str, benchmark_ticker: str, prices: dict[str, pd.DataFrame]) -> dict | None`
  - `build_signals_rows(universe: dict, prices: dict[str, pd.DataFrame]) -> list[dict]` (each row: `region`, `gics_sector`, `sector_key`, + all `SIGNAL_COLUMNS`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_pipeline.py
import numpy as np
import pandas as pd
from src.pipeline import SIGNAL_COLUMNS, build_signals_rows


def _price_df(n=260, start=100.0, step=0.5):
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame(
        {"Close": close, "Open": close, "High": close, "Low": close,
         "Volume": pd.Series(1_000_000, index=idx)}
    )


def test_build_signals_rows_produces_expected_keys():
    universe = {
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }
    prices = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    rows = build_signals_rows(universe, prices)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector_key"] == "US|Technology"
    assert row["region"] == "US"
    for col in SIGNAL_COLUMNS:
        assert col in row
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline'`.

- [ ] **Step 3: Create `src/pipeline.py` by moving code verbatim**

Move `SIGNAL_COLUMNS` (`scan.py:55-67`), `_compute_signals_for_sector` (`scan.py:97-176`) and `_build_signals_rows` (`scan.py:179-228`) into `src/pipeline.py`, renaming the two functions to drop the leading underscore (public API). Keep their bodies identical. Add module header and the `logger`.

```python
# src/pipeline.py
"""Signal-orchestration helpers shared by the live scan and the backtest.

Pure functions over a {ticker -> OHLCV DataFrame} price dict. No I/O, no
network, no "now": every signal reads the last row of whatever window it is
given, so these can be driven as-of any historical date by truncating prices.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

SIGNAL_COLUMNS = [
    "rs_ratio",
    "rs_momentum",
    "return_1m",
    "return_3m",
    "return_6m",
    "acceleration",
    "above_50dma",
    "above_200dma",
    "ma50_slope",
    "obv_slope",
    "breadth_above_50dma",
]


def compute_signals_for_sector(
    sector_key: str,
    region: str,
    gics_sector: str,
    sector_ticker: str,
    benchmark_ticker: str,
    prices: dict[str, pd.DataFrame],
) -> dict | None:
    # --- body copied verbatim from scan.py:111-176 ---
    from src.signals.relative_strength import latest_rrg
    from src.signals.momentum import compute_returns, compute_acceleration
    from src.signals.technical import compute_ma_structure, compute_obv

    if sector_ticker not in prices:
        logger.warning("Skipping %s (%s) — ticker %s not in price data", gics_sector, region, sector_ticker)
        return None
    if benchmark_ticker not in prices:
        logger.warning("Skipping %s (%s) — benchmark ticker %s not in price data", gics_sector, region, benchmark_ticker)
        return None

    sector_df = prices[sector_ticker]
    bench_df = prices[benchmark_ticker]

    if "Close" not in sector_df.columns:
        logger.warning("Skipping %s (%s) — no Close column in sector data", gics_sector, region)
        return None

    sector_close = sector_df["Close"]
    bench_close = bench_df["Close"]

    signals: dict[str, float] = {col: float("nan") for col in SIGNAL_COLUMNS}

    try:
        rrg = latest_rrg(sector_close, bench_close)
        signals["rs_ratio"] = rrg["rs_ratio"]
        signals["rs_momentum"] = rrg["rs_momentum"]
    except Exception as exc:
        logger.warning("RRG failed for %s (%s): %s", gics_sector, region, exc)

    try:
        rets = compute_returns(sector_close)
        signals["return_1m"] = rets.get("1m", float("nan"))
        signals["return_3m"] = rets.get("3m", float("nan"))
        signals["return_6m"] = rets.get("6m", float("nan"))
    except Exception as exc:
        logger.warning("compute_returns failed for %s (%s): %s", gics_sector, region, exc)

    try:
        signals["acceleration"] = compute_acceleration(sector_close)
    except Exception as exc:
        logger.warning("compute_acceleration failed for %s (%s): %s", gics_sector, region, exc)

    try:
        ma = compute_ma_structure(sector_close)
        signals["above_50dma"] = ma.get("above_50dma", float("nan"))
        signals["above_200dma"] = ma.get("above_200dma", float("nan"))
        signals["ma50_slope"] = ma.get("ma50_slope", float("nan"))
    except Exception as exc:
        logger.warning("compute_ma_structure failed for %s (%s): %s", gics_sector, region, exc)

    try:
        if "Volume" in sector_df.columns:
            obv = compute_obv(sector_close, sector_df["Volume"])
            signals["obv_slope"] = obv.get("obv_slope", float("nan"))
        else:
            logger.warning("No Volume column for %s (%s) — obv_slope set to NaN", gics_sector, region)
    except Exception as exc:
        logger.warning("compute_obv failed for %s (%s): %s", gics_sector, region, exc)

    return signals


def build_signals_rows(
    universe: dict,
    prices: dict[str, pd.DataFrame],
) -> list[dict]:
    # --- body copied verbatim from scan.py:189-228, calling the public name ---
    us_benchmark = universe["us_benchmark"]
    eu_benchmark = universe["eu_benchmark"]

    rows: list[dict] = []

    for gics_sector, ticker in universe.get("us_sectors", {}).items():
        sector_key = f"US|{gics_sector}"
        sig = compute_signals_for_sector(
            sector_key=sector_key, region="US", gics_sector=gics_sector,
            sector_ticker=ticker, benchmark_ticker=us_benchmark, prices=prices,
        )
        if sig is None:
            continue
        row = {"region": "US", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    for gics_sector, ticker in universe.get("eu_sectors", {}).items():
        sector_key = f"EU|{gics_sector}"
        sig = compute_signals_for_sector(
            sector_key=sector_key, region="EU", gics_sector=gics_sector,
            sector_ticker=ticker, benchmark_ticker=eu_benchmark, prices=prices,
        )
        if sig is None:
            continue
        row = {"region": "EU", "gics_sector": gics_sector, "sector_key": sector_key}
        row.update(sig)
        rows.append(row)

    return rows
```

- [ ] **Step 4: Rewire `scan.py` to import from `src.pipeline`**

In `scan.py`: delete the moved `SIGNAL_COLUMNS`, `_compute_signals_for_sector`, `_build_signals_rows` definitions. Add an import near the other `src` imports (after line 49):

```python
from src.pipeline import SIGNAL_COLUMNS, build_signals_rows
```

Update the call site at `scan.py:420` from `rows = _build_signals_rows(universe, prices)` to `rows = build_signals_rows(universe, prices)`. (`_inject_constituent_breadth` stays in `scan.py` — it is scan-only and uses live S&P 500 membership. It references `SIGNAL_COLUMNS`, now imported.)

- [ ] **Step 5: Run the new test and the existing scan tests**

Run: `pytest tests/test_pipeline.py tests/test_scan_smoke.py -v`
Expected: PASS (new pipeline test + existing scan smoke tests still green).

- [ ] **Step 6: Commit**

```bash
git add src/pipeline.py scan.py tests/test_pipeline.py
git commit -m "refactor: extract signal pipeline into src/pipeline.py

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backtest metrics (`src/backtest/metrics.py`)

Pure performance metrics over a monthly-return / equity series.

**Files:**
- Create: `src/backtest/__init__.py` (empty)
- Create: `src/backtest/metrics.py`
- Test: `tests/test_backtest_metrics.py`

**Interfaces:**
- Produces (all operate on `pd.Series`; `periods_per_year` defaults to 12 for monthly):
  - `equity_curve(returns: pd.Series, initial: float = 1.0) -> pd.Series`
  - `total_return(equity: pd.Series) -> float`
  - `cagr(equity: pd.Series, periods_per_year: float = 12) -> float`
  - `annualized_vol(returns: pd.Series, periods_per_year: float = 12) -> float`
  - `sharpe(returns: pd.Series, periods_per_year: float = 12) -> float`
  - `max_drawdown(equity: pd.Series) -> float` (returns a negative number, e.g. -0.25)
  - `hit_rate(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float`
  - `avg_turnover(turnovers: list[float]) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_metrics.py
import numpy as np
import pandas as pd
import pytest

from src.backtest import metrics as m


def test_equity_curve_and_total_return():
    rets = pd.Series([0.10, -0.10, 0.20])
    eq = m.equity_curve(rets)
    # 1.0 * 1.1 * 0.9 * 1.2 = 1.188
    assert eq.iloc[-1] == pytest.approx(1.188)
    assert m.total_return(eq) == pytest.approx(0.188)


def test_max_drawdown():
    eq = pd.Series([1.0, 1.2, 0.9, 1.0])  # peak 1.2 -> trough 0.9 = -0.25
    assert m.max_drawdown(eq) == pytest.approx(-0.25)


def test_sharpe_zero_vol_is_zero():
    rets = pd.Series([0.01, 0.01, 0.01])
    assert m.sharpe(rets) == 0.0


def test_hit_rate():
    strat = pd.Series([0.02, -0.01, 0.03])
    bench = pd.Series([0.01, 0.00, 0.04])
    # strat beats bench in periods 0 only (2/-? -> 1 of 3)
    assert m.hit_rate(strat, bench) == pytest.approx(1 / 3)


def test_cagr_one_year_monthly():
    rets = pd.Series([0.0] * 12)
    eq = m.equity_curve(rets)
    assert m.cagr(eq) == pytest.approx(0.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_metrics.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.backtest'`.

- [ ] **Step 3: Implement the metrics module**

```python
# src/backtest/__init__.py
# (empty — package marker)
```

```python
# src/backtest/metrics.py
"""Pure performance metrics over periodic returns / equity curves."""
from __future__ import annotations

import numpy as np
import pandas as pd


def equity_curve(returns: pd.Series, initial: float = 1.0) -> pd.Series:
    return initial * (1.0 + returns.fillna(0.0)).cumprod()


def total_return(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float(equity.iloc[-1] / equity.iloc[0] - 1.0)


def cagr(equity: pd.Series, periods_per_year: float = 12) -> float:
    n = len(equity)
    if n < 2 or equity.iloc[0] <= 0:
        return 0.0
    years = (n - 1) / periods_per_year
    if years <= 0:
        return 0.0
    return float((equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0)


def annualized_vol(returns: pd.Series, periods_per_year: float = 12) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    return float(r.std(ddof=1) * np.sqrt(periods_per_year))


def sharpe(returns: pd.Series, periods_per_year: float = 12) -> float:
    r = returns.dropna()
    if len(r) < 2:
        return 0.0
    sd = r.std(ddof=1)
    if sd == 0 or np.isnan(sd):
        return 0.0
    return float(r.mean() / sd * np.sqrt(periods_per_year))


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    return float(dd.min())


def hit_rate(strategy_returns: pd.Series, benchmark_returns: pd.Series) -> float:
    s, b = strategy_returns.align(benchmark_returns, join="inner")
    if len(s) == 0:
        return 0.0
    return float((s > b).mean())


def avg_turnover(turnovers: list[float]) -> float:
    if not turnovers:
        return 0.0
    return float(np.mean(turnovers))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_metrics.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/__init__.py src/backtest/metrics.py tests/test_backtest_metrics.py
git commit -m "feat: backtest performance metrics module

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: As-of scoring + rebalance calendar (`src/backtest/replay.py`)

Drive the existing scoring pipeline as-of a historical date for one region.

**Files:**
- Create: `src/backtest/replay.py`
- Test: `tests/test_backtest_replay.py`

**Interfaces:**
- Consumes: `src.pipeline.build_signals_rows`, `src.pipeline.SIGNAL_COLUMNS`, `src.scoring.score_all`.
- Produces:
  - `truncate_prices(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]`
  - `month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]` (last available trading day per calendar month)
  - `score_as_of(universe: dict, prices: dict[str, pd.DataFrame], as_of: pd.Timestamp, region: str) -> pd.DataFrame | None` — region-filtered scored frame (index = sector_key) or `None` if no rows. Calls `score_all(wide, sentiment_score=None, blend_sentiment=False)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_replay.py
import numpy as np
import pandas as pd

from src.backtest import replay


def _ramp(n, start, step, vol=1_000_000):
    idx = pd.bdate_range("2020-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(vol, index=idx)})


def test_month_end_dates_picks_last_trading_day_per_month():
    idx = pd.bdate_range("2021-01-01", "2021-03-31")
    ends = replay.month_end_dates(idx)
    # Last business days of Jan, Feb, Mar 2021
    assert ends[0] == pd.Timestamp("2021-01-29")
    assert ends[1] == pd.Timestamp("2021-02-26")
    assert ends[2] == pd.Timestamp("2021-03-31")


def test_truncate_prices_drops_future_rows():
    prices = {"XLK": _ramp(300, 100, 0.5)}
    cut = pd.Timestamp("2020-06-01")
    out = replay.truncate_prices(prices, cut)
    assert out["XLK"].index.max() <= cut


def test_score_as_of_returns_region_only_scored_frame():
    universe = {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }
    prices = {
        "XLK": _ramp(300, 100, 0.8),
        "XLE": _ramp(300, 100, 0.1),
        "RSP": _ramp(300, 100, 0.4),
        "EXV3.DE": _ramp(300, 100, 0.5),
        "EXSA.DE": _ramp(300, 100, 0.4),
    }
    scored = replay.score_as_of(universe, prices, pd.Timestamp("2021-01-01"), region="US")
    assert scored is not None
    assert set(scored.index) == {"US|Technology", "US|Energy"}
    assert "composite" in scored.columns
    # Higher-trend XLK should outrank XLE
    assert scored.loc["US|Technology", "composite"] > scored.loc["US|Energy", "composite"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_replay.py -v`
Expected: FAIL with `ImportError` / `module 'src.backtest.replay' not found`.

- [ ] **Step 3: Implement `replay.py`**

```python
# src/backtest/replay.py
"""Drive the existing scoring pipeline as-of a historical date, per region."""
from __future__ import annotations

import pandas as pd

from src.pipeline import SIGNAL_COLUMNS, build_signals_rows
from src.scoring import score_all


def truncate_prices(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in prices.items():
        sliced = df[df.index <= as_of]
        if not sliced.empty:
            out[ticker] = sliced
    return out


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    if len(index) == 0:
        return []
    s = pd.Series(index, index=index)
    # group by year-month period, take the max (last) trading day in each
    last_per_month = s.groupby(index.to_period("M")).max()
    return [pd.Timestamp(d) for d in last_per_month.tolist()]


def score_as_of(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    region: str,
) -> pd.DataFrame | None:
    truncated = truncate_prices(prices, as_of)
    rows = build_signals_rows(universe, truncated)
    rows = [r for r in rows if r["region"] == region]
    if not rows:
        return None
    wide = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
    scored = score_all(wide, weights_path="config/weights.yaml",
                       sentiment_score=None, blend_sentiment=False)
    return scored
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_replay.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/replay.py tests/test_backtest_replay.py
git commit -m "feat: as-of scoring and month-end calendar for backtest

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Strategy simulation (`src/backtest/strategy.py`)

Top-N equal-weight monthly rebalance from a precomputed score series. Pure given its inputs — this is where the no-look-ahead guarantee is tested.

**Files:**
- Create: `src/backtest/strategy.py`
- Test: `tests/test_backtest_strategy.py`

**Interfaces:**
- Produces:
  - `close_at(df: pd.DataFrame, date: pd.Timestamp) -> float` — last `Close` at or before `date` (NaN if none).
  - `forward_returns(prices: dict[str, pd.DataFrame], tickers: list[str], dates: list[pd.Timestamp]) -> pd.DataFrame` — index `dates[:-1]`, columns `tickers`, value = simple return from `dates[i]` to `dates[i+1]`.
  - `simulate(score_by_date: dict[pd.Timestamp, pd.DataFrame], fwd_returns: pd.DataFrame, instrument_of: dict[str, str], top_n: int = 5) -> dict` — returns `{"dates": [...], "strategy_returns": [...], "holdings": [[sector_key,...], ...], "turnover": [...]}`. At each rebalance date (all but the last), picks the `top_n` sector_keys with the highest `composite`, equal-weights their instruments' forward returns.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_strategy.py
import numpy as np
import pandas as pd

from src.backtest import strategy


def _scored(composites: dict[str, float]) -> pd.DataFrame:
    return pd.DataFrame({"composite": composites})


def _prices(values: dict[str, list[float]], dates) -> dict[str, pd.DataFrame]:
    return {t: pd.DataFrame({"Close": pd.Series(v, index=dates)}) for t, v in values.items()}


def test_forward_returns_simple_pct():
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28"), pd.Timestamp("2021-03-31")]
    prices = _prices({"XLK": [100.0, 110.0, 121.0]}, dates)
    fwd = strategy.forward_returns(prices, ["XLK"], dates)
    assert list(fwd.index) == dates[:-1]
    assert fwd.loc[dates[0], "XLK"] == 0.10
    assert fwd.loc[dates[1], "XLK"] == 0.10


def test_simulate_selects_top_n_and_earns_forward_return():
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    score_by_date = {dates[0]: _scored({"US|Tech": 2.0, "US|Energy": -1.0})}
    fwd = pd.DataFrame({"XLK": [0.05], "XLE": [-0.03]}, index=[dates[0]])
    res = strategy.simulate(score_by_date, fwd, instrument_of, top_n=1)
    assert res["holdings"][0] == ["US|Tech"]
    assert res["strategy_returns"][0] == 0.05


def test_simulate_has_no_lookahead():
    """Holdings at date[0] must not depend on any later score."""
    dates = [pd.Timestamp("2021-01-31"), pd.Timestamp("2021-02-28")]
    instrument_of = {"US|Tech": "XLK", "US|Energy": "XLE"}
    fwd = pd.DataFrame({"XLK": [0.05], "XLE": [-0.03]}, index=[dates[0]])

    base = {dates[0]: pd.DataFrame({"composite": {"US|Tech": 2.0, "US|Energy": -1.0}})}
    res_a = strategy.simulate(base, fwd, instrument_of, top_n=1)

    # Add a *future* date with an extreme score; past holding must be unchanged.
    perturbed = dict(base)
    perturbed[dates[1]] = pd.DataFrame({"composite": {"US|Tech": -99.0, "US|Energy": 99.0}})
    res_b = strategy.simulate(perturbed, fwd, instrument_of, top_n=1)
    assert res_b["holdings"][0] == res_a["holdings"][0] == ["US|Tech"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_strategy.py -v`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement `strategy.py`**

```python
# src/backtest/strategy.py
"""Top-N equal-weight monthly rebalance simulation (long-only, no costs)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def close_at(df: pd.DataFrame, date: pd.Timestamp) -> float:
    sliced = df["Close"][df.index <= date]
    if sliced.empty:
        return float("nan")
    return float(sliced.iloc[-1])


def forward_returns(
    prices: dict[str, pd.DataFrame],
    tickers: list[str],
    dates: list[pd.Timestamp],
) -> pd.DataFrame:
    rows: dict[pd.Timestamp, dict[str, float]] = {}
    for i in range(len(dates) - 1):
        d0, d1 = dates[i], dates[i + 1]
        row: dict[str, float] = {}
        for t in tickers:
            df = prices.get(t)
            if df is None:
                row[t] = float("nan")
                continue
            p0, p1 = close_at(df, d0), close_at(df, d1)
            row[t] = (p1 / p0 - 1.0) if (p0 and not np.isnan(p0) and not np.isnan(p1)) else float("nan")
        rows[d0] = row
    return pd.DataFrame.from_dict(rows, orient="index")


def simulate(
    score_by_date: dict[pd.Timestamp, pd.DataFrame],
    fwd_returns: pd.DataFrame,
    instrument_of: dict[str, str],
    top_n: int = 5,
) -> dict:
    dates = sorted(score_by_date.keys())
    out_dates: list[pd.Timestamp] = []
    strat_rets: list[float] = []
    holdings: list[list[str]] = []
    turnover: list[float] = []
    prev: set[str] = set()

    for d in dates:
        if d not in fwd_returns.index:
            continue  # last date / no forward window
        scored = score_by_date[d]
        ranked = scored.sort_values("composite", ascending=False)
        picks = list(ranked.index[:top_n])
        if not picks:
            continue

        rets = []
        for sk in picks:
            ticker = instrument_of.get(sk)
            r = fwd_returns.loc[d].get(ticker, float("nan")) if ticker else float("nan")
            if not np.isnan(r):
                rets.append(r)
        if not rets:
            continue

        out_dates.append(d)
        strat_rets.append(float(np.mean(rets)))
        holdings.append(picks)
        cur = set(picks)
        turnover.append(len(cur ^ prev) / (2 * top_n) if prev else 1.0)
        prev = cur

    return {
        "dates": out_dates,
        "strategy_returns": strat_rets,
        "holdings": holdings,
        "turnover": turnover,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_strategy.py -v`
Expected: PASS (3 tests, including the no-look-ahead test).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/strategy.py tests/test_backtest_strategy.py
git commit -m "feat: top-N monthly rebalance strategy simulation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Per-track engine (`src/backtest/engine.py`)

Wire replay + strategy + metrics into one track result. Takes already-fetched prices, so it is fully testable offline.

**Files:**
- Create: `src/backtest/engine.py`
- Test: `tests/test_backtest_engine.py`

**Interfaces:**
- Consumes: `replay.month_end_dates`, `replay.score_as_of`, `strategy.forward_returns`, `strategy.simulate`, `metrics.*`.
- Produces:
  - `run_track(universe: dict, prices: dict[str, pd.DataFrame], region: str, benchmark_ticker: str, instrument_of: dict[str, str], top_n: int = 5) -> dict | None`
  - `run_all(universe: dict, prices: dict[str, pd.DataFrame], top_n: int = 5) -> dict` — `{"US": <track or None>, "EU": <track or None>}`
- Track result dict shape:
  ```python
  {
    "region": "US", "benchmark": "RSP", "top_n": 5,
    "start": "2004-01-30", "end": "2026-05-29",
    "metrics": {"total_return","cagr","ann_vol","sharpe","max_drawdown",
                "hit_rate","avg_turnover","benchmark_total_return","benchmark_cagr"},
    "equity_curve": [{"date": "YYYY-MM-DD", "strategy": float, "benchmark": float}, ...],
    "holdings": [{"date": "YYYY-MM-DD", "sectors": ["US|Technology", ...]}, ...],
  }
  ```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_engine.py
import numpy as np
import pandas as pd

from src.backtest import engine


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE", "Health": "XLV"},
        "eu_sectors": {},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def test_run_track_produces_curve_and_metrics():
    n = 600
    prices = {
        "XLK": _ramp(n, 100, 0.9),
        "XLE": _ramp(n, 100, 0.2),
        "XLV": _ramp(n, 100, 0.5),
        "RSP": _ramp(n, 100, 0.4),
    }
    instrument_of = {"US|Technology": "XLK", "US|Energy": "XLE", "US|Health": "XLV"}
    track = engine.run_track(_universe(), prices, "US", "RSP", instrument_of, top_n=2)
    assert track is not None
    assert track["region"] == "US"
    assert len(track["equity_curve"]) > 0
    assert "cagr" in track["metrics"]
    # Strongly-trending instruments held -> positive total return
    assert track["metrics"]["total_return"] > 0


def test_run_all_handles_missing_eu_gracefully():
    n = 400
    prices = {"XLK": _ramp(n, 100, 0.9), "XLE": _ramp(n, 100, 0.2),
              "XLV": _ramp(n, 100, 0.5), "RSP": _ramp(n, 100, 0.4)}
    # No EU tickers in prices at all
    result = engine.run_all(_universe(), prices, top_n=2)
    assert result["US"] is not None
    assert result["EU"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement `engine.py`**

```python
# src/backtest/engine.py
"""Per-track orchestration: replay scores -> simulate -> metrics."""
from __future__ import annotations

import logging

import pandas as pd

from src.backtest import metrics, replay, strategy

logger = logging.getLogger(__name__)


def _track_instruments(universe: dict, region: str) -> dict[str, str]:
    key = "us_sectors" if region == "US" else "eu_sectors"
    return {f"{region}|{g}": t for g, t in universe.get(key, {}).items()}


def run_track(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    region: str,
    benchmark_ticker: str,
    instrument_of: dict[str, str],
    top_n: int = 5,
) -> dict | None:
    if benchmark_ticker not in prices:
        logger.warning("Track %s skipped — benchmark %s missing", region, benchmark_ticker)
        return None

    calendar = replay.month_end_dates(prices[benchmark_ticker].index)
    if len(calendar) < 3:
        return None

    # Score each month-end (region cohort only). Keep dates with >= top_n sectors.
    score_by_date: dict[pd.Timestamp, pd.DataFrame] = {}
    for d in calendar:
        scored = replay.score_as_of(universe, prices, d, region)
        if scored is not None and len(scored) >= top_n:
            score_by_date[d] = scored
    if len(score_by_date) < 2:
        return None

    dates = sorted(score_by_date.keys())
    track_tickers = list(instrument_of.values()) + [benchmark_ticker]
    fwd = strategy.forward_returns(prices, track_tickers, dates)

    sim = strategy.simulate(score_by_date, fwd, instrument_of, top_n=top_n)
    if not sim["dates"]:
        return None

    idx = pd.DatetimeIndex(sim["dates"])
    strat_rets = pd.Series(sim["strategy_returns"], index=idx)
    bench_rets = pd.Series(
        [fwd.loc[d, benchmark_ticker] for d in sim["dates"]], index=idx
    ).fillna(0.0)

    strat_eq = metrics.equity_curve(strat_rets)
    bench_eq = metrics.equity_curve(bench_rets)

    equity_curve = [
        {"date": d.strftime("%Y-%m-%d"),
         "strategy": float(strat_eq.loc[d]),
         "benchmark": float(bench_eq.loc[d])}
        for d in idx
    ]
    holdings = [
        {"date": d.strftime("%Y-%m-%d"), "sectors": secs}
        for d, secs in zip(sim["dates"], sim["holdings"])
    ]

    return {
        "region": region,
        "benchmark": benchmark_ticker,
        "top_n": top_n,
        "start": idx[0].strftime("%Y-%m-%d"),
        "end": idx[-1].strftime("%Y-%m-%d"),
        "metrics": {
            "total_return": metrics.total_return(strat_eq),
            "cagr": metrics.cagr(strat_eq),
            "ann_vol": metrics.annualized_vol(strat_rets),
            "sharpe": metrics.sharpe(strat_rets),
            "max_drawdown": metrics.max_drawdown(strat_eq),
            "hit_rate": metrics.hit_rate(strat_rets, bench_rets),
            "avg_turnover": metrics.avg_turnover(sim["turnover"]),
            "benchmark_total_return": metrics.total_return(bench_eq),
            "benchmark_cagr": metrics.cagr(bench_eq),
        },
        "equity_curve": equity_curve,
        "holdings": holdings,
    }


def run_all(universe: dict, prices: dict[str, pd.DataFrame], top_n: int = 5) -> dict:
    return {
        "US": run_track(universe, prices, "US", universe["us_benchmark"],
                        _track_instruments(universe, "US"), top_n=top_n),
        "EU": run_track(universe, prices, "EU", universe["eu_benchmark"],
                        _track_instruments(universe, "EU"), top_n=top_n),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_engine.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/engine.py tests/test_backtest_engine.py
git commit -m "feat: per-track backtest engine

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Results serialization (`src/backtest/results.py`)

Persist the run to the committed `backtests/` directory and load it back.

**Files:**
- Create: `src/backtest/results.py`
- Test: `tests/test_backtest_results.py`

**Interfaces:**
- Produces:
  - `write_results(tracks: dict, out_dir: str = "backtests", generated_at: str = "", top_n: int = 5) -> str` — writes `summary.json` (+ `equity_<region>.csv`, `holdings_<region>.csv` for non-null tracks); returns the summary path.
  - `load_summary(out_dir: str = "backtests") -> dict | None` — returns the parsed summary or `None` if absent.
- `summary.json` shape: `{"generated_at": str, "top_n": int, "tracks": {"US": <track|null>, "EU": <track|null>}}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_backtest_results.py
import json

from src.backtest import results


def _track():
    return {
        "region": "US", "benchmark": "RSP", "top_n": 5,
        "start": "2020-01-31", "end": "2020-03-31",
        "metrics": {"total_return": 0.1, "cagr": 0.4, "ann_vol": 0.1, "sharpe": 1.2,
                    "max_drawdown": -0.05, "hit_rate": 0.6, "avg_turnover": 0.3,
                    "benchmark_total_return": 0.05, "benchmark_cagr": 0.2},
        "equity_curve": [{"date": "2020-01-31", "strategy": 1.0, "benchmark": 1.0},
                         {"date": "2020-02-29", "strategy": 1.1, "benchmark": 1.05}],
        "holdings": [{"date": "2020-01-31", "sectors": ["US|Technology"]}],
    }


def test_write_and_load_roundtrip(tmp_path):
    out = str(tmp_path / "backtests")
    path = results.write_results({"US": _track(), "EU": None},
                                 out_dir=out, generated_at="2026-06-26T00:00:00Z", top_n=5)
    assert path.endswith("summary.json")
    loaded = results.load_summary(out)
    assert loaded["top_n"] == 5
    assert loaded["tracks"]["US"]["region"] == "US"
    assert loaded["tracks"]["EU"] is None
    # CSV exports exist for the non-null track
    assert (tmp_path / "backtests" / "equity_US.csv").exists()
    assert (tmp_path / "backtests" / "holdings_US.csv").exists()


def test_load_summary_absent_returns_none(tmp_path):
    assert results.load_summary(str(tmp_path / "nope")) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_results.py -v`
Expected: FAIL with module-not-found.

- [ ] **Step 3: Implement `results.py`**

```python
# src/backtest/results.py
"""Serialize backtest results to a committed backtests/ directory."""
from __future__ import annotations

import json
import os

import pandas as pd


def write_results(tracks: dict, out_dir: str = "backtests",
                  generated_at: str = "", top_n: int = 5) -> str:
    os.makedirs(out_dir, exist_ok=True)
    summary = {"generated_at": generated_at, "top_n": top_n, "tracks": tracks}

    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    for region, track in tracks.items():
        if not track:
            continue
        pd.DataFrame(track["equity_curve"]).to_csv(
            os.path.join(out_dir, f"equity_{region}.csv"), index=False)
        # Flatten holdings (sectors list -> comma-joined string)
        hold_rows = [{"date": h["date"], "sectors": ", ".join(h["sectors"])}
                     for h in track["holdings"]]
        pd.DataFrame(hold_rows).to_csv(
            os.path.join(out_dir, f"holdings_{region}.csv"), index=False)

    return summary_path


def load_summary(out_dir: str = "backtests") -> dict | None:
    path = os.path.join(out_dir, "summary.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_backtest_results.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/backtest/results.py tests/test_backtest_results.py
git commit -m "feat: backtest results serialization

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: CLI entry point (`backtest.py`)

Tie it together: fetch long history (isolated cache), run both tracks, write results, print a summary.

**Files:**
- Create: `backtest.py`
- Modify: `.gitignore` (ensure `backtests/` is NOT ignored; add `data/backtest_cache/` to ignored caches)
- Test: `tests/test_backtest_cli.py`

**Interfaces:**
- Consumes: `src.data.prices.load_universe`, `src.data.prices.fetch_prices`, `src.backtest.engine.run_all`, `src.backtest.results.write_results`.
- Produces: `build_ticker_list(universe: dict) -> list[str]`, `run(args) -> int`.

- [ ] **Step 1: Write the failing test (ticker-list helper, no network)**

```python
# tests/test_backtest_cli.py
import backtest


def test_build_ticker_list_dedups_and_includes_benchmarks():
    universe = {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }
    tickers = backtest.build_ticker_list(universe)
    assert tickers == ["XLK", "XLE", "EXV3.DE", "RSP", "EXSA.DE"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_backtest_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'backtest'`.

- [ ] **Step 3: Implement `backtest.py`**

```python
#!/usr/bin/env python3
"""backtest.py — strategy backtest for the sector-momentum scanner.

Fetches long price history, runs the US and EU top-N monthly rotation
strategies, and writes results to backtests/ for the dashboard to render.

    python backtest.py                 # both tracks, full history
    python backtest.py --top-n 5       # override hold count
    python backtest.py --start 2010-01-01
"""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger("backtest")

DEFAULT_START = "2003-01-01"
BACKTEST_CACHE = "data/backtest_cache"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sector-momentum strategy backtest.")
    p.add_argument("--top-n", type=int, default=5, help="Number of sectors to hold (default 5).")
    p.add_argument("--start", default=DEFAULT_START, help="History start date (YYYY-MM-DD).")
    p.add_argument("--out", default="backtests", help="Output directory.")
    return p.parse_args()


def build_ticker_list(universe: dict) -> list[str]:
    raw = (list(universe.get("us_sectors", {}).values())
           + list(universe.get("eu_sectors", {}).values())
           + [universe["us_benchmark"], universe["eu_benchmark"]])
    seen: set[str] = set()
    out: list[str] = []
    for t in raw:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def run(args: argparse.Namespace) -> int:
    from src.data.prices import load_universe, fetch_prices
    from src.backtest.engine import run_all
    from src.backtest.results import write_results

    universe = load_universe("config/universe.yaml")
    tickers = build_ticker_list(universe)
    end = date.today().strftime("%Y-%m-%d")

    logger.info("Fetching %d tickers %s → %s (cache=%s) …", len(tickers), args.start, end, BACKTEST_CACHE)
    prices = fetch_prices(tickers=tickers, start=args.start, end=end, cache_dir=BACKTEST_CACHE)
    logger.info("Got %d / %d tickers", len(prices), len(tickers))

    logger.info("Running tracks (top_n=%d) …", args.top_n)
    tracks = run_all(universe, prices, top_n=args.top_n)

    path = write_results(tracks, out_dir=args.out,
                         generated_at=datetime.utcnow().isoformat() + "Z", top_n=args.top_n)

    for region, tr in tracks.items():
        if not tr:
            logger.info("  %s: no result (insufficient data)", region)
            continue
        m = tr["metrics"]
        logger.info("  %s %s→%s | strat CAGR %.1f%% vs bench %.1f%% | Sharpe %.2f | maxDD %.1f%%",
                    region, tr["start"], tr["end"], 100 * m["cagr"],
                    100 * m["benchmark_cagr"], m["sharpe"], 100 * m["max_drawdown"])
    logger.info("Wrote %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(run(_parse_args()))
```

- [ ] **Step 4: Ensure `backtests/` is tracked and the backtest cache is ignored**

Inspect `.gitignore`. If `data/` is broadly ignored, add an explicit ignore for `data/backtest_cache/` (it likely already falls under the `data/cache` ignore). Confirm `backtests/` is NOT matched by any ignore rule (mirror how `backups/` is handled). If `backtests/` would be ignored, add `!backtests/`.

Run: `git check-ignore backtests/summary.json || echo "tracked OK"`
Expected: `tracked OK`.

- [ ] **Step 5: Run the CLI test + a real smoke run**

Run: `pytest tests/test_backtest_cli.py -v`
Expected: PASS (1 test).

Then a real run (hits the network; minutes):
Run: `python backtest.py --start 2015-01-01`
Expected: logs per-track CAGR/Sharpe lines and `Wrote backtests/summary.json`. Inspect `backtests/summary.json` — both tracks present (EU may start later), metrics finite.

- [ ] **Step 6: Commit (code + seed results)**

```bash
git add backtest.py tests/test_backtest_cli.py .gitignore backtests/
git commit -m "feat: backtest CLI with US/EU rotation tracks

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Dashboard "Backtest" tab

Read `backtests/summary.json` in the build and render per-track equity curves + a metrics table.

**Files:**
- Modify: `dashboard/build.py` (add `_build_backtest_context`, `_build_backtest_figures`; call in `main()`; add context keys)
- Modify: `dashboard/templates/index.html.j2` (tab button, panel, JS var, renderer, switchTab wiring)
- Test: `tests/test_dashboard_backtest.py`

**Interfaces:**
- Consumes: `src.backtest.results.load_summary`, existing `go` / `pio` (plotly) imports in `build.py`, `_WARM_PALETTE`.
- Produces:
  - `_build_backtest_figures(summary: dict | None) -> dict` — `{region: plotly_fig_json}` (strategy vs benchmark equity lines).
  - `_build_backtest_context(backtests_dir: str) -> dict` — `{"backtest_json": <json str>, "backtest_metrics": [rows], "has_backtest": bool}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dashboard_backtest.py
import json

from dashboard.build import _build_backtest_figures


def _summary():
    return {
        "generated_at": "2026-06-26T00:00:00Z", "top_n": 5,
        "tracks": {
            "US": {
                "region": "US", "benchmark": "RSP", "top_n": 5,
                "start": "2020-01-31", "end": "2020-03-31",
                "metrics": {"total_return": 0.1, "cagr": 0.4, "ann_vol": 0.1,
                            "sharpe": 1.2, "max_drawdown": -0.05, "hit_rate": 0.6,
                            "avg_turnover": 0.3, "benchmark_total_return": 0.05,
                            "benchmark_cagr": 0.2},
                "equity_curve": [{"date": "2020-01-31", "strategy": 1.0, "benchmark": 1.0},
                                 {"date": "2020-02-29", "strategy": 1.1, "benchmark": 1.05}],
                "holdings": [{"date": "2020-01-31", "sectors": ["US|Technology"]}],
            },
            "EU": None,
        },
    }


def test_build_backtest_figures_returns_valid_plotly_json():
    figs = _build_backtest_figures(_summary())
    assert "US" in figs
    parsed = json.loads(figs["US"])
    assert "data" in parsed and "layout" in parsed
    # strategy + benchmark traces
    assert len(parsed["data"]) == 2


def test_build_backtest_figures_empty_when_none():
    figs = _build_backtest_figures(None)
    assert figs == {}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dashboard_backtest.py -v`
Expected: FAIL with `ImportError: cannot import name '_build_backtest_figures'`.

- [ ] **Step 3: Add the builders to `dashboard/build.py`**

Add near the other `_build_*_figure` functions (e.g. after `_build_history_figure`, ~line 941). Reuse the existing module-level `go`, `pio`, `_WARM_PALETTE`.

```python
def _build_backtest_figures(summary) -> dict:
    """Per-track equity curves (strategy vs benchmark). Returns {region: fig_json}."""
    if not summary or not summary.get("tracks"):
        return {}
    figs: dict[str, str] = {}
    for region, track in summary["tracks"].items():
        if not track or not track.get("equity_curve"):
            continue
        dates = [p["date"] for p in track["equity_curve"]]
        strat = [p["strategy"] for p in track["equity_curve"]]
        bench = [p["benchmark"] for p in track["equity_curve"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=strat, mode="lines",
                                 name=f"Top {track['top_n']} strategy",
                                 line=dict(color=_WARM_PALETTE[0])))
        fig.add_trace(go.Scatter(x=dates, y=bench, mode="lines",
                                 name=f"Benchmark ({track['benchmark']})",
                                 line=dict(color=_WARM_PALETTE[3], dash="dash")))
        fig.update_layout(
            title=dict(text=f"{region} — growth of 1.0", font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Equity (×)", gridcolor="#DFD5BE"),
            paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
            legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
            margin=dict(l=50, r=20, t=50, b=50), hovermode="x unified",
        )
        figs[region] = pio.to_json(fig)
    return figs


def _build_backtest_context(backtests_dir: str) -> dict:
    """Load summary.json and shape it for the template."""
    import json as _json
    from src.backtest.results import load_summary

    summary = load_summary(backtests_dir)
    figs = _build_backtest_figures(summary)
    rows: list[dict] = []
    if summary:
        for region, track in summary["tracks"].items():
            if not track:
                continue
            m = track["metrics"]
            rows.append({
                "region": region, "start": track["start"], "end": track["end"],
                "benchmark": track["benchmark"], "top_n": track["top_n"],
                "cagr": f"{100 * m['cagr']:.1f}%",
                "benchmark_cagr": f"{100 * m['benchmark_cagr']:.1f}%",
                "sharpe": f"{m['sharpe']:.2f}",
                "max_drawdown": f"{100 * m['max_drawdown']:.1f}%",
                "hit_rate": f"{100 * m['hit_rate']:.0f}%",
                "avg_turnover": f"{100 * m['avg_turnover']:.0f}%",
            })
    return {
        "backtest_json": _json.dumps(figs),
        "backtest_metrics": rows,
        "has_backtest": bool(figs),
    }
```

- [ ] **Step 4: Wire into `main()` and the render context**

In `dashboard/build.py main()`, after the config-load block (~line 1142) add:

```python
    backtest_ctx = _build_backtest_context(str(project_root / "backtests"))
```

In the `_render(... context=dict(...))` call (~line 1216-1235), add three keys:

```python
            backtest_json=backtest_ctx["backtest_json"],
            backtest_metrics=backtest_ctx["backtest_metrics"],
            has_backtest=backtest_ctx["has_backtest"],
```

- [ ] **Step 5: Add the tab to the template**

In `dashboard/templates/index.html.j2`:

(a) Tab button — after the History button (~line 557):
```html
  <button class="tab-btn" onclick="switchTab('backtest', this)" role="tab">Backtest</button>
```

(b) Panel — after the History `</section>` (~line 789):
```html
<section id="tab-backtest" class="tab-panel">
  <p class="tab-note">Monthly top-{{ backtest_metrics[0].top_n if backtest_metrics else 5 }} rotation, equal-weight, long-only, no costs. Price-based signals only (breadth &amp; sentiment excluded). Each region scored within its own cohort.</p>
  {% if has_backtest %}
  <div class="table-wrap">
    <table>
      <thead><tr><th>Track</th><th>Window</th><th>Strategy CAGR</th><th>Bench CAGR</th><th>Sharpe</th><th>Max DD</th><th>Hit rate</th><th>Turnover</th></tr></thead>
      <tbody>
        {% for r in backtest_metrics %}
        <tr><td>{{ r.region }}</td><td>{{ r.start }} → {{ r.end }}</td><td>{{ r.cagr }}</td><td>{{ r.benchmark_cagr }}</td><td>{{ r.sharpe }}</td><td>{{ r.max_drawdown }}</td><td>{{ r.hit_rate }}</td><td>{{ r.avg_turnover }}</td></tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  <div class="chart-container" id="backtest-chart-US" style="height:420px"></div>
  <div class="chart-container" id="backtest-chart-EU" style="height:420px"></div>
  {% else %}
  <p style="text-align:center;padding:24px;color:var(--fg4)">No backtest run yet. Run <code>python backtest.py</code>.</p>
  {% endif %}
</section>
```

(c) JS var — alongside the other injected vars (~line 911):
```javascript
var BACKTEST_DATA = {{ backtest_json | safe }};
```

(d) Renderer + switchTab wiring (~line 929 and ~line 900):
```javascript
function renderBacktest() {
  if (_rendered.backtest) return;
  _rendered.backtest = true;
  ['US', 'EU'].forEach(function (rg) {
    var el = document.getElementById('backtest-chart-' + rg);
    if (el && BACKTEST_DATA[rg]) {
      var f = BACKTEST_DATA[rg];
      Plotly.newPlot(el, f.data, f.layout, {responsive: true, displayModeBar: true});
    }
  });
}
```
Add inside `switchTab`: `if (name === 'backtest') renderBacktest();`

- [ ] **Step 6: Run tests (new + the context-coverage guard)**

Run: `pytest tests/test_dashboard_backtest.py tests/test_dashboard_js.py -v`
Expected: PASS. (`test_dashboard_js.py` includes `test_render_context_covers_all_template_js_vars`, which now passes because `backtest_json` is in the context.)

- [ ] **Step 7: Rebuild the dashboard and verify the tab**

Run: `.venv/bin/python dashboard/build.py`
Expected: build succeeds; open `docs/index.html`, click **Backtest** — equity curves for US (and EU if data) + the metrics table render. With no `backtests/` present, the tab shows the "No backtest run yet" placeholder and the build still succeeds.

- [ ] **Step 8: Commit**

```bash
git add dashboard/build.py dashboard/templates/index.html.j2 tests/test_dashboard_backtest.py docs/
git commit -m "feat: dashboard Backtest tab (equity curves + metrics)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Backlog hygiene + full suite

Per the project rule, update `BACKLOG.md` in this same branch.

**Files:**
- Modify: `BACKLOG.md`

- [ ] **Step 1: Run the whole suite**

Run: `pytest -q`
Expected: all green (existing + new backtest/dashboard tests).

- [ ] **Step 2: Move the backlog item**

In `BACKLOG.md`, under `## Phase 3 features`, change the Backtest bullet to reflect Phase 1 shipped (note Phase 2 — rotation event-study — still pending), or add a Done entry dated today (2026-06-26): `~~Backtest against past rotations (Phase 1 — edge)~~ — US/EU monthly top-5 rotation backtest vs RSP/EXSA.DE; backtest.py CLI + backtests/ artifact + dashboard Backtest tab. Phase 2 (rotation event-study) pending.`

- [ ] **Step 3: Commit**

```bash
git add BACKLOG.md
git commit -m "docs: backlog — backtest phase 1 (edge) shipped

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- Edge strategy backtest (US + EU tracks, monthly top-5) → Tasks 3–7. ✓
- CLI computes + persists; dashboard renders → Tasks 7 (CLI/results) + 8 (tab). ✓
- Point-in-time / no look-ahead → enforced in `replay`/`strategy`, tested in Task 4. ✓
- Per-region scoring, price-pillars-only → Task 3 (`score_as_of` region filter + `blend_sentiment=False`). ✓
- Max-history-per-track + EU graceful start → Task 5 (`run_track` keeps dates with ≥ top_n sectors; `run_all` returns None for absent EU), tested in Task 5. ✓
- Metrics (CAGR/Sharpe/maxDD/hit-rate/turnover) → Task 2. ✓
- Committed `backtests/` artifact + cache isolation → Tasks 6, 7 (+ `.gitignore`). ✓
- Graceful-absence on the dashboard → Task 8 (placeholder + empty figures). ✓
- Phase 2 (rotation event-study) → intentionally OUT of this plan (separate follow-up), per the approved phasing.

**Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**Type consistency:** `score_by_date` is `dict[Timestamp, DataFrame]` in Tasks 4 & 5; `instrument_of` keyed by `sector_key` everywhere; track-result dict shape identical across Tasks 5, 6, 8; `_build_backtest_figures` returns `{region: json_str}` consumed as `BACKTEST_DATA[rg]` in the template. ✓

**Out of scope (Phase 2 / later):** rotation event-study (`config/rotations.yaml`, `src/backtest/rotations.py`, rotation small-multiples), transaction costs, walk-forward. Tracked in the spec.
