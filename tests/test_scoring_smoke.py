import numpy as np
import pandas as pd
import sys
sys.path.insert(0, '.')

from src.scoring import zscore_cross_section, compute_level_score, compute_change_score, compute_composite, rank_sectors, score_all

# Build a synthetic signals DataFrame (11 sectors)
np.random.seed(42)
n = 11
index = [f"Sector_{i}" for i in range(n)]
df = pd.DataFrame({
    'rs_ratio': np.random.randn(n) + 100,
    'rs_momentum': np.random.randn(n) + 100,
    'return_1m': np.random.randn(n) * 0.05,
    'return_3m': np.random.randn(n) * 0.10,
    'return_6m': np.random.randn(n) * 0.15,
    'acceleration': np.random.randn(n) * 0.02,
    'above_50dma': np.random.randn(n) * 0.05,
    'above_200dma': np.random.randn(n) * 0.08,
    'ma50_slope': np.random.randn(n) * 0.001,
    'obv_slope': np.random.randn(n),
}, index=index)

z = zscore_cross_section(df)
assert z.shape == df.shape, "z-score must preserve shape"
assert abs(z['rs_ratio'].mean()) < 1e-10, "z-score columns should have ~0 mean"

result = score_all(df)
assert set(['level_score', 'change_score', 'data_score', 'composite', 'rank']).issubset(result.columns)
assert result['rank'].min() == 1
assert result['rank'].max() == n
assert result['rank'].nunique() == n  # all ranks distinct (no ties with random data)

print("Scoring smoke tests passed")
print(result[['composite', 'rank']].sort_values('rank'))
