"""
Scoring and ranking module.

Takes a DataFrame of raw signals (one row per sector) and produces:
  - z-scored signals (cross-sectional normalization)
  - level_score: how strong the sector is right now
  - change_score: how fast it is improving
  - data_score: weighted combination of level and change
  - composite: final score (Phase 1: same as data_score since sentiment=0)
  - rank: integer rank 1..N (1 = best)

Input DataFrame columns expected:
  rs_ratio, rs_momentum, return_1m, return_3m, return_6m, acceleration,
  above_50dma, above_200dma, ma50_slope, obv_slope, breadth_above_50dma

All weights are read from config/weights.yaml (already exists).
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import rankdata

logger = logging.getLogger(__name__)

# Signals used for each sub-score (equal-weight within group)
_LEVEL_SIGNALS = ["rs_ratio", "return_3m", "return_6m", "above_50dma", "above_200dma"]
_CHANGE_SIGNALS = ["rs_momentum", "acceleration", "ma50_slope", "obv_slope"]


def zscore_cross_section(df: pd.DataFrame) -> pd.DataFrame:
    """
    Z-score each numeric column across rows (cross-sectionally).
    Columns with zero std are filled with 0.0 (not NaN).
    NaN inputs are filled with 0.0 before scoring (missing signal = neutral).
    Returns a new DataFrame with the same shape and index.
    """
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    result = df.copy()

    for col in numeric_cols:
        series = result[col].fillna(0.0)
        std = series.std(ddof=1)
        if std == 0.0 or np.isnan(std):
            result[col] = 0.0
        else:
            result[col] = (series - series.mean()) / std

    # Non-numeric columns are kept as-is
    return result


def compute_level_score(z_df: pd.DataFrame) -> pd.Series:
    """
    Equal-weighted average of level signals (z-scored):
      rs_ratio, return_3m, return_6m, above_50dma, above_200dma

    Only averages over signals that are present (handles missing columns gracefully).
    Returns a Series indexed like z_df.
    """
    present = [c for c in _LEVEL_SIGNALS if c in z_df.columns]
    if not present:
        return pd.Series(0.0, index=z_df.index)
    return z_df[present].mean(axis=1)


def compute_change_score(z_df: pd.DataFrame) -> pd.Series:
    """
    Equal-weighted average of change signals (z-scored):
      rs_momentum, acceleration, ma50_slope, obv_slope

    Only averages over signals that are present.
    Returns a Series indexed like z_df.
    """
    present = [c for c in _CHANGE_SIGNALS if c in z_df.columns]
    if not present:
        return pd.Series(0.0, index=z_df.index)
    return z_df[present].mean(axis=1)


def compute_data_score(
    level: pd.Series,
    change: pd.Series,
    level_weight: float = 0.5,
    change_weight: float = 0.5,
) -> pd.Series:
    """
    Weighted combination of level and change scores.
    data_score = level_weight * level + change_weight * change
    """
    return level_weight * level + change_weight * change


def compute_composite(
    data_score: pd.Series,
    sentiment_score: pd.Series | None = None,
    data_weight: float = 1.0,
    sentiment_weight: float = 0.0,
) -> pd.Series:
    """
    Phase 1: sentiment_score is None/ignored, composite = data_score.
    Phase 2: composite = data_weight * data_score + sentiment_weight * sentiment_score
    Weights should sum to 1.0; function does not enforce this but logs a warning if not.
    """
    total = data_weight + sentiment_weight
    if abs(total - 1.0) > 1e-9:
        logger.warning(
            "Pillar weights do not sum to 1.0 (got %.6f). Results may be mis-scaled.",
            total,
        )

    if sentiment_score is None or sentiment_weight == 0.0:
        return data_weight * data_score

    return data_weight * data_score + sentiment_weight * sentiment_score


def rank_sectors(composite: pd.Series) -> pd.Series:
    """
    Float rank 1..N where 1 = highest composite score.
    Ties broken by average rank (same as scipy.stats.rankdata method='average').
    Returns a Series of float ranks (integer values when no ties, fractional when ties).
    """
    # Negate so that highest composite gets rank 1 after rankdata
    ranks = rankdata(-composite.values, method="average")
    return pd.Series(ranks, index=composite.index)


def score_all(
    signals_df: pd.DataFrame,
    weights_path: str = "config/weights.yaml",
    sentiment_score: pd.Series | None = None,
    blend_sentiment: bool = True,
) -> pd.DataFrame:
    """
    Full pipeline: z-score → level/change → data → composite → rank.

    Returns a DataFrame with columns:
      level_score, change_score, data_score, sentiment_score,
      composite, rank

    sentiment_score: optional Series indexed like signals_df. If provided,
      it is reindexed to match signals_df (missing keys → 0.0) and passed
      to compute_composite with the configured sentiment weight.
    """
    weights_file = Path(weights_path)
    with weights_file.open() as fh:
        cfg = yaml.safe_load(fh)

    level_weight: float = float(cfg["data_pillar"]["level"])
    change_weight: float = float(cfg["data_pillar"]["change"])
    data_weight: float = float(cfg["pillars"]["data"])
    sentiment_weight: float = float(cfg["pillars"]["sentiment"])

    z_df = zscore_cross_section(signals_df)
    level = compute_level_score(z_df)
    change = compute_change_score(z_df)
    data = compute_data_score(level, change, level_weight=level_weight, change_weight=change_weight)

    # Align sentiment_score index to signals_df; fill gaps with 0.0 (neutral)
    if sentiment_score is not None:
        sentiment_score = sentiment_score.reindex(signals_df.index, fill_value=0.0)

    # Canonical composite blends sentiment only when blend_sentiment is True.
    # When False, sentiment is still stored in the output column but the
    # composite/rank stay pure-data.
    composite = compute_composite(
        data,
        sentiment_score=sentiment_score if blend_sentiment else None,
        data_weight=data_weight if blend_sentiment else 1.0,
        sentiment_weight=sentiment_weight if blend_sentiment else 0.0,
    )
    ranks = rank_sectors(composite)

    return pd.DataFrame(
        {
            "level_score": level,
            "change_score": change,
            "data_score": data,
            "sentiment_score": sentiment_score if sentiment_score is not None else np.nan,
            "composite": composite,
            "rank": ranks,
        },
        index=signals_df.index,
    )
