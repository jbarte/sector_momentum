import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.backup import load_tables, dump_tables


class _FakeCursor:
    def __init__(self, count_result):
        self.count_result = count_result
        self.executed = []          # list of (sql, params)
        self.executemany_calls = [] # list of (sql, rows)
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return (self.count_result,)
    def executemany(self, sql, rows):
        self.executemany_calls.append((sql, list(rows)))
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, count_result=0):
        self._cur = _FakeCursor(count_result)
    def cursor(self):
        return self._cur
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _tables():
    return {
        "scans": pd.DataFrame({"scan_id": [1], "run_at": ["2026-06-01"], "config_hash": ["abc"]}),
        "scores": pd.DataFrame({"scan_id": [1], "region": ["US"], "gics_sector": ["Technology"],
                                "level_score": [0.5], "change_score": [0.4], "data_score": [0.45],
                                "sentiment_score": [np.nan], "composite": [0.45], "rank": [1.0]}),
        "signals": pd.DataFrame({"scan_id": [1], "region": ["US"], "gics_sector": ["Technology"],
                                 "signal_name": ["rs_ratio"], "raw_value": [103.4], "z_value": [0.8]}),
    }


def test_load_refuses_nonempty_db_without_force():
    conn = _FakeConn(count_result=5)  # DB has rows
    with pytest.raises(RuntimeError, match="not empty"):
        load_tables(conn, _tables(), force=False)


def test_load_into_empty_db_inserts_and_resets_sequence():
    conn = _FakeConn(count_result=0)
    counts = load_tables(conn, _tables(), force=False)
    assert counts["scans"] == 1
    assert counts["signals"] == 1
    assert counts["scores"] == 1
    cur = conn._cur
    # NaN sentiment_score was converted to None (NULL)
    scores_rows = [rows for (sql, rows) in cur.executemany_calls if "scores" in sql][0]
    assert scores_rows[0][6] is None  # sentiment_score position
    # sequence reset emitted
    assert any("setval" in sql for sql, _ in cur.executed)


def test_load_force_deletes_children_before_scans():
    conn = _FakeConn(count_result=9)
    load_tables(conn, _tables(), force=True)
    deletes = [sql for sql, _ in conn._cur.executed if sql.strip().startswith("DELETE")]
    table_order = []
    for d in deletes:
        for t in ("scans", "signals", "scores", "sentiment_signals", "theme_scores", "theme_signals"):
            if t in d and t not in table_order:
                table_order.append(t)
    assert table_order.index("scans") == len(table_order) - 1, (
        f"scans must be deleted last (FK safety), but order was: {table_order}"
    )


def test_dump_tables_queries_all_tables(monkeypatch):
    seen = []
    def fake_read_sql(sql, conn):
        seen.append(sql)
        return pd.DataFrame()
    monkeypatch.setattr("src.backup.pd.read_sql_query", fake_read_sql)
    dump_tables(object())
    from src.backup import _COLUMNS
    for table in _COLUMNS:
        assert any(f"FROM {table}" in s for s in seen), f"dump_tables did not query {table}"
