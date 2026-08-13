"""Drive the existing scoring pipeline as-of a historical date, per region."""
from __future__ import annotations

import pandas as pd

from src.pipeline import SIGNAL_COLUMNS, build_theme_signals_rows
from src.scoring import score_all


def truncate_prices(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in prices.items():
        sliced = df[df.index <= as_of]
        if not sliced.empty:
            out[ticker] = sliced
    return out


# Rebalance cadences, mapped to the pandas period they group by. The "every
# n-th" entry is applied after grouping: 2W is every second week-end, 2M every
# second month-end. There is no pandas period for those, and taking every second
# element of the finer calendar is exactly what a fortnightly rebalance means.
REBALANCE_FREQS: dict[str, tuple[str, int]] = {
    "W":  ("W", 1),
    "2W": ("W", 2),
    "M":  ("M", 1),
    "2M": ("M", 2),
    "Q":  ("Q", 1),
}


def rebalance_dates(index: pd.DatetimeIndex, freq: str = "M",
                    since: pd.Timestamp | str | None = None) -> list[pd.Timestamp]:
    """Last trading day of each period in `index`, for the given cadence.

    freq is one of REBALANCE_FREQS ("W", "2W", "M", "2M", "Q"). The default "M"
    reproduces month_end_dates exactly, which is the regression gate for adding
    cadence at all.

    `since` bounds the returned calendar without bounding the price history it
    was derived from. This is the difference between "evaluate from 2008" and
    "have no data before 2008": signals with a trailing window (above_200dma
    needs 200 bars) return NaN until their lookback fills, so evaluating from
    the first available bar scores the opening months on a degraded signal set.
    Callers should fetch full history and pass `since`, never truncate the
    fetch.

    Filtering happens AFTER the period grouping on purpose. Multi-period
    cadences ("2M") take every Nth period end counting from the start of
    `index`, so slicing the index first would shift which months are review
    months — the caller would silently evaluate a different calendar than the
    one the presets were picked on.
    """
    if freq not in REBALANCE_FREQS:
        raise ValueError(
            f"unknown rebalance freq {freq!r}; expected one of "
            f"{sorted(REBALANCE_FREQS)}"
        )
    if len(index) == 0:
        return []
    period, step = REBALANCE_FREQS[freq]
    s = pd.Series(index, index=index)
    # group by period, take the max (last) trading day in each
    last_per_period = s.groupby(index.to_period(period)).max()
    dates = [pd.Timestamp(d) for d in last_per_period.tolist()]
    dates = dates[::step]
    if since is not None:
        cutoff = pd.Timestamp(since)
        dates = [d for d in dates if d >= cutoff]
    return dates


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Backwards-compatible alias for the monthly cadence."""
    return rebalance_dates(index, "M")


def score_themes_as_of(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
) -> pd.DataFrame | None:
    truncated = truncate_prices(prices, as_of)
    rows = build_theme_signals_rows(themes_cfg, truncated)
    if not rows:
        return None
    wide = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
    scored = score_all(wide, weights_path="config/weights.yaml",
                       sentiment_score=None, blend_sentiment=False)
    return scored
