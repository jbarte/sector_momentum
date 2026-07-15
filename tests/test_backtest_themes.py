"""Tests for the theme backtest pipeline (replay, engine, results)."""
import json
import numpy as np
import pandas as pd

from src.backtest import engine, replay
from src.backtest.results import write_theme_results, load_summary


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _themes_cfg():
    return {
        "benchmark": "ACWI",
        "themes": {
            "Alpha": "ALPH",
            "Beta": "BETA",
            "Gamma": "GAMM",
            "Delta": "DELT",
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


def test_write_and_load_theme_results(tmp_path):
    track = engine.run_theme_track(_themes_cfg(), _prices(), top_n=2)
    out = str(tmp_path / "bt_themes")
    path = write_theme_results(track, out_dir=out, generated_at="2026-07-15T00:00:00Z", top_n=2)
    assert path.endswith("summary.json")
    summary = load_summary(out)
    assert summary is not None
    assert summary["top_n"] == 2
    assert summary["track"]["region"] == "THEME"
    assert len(summary["track"]["equity_curve"]) > 0


def test_write_theme_results_handles_none(tmp_path):
    out = str(tmp_path / "bt_empty")
    path = write_theme_results(None, out_dir=out, generated_at="2026-07-15T00:00:00Z")
    summary = load_summary(out)
    assert summary["track"] is None
