# tests/test_pipeline_composite.py
import numpy as np
import pandas as pd
import pytest
from src.pipeline import build_composite_series, build_signals_rows


def _frame(closes, vols=None):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="D")
    data = {"Close": closes}
    if vols is not None:
        data["Volume"] = vols
    return pd.DataFrame(data, index=idx)


def test_composite_equal_weight_rebased_mean():
    # A doubles (100->200 rebased), B flat (100->100). Mean ends at 150.
    prices = {
        "A": _frame([10.0, 20.0], [100, 100]),
        "B": _frame([50.0, 50.0], [200, 200]),
    }
    out = build_composite_series(["A", "B"], prices)
    assert list(out["Close"]) == pytest.approx([100.0, 150.0])
    assert list(out["Volume"]) == [300, 300]          # summed volumes


def test_composite_drops_missing_component_and_blends_rest():
    prices = {"A": _frame([10.0, 11.0])}              # B absent
    out = build_composite_series(["A", "B"], prices)
    assert list(out["Close"]) == pytest.approx([100.0, 110.0])  # just A, rebased
    assert "Volume" not in out.columns                # no component had Volume


def test_composite_all_missing_returns_none():
    assert build_composite_series(["X", "Y"], {}) is None


def test_build_signals_rows_single_element_list_matches_string():
    # Single-element list must behave exactly like the bare-string path.
    idx = pd.date_range("2026-01-01", periods=300, freq="D")
    close = pd.Series(np.linspace(100, 130, 300), index=idx)
    bench = pd.Series(np.linspace(100, 120, 300), index=idx)
    prices = {
        "EXV3.DE": pd.DataFrame({"Close": close, "Volume": 1000}),
        "EXSA.DE": pd.DataFrame({"Close": bench, "Volume": 1000}),
    }
    u_str = {"eu_sectors": {"Technology": "EXV3.DE"}, "us_sectors": {},
             "us_benchmark": "EXSA.DE", "eu_benchmark": "EXSA.DE"}
    u_list = {"eu_sectors": {"Technology": ["EXV3.DE"]}, "us_sectors": {},
              "us_benchmark": "EXSA.DE", "eu_benchmark": "EXSA.DE"}
    r_str = build_signals_rows(u_str, prices)
    r_list = build_signals_rows(u_list, prices)

    assert len(r_str) == len(r_list), (
        f"Row count mismatch: str={len(r_str)}, list={len(r_list)}"
    )
    for i, (da, db) in enumerate(zip(r_str, r_list)):
        assert set(da.keys()) == set(db.keys()), (
            f"Row {i} key mismatch: str={set(da.keys())}, list={set(db.keys())}"
        )
        for k in da:
            va, vb = da[k], db[k]
            if isinstance(va, str):
                assert va == vb, f"Row {i}[{k!r}]: {va!r} != {vb!r}"
            else:
                assert va == pytest.approx(vb, nan_ok=True), (
                    f"Row {i}[{k!r}]: {va} != {vb}"
                )
