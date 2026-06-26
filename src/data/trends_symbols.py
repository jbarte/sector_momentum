"""Symbol-based Google Trends sentiment.

Builds {region|sector: [instrument symbols]} from the existing universe + sector
ETF configs, fetches anchor-normalized search interest, aggregates to one series
per region|sector, and scores it as a cross-sectional z. Region-aware; toggle-only.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_symbol_map(
    universe: dict,
    sector_etfs: dict,
    blocklist: set[str] | None = None,
) -> dict[str, list[str]]:
    block = {b.upper() for b in (blocklist or set())}
    out: dict[str, list[str]] = {}
    for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for sector, primary in universe.get(key, {}).items():
            symbols: list[str] = []
            candidates = [primary] + [
                e.get("ticker")
                for e in sector_etfs.get(region, {}).get(sector, [])
                if e.get("ticker")
            ]
            for sym in candidates:
                if not sym or sym.upper() in block or sym in symbols:
                    continue
                symbols.append(sym)
            if symbols:
                out[f"{region}|{sector}"] = symbols
    return out


def _slope(series: list[float]) -> float:
    vals = [float(v) for v in series]
    if len(vals) < 3 or len(set(vals)) <= 1:
        return 0.0
    x = np.arange(len(vals))
    slope, _ = np.polyfit(x, np.array(vals, dtype=float), 1)
    return float(slope)


def _normalize_by_anchor(raw: dict[str, list[float]], anchor: str) -> dict[str, list[float]]:
    anchor_series = raw.get(anchor)
    out: dict[str, list[float]] = {}
    anchor_dead = not anchor_series or all(a == 0 for a in anchor_series)
    for term, series in raw.items():
        if term == anchor:
            continue
        if anchor_dead:
            out[term] = [float(v) for v in series]
            continue
        norm = []
        for v, a in zip(series, anchor_series):
            norm.append(float(v) / a * 100.0 if a else 0.0)
        out[term] = norm
    return out


def _aggregate(
    norm_by_symbol: dict[str, list[float]],
    symbol_map: dict[str, list[str]],
    window: int = 13,
) -> dict[str, pd.Series]:
    out: dict[str, pd.Series] = {}
    for sector_key, symbols in symbol_map.items():
        live = [
            norm_by_symbol[s]
            for s in symbols
            if s in norm_by_symbol and any(v != 0 for v in norm_by_symbol[s])
        ]
        if not live:
            out[sector_key] = pd.Series([0.0] * window, dtype=float)
        else:
            arr = np.array(live, dtype=float)
            out[sector_key] = pd.Series(arr.mean(axis=0), dtype=float)
    return out


def score_symbol_sentiment(trends_by_key: dict[str, pd.Series]) -> pd.Series:
    """Score symbol sentiment: slope of each sector key's series, cross-sectionally z-scored.

    Args:
        trends_by_key: dict mapping region|sector to a pd.Series of search interest.

    Returns:
        pd.Series indexed by region|sector with cross-sectional z-scores of slopes.
    """
    from src.signals.sentiment import _cross_zscore

    slopes = {key: _slope(list(series)) for key, series in trends_by_key.items()}
    z = _cross_zscore(slopes)
    return pd.Series(z, dtype=float)
