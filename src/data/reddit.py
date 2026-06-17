"""
Reddit public JSON sentiment loader.

Searches a multireddit of 8 finance subreddits for each sector's keywords.
No OAuth required — uses the public search endpoint with a User-Agent header.

Cache: data/cache/reddit_<YYYY-MM-DD>.json (one fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

_MULTIREDDIT = (
    "stocks+investing+wallstreetbets+aktier+Finanzen+"
    "vosfinances+eupersonalfinance+EuropeFIRE"
)
_SEARCH_URL = f"https://www.reddit.com/r/{_MULTIREDDIT}/search.json"
_DEFAULT_UA = "sector-momentum-scanner/1.0 (analytical tooling, non-commercial)"
_SLEEP = 0.6  # keeps requests under 10/min


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"reddit_{date.today()}.json")


def _count_by_window(posts: list[dict]) -> dict[str, int]:
    now = datetime.now(timezone.utc).timestamp()
    cutoff_7d = now - 7 * 86400
    cutoff_30d = now - 30 * 86400
    c7 = sum(1 for p in posts if p["data"].get("created_utc", 0) >= cutoff_7d)
    c30 = sum(1 for p in posts if p["data"].get("created_utc", 0) >= cutoff_30d)
    return {"7d": c7, "30d": c30}


def fetch_reddit(
    keywords: dict[str, list[str]],
    cache_dir: str = "data/cache",
) -> dict[str, dict[str, int]] | None:
    """
    For each sector, count Reddit mentions in the last 7 and 30 days.

    Returns dict[sector, {"7d": int, "30d": int}] or None on failure.
    """
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(cache_dir)

    if os.path.exists(cache_file):
        logger.info("Reddit: cache hit %s", cache_file)
        with open(cache_file) as fh:
            return json.load(fh)

    ua = os.environ.get("REDDIT_USER_AGENT", _DEFAULT_UA)
    headers = {"User-Agent": ua}
    result: dict[str, dict[str, int]] = {}

    try:
        for sector, terms in keywords.items():
            query = "+OR+".join(terms)
            params = {"q": query, "sort": "new", "limit": 100,
                      "restrict_sr": "on", "t": "month"}
            resp = requests.get(_SEARCH_URL, params=params, headers=headers, timeout=10)
            resp.raise_for_status()
            posts = resp.json().get("data", {}).get("children", [])
            result[sector] = _count_by_window(posts)
            logger.debug("Reddit %s: %s", sector, result[sector])
            time.sleep(_SLEEP)

        tmp = cache_file + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(result, fh)
        os.replace(tmp, cache_file)
        logger.info("Reddit: fetched %d sectors → %s", len(result), cache_file)
        return result

    except Exception as exc:
        logger.warning("Reddit fetch failed (%s) — sentiment neutral this run", exc)
        return None
