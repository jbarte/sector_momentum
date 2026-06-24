"""
S&P 500 constituent loader (per GICS sector), for true sector breadth.

Source: the Wikipedia "List of S&P 500 companies" table (free, no API key).
Cached to data/cache/sp500_constituents.json with a multi-day TTL — the list
changes only a few times a year. Returns None on any failure (callers degrade
gracefully; breadth is info-only).
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"

# Wikipedia "GICS Sector" → our sector keys (config/universe.yaml us_sectors).
# Only "Information Technology" differs; the other ten are identical.
_GICS_TO_SECTOR = {
    "Information Technology": "Technology",
    "Financials": "Financials",
    "Energy": "Energy",
    "Health Care": "Health Care",
    "Industrials": "Industrials",
    "Consumer Discretionary": "Consumer Discretionary",
    "Consumer Staples": "Consumer Staples",
    "Utilities": "Utilities",
    "Materials": "Materials",
    "Real Estate": "Real Estate",
    "Communication Services": "Communication Services",
}

_CACHE_NAME = "sp500_constituents.json"


def _cache_file(cache_dir: str) -> str:
    return os.path.join(cache_dir, _CACHE_NAME)


def _cache_fresh(path: str, ttl_days: int) -> bool:
    if not os.path.exists(path):
        return False
    age_days = (time.time() - os.path.getmtime(path)) / 86400.0
    return age_days < ttl_days


def fetch_sp500_constituents(
    cache_dir: str = "data/cache",
    ttl_days: int = 7,
) -> dict[str, list[str]] | None:
    """Return {our_sector: [yf_ticker, ...]} for the S&P 500, or None on failure."""
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_file(cache_dir)

    if _cache_fresh(cache_file, ttl_days):
        logger.info("Constituents: cache hit %s", cache_file)
        try:
            return {s: list(v) for s, v in json.loads(Path(cache_file).read_text()).items()}
        except (json.JSONDecodeError, OSError):
            pass  # fall through to fresh scrape

    try:
        tables = pd.read_html(_WIKI_URL)
        df = tables[0]
        if "Symbol" not in df.columns or "GICS Sector" not in df.columns:
            logger.warning("Constituents: unexpected table columns %s", list(df.columns))
            return None

        result: dict[str, list[str]] = {}
        for _, r in df.iterrows():
            gics = str(r["GICS Sector"]).strip()
            sector = _GICS_TO_SECTOR.get(gics)
            if sector is None:
                logger.warning("Constituents: unmapped GICS sector %r — skipping", gics)
                continue
            ticker = str(r["Symbol"]).strip().replace(".", "-")  # BRK.B → BRK-B
            result.setdefault(sector, []).append(ticker)

        if not result:
            logger.warning("Constituents: no rows mapped — returning None")
            return None

        tmp = cache_file + ".tmp"
        Path(tmp).write_text(json.dumps(result))
        os.replace(tmp, cache_file)
        logger.info("Constituents: scraped %d sectors → %s",
                    len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("Constituents: fetch failed (%s) — breadth unavailable", exc)
        return None
