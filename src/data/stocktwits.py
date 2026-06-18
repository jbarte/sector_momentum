"""
StockTwits public sentiment loader.

Fetches bull/bear message counts for US sector ETF tickers.
EU sectors receive NaN (StockTwits has no EU ETF coverage).

Cache: data/cache/stocktwits_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_API_URL = "https://api.stocktwits.com/api/2/streams/symbol/{ticker}.json"
_SLEEP = 0.5


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"stocktwits_{date.today()}.json")


def _count_sentiment(messages: list[dict]) -> dict[str, int]:
    bull = sum(
        1 for m in messages
        if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish"
    )
    bear = sum(
        1 for m in messages
        if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish"
    )
    return {"bull": bull, "bear": bear}


def fetch_stocktwits(
    us_sectors: dict[str, str],
    cache_dir: str = "data/cache",
) -> dict[str, dict[str, int]] | None:
    """
    Fetch StockTwits bull/bear counts for each US sector ETF ticker.

    Args:
        us_sectors: {gics_sector: ticker}, e.g. {"Technology": "XLK"}

    Returns dict[sector, {"bull": int, "bear": int}] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("StockTwits: cache hit %s", cache_file)
        try:
            with open(cache_file) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            pass  # fall through to fresh fetch

    result: dict[str, dict[str, int]] = {}

    try:
        for sector, ticker in us_sectors.items():
            resp = requests.get(_API_URL.format(ticker=ticker), timeout=10)
            resp.raise_for_status()
            messages = resp.json().get("messages", [])
            result[sector] = _count_sentiment(messages)
            logger.debug("StockTwits %s (%s): %s", sector, ticker, result[sector])
            time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_file)
        logger.info("StockTwits: fetched %d sectors → %s", len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("StockTwits fetch failed (%s) — US sentiment neutral this run", exc)
        return None
