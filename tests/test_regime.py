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
