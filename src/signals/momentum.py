"""
Price momentum signals: multi-horizon returns and acceleration.
"""

import pandas as pd
import numpy as np


def compute_returns(close: pd.Series) -> dict[str, float]:
    """
    Returns {'1m': float, '3m': float, '6m': float} where each value
    is the return from N trading days ago to today (using last available price).

    Approximate trading day counts: 1m=21, 3m=63, 6m=126.
    Returns NaN for a horizon if there aren't enough data points.
    """
    horizons = {"1m": 21, "3m": 63, "6m": 126}
    result: dict[str, float] = {}
    clean = close.dropna()
    n = len(clean)
    last_price = clean.iloc[-1] if n > 0 else float("nan")

    for label, days in horizons.items():
        if n > days:
            past_price = clean.iloc[-(days + 1)]
            result[label] = float((last_price - past_price) / past_price)
        else:
            result[label] = float("nan")

    return result


def compute_acceleration(close: pd.Series) -> float:
    """
    Acceleration = 1M return minus 3M return.
    Positive value means recent momentum exceeds medium-term momentum
    (i.e., the sector is accelerating).
    """
    returns = compute_returns(close)
    r1m = returns["1m"]
    r3m = returns["3m"]
    if np.isnan(r1m) or np.isnan(r3m):
        return float("nan")
    return float(r1m - r3m)
