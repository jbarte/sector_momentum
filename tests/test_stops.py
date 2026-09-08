"""Trailing stop evaluation — the rule, isolated from DB, network and delivery."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from src import stops


def _prices(closes: list[float], start="2026-01-01") -> pd.DataFrame:
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame({"Close": closes}, index=idx)


def test_breaches_when_price_falls_past_threshold_from_peak():
    """Up 30% then back to +5% is a 19% drawdown from peak -- breached at 12%,
    even though the position is still ABOVE where it started. That is what
    'trailing' means, and it is the behaviour the sweep measured."""
    res = stops.evaluate_stop(_prices([100.0, 130.0, 105.0]), "2026-01-01", 0.12)
    assert res["breached"] is True
    assert res["peak"] == 130.0
    assert res["peak_on"] == dt.date(2026, 1, 2)
    assert res["drawdown"] == pytest.approx(105.0 / 130.0 - 1.0)
    assert res["stopped_on"] == dt.date(2026, 1, 3)


def test_does_not_breach_inside_the_threshold():
    res = stops.evaluate_stop(_prices([100.0, 130.0, 120.0]), "2026-01-01", 0.12)
    assert res["breached"] is False


def test_peak_is_measured_from_the_entry_date_not_the_whole_series():
    """A high BEFORE the star date is not this holding's peak. Without this the
    drawdown is measured against a price the user never held through."""
    df = _prices([200.0, 100.0, 110.0, 99.0])       # 200 predates entry
    res = stops.evaluate_stop(df, "2026-01-02", 0.12)
    assert res["peak"] == 110.0
    assert res["breached"] is False                  # 99/110-1 = -10%, inside 12%


def test_entry_after_all_prices_returns_none():
    assert stops.evaluate_stop(_prices([100.0, 101.0]), "2027-01-01", 0.12) is None


def test_empty_or_missing_frame_returns_none():
    assert stops.evaluate_stop(None, "2026-01-01", 0.12) is None
    assert stops.evaluate_stop(pd.DataFrame({"Close": []}), "2026-01-01", 0.12) is None


def test_starred_today_is_flat_and_unbreached():
    res = stops.evaluate_stop(_prices([100.0]), "2026-01-01", 0.12)
    assert res["drawdown"] == 0.0
    assert res["breached"] is False


def test_nan_closes_are_ignored_not_treated_as_zero():
    """A NaN close must not become the peak or the latest price -- either would
    fabricate a breach out of a data gap."""
    df = _prices([100.0, float("nan"), 95.0])
    res = stops.evaluate_stop(df, "2026-01-01", 0.12)
    assert res["peak"] == 100.0
    assert res["latest"] == 95.0
    assert res["breached"] is False


def test_ticker_for_resolves_a_theme_name():
    cfg = {"themes": {"Uranium & Nuclear": {"ticker": "URA"}}}
    assert stops.ticker_for("theme", "Uranium & Nuclear", cfg) == "URA"
    assert stops.ticker_for("theme", "Nope", cfg) is None
    assert stops.ticker_for("sector", "Energy", cfg) is None
