"""
Google Trends search momentum loader.

Fetches 13-week interest-over-time for each sector's primary keyword.
Batches requests and retries with backoff to survive pytrends 429s.

Resilience model:
  - Each batch is retried up to _MAX_RETRIES times with exponential backoff.
  - A batch that exhausts its retries fills its sectors with neutral 0.0 but
    does NOT abort the run — other batches still get fetched (partial success).
  - The result is cached only when ALL batches succeeded, so a partial run can
    re-attempt the missing sectors on a later invocation rather than being
    pinned to a half-empty cache for the rest of the day.
  - None is returned only when every batch failed.

Cache: data/cache/trends_<YYYY-MM-DD>.json (one full fetch per calendar day).
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from datetime import date
from pathlib import Path

import pandas as pd
from pytrends.request import TrendReq

logger = logging.getLogger(__name__)

_TIMEFRAME = "today 3-m"  # ~13 weeks
_BATCH = 5                # max keywords per pytrends request
_SLEEP = 20.0             # base inter-batch / backoff seconds; pytrends rate-limits hard
_MAX_RETRIES = 3          # attempts per batch before giving up on it
_WINDOW = 13              # weeks of interest to keep per sector


def _cache_path(cache_dir: str) -> str:
    return os.path.join(cache_dir, f"trends_{date.today()}.json")


def _extract(df, batch: list[str], kw_list: list[str]) -> dict[str, list[float]]:
    """Pull the last _WINDOW interest values per sector from a pytrends frame."""
    out: dict[str, list[float]] = {}
    for s, kw in zip(batch, kw_list):
        if df is not None and not df.empty and kw in df.columns:
            out[s] = df[kw].tolist()[-_WINDOW:]
        else:
            out[s] = [0.0] * _WINDOW
    return out


def fetch_trends(
    keywords: dict[str, list[str]],
    cache_dir: str = "data/cache",
) -> dict[str, pd.Series] | None:
    """
    Fetch Google Trends interest (13-week) for each sector's primary keyword.
    Primary keyword = first item in each sector's keyword list.

    Returns dict[sector, Series(13 floats)] (partial allowed — failed sectors are
    neutral 0.0), or None if the client could not init or every batch failed.
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
    except Exception as exc:
        logger.warning("Google Trends client init failed (%s) — sentiment neutral this run", exc)
        return None

    sectors = list(keywords.keys())
    primary = {s: keywords[s][0] for s in sectors}
    batches = [sectors[i : i + _BATCH] for i in range(0, len(sectors), _BATCH)]

    result: dict[str, list[float]] = {}
    any_success = False
    any_failure = False

    for bi, batch in enumerate(batches):
        kw_list = [primary[s] for s in batch]
        df = None
        for attempt in range(_MAX_RETRIES):
            try:
                pytrends.build_payload(kw_list, timeframe=_TIMEFRAME, geo="")
                df = pytrends.interest_over_time()
                break
            except Exception as exc:
                if attempt < _MAX_RETRIES - 1:
                    wait = _SLEEP * (2 ** attempt) + random.uniform(0, 3)
                    logger.warning(
                        "Trends batch %d/%d failed (attempt %d/%d): %s — retrying in %.0fs",
                        bi + 1, len(batches), attempt + 1, _MAX_RETRIES, exc, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Trends batch %d/%d exhausted %d retries (%s) — %d sectors neutral",
                        bi + 1, len(batches), _MAX_RETRIES, exc, len(batch),
                    )

        if df is None:
            any_failure = True
            for s in batch:
                result[s] = [0.0] * _WINDOW
        else:
            any_success = True
            result.update(_extract(df, batch, kw_list))

        if bi < len(batches) - 1:
            time.sleep(_SLEEP)

    if not any_success:
        logger.warning("Google Trends: all batches failed — sentiment neutral this run (not cached)")
        return None

    # Cache only a fully successful fetch, so a partial run can retry the gaps later.
    if not any_failure:
        tmp = cache_file + ".tmp"
        Path(tmp).write_text(json.dumps(result))
        os.replace(tmp, cache_file)
        logger.info("Trends: fetched %d sectors (full) → %s", len(result), cache_file)
    else:
        logger.info("Trends: partial fetch (%d sectors, some neutral) — not cached", len(result))

    return {s: pd.Series(v, dtype=float) for s, v in result.items()}
