import sys, os, tempfile, datetime, pandas as pd, numpy as np
sys.path.insert(0, '.')

from src.state import init_db, save_scan, load_last_scan, compute_deltas, get_scan_history

# Use a temp DB so we don't pollute data/momentum.db
with tempfile.NamedTemporaryFile(suffix='.db', delete=False) as f:
    db_path = f.name

try:
    conn = init_db(db_path)

    # Build synthetic signals and scores
    sectors = ['Technology', 'Financials', 'Energy']
    regions = ['US', 'EU'] * 3
    regions = regions[:len(sectors)]

    signals_df = pd.DataFrame([
        {'region': 'US', 'gics_sector': s, 'signal_name': 'rs_ratio', 'raw_value': float(i), 'z_value': float(i-1)}
        for i, s in enumerate(sectors)
    ])

    scores_df = pd.DataFrame({
        'region': ['US', 'US', 'US'],
        'gics_sector': sectors,
        'level_score': [0.5, 0.3, -0.2],
        'change_score': [0.4, 0.1, -0.3],
        'data_score': [0.45, 0.2, -0.25],
        'sentiment_score': [float('nan')] * 3,
        'composite': [0.45, 0.2, -0.25],
        'rank': [1.0, 2.0, 3.0],
    })

    scan_id = save_scan(conn, datetime.datetime.utcnow(), signals_df, scores_df)
    assert isinstance(scan_id, int) and scan_id > 0, f"Expected int scan_id, got {scan_id}"

    last = load_last_scan(conn)
    assert last is not None, "load_last_scan returned None after saving"
    assert len(last) == 3, f"Expected 3 rows, got {len(last)}"
    assert 'composite' in last.columns

    # Second scan with slightly different scores
    scores_df2 = scores_df.copy()
    scores_df2['composite'] = [0.5, 0.1, -0.1]
    scores_df2['rank'] = [1.0, 3.0, 2.0]
    save_scan(conn, datetime.datetime.utcnow(), signals_df, scores_df2)

    last2 = load_last_scan(conn)
    deltas = compute_deltas(scores_df2, last)
    assert 'delta_composite' in deltas.columns
    assert 'delta_rank' in deltas.columns
    assert 'emerging_flag' in deltas.columns

    history = get_scan_history(conn, n_scans=5)
    assert len(history) == 6  # 3 sectors × 2 scans

    print("State smoke tests passed")
finally:
    conn.close()
    os.unlink(db_path)
