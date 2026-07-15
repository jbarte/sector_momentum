import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from dashboard.macro import build_macro_context

def _make_spy_df(last_close, sma200_target, n=250):
    dates = pd.bdate_range(end="2026-07-14", periods=n)
    closes = np.full(n, sma200_target, dtype=float)
    closes[-1] = last_close
    return pd.DataFrame({"Close": closes}, index=dates)

def _make_vix_df(last_close):
    dates = pd.bdate_range(end="2026-07-14", periods=50)
    closes = np.full(50, last_close, dtype=float)
    return pd.DataFrame({"Close": closes}, index=dates)

def test_build_macro_context_above_200dma():
    ctx = build_macro_context(_make_spy_df(110, 100), _make_vix_df(14))
    assert ctx is not None
    assert ctx["spy_above"] is True
    assert ctx["spy_distance_pct"] > 0
    assert ctx["vix_band"] == "Calm"

def test_build_macro_context_below_200dma():
    ctx = build_macro_context(_make_spy_df(90, 100), _make_vix_df(20))
    assert ctx is not None
    assert ctx["spy_above"] is False
    assert ctx["spy_distance_pct"] < 0
    assert ctx["vix_band"] == "Elevated"

def test_vix_band_stressed():
    ctx = build_macro_context(_make_spy_df(100, 100), _make_vix_df(30))
    assert ctx["vix_band"] == "Stressed"

def test_vix_band_boundary_15():
    ctx = build_macro_context(_make_spy_df(100, 100), _make_vix_df(15))
    assert ctx["vix_band"] == "Elevated"

def test_vix_band_boundary_25():
    ctx = build_macro_context(_make_spy_df(100, 100), _make_vix_df(25))
    assert ctx["vix_band"] == "Elevated"

def test_returns_none_when_spy_is_none():
    ctx = build_macro_context(None, _make_vix_df(14))
    assert ctx is None

def test_returns_none_when_vix_is_none():
    ctx = build_macro_context(_make_spy_df(100, 100), None)
    assert ctx is None

def test_returns_none_when_spy_too_short():
    short = pd.DataFrame({"Close": [100.0] * 10},
                         index=pd.bdate_range(end="2026-07-14", periods=10))
    ctx = build_macro_context(short, _make_vix_df(14))
    assert ctx is None
