"""
Google Trends search momentum loader.

Fetches 13-week interest-over-time for each sector's primary keyword.
Batches requests to avoid 429s from pytrends.

Cache: data/cache/trends_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from pathlib import Path

import pandas as pd
from pytrends.request import TrendReq

logger = logging.getLogger(__name__)

_TIMEFRAME = "today 3-m"  # ~13 weeks
_BATCH = 5                # max keywords per pytrends request
_SLEEP = 2.5              # pytrends needs longer pauses to avoid 429


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"trends_{date.today()}.json")


def fetch_trends(
    keywords: dict[str, list[str]],
    cache_dir: str = "data/cache",
) -> dict[str, pd.Series] | None:
    """
    Fetch Google Trends interest (13-week) for each sector's primary keyword.
    Primary keyword = first item in each sector's keyword list.

    Returns dict[sector, Series(13 floats)] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("Trends: cache hit %s", cache_file)
        try:
            raw = json.loads(Path(cache_file).read_text())
            return {s: pd.Series(v, dtype=float) for s, v in raw.items()}
        except (json.JSONDecodeError, OSError):
            pass  # fall through to fresh fetch

    try:
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        sectors = list(keywords.keys())
        primary = {s: keywords[s][0] for s in sectors}
        result: dict[str, list[float]] = {}

        for i in range(0, len(sectors), _BATCH):
            batch = sectors[i : i + _BATCH]
            kw_list = [primary[s] for s in batch]
            pytrends.build_payload(kw_list, timeframe=_TIMEFRAME, geo="")
            df = pytrends.interest_over_time()

            for s, kw in zip(batch, kw_list):
                if not df.empty and kw in df.columns:
                    result[s] = df[kw].tolist()[-13:]
                else:
                    result[s] = [0.0] * 13

            if i + _BATCH < len(sectors):
                time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        Path(tmp).write_text(json.dumps(result))
        os.replace(tmp, cache_file)
        logger.info("Trends: fetched %d sectors → %s", len(result), cache_file)
        return {s: pd.Series(v, dtype=float) for s, v in result.items()}

    except Exception as exc:
        logger.warning("Google Trends fetch failed (%s) — sentiment neutral this run", exc)
        return None
