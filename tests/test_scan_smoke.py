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
