"""
Finnhub news fetcher for US sector ETFs.

Fetches recent news headlines for each US sector ETF ticker.
Requires FINNHUB_TOKEN environment variable.

Cache: data/cache/finnhub_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://finnhub.io/api/v1/company-news"
_SLEEP = 0.2  # stay well under 60 req/min free tier limit
_LOOKBACK_DAYS = 7


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"finnhub_{date.today()}.json")


def fetch_finnhub_news(
    us_sectors: dict[str, str],
    api_key: str | None = None,
    cache_dir: str = "data/cache",
) -> dict[str, list[str]] | None:
    """
    Fetch recent news headlines for each US sector ETF from Finnhub.

    Args:
        us_sectors: {gics_sector: ticker}, e.g. {"Technology": "XLK"}
        api_key: Finnhub API token. Falls back to FINNHUB_TOKEN env var.

    Returns dict[sector, list[headline_strings]] or None on failure.
    """
    token = api_key or os.environ.get("FINNHUB_TOKEN")
    if not token:
        logger.warning("Finnhub: no API key — set FINNHUB_TOKEN env var")
        return None

    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("Finnhub: cache hit %s", cache_file)
        try:
            with open(cache_file) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass

    date_to = date.today()
    date_from = date_to - timedelta(days=_LOOKBACK_DAYS)
    result: dict[str, list[str]] = {}

    try:
        for sector, ticker in us_sectors.items():
            resp = requests.get(
                _API_URL,
                params={
                    "symbol": ticker,
                    "from": str(date_from),
                    "to": str(date_to),
                    "token": token,
                },
                timeout=10,
            )
            resp.raise_for_status()
            articles = resp.json()
            headlines = [a["headline"] for a in articles if a.get("headline")]
            result[sector] = headlines
            logger.debug("Finnhub %s (%s): %d headlines", sector, ticker, len(headlines))
            time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_file)
        logger.info("Finnhub: fetched %d sectors → %s", len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("Finnhub fetch failed (%s) — US sentiment neutral this run", exc)
        return None
