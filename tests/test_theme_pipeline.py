import numpy as np
import pandas as pd
from src.pipeline import build_theme_signals_rows, SIGNAL_COLUMNS


def _ramp_prices(n=260, start=100.0, step=0.5):
    idx = pd.date_range("2025-01-01", periods=n, freq="B")
    close = pd.Series([start + step * i for i in range(n)], index=idx)
    return pd.DataFrame({"Close": close, "Volume": [1_000_000] * n}, index=idx)


def test_build_theme_rows_shape_and_keys():
    cfg = {"benchmark": "ACWI", "themes": {
        "Space": {"ticker": "UFO", "gdelt_keywords": ["space launch", "satellite"]},
        "Semiconductors": {"ticker": "SOXX", "gdelt_keywords": ["semiconductor", "chip maker"]},
    }}
    prices = {"UFO": _ramp_prices(), "SOXX": _ramp_prices(step=0.7), "ACWI": _ramp_prices(step=0.2)}
    rows = build_theme_signals_rows(cfg, prices)
    assert len(rows) == 2
    r = next(r for r in rows if r["gics_sector"] == "Space")
    assert r["region"] == "THEME"
    assert r["sector_key"] == "THEME|Space"
    assert set(SIGNAL_COLUMNS).issubset(r.keys())
    assert np.isnan(r["breadth_above_50dma"])          # breadth N/A for themes
    assert not np.isnan(r["rs_ratio"])                 # RS computed vs ACWI


def test_build_theme_rows_skips_missing_etf():
    cfg = {"benchmark": "ACWI", "themes": {
        "Space": {"ticker": "UFO", "gdelt_keywords": ["space launch"]},
        "Ghost": {"ticker": "ZZZZ", "gdelt_keywords": ["ghost"]},
    }}
    prices = {"UFO": _ramp_prices(), "ACWI": _ramp_prices(step=0.2)}
    rows = build_theme_signals_rows(cfg, prices)
    assert [r["gics_sector"] for r in rows] == ["Space"]   # ZZZZ (no data) skipped


def test_build_theme_rows_benchmark_fallback_to_spy():
    cfg = {"benchmark": "ACWI", "themes": {
        "Space": {"ticker": "UFO", "gdelt_keywords": ["space launch"]},
    }}
    prices = {"UFO": _ramp_prices(), "SPY": _ramp_prices(step=0.2)}   # no ACWI
    rows = build_theme_signals_rows(cfg, prices)
    assert len(rows) == 1
    assert not np.isnan(rows[0]["rs_ratio"])           # RS computed vs SPY fallback
