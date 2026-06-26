import numpy as np
import pandas as pd
from src.pipeline import SIGNAL_COLUMNS, build_signals_rows


def _price_df(n=260, start=100.0, step=0.5):
    idx = pd.bdate_range("2022-01-03", periods=n)
    close = pd.Series(start + step * np.arange(n), index=idx, dtype=float)
    return pd.DataFrame(
        {"Close": close, "Open": close, "High": close, "Low": close,
         "Volume": pd.Series(1_000_000, index=idx)}
    )


def test_build_signals_rows_produces_expected_keys():
    universe = {
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {},
        "us_benchmark": "RSP",
        "eu_benchmark": "EXSA.DE",
    }
    prices = {"XLK": _price_df(), "RSP": _price_df(step=0.3)}
    rows = build_signals_rows(universe, prices)
    assert len(rows) == 1
    row = rows[0]
    assert row["sector_key"] == "US|Technology"
    assert row["region"] == "US"
    for col in SIGNAL_COLUMNS:
        assert col in row
