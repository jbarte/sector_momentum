"""UCITS tracking-difference monitor: does the EU-buyable ETF track the
US-listed one this project actually scores?

`US`/`UCITS` columns display each listing's own, native-currency return — a
reader should see what the EUR-quoted fund actually did in EUR. But `diff_*`
IS FX-adjusted (via `fx_adjust_to_usd`) before comparing: an unhedged UCITS
fund's EUR price already carries the EUR/USD move on top of the US-listed
asset it holds, so a perfectly-tracking fund would otherwise show a raw gap
equal to the FX move. This corrects the original 2026-08-30 design decision
("own currency, no FX conversion needed, a return is dimensionless") — true
for two returns on the same underlying currency exposure, not true for an
unhedged cross-currency wrapper. See scripts/ucits_tracking_monitor.py's
module docstring for the full account, including why the bug was
low-consequence the day it was found (a quiet FX year) but not low-consequence
in general.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.ucits_tracking_monitor import (
    assumed_currency,
    fx_adjust_to_usd,
    resolve_yf_ticker,
    theme_pairs,
    tracking_report,
    tracking_stats,
    trailing_return,
    weekly_returns,
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


# --- assumed_currency -----------------------------------------------------

def test_assumed_currency_recognizes_xetra_as_eur():
    assert assumed_currency("XAIX.DE") == "EUR"


def test_assumed_currency_is_none_for_an_unverified_exchange():
    """The regression this guards: applying EURUSD=X to a GBP/CHF listing
    would silently corrupt diff/correlation/tracking_error exactly the way
    the un-FX-adjusted original bug did — for a different reason. Refusing on
    an unknown suffix is the safe failure, not a hardcoded EUR assumption."""
    assert assumed_currency("SOMEISIN.L") is None
    assert assumed_currency("SOMEISIN.SW") is None


def test_assumed_currency_is_none_with_no_suffix_at_all():
    assert assumed_currency("BOTZ") is None


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


# --- fx_adjust_to_usd -------------------------------------------------------

def test_fx_adjust_to_usd_converts_a_eur_price_series():
    idx = pd.bdate_range("2026-01-01", periods=5)
    eur_price = pd.Series([100.0] * 5, index=idx)
    fx = pd.Series([1.10] * 5, index=idx)  # USD per 1 EUR
    usd = fx_adjust_to_usd(eur_price, fx)
    assert usd.tolist() == pytest.approx([110.0] * 5)


def test_fx_adjust_to_usd_ffills_the_fx_rate_rather_than_intersecting_dates():
    """The two series rarely share every date exactly (different market
    holidays) — ffill the FX rate onto the price series' own dates rather than
    dropping to a bare intersection, which would silently thin the history."""
    price_idx = pd.bdate_range("2026-01-01", periods=5)
    fx_idx = price_idx[:3]  # FX feed missing the last two business days
    eur_price = pd.Series([100.0, 101.0, 102.0, 103.0, 104.0], index=price_idx)
    fx = pd.Series([1.0, 1.0, 1.0], index=fx_idx)
    usd = fx_adjust_to_usd(eur_price, fx)
    assert len(usd) == 5
    assert usd.iloc[-1] == pytest.approx(104.0)  # ffilled 1.0 rate


def test_fx_adjust_to_usd_of_an_empty_series_is_empty():
    assert fx_adjust_to_usd(pd.Series(dtype=float), pd.Series(dtype=float)).empty


# --- weekly_returns ----------------------------------------------------

def test_weekly_returns_resamples_and_diffs():
    idx = pd.bdate_range("2026-01-01", periods=15)  # a bit over 3 calendar weeks
    s = pd.Series(range(100, 115), index=idx, dtype=float)
    wr = weekly_returns(s)
    assert len(wr) >= 2
    assert (wr > 0).all()  # monotonically rising input -> every weekly return positive


def test_weekly_returns_of_a_short_series_is_empty_not_an_error():
    s = pd.Series([100.0], index=[pd.Timestamp("2026-08-28")])
    assert weekly_returns(s).empty


# --- tracking_stats ----------------------------------------------------

def _correlated_pair(n_weeks, corr_break=False):
    idx = pd.bdate_range(end="2026-08-28", periods=n_weeks * 5, freq="B")
    base = np.cumsum(np.random.default_rng(0).normal(0, 1, len(idx)))
    a = pd.Series(100 + base, index=idx)
    b = pd.Series(100 + base * (0.3 if corr_break else 1.0)
                 + (np.random.default_rng(1).normal(0, 5, len(idx)) if corr_break else 0),
                 index=idx)
    return a, b


def test_tracking_stats_reports_high_correlation_for_near_identical_series():
    a, b = _correlated_pair(60)
    stats = tracking_stats(a, b)
    assert stats["n_weeks"] >= 55
    assert stats["correlation"] > 0.95
    assert stats["tracking_error"] is not None and stats["tracking_error"] >= 0


def test_tracking_stats_is_none_below_the_minimum_joint_history():
    """The regression this guards: a fund a few months post-launch has real
    daily bars but too few weekly return observations for correlation to mean
    anything — report None with the count, not a noisy number."""
    a, b = _correlated_pair(10)
    stats = tracking_stats(a, b, min_weeks=26)
    assert stats["correlation"] is None
    assert stats["tracking_error"] is None
    assert stats["n_weeks"] < 26


def test_tracking_stats_of_no_overlap_is_none():
    a = pd.Series([1.0, 2.0], index=pd.bdate_range("2020-01-01", periods=2))
    b = pd.Series([1.0, 2.0], index=pd.bdate_range("2026-01-01", periods=2))
    stats = tracking_stats(a, b)
    assert stats["correlation"] is None and stats["n_weeks"] == 0


# --- tracking_report, extended with fx + correlation ------------------

def test_tracking_report_without_fx_matches_old_native_currency_behaviour():
    """No fx passed -> identical to the pre-FX-fix behaviour (backward compat
    for the callers/tests that predate this)."""
    prices = {"BOTZ": _grown(0.20), "XAIX.DE": _grown(0.15)}
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    rows = tracking_report(pairs, prices, as_of=pd.Timestamp("2026-08-28"))
    assert rows[0]["diff_1y"] == pytest.approx(-0.05, abs=0.02)
    assert rows[0]["correlation"] is None
    assert rows[0]["tracking_error"] is None


def test_tracking_report_with_fx_corrects_the_diff_for_currency_drift():
    """The bug this fixes: an unhedged EUR fund quotes P_eur = V_usd / fx, so
    even a PERFECT tracker shows a raw diff driven entirely by the FX move.
    Construct exactly that fund (eur_price = us_price / fx) and confirm the
    raw diff is dominated by FX while the FX-adjusted diff collapses to ~0."""
    idx = pd.bdate_range(end="2026-08-28", periods=int(1.2 * 252))
    us_values = np.linspace(100, 120, len(idx))     # underlying USD NAV: +20%
    fx_values = np.linspace(1.00, 1.20, len(idx))   # EUR strengthens 20% vs USD
    eur_values = us_values / fx_values              # unhedged EUR price of that SAME fund
    us = pd.Series(us_values, index=idx)
    eur = pd.Series(eur_values, index=idx)
    fx = pd.Series(fx_values, index=idx)
    prices = {"BOTZ": us, "XAIX.DE": eur}
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    raw = tracking_report(pairs, prices, as_of=idx[-1])
    # ~0% EUR native return (the +20% USD gain is offset by EUR's own +20%
    # strength) minus the US's +20% -> roughly -20%. Loose tolerance: the
    # trailing-return lookback point is not exactly the series' first index,
    # so this is "clearly dominated by FX," not a precise reproduction.
    assert raw[0]["diff_1y"] == pytest.approx(-0.20, abs=0.05), \
        "sanity: without FX adjustment, a perfectly-tracking fund reads as a ~20pp laggard"

    adjusted = tracking_report(pairs, prices, as_of=idx[-1], fx=fx)
    assert adjusted[0]["diff_1y"] == pytest.approx(0.0, abs=0.03), \
        "FX-adjusted: a perfectly-tracking fund must show ~0 diff regardless of FX"
    # the native-currency EUR return is still shown, unconverted, for the reader
    assert adjusted[0]["ucits_1y"] == pytest.approx(0.0, abs=0.03)


def test_tracking_report_populates_correlation_and_tracking_error_with_fx():
    idx = pd.bdate_range(end="2026-08-28", periods=300)
    rng = np.random.default_rng(2)
    base = np.cumsum(rng.normal(0, 1, len(idx)))
    us = pd.Series(100 + base, index=idx)
    eur = pd.Series(100 + base, index=idx)  # identical after FX-adjustment
    fx = pd.Series(1.0, index=idx)
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    rows = tracking_report(pairs, {"BOTZ": us, "XAIX.DE": eur}, as_of=idx[-1], fx=fx)
    assert rows[0]["correlation"] > 0.95
    assert rows[0]["n_weeks"] >= 26


def test_tracking_report_reports_correlation_none_for_a_pair_below_min_history():
    idx = pd.bdate_range(end="2026-08-28", periods=40)  # ~8 weeks
    us = pd.Series(range(100, 140), index=idx, dtype=float)
    eur = pd.Series(range(100, 140), index=idx, dtype=float)
    fx = pd.Series(1.0, index=idx)
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XAIX",
             "yf_ticker": "XAIX.DE", "match": "close"}]
    rows = tracking_report(pairs, {"BOTZ": us, "XAIX.DE": eur}, as_of=idx[-1], fx=fx)
    assert rows[0]["correlation"] is None
    assert rows[0]["n_weeks"] < 26


def test_tracking_report_refuses_to_fx_adjust_an_unrecognized_exchange(caplog):
    """The bug this guards against: fx is available and the pair fetches fine,
    but its exchange suffix (a hypothetical future non-Xetra entry) is not one
    assumed_currency recognizes. It must fall back to the raw, unadjusted diff
    for THIS PAIR — not silently apply EURUSD=X to a non-EUR listing, which
    would reintroduce exactly the currency-conflation bug this module fixes."""
    idx = pd.bdate_range(end="2026-08-28", periods=int(1.2 * 252))
    us = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
    other = pd.Series(np.linspace(100, 120, len(idx)), index=idx)
    fx = pd.Series(np.linspace(1.00, 1.20, len(idx)), index=idx)
    pairs = [{"theme": "AI", "us_ticker": "BOTZ", "ucits_ticker": "XYZ",
             "yf_ticker": "XYZ.L", "match": "close"}]  # unrecognized suffix

    import logging
    with caplog.at_level(logging.WARNING):
        rows = tracking_report(pairs, {"BOTZ": us, "XYZ.L": other}, as_of=idx[-1], fx=fx)

    # native-currency return unchanged either way here (both grow identically),
    # so the diagnostic signal is: no FX correction applied, and correlation/
    # tracking_error are None rather than computed on an unconverted series.
    assert rows[0]["correlation"] is None
    assert rows[0]["tracking_error"] is None
    assert "not recognized as EUR" in caplog.text
