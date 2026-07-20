"""Tests for per-region cohort scoring."""
import numpy as np
import pandas as pd
import pytest

from src.scoring import score_all, zscore_cross_section


US_SECTORS = [
    "Technology", "Health Care", "Financials", "Consumer Discretionary",
    "Communication Services", "Industrials", "Consumer Staples",
    "Energy", "Utilities", "Real Estate", "Materials",
]
EU_SECTORS = [
    "Banks", "Technology", "Health Care", "Industrial Goods & Services",
    "Food Beverage & Tobacco", "Insurance", "Chemicals",
    "Utilities", "Energy", "Basic Resources", "Automobiles & Parts",
    "Construction & Materials", "Personal Care Drug & Grocery",
    "Travel & Leisure",
]

SIGNAL_COLUMNS = [
    "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
    "acceleration", "above_50dma", "above_200dma", "ma50_slope",
    "obv_slope", "breadth_above_50dma",
]


def _make_wide_df(seed=42):
    """25-sector wide DataFrame matching scan.py format."""
    rng = np.random.default_rng(seed)
    keys = [f"US|{s}" for s in US_SECTORS] + [f"EU|{s}" for s in EU_SECTORS]
    data = {col: rng.standard_normal(len(keys)) for col in SIGNAL_COLUMNS}
    return pd.DataFrame(data, index=keys)


def _score_per_region(wide_df, sentiment_score=None):
    """Replicate the per-region scoring logic from scan.py."""
    scored_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        region_df = wide_df[mask]
        if region_df.empty:
            continue
        region_sentiment = sentiment_score[mask] if sentiment_score is not None else None
        region_scored = score_all(
            region_df,
            weights_path="config/weights.yaml",
            sentiment_score=region_sentiment,
            blend_sentiment=False,
        )
        scored_parts.append(region_scored)
    return pd.concat(scored_parts)


def test_per_region_ranks_bounded():
    """US ranks 1-11, EU ranks 1-14."""
    wide_df = _make_wide_df()
    scored = _score_per_region(wide_df)

    us_mask = scored.index.str.startswith("US|")
    eu_mask = scored.index.str.startswith("EU|")

    us_ranks = scored.loc[us_mask, "rank"]
    eu_ranks = scored.loc[eu_mask, "rank"]

    assert us_ranks.min() == 1.0
    assert us_ranks.max() == 11.0
    assert us_ranks.nunique() == 11

    assert eu_ranks.min() == 1.0
    assert eu_ranks.max() == 14.0
    assert eu_ranks.nunique() == 14


def test_two_rank_ones_exist():
    """There must be exactly two sectors with rank 1 (one per region)."""
    wide_df = _make_wide_df()
    scored = _score_per_region(wide_df)
    rank_ones = scored[scored["rank"] == 1.0]
    assert len(rank_ones) == 2
    regions = {k.split("|")[0] for k in rank_ones.index}
    assert regions == {"US", "EU"}


def test_per_region_zscore_isolation():
    """Z-scores within each region should have mean ~ 0."""
    wide_df = _make_wide_df()
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        z = zscore_cross_section(wide_df[mask])
        for col in z.columns:
            assert abs(z[col].mean()) < 1e-10, (
                f"{region_prefix} z-score mean for {col}: {z[col].mean()}"
            )


def test_per_region_z_df_concat():
    """Concatenated per-region z-scores cover all 25 sectors."""
    wide_df = _make_wide_df()
    z_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        if mask.any():
            z_parts.append(zscore_cross_section(wide_df[mask]))
    z_df = pd.concat(z_parts)
    assert len(z_df) == 25
    assert set(z_df.index) == set(wide_df.index)


def test_recompute_scan_produces_per_region_ranks():
    """Backfill recomputation should produce per-region ranks."""
    from scripts.backfill_region_ranks import recompute_scan

    rng = np.random.default_rng(99)
    rows = []
    for region, sectors in [("US", US_SECTORS), ("EU", EU_SECTORS)]:
        for sector in sectors:
            for signal in SIGNAL_COLUMNS:
                rows.append({
                    "region": region,
                    "gics_sector": sector,
                    "signal_name": signal,
                    "raw_value": rng.standard_normal(),
                })
    signals_df = pd.DataFrame(rows)
    scores_df, z_df = recompute_scan(signals_df)

    us_scores = scores_df[scores_df.index.str.startswith("US|")]
    eu_scores = scores_df[scores_df.index.str.startswith("EU|")]

    assert us_scores["rank"].max() == 11.0
    assert eu_scores["rank"].max() == 14.0
    assert len(scores_df) == 25
    assert len(z_df) == 25
