# Backtest against past rotations — design

**Date:** 2026-06-26
**Status:** Approved (design); pending implementation plan
**Backlog item:** Phase 3 — "Backtest against past rotations — validate signals against
historical sector rotations (e.g. energy 2021–22)"

## Purpose

Validate that the scanner's momentum signals actually have predictive value, in two
complementary ways:

1. **Edge** — a strategy backtest: does ranking sectors by composite and holding the
   leaders beat a passive benchmark?
2. **Early-flag** — a rotation event-study: for known historical rotations (e.g. energy
   2021–22), did the scanner's rank for that sector rise *before* the move?

This is analytical/validation tooling, not investment advice. It exists so we can trust
(or distrust) the composite before acting on it.

## Why this is feasible

The scoring pipeline is **point-in-time pure**: `score_all()` (`src/scoring.py:134`) is a
pure function of a signals DataFrame with no `datetime.now()` inside it, and every signal
function reads the *last row* of whatever price window it's given. So a historical score
as-of date D is just: fetch prices, truncate to `≤ D`, recompute signals, call
`score_all(..., blend_sentiment=False)`. No changes to signal or scoring logic are needed.

## Locked decisions

- **Two deliverables, two phases:** edge (strategy) first, early-flag (event-study) second.
- **Architecture:** `backtest.py` CLI **computes and persists** results; `dashboard/build.py`
  **reads** them and renders a Backtest tab. Slow compute is decoupled from the build.
- **Strategy universe:** US and EU as **two parallel tracks**.
  - US: 11 SPDR Select sector ETFs, benchmark **RSP**.
  - EU: 11 STOXX Europe 600 sector ETFs, benchmark **EXSA.DE**.
- **Strategy rule:** monthly rebalance; hold **top 5** sectors by composite, equal-weighted;
  long-only.

## Confirmed defaults

- **No transaction costs** in the MVP. Turnover is computed and reported, so a per-rebalance
  bps cost can be added later behind a flag.
- **No look-ahead.** At each month-end date D, the composite is computed using only prices
  `≤ D`. The sleeve selected at D earns the **forward** return from D to the next month-end.
  This is the primary correctness risk and is covered by a dedicated test (below).
- **Period:** maximum available history **per track** (not a common fixed start). US extends
  to ~2003 (SPDRs + RSP); EU ETFs are younger and uneven, so the EU track auto-starts at the
  first month-end where all its instruments have ≥ 200 trading days of history. The report
  states the actual start date per track.
- **Fidelity caveat (stated prominently in the report):** the backtested composite uses the
  **price-based pillars only**. Constituent breadth (US-only, computed against *today's* S&P
  500 membership) and sentiment cannot be reconstructed historically, so both are excluded
  (the scorer already treats missing signals as neutral `z = 0`). The backtest therefore
  validates the price/momentum core of the composite, not breadth or sentiment.
- **Benchmark:** each track is compared to its own benchmark held buy-and-hold (RSP / EXSA.DE).
- **Metrics:** total return, CAGR, annualized volatility, Sharpe (rf = 0), max drawdown,
  monthly hit-rate vs benchmark, average monthly turnover.

## Architecture / components

A new `src/backtest/` package of focused modules plus a top-level `backtest.py` CLI,
mirroring the existing `scan.py` / `stats.py` structure.

### Refactor (prerequisite, no behavior change)
- **`src/pipeline.py`** — extract the signal-orchestration helpers currently private in
  `scan.py` (`_build_signals_rows`, `_compute_signals_for_sector`) into a shared module so
  `scan.py` and the backtest call the *same* signal code. `scan.py` imports them from here.
  Pure move + re-export; existing scan behavior and tests must stay green.

### New modules
- **`src/backtest/replay.py`**
  - `score_as_of(universe, prices, as_of_date) -> pd.DataFrame` — truncate each price series
    to `≤ as_of_date`, build signals via `pipeline`, call `score_all(..., blend_sentiment=False)`.
    Returns the standard scored frame (index `region|gics_sector`; columns incl. `composite`,
    `rank`).
  - `month_end_dates(prices, track) -> list[pd.Timestamp]` — the rebalance calendar (last
    trading day of each month) restricted to dates where the track has enough history.
  - `score_series(universe, prices, dates, track) -> dict[date -> scored_df]` — drive
    `score_as_of` across the calendar for one track.
- **`src/backtest/strategy.py`**
  - `simulate(score_series, forward_returns, top_n=5) -> StrategyResult` — at each rebalance,
    select the top-N instruments of the track by composite, equal-weight, hold to next
    rebalance. Produces: monthly equity curve (strategy + benchmark), per-month holdings,
    per-month turnover. No look-ahead by construction (forward returns indexed strictly after
    the selection date).
- **`src/backtest/metrics.py`** — pure functions over an equity/return series: `cagr`,
  `annualized_vol`, `sharpe`, `max_drawdown`, `hit_rate(strategy, benchmark)`,
  `avg_turnover`. No I/O.
- **`src/backtest/rotations.py`** (Phase 2)
  - Reads `config/rotations.yaml` (new, editable). Each rotation: `name`, `region`,
    `gics_sector`, `window` (start/end).
  - For each rotation, produce the sector's composite **rank over time** through the window
    alongside the sector instrument's price, so the chart shows whether rank improved ahead
    of the price move.
- **`src/backtest/results.py`**
  - Serializes to a committed **`backtests/`** directory (mirrors the existing `backups/`
    pattern): `summary.json` (per-track metrics + rotation series + run metadata) and CSVs
    (`equity_<track>.csv`, `holdings_<track>.csv`). This is the source of truth the dashboard
    reads.

### CLI
- **`backtest.py`** — orchestrates: load universe → fetch full history (cached) → for each
  track, build the month-end calendar, `score_series`, `strategy.simulate`, `metrics` →
  (Phase 2) `rotations` → `results.write`. Flags: `--track us|eu|all` (default all),
  `--top-n` (default 5), `--start` (override), `--no-rotations`. Prints a concise summary.

### Dashboard
- **`dashboard/build.py`** reads `backtests/summary.json` (+ CSVs) and renders a new
  **Backtest** tab using Plotly (same approach as the existing History tab):
  - Phase 1: per-track equity curve (strategy vs benchmark) + a metrics table; the fidelity
    caveat shown inline.
  - Phase 2: rotation small-multiples (rank-over-time vs price per curated rotation).
  - If `backtests/` is absent, the tab renders a "no backtest run yet" placeholder (build
    must not fail).

## Data flow

```
backtest.py
  └─ load_universe(config/universe.yaml)
  └─ fetch_prices(all tickers, max range)          # cached in data/cache/
  └─ for track in {us, eu}:
       calendar = month_end_dates(prices, track)
       scores   = { D: score_as_of(prices ≤ D)  for D in calendar }   # no look-ahead
       result   = strategy.simulate(scores, forward_returns, top_n=5)
       metrics  = metrics.compute(result)
  └─ rotations = rotations.event_study(config/rotations.yaml)         # Phase 2
  └─ results.write("backtests/")                    # summary.json + CSVs (committed)

dashboard/build.py
  └─ read "backtests/" → render Backtest tab (Plotly)
```

## Error handling / edge cases

- **Missing/short EU history** — instruments without ≥200d at a given month-end are excluded
  from that month's ranking; the track starts when the universe is sufficiently covered.
- **yfinance gaps / failed tickers** — `fetch_prices` already soft-fails per ticker; a track
  with a missing benchmark aborts that track with a clear error, the other track proceeds.
- **Absent `backtests/`** — dashboard shows a placeholder, never errors.
- **NaN signals** — already handled by `zscore_cross_section` (→ neutral 0).

## Testing

- `metrics.py` — known return series → known CAGR / Sharpe / max-drawdown (hand-computed).
- `strategy.py` — synthetic scores + returns → expected equity curve and holdings; an
  explicit **no-look-ahead** test: perturbing a *future* month's score must not change any
  past holding or past equity value.
- `replay.py` — `score_as_of` on a window ending at the latest scan date reproduces the live
  scan's ranking for the price-based pillars (breadth/sentiment excluded).
- `rotations.py` — windowing returns the expected rank series for a synthetic rotation.
- Refactor safety — existing `scan.py` tests stay green after the `pipeline` extraction.

## Phasing

- **Phase 1 (edge):** `pipeline` refactor → `replay` → `strategy` → `metrics` → `results` →
  `backtest.py` (edge path) → Backtest tab (equity curves + metrics). Shippable on its own.
- **Phase 2 (early-flag):** `config/rotations.yaml` + `rotations.py` → results extension →
  rotation small-multiples in the tab.

## Out of scope (YAGNI)

- Transaction costs / slippage modeling (turnover is reported; costs deferred).
- Short selling, leverage, position sizing beyond equal-weight.
- Parameter optimization / walk-forward (this validates the *current* weights, it doesn't
  tune them).
- Reconstructing historical constituent breadth or sentiment.
