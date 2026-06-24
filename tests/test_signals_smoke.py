"""Smoke tests for signal calculators."""
import numpy as np
import pandas as pd

dates = pd.date_range("2023-01-01", periods=300, freq="B")
sector = pd.Series(100 * (1 + np.random.randn(300) * 0.01).cumprod(), index=dates)
bench  = pd.Series(100 * (1 + np.random.randn(300) * 0.01).cumprod(), index=dates)
volume = pd.Series(np.random.randint(1_000_000, 10_000_000, 300), index=dates).astype(float)

from src.signals.relative_strength import compute_rs, compute_rs_slope, compute_rrg, latest_rrg
from src.signals.momentum import compute_returns, compute_acceleration
from src.signals.technical import compute_ma_structure, compute_obv

rs = compute_rs(sector, bench)
assert isinstance(rs, pd.Series), "compute_rs must return Series"

slope = compute_rs_slope(rs)
assert isinstance(slope, pd.Series)

rrg = compute_rrg(sector, bench)
assert set(rrg.columns) == {'rs_ratio', 'rs_momentum'}, f"got {rrg.columns.tolist()}"

latest = latest_rrg(sector, bench)
assert 'rs_ratio' in latest and 'rs_momentum' in latest

ret = compute_returns(sector)
assert set(ret.keys()) == {'1m', '3m', '6m'}

acc = compute_acceleration(sector)
assert isinstance(acc, float)

ma = compute_ma_structure(sector)
assert {'above_50dma', 'above_200dma', 'ma50_slope'} == set(ma.keys())

obv = compute_obv(sector, volume)
assert 'obv_slope' in obv

print("All signal smoke tests passed")
