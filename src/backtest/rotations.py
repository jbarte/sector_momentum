"""Rotation event-study: did the scanner's rank lead the price move?

Reuses the point-in-time replay engine to recover a sector's rank-over-time
across a curated historical window, alongside the sector ETF's indexed price.
"""
from __future__ import annotations

import logging
import os

import pandas as pd
import yaml

from src.backtest.replay import month_end_dates, score_as_of

logger = logging.getLogger(__name__)


def load_rotations(path: str = "config/rotations.yaml") -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        data = yaml.safe_load(fh)
    return data or []


def event_study(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    rotations: list[dict],
) -> list[dict]:
    out: list[dict] = []
    for rot in rotations:
        region = rot["region"]
        sector = rot["gics_sector"]
        key = f"{region}|{sector}"
        sector_map = universe.get("us_sectors" if region == "US" else "eu_sectors", {})
        ticker = sector_map.get(sector)
        if isinstance(ticker, list):
            ticker = ticker[0] if ticker else None
        if not ticker or ticker not in prices:
            logger.warning("Rotation '%s' skipped — no price for %s (%s)", rot.get("name"), sector, ticker)
            continue

        start, end = pd.Timestamp(rot["start"]), pd.Timestamp(rot["end"])
        price_df = prices[ticker]
        calendar = [d for d in month_end_dates(price_df.index) if start <= d <= end]

        dates: list[str] = []
        ranks: list[float] = []
        comps: list[float] = []
        for d in calendar:
            scored = score_as_of(universe, prices, d, region)
            if scored is None or key not in scored.index:
                continue
            dates.append(d.strftime("%Y-%m-%d"))
            ranks.append(float(scored.loc[key, "rank"]))
            comps.append(float(scored.loc[key, "composite"]))

        if len(dates) < 2:
            logger.warning("Rotation '%s' skipped — < 2 valid month-ends in window", rot.get("name"))
            continue

        closes = [float(price_df["Close"][price_df.index <= pd.Timestamp(d)].iloc[-1]) for d in dates]
        base = closes[0]
        price_indexed = [c / base * 100.0 for c in closes] if base else [0.0] * len(closes)

        out.append({
            "name": rot["name"], "region": region, "sector": sector, "ticker": ticker,
            "dates": dates, "rank": ranks, "composite": comps, "price_indexed": price_indexed,
        })
    return out
