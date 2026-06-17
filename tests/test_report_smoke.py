import sys, os, tempfile, pandas as pd
sys.path.insert(0, '.')

from src.report import build_ranked_table, build_movers, build_swedish_overlay, write_report

scores = pd.DataFrame({
    'region': ['US', 'US', 'EU', 'EU'],
    'gics_sector': ['Technology', 'Energy', 'Financials', 'Industrials'],
    'composite': [0.8, 0.5, 0.3, -0.2],
    'level_score': [0.7, 0.4, 0.2, -0.3],
    'change_score': [0.9, 0.6, 0.4, -0.1],
    'data_score': [0.8, 0.5, 0.3, -0.2],
    'rank': [1.0, 2.0, 3.0, 4.0],
    'delta_composite': [0.1, -0.05, 0.2, -0.1],
    'delta_rank': [1, 0, 2, -1],
    'emerging_flag': [False, False, True, False],
})

table = build_ranked_table(scores)
assert '| Rank |' in table, "table must have header"
assert 'Technology' in table
assert '🌱' in table, "emerging sector must show 🌱"

movers_str = build_movers(scores)
assert 'Climbers' in movers_str

swedish_str = build_swedish_overlay(scores, top_n=3)
assert isinstance(swedish_str, str)

with tempfile.TemporaryDirectory() as tmpdir:
    path = write_report('2024-01-15', table, movers_str, swedish_str, output_dir=tmpdir)
    assert os.path.exists(path)
    content = open(path).read()
    assert 'Sector Momentum Report' in content
    assert 'not investment advice' in content

print("Report smoke tests passed")
