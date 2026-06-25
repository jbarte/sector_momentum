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


def test_breadth_injection_is_non_fatal_and_eu_is_nan():
    """If constituent fetch returns None, breadth stays NaN and rows still build;
    a helper injects true breadth for US and NaN for EU."""
    import math
    from unittest.mock import patch
    from scan import _inject_constituent_breadth

    rows = [
        {"region": "US", "gics_sector": "Technology", "sector_key": "US|Technology",
         "breadth_above_50dma": 1.0},
        {"region": "EU", "gics_sector": "Technology", "sector_key": "EU|Technology",
         "breadth_above_50dma": 1.0},
    ]
    # Constituent fetch fails → all breadth NaN, no exception raised.
    with patch("scan.fetch_sp500_constituents", return_value=None):
        _inject_constituent_breadth(rows, start="2026-01-01", end="2026-06-01")
    assert math.isnan(rows[0]["breadth_above_50dma"])
    assert math.isnan(rows[1]["breadth_above_50dma"])


def test_breadth_injection_sets_us_value_and_eu_nan():
    import math
    from unittest.mock import patch
    from scan import _inject_constituent_breadth

    rows = [
        {"region": "US", "gics_sector": "Technology", "sector_key": "US|Technology",
         "breadth_above_50dma": float("nan")},
        {"region": "EU", "gics_sector": "Technology", "sector_key": "EU|Technology",
         "breadth_above_50dma": float("nan")},
    ]
    with patch("scan.fetch_sp500_constituents", return_value={"Technology": ["A", "B"]}), \
         patch("scan.fetch_prices", return_value={}), \
         patch("scan.compute_constituent_breadth", return_value={"US|Technology": 0.66}):
        _inject_constituent_breadth(rows, start="2026-01-01", end="2026-06-01")
    assert rows[0]["breadth_above_50dma"] == 0.66      # US injected
    assert math.isnan(rows[1]["breadth_above_50dma"])  # EU forced NaN


# ---------------------------------------------------------------------------
# Helpers for run() integration tests
# ---------------------------------------------------------------------------

def _make_minimal_universe():
    return {
        "us_benchmark": "SPY",
        "eu_benchmark": "SXXP",
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {"Technology": "EXV3.DE"},
        "price_lookback_days": 252,
    }


def _make_minimal_prices():
    import pandas as pd
    idx = pd.date_range("2025-01-01", periods=300, freq="B")
    df = pd.DataFrame({"Close": [100.0] * 300, "Volume": [1_000_000] * 300}, index=idx)
    return {"SPY": df, "SXXP": df, "XLK": df, "EXV3.DE": df}


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
        index=["US|Technology"],
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

    argv = ["scan.py"] + (extra_argv or [])
    monkeypatch.setattr(sys, "argv", argv)

    universe = _make_minimal_universe()
    prices = _make_minimal_prices()
    scored = _make_minimal_scored()

    # Long signals df stub
    long_signals = pd.DataFrame(columns=["region", "gics_sector", "signal_name", "raw_value", "z_value"])

    # scored_with_deltas: needs region + gics_sector + composite + rank
    scored_with_deltas = pd.DataFrame({
        "region": ["US"],
        "gics_sector": ["Technology"],
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
    monkeypatch.setattr("scan.fetch_sp500_constituents", lambda: None)
    monkeypatch.setattr("scan.compute_constituent_breadth", lambda *a, **k: {})

    # Patch inside run()'s local imports by replacing the module attributes after import
    import src.data.prices as _prices_mod
    import src.scoring as _scoring_mod
    import src.data.trends as _trends_mod
    import src.state as _state_mod
    import src.report as _report_mod

    monkeypatch.setattr(_prices_mod, "fetch_prices", lambda *a, **k: prices)
    monkeypatch.setattr(_prices_mod, "load_universe", lambda *a, **k: universe)
    monkeypatch.setattr(_scoring_mod, "score_all", lambda *a, **k: scored)
    def _fake_zscore(wide_df, *a, **k):
        z = pd.DataFrame(
            {col: [0.0] for col in scan.SIGNAL_COLUMNS},
            index=pd.Index(["US|Technology"], name="sector_key"),
        )
        return z
    monkeypatch.setattr(_scoring_mod, "zscore_cross_section", _fake_zscore)
    monkeypatch.setattr(_trends_mod, "fetch_trends", lambda *a, **k: {})
    monkeypatch.setattr(_state_mod, "init_db", lambda: fake_conn)
    monkeypatch.setattr(_state_mod, "save_scan", lambda *a, **k: 42)
    monkeypatch.setattr(_state_mod, "load_last_scan", lambda *a, **k: None)
    monkeypatch.setattr(_state_mod, "compute_deltas", lambda *a, **k: scored_with_deltas)
    monkeypatch.setattr(_report_mod, "build_ranked_table", lambda *a, **k: scored_with_deltas)
    monkeypatch.setattr(_report_mod, "build_movers", lambda *a, **k: {})
    monkeypatch.setattr(_report_mod, "build_swedish_overlay", lambda *a, **k: {})
    monkeypatch.setattr(_report_mod, "write_report", lambda *a, **k: "/tmp/report.html")

    # Also patch sentiment helper to avoid opening config file
    monkeypatch.setattr("scan._compute_sentiment_for_scan", lambda *a, **k: pd.Series({"US|Technology": 0.0, "EU|Technology": 0.0}))

    # Also patch open() for sentiment_keywords.yaml by patching yaml.safe_load via the trends call above
    import yaml as _yaml
    import builtins
    original_open = builtins.open

    def fake_open(path, *a, **k):
        if isinstance(path, str) and "sentiment_keywords" in path:
            import io
            return io.StringIO("{}")
        return original_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", fake_open)

    # Stub out dashboard build
    monkeypatch.setattr("scan.os.path.exists", lambda p: False)

    args = scan._parse_args()
    return scan.run(args)


# ---------------------------------------------------------------------------
# Backup wiring tests
# ---------------------------------------------------------------------------


def test_backup_called_after_successful_save(monkeypatch, tmp_path):
    """run() invokes backup_database once after save_scan, by default."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_database", lambda conn, *a, **k: calls.append(conn) or tmp_path)
    _run_minimal_scan(monkeypatch)
    assert len(calls) == 1


def test_no_backup_flag_skips_backup(monkeypatch, tmp_path):
    """run() skips backup_database when --no-backup is passed."""
    import scan
    calls = []
    monkeypatch.setattr(scan, "backup_database", lambda conn, *a, **k: calls.append(conn) or tmp_path)
    _run_minimal_scan(monkeypatch, extra_argv=["--no-backup"])
    assert calls == []


def test_backup_failure_is_non_fatal(monkeypatch, tmp_path):
    """A backup_database that raises must not abort the scan."""
    import scan
    def boom(conn, *a, **k):
        raise RuntimeError("disk full")
    monkeypatch.setattr(scan, "backup_database", boom)
    rc = _run_minimal_scan(monkeypatch)
    assert rc in (0, None)  # scan still completes despite backup failure


def test_compute_sentiment_for_scan_trends_only_returns_series():
    """scan.py's sentiment helper returns a per-sector Series from Trends only."""
    import pandas as pd
    from scan import _compute_sentiment_for_scan

    keywords = {"Technology": ["AI"], "Energy": ["oil"]}
    sector_keys = ["US|Technology", "US|Energy", "EU|Technology", "EU|Energy"]
    us_sectors = {"Technology": "XLK", "Energy": "XLE"}
    eu_sectors = {"Technology": "EXV3.DE", "Energy": "EXV4.DE"}

    # Trends present for Technology, absent for Energy -> Energy sentiment = 0.0
    trends = {
        "Technology": pd.Series([float(i) for i in range(13)]),  # rising slope
        "Energy": pd.Series([5.0] * 13),                          # flat slope
    }

    result = _compute_sentiment_for_scan(
        trends_data=trends,
        sector_keys=sector_keys,
        us_sectors=us_sectors,
        eu_sectors=eu_sectors,
    )

    assert isinstance(result, pd.Series)
    assert set(result.index) == set(sector_keys)
    # No NaNs in the output (all-NaN sector collapses to 0.0 inside compute_sentiment_score)
    assert not result.isna().any()
