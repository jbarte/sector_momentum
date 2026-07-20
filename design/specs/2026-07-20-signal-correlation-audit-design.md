# Signal correlation audit: drop `above_200dma`

## Problem

The level score equal-weights 5 signals: `rs_ratio`, `return_3m`,
`return_6m`, `above_50dma`, `above_200dma`. The last two both measure
"how far is price from its moving average" at different windows (50-day
vs 200-day) and are structurally collinear — in trending markets they
move in lockstep. This effectively double-counts the MA-distance factor
relative to momentum (`rs_ratio`) and multi-horizon returns.

## Solution

1. Run a one-time correlation analysis script to confirm the hypothesis
   and document the decision.
2. Remove `above_200dma` from `_LEVEL_SIGNALS` in scoring. Level goes
   from 5 signals to 4.
3. Update dashboard breakdown to stop showing `above_200dma` as a scored
   level signal.
4. Validate via backtest (before/after CAGR, Sharpe, max-drawdown).

The raw `above_200dma` value is still computed and stored in the DB —
only its role as a scoring input and its appearance in the breakdown
panel change.

## Scope

### 1. One-time correlation script

Create `scripts/signal_correlation.py`. It loads the latest scan's raw
signal values from the DB (or computes them from cached prices) and
prints the 9x9 Pearson correlation matrix for the scored signals,
highlighting pairs with |r| > 0.7.

This is a local diagnostic tool — not CI, not production. It documents
the decision to drop `above_200dma`.

### 2. Remove `above_200dma` from scoring

**`src/scoring.py`**: Change `_LEVEL_SIGNALS` from:
```python
_LEVEL_SIGNALS = ["rs_ratio", "return_3m", "return_6m", "above_50dma", "above_200dma"]
```
to:
```python
_LEVEL_SIGNALS = ["rs_ratio", "return_3m", "return_6m", "above_50dma"]
```

**`config/weights.yaml`**: Remove `above_200dma` from `level_signals`
display ordering section.

### 3. Update dashboard breakdown

**`dashboard/breakdown.py`**:
- Remove `above_200dma` from `_SIGNAL_META` (or change its `group` to
  `"info"` so it appears as an unscored reference signal alongside
  `return_1m` and `breadth_above_50dma`).
- Remove `above_200dma` from `_SIGNAL_DESCRIPTIONS` if demoting, or
  keep if showing as info.
- Update the score-tree meta text from `"5 signals"` to `"4 signals"`
  for the level row.

**Decision: demote to info.** `above_200dma` is still useful context
(is the sector in a long-term uptrend?). Show it in the "Not scored"
info row alongside `return_1m` and `breadth_above_50dma`, rather than
hiding it entirely.

### 4. Rescore.js

No change needed. `rescore.js` operates on pre-computed `data_score`
and `sentiment_score` from scan history — it does not reference
individual signals.

### 5. Backtest validation

Run `python backtest.py` before and after the scoring change. Compare
US and EU track CAGR, Sharpe, and max-drawdown. The change should be
roughly neutral. If dropping the signal materially degrades performance,
reconsider.

### 6. Tests

- Update `tests/test_scoring.py`: any test that asserts on level-score
  computation with 5 signals should reflect the new 4-signal list.
- Update `tests/test_dashboard_js.py` or `tests/test_breakdown.py` if
  they assert on the breakdown panel's signal count or structure.

### 7. BACKLOG.md

- Rewrite the queued "Signal correlation audit + risk-adjusted momentum"
  section to cover only what remains: risk-adjusted momentum
  (return/volatility) and max-drawdown leaderboard column.
- Add a Done entry for the correlation audit and `above_200dma` removal.

## What doesn't change

- `src/signals/technical.py` — still computes `above_200dma`
- `src/pipeline.py` / `SIGNAL_COLUMNS` — `above_200dma` stays in the
  signal row and DB storage
- `scan.py`, DB schema — unchanged
- `rescore.js` — unchanged (operates on aggregate scores, not signals)
- The backtest engine — unchanged

## Out of scope

- Risk-adjusted momentum / max-drawdown signals (remains queued)
- PCA or inverse-correlation weighting
- Moving `ma50_slope` between groups
- Changes to the backtest engine
