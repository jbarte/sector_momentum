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
