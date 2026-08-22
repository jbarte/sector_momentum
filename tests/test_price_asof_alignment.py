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
        "AAA": _series("2026-08-07"),   # fresh (just refetched)
        "BBB": _series("2026-08-06"),   # one day behind (still on cache)
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
# Source semantics
# ---------------------------------------------------------------------------
#
# stooq was retired 2026-08-09 (src/data/prices.py module docstring): its CSV
# endpoint now requires solving a JavaScript proof-of-work challenge, which no
# HTTP client can pass. The two tests that lived here pinned stooq's inclusive
# `d2` against yfinance's exclusive `end` resolving to the same final bar —
# moot with one source. What remains is the still-live half of that contract.

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


# ---------------------------------------------------------------------------
# Non-scan price consumers
#
# Five callers reach fetch_prices, and they are NOT all the same shape — which
# is what makes "apply alignment everywhere" the wrong fix:
#
#   scan.py          end=today   cross-sectional      aligns (already)
#   correlation.py   end=today   cross-sectional      must align
#   macro.py         end=today   two indices, own history   neither
#   badges.py        end=+15d    per-ticker forward returns  must cap end
#   validation.py    end=+30d    per-ticker forward returns  must cap end
#
# Aligning badges/validation would DROP tickers lagging the cohort and truncate
# every series to a shared date — deleting themes from a forward-return sample
# and shortening its newest windows. Capping `end` is what those two need.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("requested,today,expected", [
    # Future end -> clamped. This is the badges/validation case: their window
    # is sized off a forward horizon, so it overshoots for the newest scans.
    ("2026-09-05", "2026-08-22", "2026-08-22"),
    # Already in the past -> untouched. Historical backfills must keep the
    # narrow window they asked for rather than silently widening to today.
    ("2026-07-01", "2026-08-22", "2026-07-01"),
    # Exactly today -> untouched (scan.py's own case).
    ("2026-08-22", "2026-08-22", "2026-08-22"),
])
def test_capped_end_clamps_a_future_end_to_today(requested, today, expected):
    """`end` is EXCLUSIVE and is the mechanism that keeps an in-progress
    session out of the data, but `_cache_is_fresh` never receives it -- that
    check always measures the cache against today. So an `end` past today lets
    yfinance return today's partial candle and then has it judged fresh, in the
    same shared data/cache/ scan.py reads and scores from.

    Clamping loses no data, because no bar exists past today.
    """
    import datetime as _dt
    from src.data import prices as prices_mod

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date.fromisoformat(today)

    with patch.object(prices_mod, "date", _FixedDate):
        assert prices_mod.capped_end(pd.Timestamp(requested)) == expected


def test_capped_end_accepts_dates_as_well_as_timestamps():
    """Callers hold pandas Timestamps (`max(scan_dates.values()) + timedelta`);
    scan.py and the tests hold plain dates. Both must work, since the point of
    the helper is that every caller can route `end` through it."""
    import datetime as _dt
    from src.data import prices as prices_mod

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 8, 22)

    with patch.object(prices_mod, "date", _FixedDate):
        assert prices_mod.capped_end(_dt.date(2026, 9, 5)) == "2026-08-22"
        assert prices_mod.capped_end(pd.Timestamp("2026-09-05")) == "2026-08-22"


def test_fetch_prices_clamps_a_future_end_at_the_chokepoint():
    """The invariant is enforced in `fetch_prices`, not asked of each caller.

    Seven call sites reach it. Two (`badges.py`, `validation.py`) size their
    window from a forward-return horizon and legitimately overshoot today; the
    rest already pass `end=today` or earlier. Clamping per-caller worked but
    was opt-in, and the first audit of it undercounted the callers by two --
    which is exactly how an opt-in invariant decays.
    """
    import datetime as _dt
    from src.data import prices as prices_mod

    class _FixedDate(_dt.date):
        @classmethod
        def today(cls):
            return _dt.date(2026, 8, 22)

    seen = {}

    def _fake_fetch(ticker, start, end):
        seen["end"] = end
        return None, None

    with patch.object(prices_mod, "date", _FixedDate), \
         patch.object(prices_mod, "_fetch_single", _fake_fetch), \
         patch.object(prices_mod, "_cache_is_fresh", lambda *a, **k: False):
        prices_mod.fetch_prices(["XLK"], "2026-01-01", "2026-09-05",
                                cache_dir="/tmp/_clamp_test_cache")

    assert seen["end"] == "2026-08-22", (
        f"fetch_prices passed end={seen['end']!r} through to the fetcher -- a "
        f"future end admits today's partial candle into the shared cache"
    )


def _strip_py_comments(text: str) -> str:
    """Drop `#` comments so a name MENTIONED in prose cannot satisfy a check
    for that name being CALLED.

    Written after a sabotage run passed when it should have failed: removing
    the real call left the function's name in the comment beside it, which a
    plain substring check happily accepted.
    """
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def test_correlation_aligns_its_cohort_before_correlating():
    """The heatmap correlates tickers AGAINST EACH OTHER, so it is exactly the
    cross-sectional case align_cohort_asof exists for -- and it was the one
    cross-sectional consumer that never called it.

    _compute_correlation_matrix builds `pd.DataFrame(closes)` across the UNION
    of every ticker's dates, then takes `returns.tail(60)`. One ticker whose
    series runs three days past the rest contributes three rows that are NaN
    for everyone else, so the 60-row window holds fewer than 60 usable
    observations for every pair -- silently, since `.corr()` drops NaN pairwise
    and reports a number regardless.
    """
    code = _strip_py_comments(
        (Path(__file__).parent.parent / "dashboard/correlation.py").read_text()
    )
    # The CALL, not the name: an import line and an explanatory comment both
    # carry the bare name, and a substring check on it survives deleting the
    # call itself -- confirmed by sabotage.
    assert "align_cohort_asof(prices" in code, (
        "correlation.py correlates tickers against each other without aligning "
        "them to a shared as-of date first"
    )


def test_correlation_reports_the_aligned_asof_not_the_newest_ticker():
    """`correlation_date` goes into the heatmap's page context. Taking max()
    across every ticker's last date reported the freshest single ticker rather
    than the date the matrix was actually computed on, overstating freshness
    whenever one series ran ahead -- the same staleness the alignment above
    removes.

    No template renders this value today (checked: `correlation.py` and this
    file plus `test_correlation.py` are its only references), so the fix has no
    visible effect right now. Pinned anyway because a wrong date sitting in the
    context is a trap for whoever first renders it, and because the correct
    value is now free -- `align_cohort_asof` already returns it.
    """
    # Whole file, not a split on a function name: correlation.py's builder is
    # `build_correlation_context`, so the previous split on "build_page_context"
    # silently matched nothing and scoped the check to the entire text anyway.
    # An unscoped check is honest about what it covers; a split that names the
    # wrong function is a no-op that looks precise.
    code = _strip_py_comments(
        (Path(__file__).parent.parent / "dashboard/correlation.py").read_text()
    )
    assert "max(all_dates)" not in code, (
        "correlation_date still reports the newest date any single ticker "
        "reached instead of the cohort's aligned as-of date"
    )
