"""
Price data loader.

Fetches daily OHLCV price data for a list of tickers. Tries stooq first
(via pandas_datareader), falls back to yfinance. Both are fragile free
sources — aggressive caching minimises live fetches.

Cache location: data/cache/<ticker>_prices.parquet
Cache validity: refreshed if the cached data doesn't extend to yesterday.
"""

import logging
import os
from datetime import date, timedelta

import pandas as pd
import yaml

logger = logging.getLogger(__name__)

_OHLCV_COLS = ["Close", "Open", "High", "Low", "Volume"]


def _sanitize_ticker(ticker: str) -> str:
    """Replace characters that are unsafe in filenames."""
    return ticker.replace(".", "_").replace("/", "_")


def _cache_path(ticker: str, cache_dir: str) -> str:
    return os.path.join(cache_dir, f"{_sanitize_ticker(ticker)}_prices.parquet")


def _cache_is_fresh(path: str) -> bool:
    """Return True if the cache file exists and its last date >= yesterday."""
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return False
        yesterday = date.today() - timedelta(days=1)
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        return last_cached >= yesterday
    except Exception:
        return False


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the DataFrame has exactly the required OHLCV columns."""
    # yfinance may return MultiIndex columns when downloading a single ticker
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Normalise column names: title-case the first letter so "close" -> "Close"
    rename = {}
    for col in df.columns:
        title = col.strip().title()
        if title in _OHLCV_COLS and col != title:
            rename[col] = title
    if rename:
        df = df.rename(columns=rename)

    # Keep only the columns we care about (in a consistent order)
    present = [c for c in _OHLCV_COLS if c in df.columns]
    return df[present].copy()


def _fetch_stooq(ticker: str, start: str, end: str) -> pd.DataFrame:
    import pandas_datareader as pdr  # type: ignore

    df = pdr.DataReader(ticker, "stooq", start, end)
    # stooq returns newest-first — sort ascending
    df = df.sort_index(ascending=True)
    return df


def _fetch_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf  # type: ignore

    df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    return df


def _fetch_single(ticker: str, start: str, end: str) -> pd.DataFrame | None:
    """Try stooq then yfinance. Returns a normalised DataFrame or None."""
    for source, fetch_fn in [("stooq", _fetch_stooq), ("yfinance", _fetch_yfinance)]:
        try:
            df = fetch_fn(ticker, start, end)
            if df is None or df.empty:
                logger.warning("Empty response from %s for ticker %s", source, ticker)
                continue
            df = _normalize_columns(df)
            if df.empty or "Close" not in df.columns:
                logger.warning("No usable columns from %s for ticker %s", source, ticker)
                continue
            df.index = pd.to_datetime(df.index)
            df = df.sort_index(ascending=True)
            return df
        except Exception as exc:
            logger.warning("Failed to fetch %s via %s: %s", ticker, source, exc)
    return None


def fetch_prices(
    tickers: list[str],
    start: str,
    end: str,
    cache_dir: str = "data/cache",
) -> dict[str, pd.DataFrame]:
    """
    Returns a dict mapping ticker -> DataFrame with columns:
        Close, Open, High, Low, Volume
    All indexed by date (DatetimeIndex, ascending).

    Tickers that fail both stooq and yfinance are logged and omitted
    from the returned dict (soft failure — never raises).
    """
    os.makedirs(cache_dir, exist_ok=True)
    result: dict[str, pd.DataFrame] = {}

    for ticker in tickers:
        path = _cache_path(ticker, cache_dir)

        if _cache_is_fresh(path):
            try:
                df = pd.read_parquet(path)
                df.index = pd.to_datetime(df.index)
                result[ticker] = df
                logger.debug("Loaded %s from cache (%s rows)", ticker, len(df))
                continue
            except Exception as exc:
                logger.warning("Cache read failed for %s: %s — re-fetching", ticker, exc)

        df = _fetch_single(ticker, start, end)
        if df is None:
            logger.warning("Skipping %s — all fetch attempts failed", ticker)
            continue

        try:
            df.to_parquet(path)
        except Exception as exc:
            logger.warning("Could not write cache for %s: %s", ticker, exc)

        result[ticker] = df
        logger.debug("Fetched %s (%s rows)", ticker, len(df))

    return result


def load_universe(config_path: str = "config/universe.yaml") -> dict:
    """Load universe.yaml and return the parsed dict."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)
