import numpy as np
import pandas as pd
from src.backtest.rotations import event_study


def _ramp(n, start, step):
    idx = pd.bdate_range("2018-01-01", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame({"Close": close, "Open": close, "High": close,
                         "Low": close, "Volume": pd.Series(1_000_000, index=idx)})


def _universe():
    return {
        "us_sectors": {"Technology": "XLK", "Energy": "XLE", "Health Care": "XLV"},
        "eu_sectors": {}, "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE",
    }


def _prices():
    n = 500
    return {"XLK": _ramp(n, 100, 0.9), "XLE": _ramp(n, 100, 0.2),
            "XLV": _ramp(n, 100, 0.5), "RSP": _ramp(n, 100, 0.4)}


def test_event_study_produces_rank_and_indexed_price():
    rots = [{"name": "Tech run", "region": "US", "gics_sector": "Technology",
             "start": "2019-01-01", "end": "2019-09-30"}]
    out = event_study(_universe(), _prices(), rots)
    assert len(out) == 1
    e = out[0]
    assert e["sector"] == "Technology" and e["ticker"] == "XLK"
    assert len(e["dates"]) >= 2
    assert e["price_indexed"][0] == 100.0
    assert len(e["rank"]) == len(e["dates"])


def test_event_study_skips_unknown_sector():
    rots = [{"name": "Bogus", "region": "US", "gics_sector": "Nonexistent",
             "start": "2019-01-01", "end": "2019-09-30"}]
    assert event_study(_universe(), _prices(), rots) == []
