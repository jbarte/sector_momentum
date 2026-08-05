"""Smoke tests for scan.py utility functions (no network calls)."""

import math
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scan import (
    SIGNAL_COLUMNS,
    _build_long_signals_df,
    _build_scored_df_for_db,
    _parse_args,
)


def _make_rows(n: int = 4) -> list[dict]:
    """Build n fake signal rows (2 US + 2 EU)."""
    data = [
        ("US", "Technology", "US|Technology"),
        ("US", "Financials", "US|Financials"),
        ("EU", "Technology", "EU|Technology"),
        ("EU", "Financials", "EU|Financials"),
    ]
    rows = []
    for i, (region, sector, key) in enumerate(data[:n]):
        row = {"region": region, "gics_sector": sector, "sector_key": key}
        for col in SIGNAL_COLUMNS:
            row[col] = float(i)
        rows.append(row)
    return rows


def test_build_long_signals_df_shape():
    rows = _make_rows(4)
    long_df = _build_long_signals_df(rows)
    assert set(long_df.columns) == {"region", "gics_sector", "signal_name", "raw_value", "z_value"}
    assert len(long_df) == 4 * len(SIGNAL_COLUMNS)


def test_build_long_signals_df_z_value_nan():
    rows = _make_rows(2)
    long_df = _build_long_signals_df(rows)
    assert long_df["z_value"].isna().all()


def test_build_long_signals_df_empty():
    long_df = _build_long_signals_df([])
    assert long_df.empty
    assert "signal_name" in long_df.columns


def test_build_scored_df_for_db_columns():
    scored = pd.DataFrame(
        {
            "level_score": [0.5, 0.3],
            "change_score": [0.2, 0.4],
            "data_score": [0.35, 0.35],
            "sentiment_score": [float("nan"), float("nan")],
            "composite": [0.35, 0.35],
            "rank": [1.0, 2.0],
        },
        index=["US|Technology", "EU|Financials"],
    )
    df = _build_scored_df_for_db(scored)
    assert "region" in df.columns
    assert "gics_sector" in df.columns
    assert list(df["region"]) == ["US", "EU"]
    assert list(df["gics_sector"]) == ["Technology", "Financials"]


def test_parse_args_defaults(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scan.py"])
    args = _parse_args()
    assert args.dry_run is False
    assert args.no_dashboard is False


def test_parse_args_flags(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run", "--no-dashboard"])
    args = _parse_args()
    assert args.dry_run is True
    assert args.no_dashboard is True


# ---------------------------------------------------------------------------
# Helpers for run() integration tests
# ---------------------------------------------------------------------------

def _make_minimal_themes_cfg():
    return {"benchmark": "SPY", "themes": {"Space": {"ticker": "UFO"}}}


def _make_minimal_scan_cfg():
    return {"price_lookback_days": 252}


def _make_minimal_prices():
    import pandas as pd
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    df = pd.DataFrame({"Close": [100.0] * 300, "Volume": [1_000_000] * 300}, index=idx)
    return {"SPY": df, "UFO": df}


def _make_minimal_scored():
    import pandas as pd
    return pd.DataFrame(
        {
            "level_score": [0.5],
            "change_score": [0.2],
            "data_score": [0.35],
            "sentiment_score": [0.0],
            "composite": [0.35],
            "rank": [1.0],
        },
        index=["THEME|Space"],
    )


def _run_minimal_scan(monkeypatch, extra_argv=None):
    """
    Invoke scan.run() with all external dependencies stubbed out.
    Returns the exit code (or None if run() returns None).
    extra_argv is appended to sys.argv after 'scan.py'.
    """
    import sys
    import pandas as pd
    import scan

    argv = ["scan.py", "--no-finbert"] + (extra_argv or [])
    monkeypatch.setattr(sys, "argv", argv)

    themes_cfg = _make_minimal_themes_cfg()
    prices = _make_minimal_prices()
    scored = _make_minimal_scored()

    # Long signals df stub
    long_signals = pd.DataFrame(columns=["region", "gics_sector", "signal_name", "raw_value", "z_value"])

    # scored_with_deltas: needs region + gics_sector + composite + rank
    scored_with_deltas = pd.DataFrame({
        "region": ["THEME"],
        "gics_sector": ["Space"],
        "composite": [0.35],
        "rank": [1.0],
        "level_score": [0.5],
        "change_score": [0.2],
        "data_score": [0.35],
        "sentiment_score": [0.0],
    })

    from unittest.mock import MagicMock
    fake_conn = MagicMock()

    monkeypatch.setattr("scan.fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr("scan._load_config", lambda path: (
        _make_minimal_themes_cfg() if "themes" in path
        else _make_minimal_scan_cfg() if "universe" in path
        else {}))
    monkeypatch.setattr("scan.build_theme_signals_rows", lambda *a, **k: [
        {"region": "THEME", "gics_sector": "Space", "sector_key": "THEME|Space",
         **{c: 1.0 for c in scan.SIGNAL_COLUMNS}},
    ])

    # Patch inside run()'s local imports by replacing the module attributes after import
    import src.data.prices as _prices_mod
    import src.scoring as _scoring_mod
    import src.state as _state_mod
    import src.report as _report_mod

    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr(_scoring_mod, "score_all", lambda *a, **k: scored)
    def _fake_zscore(wide_df, *a, **k):
        z = pd.DataFrame(
            {col: [0.0] for col in scan.SIGNAL_COLUMNS},
            index=pd.Index(["THEME|Space"], name="sector_key"),
        )
        return z
    monkeypatch.setattr(_scoring_mod, "zscore_cross_section", _fake_zscore)
    monkeypatch.setattr(_state_mod, "init_db", lambda: fake_conn)
    monkeypatch.setattr(_state_mod, "save_scan", lambda *a, **k: 42)
    monkeypatch.setattr(_state_mod, "load_last_scan", lambda *a, **k: None)
    monkeypatch.setattr(_state_mod, "compute_deltas", lambda *a, **k: scored_with_deltas)
    monkeypatch.setattr(_report_mod, "build_ranked_table", lambda *a, **k: scored_with_deltas)
    monkeypatch.setattr(_report_mod, "build_movers", lambda *a, **k: {})
    monkeypatch.setattr(_report_mod, "write_report", lambda *a, **k: "/tmp/report.html")

    # Stub out dashboard build
    monkeypatch.setattr("scan.os.path.exists", lambda p: False)

    args = scan._parse_args()
    return scan.run(args)


# ---------------------------------------------------------------------------
# Backup wiring tests
# ---------------------------------------------------------------------------


def test_backup_called_after_successful_save(monkeypatch, tmp_path):
    """run() invokes backup_to_storage once (pre-run), by default."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_to_storage", lambda conn, *a, **k: calls.append(conn) or "backup-file.sql.gz")
    _run_minimal_scan(monkeypatch)
    assert len(calls) == 1


def test_no_backup_flag_skips_backup(monkeypatch, tmp_path):
    """run() skips backup_to_storage when --no-backup is passed."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_to_storage", lambda conn, *a, **k: calls.append(conn) or "backup-file.sql.gz")
    _run_minimal_scan(monkeypatch, extra_argv=["--no-backup"])
    assert calls == []


def test_backup_failure_is_non_fatal(monkeypatch, tmp_path):
    """A backup_to_storage that raises must not abort the scan."""
    import scan
    def boom(conn, *a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(scan, "backup_to_storage", boom)
    rc = _run_minimal_scan(monkeypatch)
    assert rc in (0, None)  # scan still completes despite backup failure


def test_pre_run_backup_is_called_and_nonfatal(monkeypatch):
    """backup_to_storage runs before save_scan; a failure does not abort the scan."""
    import scan
    calls = []

    def boom(conn, *a, **k):
        calls.append("backup")
        raise RuntimeError("storage down")

    monkeypatch.setattr(scan, "backup_to_storage", boom)
    rc = _run_minimal_scan(monkeypatch)
    # backup was attempted
    assert calls == ["backup"]
    # scan still completed despite the error
    assert rc in (0, None)


def test_pre_run_backup_skipped_with_no_backup_flag(monkeypatch):
    """backup_to_storage is NOT called when --no-backup is passed."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_to_storage", lambda conn, *a, **k: calls.append("backup"))
    _run_minimal_scan(monkeypatch, extra_argv=["--no-backup"])
    assert calls == []


def test_coverage_guard_aborts_on_partial_scan(monkeypatch):
    """run() returns 1 if <80% of expected sectors produce signal rows."""
    import scan
    import src.data.prices as _prices_mod
    import src.scoring as _scoring_mod

    themes_cfg = {"benchmark": "SPY",
                  "themes": {f"Theme{i}": {"ticker": f"T{i}"} for i in range(20)}}
    # Only 3 of 20 expected themes → 15% coverage → should abort
    rows = [
        {"region": "THEME", "gics_sector": f"Theme{i}", "sector_key": f"THEME|Theme{i}",
         **{c: 1.0 for c in scan.SIGNAL_COLUMNS}}
        for i in range(3)
    ]

    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run", "--no-finbert"])
    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: {})
    monkeypatch.setattr(scan, "fetch_prices", lambda *a, **k: {})
    monkeypatch.setattr("scan._load_config", lambda path: (
        themes_cfg if "themes" in path
        else {"price_lookback_days": 252} if "universe" in path else {}))
    monkeypatch.setattr("scan.build_theme_signals_rows", lambda *a, **k: rows)

    args = scan._parse_args()
    rc = scan.run(args)
    assert rc == 1


def test_coverage_guard_passes_at_80_percent(monkeypatch):
    """run() does NOT abort when coverage is exactly 80%."""
    import scan
    import src.data.prices as _prices_mod
    import src.scoring as _scoring_mod
    import src.state as _state_mod
    import src.report as _report_mod
    from unittest.mock import MagicMock

    themes_cfg = {"benchmark": "SPY",
                  "themes": {f"Theme{i}": {"ticker": f"T{i}"} for i in range(10)}}
    # 8 of 10 expected → exactly 80% → should pass
    rows = [
        {"region": "THEME", "gics_sector": f"Theme{i}", "sector_key": f"THEME|Theme{i}",
         **{c: 1.0 for c in scan.SIGNAL_COLUMNS}}
        for i in range(8)
    ]

    monkeypatch.setattr(sys, "argv", ["scan.py", "--dry-run", "--no-finbert"])
    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: {})
    monkeypatch.setattr(scan, "fetch_prices", lambda *a, **k: {})
    monkeypatch.setattr("scan._load_config", lambda path: (
        themes_cfg if "themes" in path
        else {"price_lookback_days": 252} if "universe" in path else {}))
    monkeypatch.setattr("scan.build_theme_signals_rows", lambda *a, **k: rows)

    wide_idx = pd.Index([r["sector_key"] for r in rows], name="sector_key")
    scored = pd.DataFrame(
        {col: [0.0] * len(rows) for col in ["level_score", "change_score", "data_score",
                                              "sentiment_score", "composite", "rank"]},
        index=wide_idx,
    )
    monkeypatch.setattr(_scoring_mod, "score_all", lambda *a, **k: scored)
    monkeypatch.setattr(_scoring_mod, "zscore_cross_section",
                        lambda df: pd.DataFrame({c: [0.0] * len(df) for c in df.columns},
                                                index=df.index))

    monkeypatch.setattr(_state_mod, "init_db", lambda: MagicMock())
    monkeypatch.setattr(_state_mod, "load_last_scan", lambda *a, **k: None)
    scored_with_deltas = pd.DataFrame({
        "region": ["US"] * 4 + ["EU"] * 4,
        "gics_sector": [f"Sector{i}" for i in range(4)] * 2,
        "composite": [0.0] * 8, "rank": list(range(1, 9)),
        "level_score": [0.0] * 8, "change_score": [0.0] * 8,
        "data_score": [0.0] * 8, "sentiment_score": [0.0] * 8,
    })
    monkeypatch.setattr(_state_mod, "compute_deltas", lambda *a, **k: scored_with_deltas)
    monkeypatch.setattr(scan, "backup_to_storage", lambda *a, **k: "backup.zip")

    args = scan._parse_args()
    rc = scan.run(args)
    assert rc == 0


def test_conn_closed_on_exception(monkeypatch):
    """DB connection is closed even when save_scan raises."""
    import scan
    from unittest.mock import MagicMock
    import src.data.prices as _prices_mod
    import src.scoring as _scoring_mod
    import src.state as _state_mod

    prices = _make_minimal_prices()
    scored = _make_minimal_scored()

    fake_conn = MagicMock()

    monkeypatch.setattr(sys, "argv", ["scan.py", "--no-finbert"])
    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr(scan, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr("scan._load_config", lambda path: (
        _make_minimal_themes_cfg() if "themes" in path
        else _make_minimal_scan_cfg() if "universe" in path else {}))
    monkeypatch.setattr("scan.build_theme_signals_rows", lambda *a, **k: [
        {"region": "THEME", "gics_sector": "Space", "sector_key": "THEME|Space",
         **{c: 1.0 for c in scan.SIGNAL_COLUMNS}},
    ])
    monkeypatch.setattr(_scoring_mod, "score_all", lambda *a, **k: scored)
    monkeypatch.setattr(_scoring_mod, "zscore_cross_section",
                        lambda df: pd.DataFrame({c: [0.0] for c in df.columns},
                                                index=pd.Index(["THEME|Space"], name="sector_key")))
    monkeypatch.setattr(_state_mod, "init_db", lambda: fake_conn)
    monkeypatch.setattr(_state_mod, "load_last_scan", lambda *a, **k: None)
    scored_with_deltas = pd.DataFrame({
        "region": ["THEME"], "gics_sector": ["Space"],
        "composite": [0.35], "rank": [1.0],
        "level_score": [0.5], "change_score": [0.2],
        "data_score": [0.35], "sentiment_score": [0.0],
    })
    monkeypatch.setattr(_state_mod, "compute_deltas", lambda *a, **k: scored_with_deltas)

    def boom(*a, **k):
        raise RuntimeError("DB write exploded")
    monkeypatch.setattr(_state_mod, "save_scan", boom)
    monkeypatch.setattr(scan, "backup_to_storage", lambda *a, **k: "backup.zip")

    args = scan._parse_args()
    with pytest.raises(RuntimeError, match="DB write exploded"):
        scan.run(args)

    fake_conn.close.assert_called_once()
