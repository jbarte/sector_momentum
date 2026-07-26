"""Walk-forward scheme selection over precomputed fixed-scheme return series.

Pure functions only -- no I/O, no network, no DB. A "scheme" is one weighting
choice (a fixed level/change split, or a date-dependent rule); its monthly
return series comes from a completed `run_track` result.

A fixed scheme's returns do not depend on when you evaluate them, so the
walk-forward track is a *stitch* of the per-scheme series: at each re-selection
date pick the best scheme over the strictly-preceding window, then splice in
that scheme's returns for the next `cadence` months. No nested backtests.
"""
from __future__ import annotations

import logging
import math

import pandas as pd

from src.backtest import metrics

logger = logging.getLogger(__name__)


def returns_from_equity_curve(equity_curve: list[dict],
                             key: str = "strategy") -> pd.Series:
    """Monthly returns implied by a `run_track` equity curve, indexed by date.

    `run_track`'s equity curve is a cumulative product starting at 1.0, so
    pct_change() recovers the per-period returns exactly. The resulting index is
    the realization date of each return (the curve's dates from the second point
    onward).
    """
    if not equity_curve or len(equity_curve) < 2:
        return pd.Series(dtype=float)
    idx = pd.to_datetime([p["date"] for p in equity_curve])
    values = pd.Series([float(p[key]) for p in equity_curve],
                       index=idx, dtype=float)
    return values.pct_change().iloc[1:]


def align_scheme_returns(returns_by_scheme: dict[str, pd.Series]) -> pd.DataFrame:
    """Align per-scheme return series onto the dates they all share.

    All schemes run over the same calendar, so this is normally a no-op; any
    dropped month means a scheme lacked data there, which is logged rather than
    silently absorbed.
    """
    if not returns_by_scheme:
        return pd.DataFrame()
    df = pd.DataFrame(returns_by_scheme)
    before = len(df)
    df = df.dropna(how="any")
    if len(df) < before:
        logger.warning("Dropped %d month(s) not shared by every scheme",
                       before - len(df))
    return df


def select_scheme(returns: pd.DataFrame, pos: int, window: int) -> str | None:
    """Best scheme by Sharpe over `returns.iloc[pos-window:pos]`.

    The window ends at `pos` exclusive, so selection never sees `returns.iloc[pos]`
    itself -- the month the pick is about to be applied to (see `stitch_walk_forward`'s
    `effective_from`). Returns None when history is too short.

    `metrics.sharpe` always returns a float (it guards len<2 and sd==0, both
    yielding 0.0) and never raises, so no per-scheme metric guard is needed: a
    flat scheme simply scores 0.0. Ties break by column order, which is
    deterministic for a fixed scheme list.
    """
    if returns.empty or pos < window or pos > len(returns):
        return None
    trailing = returns.iloc[pos - window:pos]
    if trailing.empty:
        return None

    best_name, best_score = None, -math.inf
    for name in returns.columns:
        score = float(metrics.sharpe(trailing[name]))
        if score > best_score:
            best_name, best_score = name, score
    return best_name


def stitch_walk_forward(
    returns: pd.DataFrame,
    window: int,
    cadence: int,
    baseline: str,
) -> tuple[pd.Series, list[tuple]]:
    """Out-of-sample return series from periodic scheme re-selection.

    Months before the first full `window` use `baseline`, so every track covers
    the same period and comparisons stay apples-to-apples. Thereafter, every
    `cadence` months the best trailing-window scheme is selected and applied
    forward.

    Returns (stitched monthly returns indexed like `returns`,
    [(effective_from, scheme_name), ...]). `effective_from` is the first month
    the pick is *applied to* (`returns.index[pos]`), not the date the decision
    was made -- the decision itself uses only data strictly before that month
    (`returns.iloc[pos-window:pos]`, via `select_scheme`). Reading it as a
    "selection date" invites exactly the look-ahead misreading this stitching
    is designed to avoid.
    """
    if returns.empty:
        return pd.Series(dtype=float), []
    if baseline not in returns.columns:
        raise ValueError(f"baseline {baseline!r} not among schemes "
                         f"{list(returns.columns)}")
    if window < 1 or cadence < 1:
        raise ValueError("window and cadence must be >= 1")

    n = len(returns)
    out: list[float] = []
    history: list[tuple] = []

    warmup = min(window, n)
    out.extend(returns[baseline].iloc[:warmup].tolist())

    pos = window
    while pos < n:
        pick = select_scheme(returns, pos, window) or baseline
        effective_from = returns.index[pos]
        history.append((effective_from, pick))
        end = min(pos + cadence, n)
        out.extend(returns[pick].iloc[pos:end].tolist())
        pos = end

    return pd.Series(out, index=returns.index[:len(out)], dtype=float), history


def count_switches(history: list[tuple]) -> int:
    """Number of times the selected scheme changed between re-selections."""
    names = [name for _, name in history]
    return sum(1 for a, b in zip(names, names[1:]) if a != b)
