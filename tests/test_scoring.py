"""Unit tests for the scoring module."""
import numpy as np
import pandas as pd
import pytest

from src.scoring import zscore_cross_section, rank_sectors, score_all


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signals_df(n=11, seed=42):
    """Synthetic signals DataFrame with n sectors."""
    rng = np.random.default_rng(seed)
    index = [f"Sector_{i}" for i in range(n)]
    return pd.DataFrame(
        {
            "rs_ratio": rng.standard_normal(n) + 100,
            "rs_momentum": rng.standard_normal(n) + 100,
            "return_1m": rng.standard_normal(n) * 0.05,
            "return_3m": rng.standard_normal(n) * 0.10,
            "return_6m": rng.standard_normal(n) * 0.15,
            "acceleration": rng.standard_normal(n) * 0.02,
            "above_50dma": rng.standard_normal(n) * 0.05,
            "above_200dma": rng.standard_normal(n) * 0.08,
            "ma50_slope": rng.standard_normal(n) * 0.001,
            "obv_slope": rng.standard_normal(n),
        },
        index=index,
    )


# ---------------------------------------------------------------------------
# zscore_cross_section tests
# ---------------------------------------------------------------------------

def test_zscore_mean_is_zero():
    """After z-scoring, each numeric column mean should be approximately 0."""
    df = _make_signals_df(n=11)
    z = zscore_cross_section(df)
    for col in z.select_dtypes(include=[np.number]).columns:
        assert abs(z[col].mean()) < 1e-10, (
            f"Column '{col}' mean after z-score: {z[col].mean()}"
        )


def test_zscore_std_is_one():
    """After z-scoring, each column std should be approximately 1 (ddof=1, n>=3)."""
    df = _make_signals_df(n=11)
    z = zscore_cross_section(df)
    for col in z.select_dtypes(include=[np.number]).columns:
        assert abs(z[col].std(ddof=1) - 1.0) < 1e-10, (
            f"Column '{col}' std after z-score: {z[col].std(ddof=1)}"
        )


def test_zscore_all_nan_column():
    """A column with all NaN values should become all 0.0."""
    df = _make_signals_df(n=5)
    df["all_nan"] = np.nan
    z = zscore_cross_section(df)
    assert (z["all_nan"] == 0.0).all(), "All-NaN column should be filled with 0.0"


def test_zscore_constant_column():
    """A column with all same values → std=0 → all 0.0 (not NaN)."""
    df = _make_signals_df(n=5)
    df["constant"] = 42.0
    z = zscore_cross_section(df)
    assert (z["constant"] == 0.0).all(), "Constant column should be all 0.0 after z-score"


def test_zscore_nan_gets_neutral_z_without_distorting_peers():
    """Regression: NaN in a ~100-centred column must not distort other rows.

    The old code filled NaN with raw 0.0 *before* computing mean/std, which
    dragged the mean toward 0 and gave every peer an inflated z-score.  The
    fix computes stats on non-NaN only, then fills missing z with 0.0.
    """
    df = pd.DataFrame({
        "high_centre": [100.0, 101.0, 99.0, 102.0, np.nan],
    }, index=[f"S{i}" for i in range(5)])
    z = zscore_cross_section(df)

    # NaN sector gets z=0.0 (neutral)
    assert z.loc["S4", "high_centre"] == 0.0

    # Non-NaN peers must match z-scores computed without the NaN row
    clean = df["high_centre"].dropna()
    expected_z = (clean - clean.mean()) / clean.std(ddof=1)
    for idx in expected_z.index:
        assert abs(z.loc[idx, "high_centre"] - expected_z[idx]) < 1e-12, (
            f"Peer {idx} z-score diverged: got {z.loc[idx, 'high_centre']}, "
            f"expected {expected_z[idx]}"
        )


# ---------------------------------------------------------------------------
# rank_sectors tests
# ---------------------------------------------------------------------------

def test_rank_best_composite_gets_rank_1():
    """Highest composite score should receive rank 1."""
    composite = pd.Series({"A": 2.0, "B": 1.0, "C": -0.5})
    ranks = rank_sectors(composite)
    assert ranks["A"] == 1.0, f"Expected rank 1 for highest composite, got {ranks['A']}"


def test_rank_ties():
    """Two equal composites get 1.5 (average of 1 and 2), third gets 3.0."""
    composite = pd.Series({"A": 1.0, "B": 1.0, "C": 0.5})
    ranks = rank_sectors(composite)
    assert ranks["A"] == 1.5, f"Expected 1.5 for tied rank, got {ranks['A']}"
    assert ranks["B"] == 1.5, f"Expected 1.5 for tied rank, got {ranks['B']}"
    assert ranks["C"] == 3.0, f"Expected 3.0 for lowest composite, got {ranks['C']}"


# ---------------------------------------------------------------------------
# score_all tests
# ---------------------------------------------------------------------------

def test_score_all_output_columns():
    """Output has level_score, change_score, data_score, composite, rank."""
    df = _make_signals_df(n=11)
    result = score_all(df)
    required = {"level_score", "change_score", "data_score", "composite", "rank"}
    assert required.issubset(set(result.columns)), (
        f"Missing columns: {required - set(result.columns)}"
    )


def test_score_all_rank_range():
    """Ranks run from 1 to n with no gaps for random data (no ties expected)."""
    n = 11
    df = _make_signals_df(n=n)
    result = score_all(df)
    assert result["rank"].min() == 1.0, f"Min rank should be 1, got {result['rank'].min()}"
    assert result["rank"].max() == float(n), f"Max rank should be {n}, got {result['rank'].max()}"
    assert result["rank"].nunique() == n, "All ranks should be distinct for random data"


def test_score_all_uses_sentiment_score_when_provided(tmp_path):
    import yaml

    # Create a weights.yaml with sentiment weight active
    weights = {
        "data_pillar": {"level": 0.5, "change": 0.5},
        "pillars": {"data": 0.70, "sentiment": 0.30},
    }
    weights_file = tmp_path / "weights.yaml"
    weights_file.write_text(yaml.dump(weights))

    # 4 sectors, flat data signals (z-scores all → 0)
    signals = pd.DataFrame(
        {col: [1.0, 1.0, 1.0, 1.0] for col in [
            "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
            "acceleration", "above_50dma", "above_200dma", "ma50_slope",
            "obv_slope", "breadth_above_50dma",
        ]},
        index=["US|Tech", "US|Energy", "EU|Tech", "EU|Energy"],
    )
    # Give one sector a high positive sentiment score
    sentiment = pd.Series(
        {"US|Tech": 2.0, "US|Energy": -1.0, "EU|Tech": 0.0, "EU|Energy": 0.0}
    )
    result = score_all(signals, weights_path=str(weights_file), sentiment_score=sentiment)

    # With sentiment weight > 0 and flat data, US|Tech (highest sentiment) > US|Energy (lowest)
    assert result.loc["US|Tech", "composite"] > result.loc["US|Energy", "composite"]
    assert not pd.isna(result.loc["US|Tech", "sentiment_score"])


def test_score_all_blend_sentiment_false_keeps_composite_pure_data():
    # Two sectors, distinct signal values so data_score differs
    idx = ["US|Technology", "US|Energy"]
    signals = pd.DataFrame(
        {
            "rs_ratio": [1.0, -1.0], "return_3m": [1.0, -1.0], "return_6m": [1.0, -1.0],
            "above_50dma": [1.0, -1.0], "above_200dma": [1.0, -1.0],
            "rs_momentum": [1.0, -1.0], "acceleration": [1.0, -1.0],
            "ma50_slope": [1.0, -1.0], "obv_slope": [1.0, -1.0],
            "return_1m": [0.0, 0.0], "breadth_above_50dma": [0.0, 0.0],
        },
        index=idx,
    )
    sentiment = pd.Series({"US|Technology": -5.0, "US|Energy": 5.0})  # would flip order if blended

    out = score_all(signals, sentiment_score=sentiment, blend_sentiment=False)

    # sentiment_score column is populated (not NaN)
    assert out.loc["US|Technology", "sentiment_score"] == -5.0
    assert out.loc["US|Energy", "sentiment_score"] == 5.0
    # composite equals data_score exactly (pure data, sentiment NOT blended)
    pd.testing.assert_series_equal(
        out["composite"], out["data_score"], check_names=False
    )
    # Technology (higher data) still ranks 1 despite negative sentiment
    assert out.loc["US|Technology", "rank"] == 1.0
