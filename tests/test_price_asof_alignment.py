"""Cohort as-of alignment — every ticker must be scored on the same last bar.

The composite z-scores each signal *across* the theme cohort, and every signal
reads the last row of the series it is handed. A cohort whose members end on
different dates therefore ranks one theme's Tuesday reading against another's
Wednesday reading. Nothing upstream guarantees a common end date, so these
tests pin it.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.data.prices import MAX_ASOF_LAG_DAYS, align_cohort_asof
from src.pipeline import build_theme_signals_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _series(last: str, n: int = 260) -> pd.DataFrame:
    """OHLCV frame of `n` business days ending on (and including) `last`."""
    idx = pd.bdate_range(end=pd.Timestamp(last), periods=n)
    close = pd.Series(100.0 + 0.5 * np.arange(n), index=idx)
    return pd.DataFrame({
        "Close": close,
        "Open": close - 0.1,
        "High": close + 0.5,
        "Low": close - 0.5,
        "Volume": pd.Series(1_000_000.0, index=idx),
    })


def _last_dates(prices: dict[str, pd.DataFrame]) -> set[pd.Timestamp]:
    return {pd.Timestamp(df.index.max()) for df in prices.values()}


# ---------------------------------------------------------------------------
# align_cohort_asof — the invariant
# ---------------------------------------------------------------------------

def test_staggered_cohort_ends_on_one_date():
    """The headline invariant: mixed last bars in, one shared last bar out."""
    prices = {
        "AAA": _series("2026-08-07"),   # fresh (e.g. stooq, inclusive end)
        "BBB": _series("2026-08-06"),   # one day behind (e.g. yfinance)
        "CCC": _series("2026-08-07"),
        "DDD": _series("2026-08-06"),
    }
    aligned, as_of = align_cohort_asof(prices)

    assert as_of == pd.Timestamp("2026-08-06")
    assert set(aligned) == set(prices)
    assert _last_dates(aligned) == {pd.Timestamp("2026-08-06")}


def test_already_aligned_cohort_is_untouched():
    prices = {t: _series("2026-08-07") for t in ("AAA", "BBB", "CCC")}
    aligned, as_of = align_cohort_asof(prices)

    assert as_of == pd.Timestamp("2026-08-07")
    for ticker, df in aligned.items():
        pd.testing.assert_frame_equal(df, prices[ticker])


def test_single_ticker_cohort():
    aligned, as_of = align_cohort_asof({"AAA": _series("2026-08-07")})
    assert as_of == pd.Timestamp("2026-08-07")
    assert len(aligned["AAA"]) == 260


def test_empty_input_returns_none_asof():
    aligned, as_of = align_cohort_asof({})
    assert aligned == {}
    assert as_of is None


def test_all_empty_frames_return_none_asof():
    aligned, as_of = align_cohort_asof({"AAA": pd.DataFrame(), "BBB": pd.DataFrame()})
    assert aligned == {}
    assert as_of is None


def test_empty_frame_is_ignored_not_fatal():
    prices = {"AAA": _series("2026-08-07"), "EMPTY": pd.DataFrame()}
    aligned, as_of = align_cohort_asof(prices)
    assert as_of == pd.Timestamp("2026-08-07")
    assert set(aligned) == {"AAA"}


# ---------------------------------------------------------------------------
# align_cohort_asof — stale-ticker handling
# ---------------------------------------------------------------------------

def test_grossly_stale_ticker_is_dropped_not_absorbed():
    """A frozen series must not drag the whole cohort back to its date."""
    prices = {
        "AAA": _series("2026-08-07"),
        "BBB": _series("2026-08-07"),
        "CCC": _series("2026-08-07"),
        "STALE": _series("2026-07-01"),
    }
    aligned, as_of = align_cohort_asof(prices)

    assert as_of == pd.Timestamp("2026-08-07")
    assert "STALE" not in aligned
    assert _last_dates(aligned) == {pd.Timestamp("2026-08-07")}


def test_lag_within_tolerance_pulls_cohort_back_instead_of_dropping():
    """A ticker one trading day behind is kept; everyone moves to its date."""
    prices = {
        "AAA": _series("2026-08-07"),
        "BBB": _series("2026-08-07"),
        "LAGGY": _series("2026-08-06"),
    }
    aligned, as_of = align_cohort_asof(prices)

    assert as_of == pd.Timestamp("2026-08-06")
    assert set(aligned) == {"AAA", "BBB", "LAGGY"}


def test_lag_tolerance_boundary():
    """max_lag_days is inclusive: exactly at the limit is kept, one past drops."""
    modal = pd.Timestamp("2026-08-07")
    at_limit = (modal - pd.Timedelta(days=MAX_ASOF_LAG_DAYS)).strftime("%Y-%m-%d")
    past_limit = (modal - pd.Timedelta(days=MAX_ASOF_LAG_DAYS + 1)).strftime("%Y-%m-%d")

    kept, _ = align_cohort_asof({
        "AAA": _series("2026-08-07"), "BBB": _series("2026-08-07"),
        "EDGE": _series(at_limit),
    })
    assert "EDGE" in kept

    dropped, _ = align_cohort_asof({
        "AAA": _series("2026-08-07"), "BBB": _series("2026-08-07"),
        "EDGE": _series(past_limit),
    })
    assert "EDGE" not in dropped


def test_modal_date_breaks_ties_toward_the_fresher_group():
    """Two equal-sized groups: anchor on the later one, so the stale half is
    judged against the fresh date rather than the other way round."""
    prices = {
        "A1": _series("2026-08-07"), "A2": _series("2026-08-07"),
        "B1": _series("2026-08-06"), "B2": _series("2026-08-06"),
    }
    aligned, as_of = align_cohort_asof(prices)
    assert as_of == pd.Timestamp("2026-08-06")
    assert set(aligned) == set(prices)


def test_stats_out_records_asof_spread_and_drops():
    stats: dict = {}
    align_cohort_asof(
        {
            "AAA": _series("2026-08-07"),
            "BBB": _series("2026-08-07"),
            "LAGGY": _series("2026-08-06"),
            "STALE": _series("2026-07-01"),
        },
        stats_out=stats,
    )
    assert stats["asof"] == "2026-08-06"
    assert stats["asof_spread_days"] == (pd.Timestamp("2026-08-07") - pd.Timestamp("2026-07-01")).days
    assert stats["asof_dropped"] == ["STALE"]


def test_stats_out_on_empty_input():
    stats: dict = {}
    align_cohort_asof({}, stats_out=stats)
    assert stats == {"asof": None, "asof_spread_days": 0, "asof_dropped": []}


def test_alignment_does_not_mutate_the_input():
    prices = {"AAA": _series("2026-08-07"), "BBB": _series("2026-08-06")}
    before = {t: df.copy() for t, df in prices.items()}
    align_cohort_asof(prices)
    for ticker, df in prices.items():
        pd.testing.assert_frame_equal(df, before[ticker])


# ---------------------------------------------------------------------------
# The alignment actually changes what gets scored
# ---------------------------------------------------------------------------

def _themes_cfg() -> dict:
    return {
        "benchmark": "BENCH",
        "themes": {"Alpha": {"ticker": "AAA"}, "Beta": {"ticker": "BBB"}},
    }


def test_extra_bar_is_excluded_from_signals_after_alignment():
    """A ticker holding one bar the cohort lacks must not score on it."""
    aaa = _series("2026-08-07")
    # Make the extra bar impossible to miss: +40% on the final day.
    aaa.loc[aaa.index[-1], "Close"] = float(aaa["Close"].iloc[-2]) * 1.4

    prices = {"AAA": aaa, "BBB": _series("2026-08-06"), "BENCH": _series("2026-08-06")}

    unaligned = build_theme_signals_rows(_themes_cfg(), prices)
    aligned_prices, _ = align_cohort_asof(prices)
    aligned = build_theme_signals_rows(_themes_cfg(), aligned_prices)

    def _alpha(rows):
        return next(r for r in rows if r["gics_sector"] == "Alpha")

    # Unaligned, the spike lands in Alpha's 1m return; aligned, it does not.
    assert _alpha(unaligned)["return_1m"] > _alpha(aligned)["return_1m"] + 0.2

    # And the aligned value equals scoring the truncated series directly.
    truncated = {t: df[df.index <= pd.Timestamp("2026-08-06")] for t, df in prices.items()}
    assert _alpha(aligned)["return_1m"] == pytest.approx(
        _alpha(build_theme_signals_rows(_themes_cfg(), truncated))["return_1m"]
    )


# ---------------------------------------------------------------------------
# The scan actually applies it
# ---------------------------------------------------------------------------

def test_scan_scores_every_theme_as_of_one_date(monkeypatch):
    """End-to-end pin: whatever fetch_prices returns, the frames handed to the
    signal builder all end on the same date."""
    import scan
    import src.data.prices as _prices_mod
    import src.report as _report_mod
    import src.scoring as _scoring_mod
    import src.state as _state_mod

    themes_cfg = {
        "benchmark": "BENCH",
        "themes": {"Alpha": {"ticker": "AAA"}, "Beta": {"ticker": "BBB"},
                   "Gamma": {"ticker": "CCC"}},
    }
    # Deliberately staggered, the way a mixed cache/live run leaves things.
    prices = {
        "AAA": _series("2026-08-07"),
        "BBB": _series("2026-08-06"),
        "CCC": _series("2026-08-07"),
        "BENCH": _series("2026-08-06"),
        "SPY": _series("2026-08-07"),
    }

    seen: dict = {}

    def _capture(cfg, price_dict, **kwargs):
        seen["prices"] = price_dict
        return [
            {"region": "THEME", "gics_sector": name, "sector_key": f"THEME|{name}",
             **{c: 1.0 for c in scan.SIGNAL_COLUMNS}}
            for name in cfg["themes"]
        ]

    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run", "--no-finbert",
                                      "--no-dashboard", "--no-backup", "--no-alerts"])
    monkeypatch.setattr(scan, "_load_config", lambda path: (
        themes_cfg if "themes" in path
        else {"price_lookback_days": 252} if "universe" in path
        else {}))
    monkeypatch.setattr(scan, "fetch_prices", lambda *a, **k: dict(prices))
    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: dict(prices))
    monkeypatch.setattr(scan, "build_theme_signals_rows", _capture)

    scored = pd.DataFrame(
        {"level_score": [0.5] * 3, "change_score": [0.2] * 3, "data_score": [0.35] * 3,
         "sentiment_score": [float("nan")] * 3, "composite": [0.35] * 3,
         "rank": [1.0, 2.0, 3.0]},
        index=["THEME|Alpha", "THEME|Beta", "THEME|Gamma"],
    )
    monkeypatch.setattr(_scoring_mod, "score_all", lambda *a, **k: scored)
    monkeypatch.setattr(
        _scoring_mod, "zscore_cross_section",
        lambda wide_df, *a, **k: pd.DataFrame(
            {col: [0.0] * 3 for col in scan.SIGNAL_COLUMNS},
            index=pd.Index(scored.index, name="sector_key"),
        ),
    )
    monkeypatch.setattr(_state_mod, "init_db", lambda: MagicMock())
    monkeypatch.setattr(_state_mod, "load_last_scan", lambda *a, **k: None)
    monkeypatch.setattr(_state_mod, "compute_deltas", lambda cur, prior: cur)
    monkeypatch.setattr(_report_mod, "build_ranked_table", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(_report_mod, "build_movers", lambda *a, **k: {})
    monkeypatch.setattr(_report_mod, "write_report", lambda *a, **k: "/tmp/report.html")

    rc = scan.run(scan._parse_args())

    assert rc == 0
    assert _last_dates(seen["prices"]) == {pd.Timestamp("2026-08-06")}


def test_scan_aborts_when_nothing_survives_alignment(monkeypatch):
    import scan
    import src.data.prices as _prices_mod

    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run", "--no-finbert"])
    monkeypatch.setattr(scan, "_load_config", lambda path: (
        {"benchmark": "BENCH", "themes": {"Alpha": {"ticker": "AAA"}}} if "themes" in path
        else {"price_lookback_days": 252} if "universe" in path
        else {}))
    monkeypatch.setattr(scan, "fetch_prices", lambda *a, **k: {"AAA": pd.DataFrame()})
    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: {"AAA": pd.DataFrame()})

    assert scan.run(scan._parse_args()) == 1


# ---------------------------------------------------------------------------
# Source semantics — the upstream cause of a staggered cohort
# ---------------------------------------------------------------------------

def test_yfinance_end_is_passed_through_exclusive():
    """`end` is exclusive in this module, which is yfinance's own semantics.

    Adding a day to make it inclusive would pull in Yahoo's partial candle for
    an in-progress session, and _cache_is_fresh would then keep that half-formed
    close forever — so the adapter must pass `end` straight through.
    """
    from src.data.prices import _fetch_yfinance

    fake_yf = MagicMock()
    fake_yf.download.return_value = _series("2026-08-07", n=5)
    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        _fetch_yfinance("SPY", "2026-01-01", "2026-08-07")

    assert fake_yf.download.call_args.kwargs["end"] == "2026-08-07"
    assert fake_yf.download.call_args.kwargs["start"] == "2026-01-01"


def test_stooq_d2_is_converted_to_the_exclusive_contract():
    """stooq's d2 is inclusive, so it must be end-1 to denote the same bar."""
    from src.data.prices import _fetch_stooq

    captured: dict = {}

    class _Resp:
        text = "Date,Open,High,Low,Close,Volume\n2026-08-06,1,1,1,1,10\n"

        def raise_for_status(self):
            pass

    def _fake_get(url, params=None, timeout=None):
        captured.update(params)
        return _Resp()

    with patch("src.data.prices._requests.get", _fake_get):
        _fetch_stooq("SPY", "2026-01-01", "2026-08-07")

    assert captured["d2"] == "20260806"


def test_both_sources_denote_the_same_final_bar():
    """The skew this whole change is about: stooq's d2 and yfinance's end must
    resolve to the same last session, or the two sources stagger the cohort."""
    from src.data.prices import _fetch_stooq, _fetch_yfinance

    fake_yf = MagicMock()
    fake_yf.download.return_value = _series("2026-08-06", n=5)

    captured: dict = {}

    class _Resp:
        text = "Date,Open,High,Low,Close,Volume\n2026-08-06,1,1,1,1,10\n"

        def raise_for_status(self):
            pass

    def _fake_get(url, params=None, timeout=None):
        captured.update(params)
        return _Resp()

    with patch.dict(sys.modules, {"yfinance": fake_yf}):
        _fetch_yfinance("SPY", "2026-01-01", "2026-08-07")
    with patch("src.data.prices._requests.get", _fake_get):
        _fetch_stooq("SPY", "2026-01-01", "2026-08-07")

    stooq_last = pd.Timestamp(captured["d2"])                       # inclusive
    yf_last = pd.Timestamp(fake_yf.download.call_args.kwargs["end"]) - pd.Timedelta(days=1)
    assert stooq_last == yf_last == pd.Timestamp("2026-08-06")
