"""
Macro data loader (stub for Phase 1).

Full FRED integration is deferred to Phase 2. This module provides the
interface so scan.py can call it without conditionals.
"""

import logging

logger = logging.getLogger(__name__)

FRED_SERIES = {
    "DGS10": "US 10Y Treasury yield",
    "DGS2": "US 2Y Treasury yield",
    "T10Y2Y": "US yield curve spread (10Y-2Y)",
    "DTWEXBGS": "USD broad trade-weighted index",
}


def fetch_fred(
    series_ids: list[str] | None = None,
    api_key: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """
    Returns an empty dict in Phase 1. Phase 2 will call FRED API via fredapi.
    """
    logger.debug("fetch_fred called (stub — Phase 1, returns empty dict)")
    return {}
