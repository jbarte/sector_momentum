"""Drive the existing scoring pipeline as-of a historical date, per region."""
from __future__ import annotations

import pandas as pd

from src.pipeline import SIGNAL_COLUMNS, build_signals_rows
from src.scoring import score_all


def truncate_prices(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in prices.items():
        sliced = df[df.index <= as_of]
        if not sliced.empty:
            out[ticker] = sliced
    return out


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    if len(index) == 0:
        return []
    s = pd.Series(index, index=index)
    # group by year-month period, take the max (last) trading day in each
    last_per_month = s.groupby(index.to_period("M")).max()
    return [pd.Timestamp(d) for d in last_per_month.tolist()]


def score_as_of(
    universe: dict,
    prices: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
    region: str,
) -> pd.DataFrame | None:
    truncated = truncate_prices(prices, as_of)
    rows = build_signals_rows(universe, truncated)
    rows = [r for r in rows if r["region"] == region]
    if not rows:
        return None
    wide = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
    scored = score_all(wide, weights_path="config/weights.yaml",
                       sentiment_score=None, blend_sentiment=False)
    return scored
