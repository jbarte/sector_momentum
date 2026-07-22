"""Tests for regime-conditional weighting: score_all overrides, score_as_of
passthrough, regime helpers, and the run_track weights_fn hook."""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.scoring import score_all
from src.pipeline import SIGNAL_COLUMNS


def _wide(n=6):
    # Distinct level-ish vs change-ish signals so weighting changes the order.
    rng = np.random.default_rng(0)
    idx = [f"US|S{i}" for i in range(n)]
    data = {c: rng.normal(size=n) for c in SIGNAL_COLUMNS}
    return pd.DataFrame(data, index=idx)


def test_score_all_none_matches_config_default():
    wide = _wide()
    a = score_all(wide, sentiment_score=None, blend_sentiment=False)
    b = score_all(wide, sentiment_score=None, blend_sentiment=False,
                  level_weight=None, change_weight=None)
    pd.testing.assert_frame_equal(a, b)


def test_score_all_weight_override_changes_data_score():
    wide = _wide()
    lvl_heavy = score_all(wide, blend_sentiment=False, level_weight=0.9, change_weight=0.1)
    chg_heavy = score_all(wide, blend_sentiment=False, level_weight=0.1, change_weight=0.9)
    # The composite (== data_score when sentiment off) must differ under different splits.
    assert not np.allclose(lvl_heavy["data_score"].values, chg_heavy["data_score"].values)


def test_score_as_of_forwards_weights(monkeypatch):
    import src.backtest.replay as replay

    captured = {}

    def fake_score_all(wide, weights_path="config/weights.yaml", sentiment_score=None,
                       blend_sentiment=True, level_weight=None, change_weight=None):
        captured["lw"] = level_weight
        captured["cw"] = change_weight
        return pd.DataFrame({"rank": range(len(wide))}, index=wide.index)

    # One US sector row so build_signals_rows yields something; simplest: monkeypatch
    # build_signals_rows to return a fixed row set.
    def fake_rows(universe, prices):
        return [{"sector_key": "US|Tech", "region": "US",
                 **{c: 1.0 for c in replay.SIGNAL_COLUMNS}}]

    monkeypatch.setattr(replay, "build_signals_rows", fake_rows)
    monkeypatch.setattr(replay, "score_all", fake_score_all)

    replay.score_as_of({}, {}, pd.Timestamp("2020-01-31"), "US",
                       level_weight=0.7, change_weight=0.3)
    assert captured == {"lw": 0.7, "cw": 0.3}


def _spy(values, start="2019-01-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.DataFrame({"Close": values}, index=idx)


def test_is_risk_on_above_and_below():
    from src.backtest.regime import is_risk_on
    up = _spy(list(range(1, 261)))          # steadily rising -> last >= SMA200
    down = _spy(list(range(260, 0, -1)))    # steadily falling -> last < SMA200
    assert is_risk_on(up, up.index[-1]) is True
    assert is_risk_on(down, down.index[-1]) is False


def test_is_risk_on_warmup_defaults_true():
    from src.backtest.regime import is_risk_on
    short = _spy([100.0] * 50)               # < 200 closes
    assert is_risk_on(short, short.index[-1]) is True


def test_is_risk_on_no_lookahead():
    from src.backtest.regime import is_risk_on
    # Rising then a late spike; as-of an early date must ignore later data.
    vals = list(range(1, 220))
    df = _spy(vals)
    early = df.index[209]
    # as-of early: 200-SMA of first 210 closes, last close 210 -> above -> True
    assert is_risk_on(df, early) is True


def test_make_weights_fn_picks_regime():
    from src.backtest.regime import make_weights_fn
    up = _spy(list(range(1, 261)))
    down = _spy(list(range(260, 0, -1)))
    fn_up = make_weights_fn(up, on=(0.5, 0.5), off=(0.3, 0.7))
    fn_down = make_weights_fn(down, on=(0.5, 0.5), off=(0.3, 0.7))
    assert fn_up(up.index[-1]) == (0.5, 0.5)
    assert fn_down(down.index[-1]) == (0.3, 0.7)


def test_regime_stats_counts_switches():
    from src.backtest.regime import regime_stats
    # 260 rising then 260 falling -> risk-on for the tail of the rise, off in the fall.
    df = _spy(list(range(1, 261)) + list(range(260, 0, -1)))
    dates = list(df.index)
    stats = regime_stats(df, dates)
    assert stats["n_dates"] == len(dates)
    assert 0.0 <= stats["pct_risk_on"] <= 1.0
    assert stats["n_switches"] >= 1


def test_run_track_passes_weights_fn(monkeypatch):
    import src.backtest.engine as engine

    seen_weights = []

    def fake_score_as_of(universe, prices, d, region, level_weight=None, change_weight=None):
        seen_weights.append((level_weight, change_weight))
        # Return a minimal valid scored frame with >= top_n rows.
        idx = [f"US|S{i}" for i in range(5)]
        return pd.DataFrame({"composite": range(5), "rank": range(1, 6)}, index=idx)

    monkeypatch.setattr(engine.replay, "score_as_of", fake_score_as_of)
    monkeypatch.setattr(engine.replay, "month_end_dates",
                        lambda idx: [pd.Timestamp("2020-01-31"), pd.Timestamp("2020-02-29"),
                                     pd.Timestamp("2020-03-31")])
    # Minimal forward-returns + simulate stubs so run_track reaches the scoring loop.
    monkeypatch.setattr(engine.strategy, "forward_returns",
                        lambda prices, tickers, dates: pd.DataFrame(
                            0.0, index=dates, columns=tickers))
    monkeypatch.setattr(engine.strategy, "simulate",
                        lambda *a, **k: {"dates": [], "strategy_returns": [],
                                         "holdings": [], "turnover": []})

    prices = {"BENCH": pd.DataFrame({"Close": [1.0]},
                                    index=[pd.Timestamp("2020-01-31")])}
    engine.run_track({}, prices, "US", "BENCH", {"US|S0": "T0"},
                     top_n=5, weights_fn=lambda d: (0.2, 0.8))
    # Every scored date used the regime weights.
    assert seen_weights and all(w == (0.2, 0.8) for w in seen_weights)
