import numpy as np
import pandas as pd
from src.data.trends_symbols import _slope, _normalize_by_anchor, _aggregate


def test_slope_sign():
    assert _slope([1, 2, 3, 4]) > 0
    assert _slope([4, 3, 2, 1]) < 0
    assert _slope([0, 0, 0, 0]) == 0.0
    assert _slope([5]) == 0.0


def test_normalize_by_anchor_divides_and_drops_anchor():
    raw = {"SPY": [10.0, 10.0, 10.0], "XLK": [5.0, 10.0, 20.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert "SPY" not in out
    assert out["XLK"] == [50.0, 100.0, 200.0]   # (x/anchor)*100


def test_normalize_anchor_all_zero_passthrough():
    raw = {"SPY": [0.0, 0.0], "XLK": [3.0, 4.0]}
    out = _normalize_by_anchor(raw, "SPY")
    assert out["XLK"] == [3.0, 4.0]


def test_aggregate_means_live_symbols_and_zeros_dead():
    norm = {"XLK": [2.0, 4.0], "VGT": [4.0, 8.0], "DEAD": [0.0, 0.0]}
    smap = {"US|Technology": ["XLK", "VGT", "DEAD"], "US|Energy": ["DEAD"]}
    agg = _aggregate(norm, smap, window=2)
    assert list(agg["US|Technology"]) == [3.0, 6.0]   # mean of XLK,VGT; DEAD excluded
    assert list(agg["US|Energy"]) == [0.0, 0.0]       # no live symbols
