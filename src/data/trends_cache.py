"""Durable per-day cache of Google Trends batches in Supabase Storage.

One JSON object per UTC day, keyed by geo then batch-key, holding each
successful batch's anchor-normalized {ticker: series}. Fail-open: any Storage
error degrades to an empty cache / no-op so the scan proceeds with a live fetch.
"""
from __future__ import annotations

import json
import logging

from src import storage_backup

logger = logging.getLogger(__name__)

DEFAULT_CACHE_BUCKET = "trends-cache"


def cache_object_name(date_str: str) -> str:
    """'2026-07-07' -> 'trends_cache_2026-07-07.json'."""
    return f"trends_cache_{date_str}.json"


def batch_key(tickers: list[str]) -> str:
    """Deterministic, order-independent key for a batch: sorted tickers joined by '|'."""
    return "|".join(sorted(tickers))
