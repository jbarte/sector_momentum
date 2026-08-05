# tests/test_backtest_engine.py
import numpy as np
import pandas as pd

from src.backtest import engine


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE", "Health": "XLV"},
        "eu_sectors": {},
        "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


