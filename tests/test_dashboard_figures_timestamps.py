"""Tests for ISO8601 timestamp parsing in dashboard/figures.py."""
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.figures import _build_drilldown_data, _build_history_figure


def _history_df(run_at_values):
    """Minimal history DataFrame with the given run_at strings."""
    rows = []
    for i, ts in enumerate(run_at_values):
        rows.append({
            "scan_id": i + 1,
            "run_at": ts,
            "region": "US",
            "gics_sector": "Technology",
            "composite": 0.5 + i * 0.01,
            "level_score": 0.4,
            "change_score": 0.3,
            "data_score": 0.6,
            "rank": i + 1,
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("run_at_values", [
    ["2026-06-10T12:00:00", "2026-06-11T12:00:00"],
    ["2026-06-10T12:00:00+00:00", "2026-06-11T12:00:00+00:00"],
    ["2026-06-10T12:00:00.123456+00:00", "2026-06-11T12:00:00.654321+00:00"],
    ["2026-06-10T12:00:00", "2026-06-11T12:00:00+00:00"],
])
def test_build_drilldown_data_mixed_timestamps(run_at_values):
    df = _history_df(run_at_values)
    sector_signal_data, sector_keys, _ = _build_drilldown_data(df)
    assert "US|Technology" in sector_keys
    assert "US|Technology" in sector_signal_data


@pytest.mark.parametrize("run_at_values", [
    ["2026-06-10T12:00:00", "2026-06-11T12:00:00"],
    ["2026-06-10T12:00:00+00:00", "2026-06-11T12:00:00+00:00"],
    ["2026-06-10T12:00:00.123456+00:00", "2026-06-11T12:00:00.654321+00:00"],
    ["2026-06-10T12:00:00", "2026-06-11T12:00:00+00:00"],
])
def test_build_history_figure_mixed_timestamps(run_at_values):
    df = _history_df(run_at_values)
    result = _build_history_figure(df)
    assert isinstance(result, str)
    assert len(result) > 0
