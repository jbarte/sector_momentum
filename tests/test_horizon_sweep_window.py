"""The sweep's evaluation window must leave the signals room to warm up.

`--start` used to be passed straight to `fetch_prices`, so a windowed run began
scoring on its first fetched bar with no history behind it. `above_200dma` needs
200 bars and is NaN before that, so the opening months ran on a degraded signal
set — enough, on a 2008 start, to invert which horizon preset looked best.

Separating the fetch from the evaluation window fixed that, but only if the two
constants stay far enough apart. These tests pin that relationship, including
for the DEFAULT invocation — the one that actually picks the presets.
"""
import pandas as pd
import pytest

from scripts.horizon_sweep import (
    DEFAULT_START,
    FETCH_START,
    WARMUP_DAYS,
    _validate_start,
)


def test_default_start_leaves_a_warmup_window():
    """The bare `python3 scripts/horizon_sweep.py` must not evaluate cold.

    Regression: DEFAULT_START was once FETCH_START itself, so the default run
    — the preset-picking one — still began on the first fetched bar.
    """
    assert _validate_start(DEFAULT_START) == pd.Timestamp(DEFAULT_START)
    gap = pd.Timestamp(DEFAULT_START) - pd.Timestamp(FETCH_START)
    assert gap >= pd.Timedelta(days=WARMUP_DAYS)


def test_warmup_window_covers_the_200_bar_signal():
    """WARMUP_DAYS is calendar days; the requirement is 200 *trading* days."""
    trading_days = WARMUP_DAYS * 252 / 365.25
    assert trading_days >= 200


def test_start_inside_the_warmup_window_is_rejected():
    with pytest.raises(ValueError, match="warm"):
        _validate_start("2003-06-01")


def test_start_before_the_fetch_is_rejected():
    """Silently a no-op before: the filter matched nothing and the report header
    still claimed the requested start, mislabeling a window it never used."""
    with pytest.raises(ValueError, match="warm"):
        _validate_start("2000-01-01")


def test_start_well_after_the_fetch_is_accepted():
    assert _validate_start("2015-01-01") == pd.Timestamp("2015-01-01")
