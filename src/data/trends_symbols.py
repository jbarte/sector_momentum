"""Symbol-based Google Trends sentiment.

Builds {region|sector: [instrument symbols]} from the existing universe + sector
ETF configs, fetches anchor-normalized search interest, aggregates to one series
per region|sector, and scores it as a cross-sectional z. Region-aware; toggle-only.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def build_symbol_map(
    universe: dict,
    sector_etfs: dict,
    blocklist: set[str] | None = None,
) -> dict[str, list[str]]:
    block = {b.upper() for b in (blocklist or set())}
    out: dict[str, list[str]] = {}
    for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
        for sector, primary in universe.get(key, {}).items():
            symbols: list[str] = []
            candidates = [primary] + [
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
