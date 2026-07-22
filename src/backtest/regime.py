"""Point-in-time market-regime helpers for the regime-weighting research spike.

Regime is binary: risk-on when SPY's latest close (on/before a date) is at or
above its trailing 200-day simple moving average, else risk-off. Everything is
computed from closes on/before the as-of date — no look-ahead.
"""
from __future__ import annotations

from typing import Callable

import pandas as pd

_SMA_WINDOW = 200


def is_risk_on(spy_df: pd.DataFrame | None, as_of: pd.Timestamp,
               sma_window: int = _SMA_WINDOW) -> bool:
    """True if SPY's close as-of `as_of` >= its trailing SMA(sma_window).

    Uses only closes on/before `as_of`. During warm-up (< sma_window closes)
    or when SPY data is missing, defaults to True (risk-on / neutral)."""
    if spy_df is None or spy_df.empty or "Close" not in spy_df.columns:
        return True
    closes = spy_df["Close"]
    closes = closes[closes.index <= as_of].dropna()
    if len(closes) < sma_window:
        return True
    sma = float(closes.iloc[-sma_window:].mean())
    return float(closes.iloc[-1]) >= sma


def make_weights_fn(spy_df: pd.DataFrame | None,
                    on: tuple[float, float],
                    off: tuple[float, float],
                    sma_window: int = _SMA_WINDOW) -> Callable[[pd.Timestamp], tuple[float, float]]:
    """Return a fn mapping a rebalance date -> (level_weight, change_weight):
    `on` when risk-on, `off` when risk-off."""
    def _fn(as_of: pd.Timestamp) -> tuple[float, float]:
        return on if is_risk_on(spy_df, as_of, sma_window) else off
    return _fn


def regime_stats(spy_df: pd.DataFrame | None, dates,
                 sma_window: int = _SMA_WINDOW) -> dict:
    """Fraction of `dates` risk-on and number of regime switches across them."""
    flags = [is_risk_on(spy_df, d, sma_window) for d in dates]
    n = len(flags)
    pct_on = (sum(flags) / n) if n else 0.0
    switches = sum(1 for a, b in zip(flags, flags[1:]) if a != b)
    return {"pct_risk_on": pct_on, "n_switches": switches, "n_dates": n}
