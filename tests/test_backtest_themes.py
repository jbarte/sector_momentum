"""Tests for the theme backtest pipeline (replay, engine, results)."""
import json
import numpy as np
import pandas as pd

from src.backtest import engine, replay
from src.backtest.results import write_results, load_summary


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _themes_cfg():
    return {
        "benchmark": "ACWI",
        "themes": {
            "Alpha": {"ticker": "ALPH", "gdelt_keywords": ["alpha tech"]},
            "Beta": {"ticker": "BETA", "gdelt_keywords": ["beta corp"]},
            "Gamma": {"ticker": "GAMM", "gdelt_keywords": ["gamma energy"]},
            "Delta": {"ticker": "DELT", "gdelt_keywords": ["delta bio"]},
        },
    }


def _prices(n=600):
    return {
        "ALPH": _ramp(n, 100, 0.9),
        "BETA": _ramp(n, 100, 0.2),
        "GAMM": _ramp(n, 100, 0.5),
        "DELT": _ramp(n, 100, 0.3),
        "ACWI": _ramp(n, 100, 0.4),
        "SPY": _ramp(n, 100, 0.4),
    }


def test_score_themes_as_of_returns_theme_keys():
    prices = _prices()
    scored = replay.score_themes_as_of(_themes_cfg(), prices, pd.Timestamp("2020-01-15"))
    assert scored is not None
    assert all(k.startswith("THEME|") for k in scored.index)
    assert "composite" in scored.columns
    assert len(scored) == 4


def test_score_themes_as_of_ranks_by_trend():
    prices = _prices()
    scored = replay.score_themes_as_of(_themes_cfg(), prices, pd.Timestamp("2020-01-15"))
    assert scored.loc["THEME|Alpha", "composite"] > scored.loc["THEME|Beta", "composite"]


def test_score_themes_as_of_returns_none_when_no_data():
    scored = replay.score_themes_as_of(_themes_cfg(), {}, pd.Timestamp("2020-01-15"))
    assert scored is None


def test_theme_instruments_maps_names_to_tickers():
    inst = engine._theme_instruments(_themes_cfg())
    assert inst == {
        "THEME|Alpha": "ALPH",
        "THEME|Beta": "BETA",
        "THEME|Gamma": "GAMM",
        "THEME|Delta": "DELT",
    }


def test_run_theme_track_produces_curve_and_metrics():
    track = engine.run_theme_track(_themes_cfg(), _prices(), top_n=2)
    assert track is not None
    assert track["region"] == "THEME"
    assert track["benchmark"] == "ACWI"
    assert len(track["equity_curve"]) > 0
    assert "cagr" in track["metrics"]
    assert track["metrics"]["total_return"] > 0


def test_run_theme_track_falls_back_to_spy():
    prices = _prices()
    del prices["ACWI"]
    cfg = _themes_cfg()
    track = engine.run_theme_track(cfg, prices, top_n=2)
    assert track is not None
    assert track["benchmark"] == "SPY"


def test_run_theme_track_returns_none_without_benchmark():
    prices = _prices()
    del prices["ACWI"]
    del prices["SPY"]
    track = engine.run_theme_track(_themes_cfg(), prices, top_n=2)
    assert track is None


def test_theme_track_round_trips_through_the_shared_tracks_dict(tmp_path):
    """The theme track shares one summary.json with the sector tracks, keyed by
    region. It used to be written to backtests_themes/ under a different shape
    ({"track": …} not {"tracks": {…}}), which no reader ever looked at."""
    track = engine.run_theme_track(_themes_cfg(), _prices(), top_n=2)
    out = str(tmp_path / "bt")
    path = write_results({"THEME": track}, out_dir=out,
                         generated_at="2026-07-15T00:00:00Z", top_n=5)
    assert path.endswith("summary.json")

    summary = load_summary(out)
    assert summary["tracks"]["THEME"]["region"] == "THEME"
    assert len(summary["tracks"]["THEME"]["equity_curve"]) > 0
    # Per-track top_n must survive: themes rebalance top 2 here while the
    # summary-level top_n is the sector default of 5.
    assert summary["tracks"]["THEME"]["top_n"] == 2
    assert summary["top_n"] == 5
    assert (tmp_path / "bt" / "equity_THEME.csv").exists()
    assert (tmp_path / "bt" / "holdings_THEME.csv").exists()


def test_write_results_skips_a_theme_track_that_produced_nothing(tmp_path):
    """run_theme_track returns None on insufficient data; that must not abort
    the sector tracks sharing the dict."""
    out = str(tmp_path / "bt_none")
    write_results({"THEME": None}, out_dir=out, generated_at="2026-07-15T00:00:00Z")
    summary = load_summary(out)
    assert summary["tracks"]["THEME"] is None
    assert not (tmp_path / "bt_none" / "equity_THEME.csv").exists()


def test_dashboard_context_surfaces_the_theme_track(tmp_path):
    """The wiring this change exists for: a THEME track in summary.json must
    reach BACKTEST_DATA, which the template keys by region."""
    import json
    from dashboard.build import _build_backtest_context

    track = engine.run_theme_track(_themes_cfg(), _prices(), top_n=2, buffer_frac=0.28)
    out = str(tmp_path / "bt_ctx")
    write_results({"THEME": track}, out_dir=out, generated_at="2026-07-15T00:00:00Z")

    ctx = _build_backtest_context(out)
    assert ctx["has_backtest"] is True
    assert "THEME" in json.loads(ctx["backtest_json"])
    assert [r["region"] for r in ctx["backtest_metrics"]] == ["THEME"]
    # The regression this task fixes: run_theme_track's return dict key was
    # renamed "buffer" -> "buffer_frac" (Task 3), and figures.py must read
    # the new key, not silently default to 0.
    assert ctx["backtest_metrics"][0]["buffer_pct"] == f"{100 * track['buffer_frac']:.0f}%"
    assert ctx["backtest_metrics"][0]["buffer_pct"] != "0%", (
        "buffer_pct defaulted to 0 -- figures.py is still reading the old 'buffer' key"
    )


# ---------------------------------------------------------------------------
# Evaluation window vs fetch window
# ---------------------------------------------------------------------------
# backtest.py used to pass --start straight to fetch_prices, so a windowed run
# began scoring on its first fetched bar with no history behind it and
# above_200dma was NaN for the opening ~200 bars. The same defect in
# scripts/horizon_sweep.py inverted the horizon preset ranking (fixed
# 2026-08-13); this is its twin, and the guard belongs at the engine boundary
# where the calendar is actually derived.

def test_run_theme_track_bounds_the_calendar_with_since():
    prices = _prices()
    full = engine.run_theme_track(_themes_cfg(), prices, top_n=2)
    assert full is not None
    all_dates = [pt["date"] for pt in full["equity_curve"]]
    cut = all_dates[len(all_dates) // 2]

    bounded = engine.run_theme_track(_themes_cfg(), prices, top_n=2, since=cut)
    assert bounded is not None
    kept = [pt["date"] for pt in bounded["equity_curve"]]
    assert kept, "bounding must not empty the track"
    assert min(kept) >= cut
    assert len(kept) < len(all_dates), "since did not bound anything"


def test_run_theme_track_since_none_is_the_old_behaviour():
    """The parameter is opt-in: every existing caller must be unaffected."""
    prices = _prices()
    a = engine.run_theme_track(_themes_cfg(), prices, top_n=2)
    b = engine.run_theme_track(_themes_cfg(), prices, top_n=2, since=None)
    assert [p["date"] for p in a["equity_curve"]] == [p["date"] for p in b["equity_curve"]]


def test_run_theme_track_since_past_the_data_returns_none():
    """Fewer than 3 rebalance dates is already the "not enough data" path; an
    over-late window must take it rather than raising."""
    prices = _prices()
    assert engine.run_theme_track(_themes_cfg(), prices, top_n=2,
                                  since="2099-01-01") is None
