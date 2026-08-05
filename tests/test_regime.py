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


def test_score_all_none_resolves_to_config_weights():
    """None overrides must resolve to the config's level/change split, not just
    equal another None call."""
    import yaml
    cfg = yaml.safe_load(Path("config/weights.yaml").read_text())
    lw = float(cfg["data_pillar"]["level"])
    cw = float(cfg["data_pillar"]["change"])
    wide = _wide()
    default = score_all(wide, sentiment_score=None, blend_sentiment=False)
    explicit = score_all(wide, sentiment_score=None, blend_sentiment=False,
                         level_weight=lw, change_weight=cw)
    pd.testing.assert_frame_equal(default, explicit)


def test_score_all_weight_override_changes_data_score():
    wide = _wide()
    lvl_heavy = score_all(wide, blend_sentiment=False, level_weight=0.9, change_weight=0.1)
    chg_heavy = score_all(wide, blend_sentiment=False, level_weight=0.1, change_weight=0.9)
    # The composite (== data_score when sentiment off) must differ under different splits.
    assert not np.allclose(lvl_heavy["data_score"].values, chg_heavy["data_score"].values)


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
    # Rise to a peak (day 209), then a sustained decline back down. The
    # point-in-time regime at the peak must be risk-on (price above its trailing
    # SMA200), even though the *latest* regime is risk-off. If is_risk_on failed
    # to truncate closes to <= as_of, it would use the whole series' tail and
    # flip the peak-date answer to False — so this fixture actually exercises the
    # no-look-ahead guarantee (a monotonic series would not).
    vals = list(range(100, 310)) + list(range(309, 99, -1))  # 210 up, 210 down
    df = _spy(vals)
    peak = df.index[209]
    assert is_risk_on(df, peak) is True          # point-in-time at the peak
    assert is_risk_on(df, df.index[-1]) is False  # latest, after the decline


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


