"""
Technical signals: moving averages and on-balance volume.
"""

import pandas as pd
import numpy as np


def compute_ma_structure(close: pd.Series) -> dict[str, float]:
    """
    Returns:
      'above_50dma'  : (close[-1] - ma50[-1]) / ma50[-1]  — pct distance above/below 50DMA
      'above_200dma' : (close[-1] - ma200[-1]) / ma200[-1] — pct distance above/below 200DMA
      'ma50_slope'   : slope of the 50DMA over the last 10 periods (normalized by ma50[-1])

    Returns NaN for any value that cannot be computed (insufficient data).
    """
    nan = float("nan")
    result = {"above_50dma": nan, "above_200dma": nan, "ma50_slope": nan}

    clean = close.dropna()
    n = len(clean)

    if n < 1:
        return result

    last_price = float(clean.iloc[-1])

    # 50DMA
    if n >= 50:
        ma50 = clean.rolling(50).mean()
        ma50_last = float(ma50.iloc[-1])
        result["above_50dma"] = (last_price - ma50_last) / ma50_last

        # Slope of 50DMA over last 10 periods
        if n >= 59:  # need 50 + 9 extra periods to get 10 MA values
            ma50_tail = ma50.dropna().iloc[-10:]
            if len(ma50_tail) == 10:
                x = np.arange(10)
                slope, _ = np.polyfit(x, ma50_tail.values, 1)
                result["ma50_slope"] = float(slope / ma50_last)

    # 200DMA
    if n >= 200:
        ma200 = clean.rolling(200).mean()
        ma200_last = float(ma200.iloc[-1])
        result["above_200dma"] = (last_price - ma200_last) / ma200_last

    return result


def compute_obv(close: pd.Series, volume: pd.Series) -> dict[str, float]:
    """
    On-balance volume and its trend.

    OBV accumulates volume on up days and subtracts volume on down days.

    Returns:
      'obv_slope': linear regression slope of OBV over the last 20 periods,
                   normalized by mean(abs(OBV)) to make it scale-independent.

    Returns NaN if insufficient data.
    """
    nan = float("nan")
    result = {"obv_slope": nan}

    # Align series on common index
    common_index = close.index.intersection(volume.index)
    if len(common_index) < 21:  # need at least 20 OBV values
        return result

    close_aligned = close.loc[common_index].dropna()
    volume_aligned = volume.loc[close_aligned.index]

    # Guard against NaN in volume
    volume_aligned = volume_aligned.dropna()
    close_aligned = close_aligned.loc[volume_aligned.index]

    price_diff = close_aligned.diff()
    direction = np.sign(price_diff).fillna(0)
    obv = (direction * volume_aligned).cumsum()

    obv_tail = obv.iloc[-20:]
    if len(obv_tail) < 20:
        return result

    # Guard against NaN in obv_tail before polyfit
    if obv_tail.isna().any():
        return result

    mean_abs_obv = float(obv_tail.abs().mean())
    if mean_abs_obv == 0:
        return result

    x = np.arange(20)
    slope, _ = np.polyfit(x, obv_tail.values.astype(float), 1)
    result["obv_slope"] = float(slope / mean_abs_obv)

    return result


def compute_max_drawdown(close: pd.Series, window: int = 252) -> float:
    """Trailing max drawdown over the last ``window`` closes.

    Returns the most negative peak-to-trough decline as a fraction
    (e.g. -0.32 for a 32% drawdown), 0.0 if the window only rose, or NaN
    when there are fewer than 2 usable closes.
    """
    clean = close.dropna()
    if len(clean) < 2:
        return float("nan")
    recent = clean.iloc[-window:]
    running_max = recent.cummax()
    drawdown = recent / running_max - 1.0
    return float(drawdown.min())
