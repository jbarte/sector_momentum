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
import yaml

from src.data.trends_cache import batch_key

logger = logging.getLogger(__name__)

DEFAULT_ANCHOR = "YouTube"
DEFAULT_REGION_GEOS = {"US": ["US"], "EU": ["DE", "FR", "GB"]}


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


def load_geo_config(path: str = "config/trends_geo.yaml") -> tuple[str, dict[str, list[str]]]:
    """Load (anchor, region_geos) from the geo config.

    Missing file or missing keys fall back to DEFAULT_ANCHOR / DEFAULT_REGION_GEOS.
    """
    try:
        with open(path, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return DEFAULT_ANCHOR, DEFAULT_REGION_GEOS
    anchor = cfg.get("anchor") or DEFAULT_ANCHOR
    region_geos = cfg.get("region_geos") or DEFAULT_REGION_GEOS
    return anchor, region_geos


def load_entities(path: str = "config/trends_entities.yaml") -> dict[str, str]:
    """Load {ticker: entity mid} from the entities config.

    The on-disk shape is ``{ticker: {mid: ..., title: ...}}``; this flattens to
    ``{ticker: mid}`` and skips any entry lacking a ``mid``. A missing or empty
    file yields ``{}`` (every ticker then falls back to a raw-string query).
    """
    try:
        with open(path, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
    except FileNotFoundError:
        return {}
    # Intentionally no broader except here: a malformed YAML should fail
    # loud (propagate yaml.YAMLError) rather than silently returning {},
    # so a broken hand-edit is caught instead of silently disabling all
    # entities.
    result = {
        ticker: entry["mid"]
        for ticker, entry in cfg.items()
        if isinstance(entry, dict) and entry.get("mid")
    }

    # Two tickers resolving to the same mid collapse to a single Trends
    # payload column, silently dropping one ticker's signal. Warn (don't
    # raise) so a config typo degrades gracefully instead of aborting the
    # scan.
    tickers_by_mid: dict[str, list[str]] = {}
    for ticker, mid in result.items():
        tickers_by_mid.setdefault(mid, []).append(ticker)
    for mid, tickers in tickers_by_mid.items():
        if len(tickers) > 1:
            logger.warning(
                "Duplicate entity mid %s shared by tickers %s — Trends will "
                "collapse them into one column, dropping all but one signal",
                mid, tickers,
            )

    return result


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


def _acceleration(series: list[float]) -> float:
    """Second derivative proxy: slope of the recent half minus the earlier half.

    Positive = attention is picking up speed; negative = decelerating. Needs at
    least 6 points (3 per half) for both halves to yield a real slope.
    """
    vals = [float(v) for v in series]
    if len(vals) < 6:
        return 0.0
    mid = len(vals) // 2
    return _slope(vals[mid:]) - _slope(vals[:mid])


def _range_position(series: list[float]) -> float:
    """Latest value's percentile within the window's min–max, in [0, 1].

    0 = at the window low, 1 = at the window high. Flat series → 0.5 (neutral).
    """
    vals = [float(v) for v in series]
    if not vals:
        return 0.5
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return 0.5
    return (vals[-1] - lo) / (hi - lo)


def _spike_z(series: list[float]) -> float:
    """Z-score of the latest point vs the trailing mean/std (interest breakout).

    Uses all-but-last as the trailing baseline. Flat/short series → 0.0.
    """
    vals = [float(v) for v in series]
    if len(vals) < 3:
        return 0.0
    trailing = vals[:-1]
    mean = sum(trailing) / len(trailing)
    var = sum((x - mean) ** 2 for x in trailing) / (len(trailing) - 1)
    std = var ** 0.5
    if std == 0.0:
        return 0.0
    return (vals[-1] - mean) / std


def _volatility(series: list[float]) -> float:
    """Stability of interest: std of week-over-week percentage changes.

    Higher = choppier attention. Zero-valued prior weeks are skipped to avoid
    divide-by-zero blowups. Short/flat series → 0.0.
    """
    vals = [float(v) for v in series]
    if len(vals) < 3:
        return 0.0
    changes = [
        (b - a) / a
        for a, b in zip(vals[:-1], vals[1:])
        if a != 0
    ]
    if len(changes) < 2:
        return 0.0
    mean = sum(changes) / len(changes)
    var = sum((c - mean) ** 2 for c in changes) / (len(changes) - 1)
    return var ** 0.5


# Derived-signal names, in display order. Kept as a module constant so scan.py,
# state.py, and the dashboard agree on the set without duplicating the list.
DERIVED_SIGNAL_NAMES = (
    "momentum",
    "acceleration",
    "range_position",
    "spike",
    "volatility",
)


def derived_signals(series) -> dict[str, float]:
    """Compute all derived Trends signals for one sector's interest series.

    Returns a dict keyed by DERIVED_SIGNAL_NAMES. These are raw per-sector
    values (not cross-sectionally z-scored) — the page displays them as-is and
    only ``momentum`` feeds ``score_symbol_sentiment`` for the composite toggle.
    """
    vals = list(series)
    return {
        "momentum": _slope(vals),
        "acceleration": _acceleration(vals),
        "range_position": _range_position(vals),
        "spike": _spike_z(vals),
        "volatility": _volatility(vals),
    }


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


def _symbols_by_region(symbol_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """Group unique symbols by the region prefix of each 'REGION|Sector' key.

    Preserves first-seen order and de-dupes within each region.
    """
    out: dict[str, list[str]] = {}
    for key, symbols in symbol_map.items():
        region = key.split("|", 1)[0]
        bucket = out.setdefault(region, [])
        for s in symbols:
            if s not in bucket:
                bucket.append(s)
    return out


def _average_geo_series(
    per_geo: list[dict[str, list[float]]],
    window: int,
) -> dict[str, list[float]]:
    """Average each ticker's series across the geos where it is live.

    A series is live if it has any non-zero value. Tickers live in no geo
    yield an all-zero series of length `window`.
    """
    tickers: list[str] = []
    for m in per_geo:
        for t in m:
            if t not in tickers:
                tickers.append(t)
    out: dict[str, list[float]] = {}
    for t in tickers:
        live = [m[t] for m in per_geo if t in m and any(v != 0 for v in m[t])]
        if not live:
            out[t] = [0.0] * window
        else:
            arr = np.array(live, dtype=float)
            out[t] = [float(v) for v in arr.mean(axis=0)]
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
    Any term missing from ``term_to_ticker`` passes through unchanged. In
    the normal fetch_symbol_trends flow, every non-anchor term is a key in
    term_to_ticker by construction (it is built from the same batch), so
    this fallback is defensive-only and never actually triggers.
    """
    out: dict[str, list[float]] = {}
    for term, series in raw_by_term.items():
        key = anchor if term == anchor else term_to_ticker.get(term, term)
        out[key] = series
    return out


def _build_chained_batches(terms: list[str], batch_size: int = 5) -> list[list[str]]:
    """Split terms into overlapping batches for anchor-chaining.

    The last term of batch N becomes the first term of batch N+1 (the bridge).
    With batch_size=5 and 11 terms: [[S0..S4], [S4..S8], [S8..S10]].
    """
    if not terms:
        return []
    if len(terms) <= batch_size:
        return [list(terms)]
    batches: list[list[str]] = []
    stride = batch_size - 1
    i = 0
    while i < len(terms):
        batch = terms[i : i + batch_size]
        batches.append(batch)
        i += stride
        if i >= len(terms):
            break
    return batches


import random
import time


def _new_client(timeout=(10, 25)):
    from pytrends.request import TrendReq
    return TrendReq(hl="en-US", tz=0, timeout=timeout)


def _fetch_geo(
    client,
    symbols: list[str],
    anchor: str,
    geo: str,
    timeframe: str,
    window: int,
    batch_size: int,
    sleep_s: float,
    max_retries: int,
    entities: dict[str, str],
    cache: dict | None = None,
) -> dict[str, list[float]]:
    """Fetch + anchor-normalize one geo's symbols. Returns {ticker: series}."""
    batches = [symbols[i:i + batch_size] for i in range(0, len(symbols), batch_size)]
    norm_by_symbol: dict[str, list[float]] = {}
    for bi, batch in enumerate(batches):
        if cache is not None:
            key = batch_key(batch)
            cached = cache.get(geo, {}).get(key)
            if isinstance(cached, dict):
                norm_by_symbol.update(cached)
                continue                      # skip API call and inter-batch sleep
        query_terms, term_to_ticker = _resolve_query_terms(batch, entities)
        terms = [anchor] + query_terms
        df = None
        for attempt in range(max_retries):
            try:
                client.build_payload(terms, timeframe=timeframe, geo=geo)
                df = client.interest_over_time()
                break
            except Exception as exc:
                if attempt < max_retries - 1:
                    time.sleep(sleep_s * (2 ** attempt) + random.uniform(0, 3))
                else:
                    logger.warning("Trends batch %d (geo=%s) failed (%s) — %d symbols neutral",
                                   bi + 1, geo or "world", exc, len(batch))
        if df is not None and not df.empty:
            raw_by_term = {t: [float(v) for v in df[t].tolist()[-window:]]
                           for t in terms if t in df.columns}
            raw = _rekey_by_ticker(raw_by_term, anchor, term_to_ticker)
            normalized = _normalize_by_anchor(raw, anchor)
            norm_by_symbol.update(normalized)
            if cache is not None:
                cache.setdefault(geo, {})[key] = normalized
        if bi < len(batches) - 1 and sleep_s:
            time.sleep(sleep_s)
    return norm_by_symbol


def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = DEFAULT_ANCHOR,
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, pd.Series]:
    if client is None:
        try:
            client = _new_client()
        except Exception as exc:
            logger.warning("Trends client init failed (%s) — sentiment neutral", exc)
            return _aggregate({}, symbol_map, window=window)

    entities = entities or {}
    region_geos = region_geos if region_geos is not None else DEFAULT_REGION_GEOS
    by_region = _symbols_by_region(symbol_map)

    norm_by_symbol: dict[str, list[float]] = {}
    for region, symbols in by_region.items():
        geos = region_geos.get(region, [""])
        per_geo = [
            _fetch_geo(client, symbols, anchor, geo, timeframe, window,
                       batch_size, sleep_s, max_retries, entities, cache=cache)
            for geo in geos
        ]
        if len(per_geo) == 1:
            norm_by_symbol.update(per_geo[0])
        else:
            norm_by_symbol.update(_average_geo_series(per_geo, window))

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
