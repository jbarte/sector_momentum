"""UCITS tracking-difference monitor: does the EU-buyable ETF track the
US-listed one this project actually scores?

Comparison is deliberately IN EACH LISTING'S OWN CURRENCY, not converted to a
common one — a percentage return is dimensionless, so a US ETF's USD return and
its UCITS equivalent's EUR return are already comparable without an FX rate.
Converting first would only inject FX movement into a number meant to isolate
tracking quality. (Decided 2026-08-30, Jonas.)
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ucits_tracking_monitor import (
    resolve_yf_ticker,
    theme_pairs,
    trailing_return,
    tracking_report,
)


# --- trailing_return ---------------------------------------------------

def test_trailing_return_over_an_exact_window():
    idx = pd.bdate_range("2025-01-01", "2026-01-01")
    s = pd.Series(100.0, index=idx)
    s.loc[idx[-1]] = 110.0  # +10% from a year ago to today
    r = trailing_return(s, as_of=idx[-1], months=12)
    assert r == pytest.approx(0.10, abs=1e-9)


def test_trailing_return_uses_the_last_price_at_or_before_the_target_date():
    """Calendar offsets rarely land on a trading day — asof semantics, not a KeyError."""
    idx = pd.bdate_range("2025-01-01", "2025-06-30")
    s = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    r = trailing_return(s, as_of=idx[-1], months=3)
    assert r is not None and r > 0


def test_trailing_return_is_none_when_history_does_not_reach_back_far_enough():
    idx = pd.bdate_range("2026-06-01", "2026-08-28")  # under 3 months of history
    s = pd.Series(100.0, index=idx)
    assert trailing_return(s, as_of=idx[-1], months=3) is None


def test_trailing_return_is_none_for_an_empty_series():
    assert trailing_return(pd.Series(dtype=float), as_of=pd.Timestamp("2026-01-01"),
                           months=3) is None


def test_trailing_return_ignores_nan_at_the_as_of_date_by_looking_back():
    """A trailing NaN (today's incomplete candle) must not blank the return."""
    idx = pd.bdate_range("2025-01-01", "2026-01-01")
    s = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
    s.iloc[-1] = float("nan")
    r = trailing_return(s, as_of=idx[-1], months=12)
    assert r is not None and not pd.isna(r)


# --- resolve_yf_ticker ---------------------------------------------------

def test_resolve_yf_ticker_appends_the_xetra_suffix():
    """All 17 shipped UCITS entries resolve on Xetra (verified live, 2026-08-30).
    A future entry that does not is a fetch failure the report surfaces, not a
    silent wrong-ticker fetch — see theme_pairs' contract below."""
    assert resolve_yf_ticker("XAIX") == "XAIX.DE"


def test_resolve_yf_ticker_does_not_double_suffix():
    assert resolve_yf_ticker("XAIX.DE") == "XAIX.DE"


# --- theme_pairs ---------------------------------------------------------

def _cfg():
    return {
        "themes": {
            "Artificial Intelligence & Robotics": {"ticker": "BOTZ"},
            "Shipping": {"ticker": "BOAT", "unbuyable": True},
        },
        "ucits": {
            "Artificial Intelligence & Robotics": [
                {"ticker": "XAIX", "match": "close"},
            ],
            # Shipping has no ucits block, matching the real config.
        },
    }


def test_theme_pairs_yields_one_pair_per_ucits_entry():
    pairs = theme_pairs(_cfg())
    assert pairs == [{
        "theme": "Artificial Intelligence & Robotics",
        "us_ticker": "BOTZ",
        "ucits_ticker": "XAIX",
        "yf_ticker": "XAIX.DE",
        "match": "close",
    }]


def test_theme_pairs_skips_a_theme_with_no_ucits_block():
    """Shipping: unbuyable and has no ucits entry — nothing to compare."""
    pairs = theme_pairs(_cfg())
    assert all(p["theme"] != "Shipping" for p in pairs)


def test_theme_pairs_handles_multiple_candidates_for_one_theme():
    cfg = _cfg()
    cfg["ucits"]["Artificial Intelligence & Robotics"].append(
        {"ticker": "OTHR", "match": "partial"})
    pairs = theme_pairs(cfg)
    assert [p["ucits_ticker"] for p in pairs] == ["XAIX", "OTHR"]


# --- tracking_report -------------------------------------------------------

def _grown(total_return, years=1.2):
    """A daily series spanning slightly over `years`, growing linearly to
    `total_return` over the trailing `years=1` window exactly (252 trading
    days back from the end) — the margin at the front is history the trailing
    lookback should never need."""
    idx = pd.bdate_range(end="2026-08-28", periods=int(years * 252))
    n = len(idx)
    values = [100.0] * (n - 252) + list(np.linspace(100, 100 * (1 + total_return), 252))
    return pd.Series(values, index=idx)


def test_tracking_report_computes_the_haircut_in_native_currency():
    """The number that matters: UCITS return minus US return, no FX involved."""
    prices = {"BOTZ": _grown(0.20), "XAIX.DE": _grown(0.15)}
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    rows = tracking_report(pairs, prices, as_of=pd.Timestamp("2026-08-28"))
    assert len(rows) == 1
    row = rows[0]
    assert row["us_1y"] == pytest.approx(0.20, abs=0.01)
    assert row["ucits_1y"] == pytest.approx(0.15, abs=0.01)
    assert row["diff_1y"] == pytest.approx(-0.05, abs=0.02)


def test_tracking_report_flags_a_pair_missing_price_data_instead_of_crashing():
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    rows = tracking_report(pairs, {"BOTZ": _grown(0.2)},  # XAIX.DE absent
                           as_of=pd.Timestamp("2026-08-28"))
    assert rows[0]["ucits_1y"] is None
    assert rows[0]["diff_1y"] is None


def test_tracking_report_is_grouped_and_sortable_by_match_quality():
    prices = {
        "A": _grown(0.10), "AX.DE": _grown(0.10),
        "B": _grown(0.10), "BX.DE": _grown(0.10),
    }
    pairs = [
        {"theme": "T1", "us_ticker": "A", "ucits_ticker": "AX", "yf_ticker": "AX.DE", "match": "partial"},
        {"theme": "T2", "us_ticker": "B", "ucits_ticker": "BX", "yf_ticker": "BX.DE", "match": "exact"},
    ]
    rows = tracking_report(pairs, prices, as_of=pd.Timestamp("2026-08-28"))
    assert {r["match"] for r in rows} == {"partial", "exact"}
