"""
Sentiment signal calculator.

Combines Reddit mention velocity, Google Trends search momentum, and
Finnhub news headline sentiment (scored with VADER) into a single
sentiment_score per sector.

Missing sources produce NaN for that signal. A sector with all NaN
signals gets 0.0 (neutral) so the data pillar carries full weight.
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def _cross_zscore(values: dict[str, float]) -> dict[str, float]:
    """Z-score a dict of {key: float}. NaN inputs excluded from mean/std."""
    valid = {k: v for k, v in values.items() if not math.isnan(v)}
    if len(valid) < 2:
        return {k: 0.0 if not math.isnan(v) else float("nan") for k, v in values.items()}
    arr = list(valid.values())
    mean = sum(arr) / len(arr)
    std = (sum((x - mean) ** 2 for x in arr) / (len(arr) - 1)) ** 0.5
    if std == 0.0:
        return {k: 0.0 for k in values}
    return {
        k: (v - mean) / std if not math.isnan(v) else float("nan")
        for k, v in values.items()
    }


def _mention_velocity(
    reddit_data: dict[str, dict] | None,
    sectors: list[str],
) -> dict[str, float]:
    """velocity = (7d_count/7) / (30d_count/30 + 1), cross-sectional z-score."""
    raw: dict[str, float] = {}
    for s in sectors:
        if reddit_data is None or s not in reddit_data:
            raw[s] = float("nan")
        else:
            d = reddit_data[s]
            daily_7d = d.get("7d", 0) / 7.0
            daily_30d = d.get("30d", 0) / 30.0
            raw[s] = daily_7d / (daily_30d + 1.0)
    return _cross_zscore(raw)


def _search_momentum(
    trends_data: dict[str, pd.Series] | None,
    sectors: list[str],
) -> dict[str, float]:
    """Linear regression slope of 13-week interest series, cross-sectional z-score."""
    raw: dict[str, float] = {}
    for s in sectors:
        if trends_data is None or s not in trends_data:
            raw[s] = float("nan")
        else:
            series = trends_data[s].dropna()
            if len(series) < 3:
                raw[s] = float("nan")
                continue
            x = np.arange(len(series))
            slope, _ = np.polyfit(x, series.values.astype(float), 1)
            raw[s] = float(slope)
    return _cross_zscore(raw)


def _news_sentiment(
    finnhub_data: dict[str, list[str]] | None,
    us_sectors: dict[str, str],
    sector_keys: list[str],
) -> dict[str, float]:
    """
    Average VADER compound score across all headlines for each US sector.
    EU sectors → NaN (no Finnhub coverage on free tier).
    US scores are cross-sectional z-scored before returning.
    """
    if finnhub_data is None:
        return {key: float("nan") for key in sector_keys}

    try:
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
        sia = SentimentIntensityAnalyzer()
    except ImportError:
        logger.warning("vaderSentiment not installed — news sentiment unavailable")
        return {key: float("nan") for key in sector_keys}

    us_raw: dict[str, float] = {}
    for sector in us_sectors:
        headlines = finnhub_data.get(sector, [])
        if not headlines:
            us_raw[sector] = float("nan")
        else:
            scores = [sia.polarity_scores(h)["compound"] for h in headlines]
            us_raw[sector] = sum(scores) / len(scores)

    us_zscored = _cross_zscore(us_raw)

    result: dict[str, float] = {}
    for key in sector_keys:
        region, sector = key.split("|", 1)
        if region == "US":
            result[key] = us_zscored.get(sector, float("nan"))
        else:
            result[key] = float("nan")
    return result


def compute_sentiment_score(
    reddit_data: dict[str, dict] | None,
    trends_data: dict[str, pd.Series] | None,
    finnhub_data: dict[str, list[str]] | None,
    sector_keys: list[str],
    us_sectors: dict[str, str],
    eu_sectors: dict[str, str],
) -> pd.Series:
    """
    Combine three sentiment signals into one score per sector.

    Args:
        sector_keys: ["US|Technology", "EU|Technology", ...]
        us_sectors:  {"Technology": "XLK", ...}
        eu_sectors:  {"Technology": "EXV3.DE", ...}

    Returns pd.Series indexed by sector_key. All-NaN sector → 0.0.
    """
    unique_sectors = list({key.split("|", 1)[1] for key in sector_keys})

    velocity = _mention_velocity(reddit_data, unique_sectors)
    momentum = _search_momentum(trends_data, unique_sectors)
    news = _news_sentiment(finnhub_data, us_sectors, sector_keys)

    scores: dict[str, float] = {}
    for key in sector_keys:
        sector = key.split("|", 1)[1]
        signals = [
            velocity.get(sector, float("nan")),
            momentum.get(sector, float("nan")),
            news.get(key, float("nan")),
        ]
        valid = [s for s in signals if not math.isnan(s)]
        scores[key] = sum(valid) / len(valid) if valid else 0.0

    return pd.Series(scores)
