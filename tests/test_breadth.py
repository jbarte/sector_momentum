import math

import numpy as np
import pandas as pd

from src.signals.breadth import compute_constituent_breadth


def _frame(values: list[float]) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=len(values), freq="D")
    return pd.DataFrame({"Close": values}, index=idx)


def _above() -> pd.DataFrame:
    # 60 days flat at 100 then jump to 200 → last close well above 50-DMA
    return _frame([100.0] * 60 + [200.0])


def _below() -> pd.DataFrame:
    # 60 days flat at 100 then drop to 50 → last close below 50-DMA
    return _frame([100.0] * 60 + [50.0])


def test_breadth_fraction_is_count_above_over_valid():
    constituents = {"Technology": ["A", "B", "C", "D"]}
    prices = {"A": _above(), "B": _above(), "C": _above(), "D": _below()}  # 3/4
    out = compute_constituent_breadth(prices, constituents)
    assert math.isclose(out["US|Technology"], 0.75, abs_tol=1e-9)


def test_under_coverage_returns_nan():
    # Only 1 of 4 constituents has data → 25% < 60% → NaN
    constituents = {"Energy": ["A", "B", "C", "D"]}
    prices = {"A": _above()}
    out = compute_constituent_breadth(prices, constituents)
    assert math.isnan(out["US|Energy"])


def test_short_history_excluded_from_denominator():
    # B has < 50 closes → not "valid"; A counts. 1 valid of 2 listed = 50% < 60% → NaN
    constituents = {"Materials": ["A", "B"]}
    prices = {"A": _above(), "B": _frame([100.0] * 10)}
    out = compute_constituent_breadth(prices, constituents)
    assert math.isnan(out["US|Materials"])


def test_empty_sector_is_nan():
    out = compute_constituent_breadth({}, {"Utilities": []})
    assert math.isnan(out["US|Utilities"])
