"""Tests for dashboard.validation — forward-return validation & holding-period stats."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from dashboard.validation import (
    _top5_runs,
    _holding_stats,
    _compute_forward_returns,
    _aggregate_fwd_returns,
    build_validation_context,
    MIN_SCANS,
)


def _history(rows: list[tuple]) -> pd.DataFrame:
    """Build a scan-history DataFrame from (scan_id, run_at, region, sector, composite, change_score, rank)."""
    return pd.DataFrame(
        rows,
        columns=["scan_id", "run_at", "region", "gics_sector", "composite", "change_score", "rank"],
    )


class TestTop5Runs:
    def test_single_run(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.9, 0.1, 1),
            (2, "2026-01-02", "US", "Energy", 0.8, 0.1, 3),
            (3, "2026-01-03", "US", "Energy", 0.7, 0.1, 5),
        ])
        runs = _top5_runs(df, "US")
        assert len(runs) == 1
        assert runs[0]["sector"] == "Energy"
        assert runs[0]["duration"] == 3
        assert runs[0]["ongoing"] is True

    def test_run_ends(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.9, 0.1, 2),
            (2, "2026-01-02", "US", "Energy", 0.8, 0.1, 4),
            (3, "2026-01-03", "US", "Energy", 0.5, 0.1, 7),
        ])
        runs = _top5_runs(df, "US")
        assert len(runs) == 1
        assert runs[0]["duration"] == 2
        assert runs[0]["ongoing"] is False

    def test_two_runs_with_gap(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.9, 0.1, 1),
            (2, "2026-01-02", "US", "Energy", 0.5, 0.1, 8),
            (3, "2026-01-03", "US", "Energy", 0.9, 0.1, 3),
            (4, "2026-01-04", "US", "Energy", 0.9, 0.1, 2),
        ])
        runs = _top5_runs(df, "US")
        assert len(runs) == 2
        assert runs[0]["duration"] == 1
        assert runs[0]["ongoing"] is False
        assert runs[1]["duration"] == 2
        assert runs[1]["ongoing"] is True

    def test_never_in_top5(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.1, 0.1, 8),
            (2, "2026-01-02", "US", "Energy", 0.1, 0.1, 9),
        ])
        runs = _top5_runs(df, "US")
        assert runs == []

    def test_multiple_sectors(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.9, 0.1, 1),
            (1, "2026-01-01", "US", "Tech", 0.8, 0.1, 2),
            (2, "2026-01-02", "US", "Energy", 0.5, 0.1, 7),
            (2, "2026-01-02", "US", "Tech", 0.7, 0.1, 3),
        ])
        runs = _top5_runs(df, "US")
        energy_runs = [r for r in runs if r["sector"] == "Energy"]
        tech_runs = [r for r in runs if r["sector"] == "Tech"]
        assert len(energy_runs) == 1
        assert energy_runs[0]["duration"] == 1
        assert len(tech_runs) == 1
        assert tech_runs[0]["duration"] == 2

    def test_filters_by_region(self):
        df = _history([
            (1, "2026-01-01", "US", "Energy", 0.9, 0.1, 1),
            (1, "2026-01-01", "EU", "Energy", 0.9, 0.1, 2),
        ])
        us_runs = _top5_runs(df, "US")
        eu_runs = _top5_runs(df, "EU")
        assert len(us_runs) == 1
        assert us_runs[0]["region"] == "US"
        assert len(eu_runs) == 1
        assert eu_runs[0]["region"] == "EU"

    def test_empty_df(self):
        df = pd.DataFrame(
            columns=["scan_id", "run_at", "region", "gics_sector", "composite", "change_score", "rank"]
        )
        assert _top5_runs(df, "US") == []


class TestHoldingStats:
    def test_basic_stats(self):
        runs = [
            {"region": "US", "sector": "Energy", "duration": 5, "ongoing": False},
            {"region": "US", "sector": "Tech", "duration": 10, "ongoing": False},
            {"region": "US", "sector": "Health", "duration": 3, "ongoing": False},
        ]
        stats = _holding_stats(runs, "US")
        assert stats["region"] == "US"
        assert stats["runs"] == 3
        assert stats["median"] == 5
        assert stats["min"] == 3
        assert stats["max"] == 10

    def test_excludes_ongoing(self):
        runs = [
            {"region": "US", "sector": "Energy", "duration": 5, "ongoing": False},
            {"region": "US", "sector": "Tech", "duration": 10, "ongoing": True},
        ]
        stats = _holding_stats(runs, "US")
        assert stats["runs"] == 1
        assert stats["ongoing"] == 1
        assert stats["median"] == 5

    def test_no_completed_runs(self):
        runs = [
            {"region": "US", "sector": "Tech", "duration": 10, "ongoing": True},
        ]
        stats = _holding_stats(runs, "US")
        assert stats["runs"] == 0
        assert stats["ongoing"] == 1
        assert stats["median"] is None

    def test_empty_runs(self):
        stats = _holding_stats([], "US")
        assert stats["runs"] == 0
        assert stats["ongoing"] == 0
        assert stats["median"] is None


class TestComputeForwardReturns:
    def test_basic_returns(self):
        history = _history([
            (1, "2026-01-02", "US", "Energy", 0.9, 0.1, 2),
            (2, "2026-01-03", "US", "Energy", 0.5, 0.1, 8),
        ])
        trading_days = pd.bdate_range("2026-01-02", periods=30)
        prices_data = [100.0 + i * 0.5 for i in range(30)]
        sector_prices = pd.DataFrame(
            {"Close": prices_data}, index=trading_days
        )
        benchmark_prices = pd.DataFrame(
            {"Close": [100.0] * 30}, index=trading_days
        )
        ticker_map = {"US|Energy": "XLE"}

        obs = _compute_forward_returns(
            history, {"XLE": sector_prices}, {"RSP": benchmark_prices},
            "US", "RSP", ticker_map, [5, 21],
        )
        assert len(obs) == 1
        assert obs[0]["region"] == "US"
        assert obs[0]["sector"] == "Energy"
        assert 5 in obs[0]["excess"]
        assert 21 in obs[0]["excess"]
        assert obs[0]["excess"][5] > 0
        assert obs[0]["excess"][21] > 0

    def test_skips_pending(self):
        history = _history([
            (1, "2026-07-15", "US", "Energy", 0.9, 0.1, 2),
        ])
        trading_days = pd.bdate_range("2026-07-14", periods=5)
        sector_prices = pd.DataFrame(
            {"Close": [100.0] * 5}, index=trading_days
        )
        benchmark_prices = pd.DataFrame(
            {"Close": [100.0] * 5}, index=trading_days
        )
        ticker_map = {"US|Energy": "XLE"}

        obs = _compute_forward_returns(
            history, {"XLE": sector_prices}, {"RSP": benchmark_prices},
            "US", "RSP", ticker_map, [5, 21],
        )
        assert obs == []


class TestAggregateFwdReturns:
    def test_aggregation(self):
        observations = [
            {"region": "US", "sector": "A", "excess": {5: 0.01, 21: 0.03}},
            {"region": "US", "sector": "B", "excess": {5: -0.005, 21: 0.02}},
            {"region": "US", "sector": "C", "excess": {5: 0.02, 21: -0.01}},
        ]
        result = _aggregate_fwd_returns(observations, "US")
        assert len(result) == 2
        r5 = [r for r in result if r["horizon"] == "5d"][0]
        r21 = [r for r in result if r["horizon"] == "1m"][0]
        assert r5["obs"] == 3
        assert r5["region"] == "US"
        assert abs(r5["hit_rate"] - 2 / 3) < 0.01

    def test_empty_observations(self):
        result = _aggregate_fwd_returns([], "US")
        assert len(result) == 2
        assert result[0]["obs"] == 0
        assert result[0]["hit_rate"] is None


class TestBuildValidationContext:
    def test_below_min_scans(self):
        df = _history([
            (i, f"2026-01-{i:02d}", "US", "Energy", 0.9, 0.1, 1)
            for i in range(1, MIN_SCANS)
        ])
        shared = {
            "all_scores_df": df,
            "universe": {"us_sectors": {"Energy": "XLE"}, "eu_sectors": {},
                         "us_benchmark": "RSP", "eu_benchmark": "EXSA.DE"},
            "project_root": Path("/tmp"),
        }
        ctx = build_validation_context(shared)
        assert ctx["validation_min_scans_met"] is False
        assert "validation_fwd_returns" not in ctx

    @patch("dashboard.validation.fetch_prices")
    def test_produces_context_keys(self, mock_prices):
        rows = []
        for i in range(1, 21):
            rows.append((i, f"2026-01-{i:02d}", "US", "Energy", 0.9, 0.1, 2))
            rows.append((i, f"2026-01-{i:02d}", "EU", "Energy", 0.8, 0.1, 3))
        df = _history(rows)

        trading_days = pd.bdate_range("2026-01-01", periods=80)
        flat_prices = pd.DataFrame(
            {"Close": [100.0] * len(trading_days)}, index=trading_days
        )
        mock_prices.return_value = {
            "XLE": flat_prices, "EXH1.DE": flat_prices,
            "RSP": flat_prices, "EXSA.DE": flat_prices,
        }

        shared = {
            "all_scores_df": df,
            "universe": {
                "us_sectors": {"Energy": "XLE"},
                "eu_sectors": {"Energy": "EXH1.DE"},
                "us_benchmark": "RSP",
                "eu_benchmark": "EXSA.DE",
            },
            "project_root": Path("/tmp"),
        }
        ctx = build_validation_context(shared)
        assert ctx["validation_min_scans_met"] is True
        assert "validation_fwd_returns" in ctx
        assert "validation_holding" in ctx
        assert len(ctx["validation_fwd_returns"]) == 6
        assert len(ctx["validation_holding"]) == 3
