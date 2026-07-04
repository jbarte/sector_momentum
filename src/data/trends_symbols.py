"""Symbol-based Google Trends sentiment.

Builds {region|sector: [instrument symbols]} from the existing universe + sector
ETF configs, fetches anchor-normalized search interest, aggregates to one series
per region|sector, and scores it as a cross-sectional z. Region-aware; toggle-only.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _cross_zscore(values: dict[str, float]) -> dict[str, float]:
    """Z-score a dict of {key: float}. NaN inputs excluded from mean/std."""
    valid = {k: v for k, v in values.items() if not math.isnan(v)}
    if len(valid) < 2:
        return {k: 0.0 if not math.isnan(v) else float("nan") for k, v in values.items()}
    arr = list(valid.values())
    mean = sum(arr) / len(arr)
    std = (sum((x - mean) ** 2 for x in arr) / (len(arr) - 1)) ** 0.5
    if std == 0.0:
        return {k: 0.0 for k in values}
    return {
        k: (v - mean) / std if not math.isnan(v) else float("nan")
        for k, v in values.items()
    }


def build_symbol_map(
    universe: dict,
    sector_etfs: dict,
    blocklist: set[str] | None = None,
) -> dict[str, list[str]]:
    block = {str(b).upper() for b in (blocklist or set())}
    out: dict[str, list[str]] = {}
    for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for sector, primary in universe.get(key, {}).items():
            symbols: list[str] = []
            prims = primary if isinstance(primary, list) else [primary]
            candidates = prims + [
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


def _resolve_query_terms(
    tickers: list[str],
    entities: dict[str, str],
) -> tuple[list[str], dict[str, str]]:
    """Map a batch of tickers to Trends query terms.

    Each ticker becomes its approved entity mid if present in ``entities``,
    otherwise the raw ticker string (fallback). Returns the query-term list
    (aligned with ``tickers``) plus a term->ticker map for re-keying the
    fetched columns back to tickers.
    """
    terms: list[str] = []
    term_to_ticker: dict[str, str] = {}
    for t in tickers:
        term = entities.get(t, t)
        terms.append(term)
        term_to_ticker[term] = t
    return terms, term_to_ticker


def _rekey_by_ticker(
    raw_by_term: dict[str, list[float]],
    anchor: str,
    term_to_ticker: dict[str, str],
) -> dict[str, list[float]]:
    """Re-key a {query-term: series} dict to {ticker: series}.

    The ``anchor`` key is left as-is (it is normalized/dropped downstream).
    Any term missing from ``term_to_ticker`` passes through unchanged.
    """
    out: dict[str, list[float]] = {}
    for term, series in raw_by_term.items():
        key = anchor if term == anchor else term_to_ticker.get(term, term)
        out[key] = series
    return out


import random
import time


def _new_client(timeout=(10, 25)):
    from pytrends.request import TrendReq
    return TrendReq(hl="en-US", tz=0, timeout=timeout)


def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = "SPY",
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
) -> dict[str, pd.Series]:
    if client is None:
        try:
            client = _new_client()
        except Exception as exc:
            logger.warning("Trends client init failed (%s) — sentiment neutral", exc)
            return _aggregate({}, symbol_map, window=window)

    entities = entities or {}
    symbols = sorted({s for syms in symbol_map.values() for s in syms})
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    norm_by_symbol: dict[str, list[float]] = {}

    for bi, batch in enumerate(batches):
        query_terms, term_to_ticker = _resolve_query_terms(batch, entities)
        terms = [anchor] + query_terms
        df = None
        for attempt in range(max_retries):
            try:
                client.build_payload(terms, timeframe=timeframe, geo="")
                df = client.interest_over_time()
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                else:
                    logger.warning("Trends batch %d failed (%s) — %d symbols neutral",
                                   bi + 1, exc, len(batch))
        if df is not None and not df.empty:
            raw_by_term = {t: [float(v) for v in df[t].tolist()[-window:]]
                           for t in terms if t in df.columns}
            raw = _rekey_by_ticker(raw_by_term, anchor, term_to_ticker)
            norm_by_symbol.update(_normalize_by_anchor(raw, anchor))
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)

    return _aggregate(norm_by_symbol, symbol_map, window=window)


def score_symbol_sentiment(trends_by_key: dict[str, pd.Series]) -> pd.Series:
    """Score symbol sentiment: slope of each sector key's series, cross-sectionally z-scored.

    Args:
        trends_by_key: dict mapping region|sector to a pd.Series of search interest.

    Returns:
        pd.Series indexed by region|sector with cross-sectional z-scores of slopes.
    """
    slopes = {key: _slope(list(series)) for key, series in trends_by_key.items()}
    z = _cross_zscore(slopes)
    return pd.Series(z, dtype=float)
