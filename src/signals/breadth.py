"""
True constituent breadth: the share of a sector's constituents trading above
their own 50-day moving average. Equal-weight, info-only.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)

_MA_WINDOW = 50


def _is_above_50dma(close: pd.Series) -> bool | None:
    """True/False if computable (>=50 valid closes), else None."""
    clean = close.dropna()
    if len(clean) < _MA_WINDOW:
        return None
    ma50 = float(clean.rolling(_MA_WINDOW).mean().iloc[-1])
    return float(clean.iloc[-1]) > ma50


def compute_constituent_breadth(
    prices: dict[str, "pd.DataFrame"],
    constituents: dict[str, list[str]],
    min_coverage: float = 0.60,
) -> dict[str, float]:
    """Return {"US|<sector>": pct_above_50dma in [0,1]} or NaN when under-covered."""
    out: dict[str, float] = {}
    for sector, tickers in constituents.items():
        n_listed = len(tickers)
        above = 0
        valid = 0
        for t in tickers:
            df = prices.get(t)
            if df is None or "Close" not in df.columns:
                continue
            verdict = _is_above_50dma(df["Close"])
            if verdict is None:
                continue
            valid += 1
            if verdict:
                above += 1
        coverage = (valid / n_listed) if n_listed else 0.0
        if valid == 0 or coverage < min_coverage:
            out[f"US|{sector}"] = float("nan")
        else:
            out[f"US|{sector}"] = above / valid
    return out
