"""
Relative strength signals: RS ratio, RS slope, and RRG coordinates.

The Relative Rotation Graph (RRG) uses two axes:
  - RS-Ratio: the sector's relative strength vs benchmark, smoothed and
    normalized around 100. Values > 100 mean outperforming.
  - RS-Momentum: the rate of change of RS-Ratio. Values > 100 mean
    RS-Ratio is rising (improving relative strength).

Quadrant interpretation:
  Leading    (RS-Ratio>100, RS-Momentum>100): outperforming and accelerating
  Weakening  (RS-Ratio>100, RS-Momentum<100): outperforming but decelerating
  Lagging    (RS-Ratio<100, RS-Momentum<100): underperforming and decelerating
  Improving  (RS-Ratio<100, RS-Momentum>100): underperforming but accelerating <- early signal
"""

import pandas as pd
import numpy as np


def compute_rs(sector_close: pd.Series, benchmark_close: pd.Series) -> pd.Series:
    """
    Returns the raw relative strength ratio (sector / benchmark),
    aligned on the intersection of their date indices.
    """
    common_index = sector_close.index.intersection(benchmark_close.index)
    sector_aligned = sector_close.loc[common_index]
    bench_aligned = benchmark_close.loc[common_index]
    return sector_aligned / bench_aligned


def compute_rs_slope(rs: pd.Series, window: int = 10) -> pd.Series:
    """
    Rolling linear-regression slope of the RS series over `window` periods.
    Use numpy polyfit on rolling windows. Returns a Series (same index as rs,
    NaN for the first window-1 periods).
    """
    slopes = np.full(len(rs), np.nan)
    rs_values = rs.values
    x = np.arange(window)

    for i in range(window - 1, len(rs_values)):
        y = rs_values[i - window + 1 : i + 1]
        if np.any(np.isnan(y)):
            continue
        slope, _ = np.polyfit(x, y, 1)
        slopes[i] = slope

    return pd.Series(slopes, index=rs.index)


def compute_rrg(
    sector_close: pd.Series,
    benchmark_close: pd.Series,
    slow: int = 10,
    fast: int = 5,
) -> pd.DataFrame:
    """
    Compute RRG coordinates.

    Algorithm:
    1. raw_rs = sector_close / benchmark_close
    2. smoothed_rs = raw_rs.ewm(span=slow).mean()
    3. rs_ratio = (smoothed_rs / smoothed_rs.shift(slow)) * 100
       (normalize around 100 by expressing current smoothed RS as % of its
       own slow-period-ago value; when equal, ratio = 100)
    4. rs_momentum = (rs_ratio / rs_ratio.shift(fast)) * 100
       (same normalization trick: when rs_ratio is flat, momentum = 100)

    Returns a DataFrame with columns ['rs_ratio', 'rs_momentum'],
    same index as input series.
    """
    raw_rs = compute_rs(sector_close, benchmark_close)
    smoothed_rs = raw_rs.ewm(span=slow).mean()
    rs_ratio = (smoothed_rs / smoothed_rs.shift(slow)) * 100
    rs_momentum = (rs_ratio / rs_ratio.shift(fast)) * 100

    return pd.DataFrame(
        {"rs_ratio": rs_ratio, "rs_momentum": rs_momentum},
        index=raw_rs.index,
    )


def latest_rrg(sector_close: pd.Series, benchmark_close: pd.Series, fast: int = 5) -> dict:
    """
    Returns {'rs_ratio': float, 'rs_momentum': float} for the most recent date.
    Returns NaN values if computation fails.
    """
    try:
        rrg = compute_rrg(sector_close, benchmark_close, fast=fast)
        last_row = rrg.dropna(how="all").iloc[-1]
        return {
            "rs_ratio": float(last_row["rs_ratio"]),
            "rs_momentum": float(last_row["rs_momentum"]),
        }
    except Exception:
        return {"rs_ratio": float("nan"), "rs_momentum": float("nan")}
