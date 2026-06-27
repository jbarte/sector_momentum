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
