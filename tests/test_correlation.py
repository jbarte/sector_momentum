"""Tests for dashboard.correlation — rolling correlation heatmap."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_prices(n_tickers: int, n_days: int, seed: int = 42) -> dict[str, pd.DataFrame]:
    """Generate synthetic price DataFrames for testing."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(end="2026-07-18", periods=n_days)
    result: dict[str, pd.DataFrame] = {}
    for i in range(n_tickers):
        close = 100 + np.cumsum(rng.normal(0, 1, n_days))
        result[f"TICK{i}"] = pd.DataFrame({"Close": close}, index=dates)
    return result


def _make_universe(n_us: int = 11, n_eu: int = 14) -> dict:
    """Build a universe config dict matching the ticker count."""
    us = {f"Sector{i}": f"TICK{i}" for i in range(n_us)}
    eu = {f"EUSector{i}": f"TICK{n_us + i}" for i in range(n_eu)}
    return {"us_sectors": us, "eu_sectors": eu}


# ---------------------------------------------------------------------------
# Unit tests for internal helpers
# ---------------------------------------------------------------------------

class TestComputeCorrelationMatrix:
    def test_shape_25x25(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(25, 80)
        tickers = list(prices.keys())
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        assert matrix.shape == (25, 25)

    def test_diagonal_is_one(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(5, 80)
        tickers = list(prices.keys())
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        np.testing.assert_allclose(np.diag(matrix.values), 1.0, atol=1e-10)

    def test_symmetry(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(5, 80)
        tickers = list(prices.keys())
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        np.testing.assert_allclose(matrix.values, matrix.values.T, atol=1e-10)

    def test_values_in_range(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(5, 80)
        tickers = list(prices.keys())
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        vals = matrix.values
        assert np.all((vals >= -1.0 - 1e-10) & (vals <= 1.0 + 1e-10))

    def test_insufficient_data_returns_none(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(5, 30)  # only 30 days, need 60
        tickers = list(prices.keys())
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        assert matrix is None

    def test_missing_ticker_produces_nan_row(self):
        from dashboard.correlation import _compute_correlation_matrix

        prices = _make_prices(5, 80)
        tickers = list(prices.keys()) + ["MISSING"]
        matrix = _compute_correlation_matrix(prices, tickers, window=60)
        assert matrix is not None
        assert matrix.shape == (6, 6)
        assert matrix.loc["MISSING"].drop("MISSING").isna().all()


class TestOrderLabels:
    def test_us_before_eu(self):
        from dashboard.correlation import _order_labels

        universe = _make_universe(3, 2)
        ranks = {
            "US|Sector0": 2, "US|Sector1": 1, "US|Sector2": 3,
            "EU|EUSector0": 1, "EU|EUSector1": 2,
        }
        labels, tickers = _order_labels(universe, ranks)
        # US should come first, ordered by rank
        assert labels[0] == "Sector1 (US)"   # rank 1
        assert labels[1] == "Sector0 (US)"   # rank 2
        assert labels[2] == "Sector2 (US)"   # rank 3
        assert labels[3] == "EUSector0 (EU)" # rank 1
        assert labels[4] == "EUSector1 (EU)" # rank 2

    def test_top5_bold(self):
        from dashboard.correlation import _order_labels

        universe = _make_universe(11, 14)
        ranks = {}
        for i, name in enumerate(universe["us_sectors"]):
            ranks[f"US|{name}"] = i + 1
        for i, name in enumerate(universe["eu_sectors"]):
            ranks[f"EU|{name}"] = i + 1
        labels, _ = _order_labels(universe, ranks)
        bold_count = sum(1 for l in labels if l.startswith("<b>"))
        assert bold_count == 10  # top 5 US + top 5 EU


class TestBuildCorrelationContext:
    def test_context_keys(self, monkeypatch):
        from dashboard import correlation

        prices = _make_prices(25, 80)
        universe = _make_universe()
        tickers = list(universe["us_sectors"].values()) + list(universe["eu_sectors"].values())
        # Map synthetic tickers to match universe
        mapped_prices = {}
        for i, t in enumerate(tickers):
            mapped_prices[t] = prices[f"TICK{i}"]

        monkeypatch.setattr(
            correlation, "fetch_prices",
            lambda tickers, start, end, cache_dir: mapped_prices,
        )

        ranks = {}
        for i, name in enumerate(universe["us_sectors"]):
            ranks[f"US|{name}"] = i + 1
        for i, name in enumerate(universe["eu_sectors"]):
            ranks[f"EU|{name}"] = i + 1

        shared = {
            "project_root": __import__("pathlib").Path("."),
            "universe": universe,
            "history_df": _history_df_with_ranks(ranks),
        }
        ctx = correlation.build_correlation_context(shared)
        assert "correlation_fig_json" in ctx
        assert "correlation_n_days" in ctx
        assert "correlation_date" in ctx
        assert ctx["correlation_fig_json"] is not None

    def test_context_none_on_failure(self, monkeypatch):
        from dashboard import correlation

        monkeypatch.setattr(
            correlation, "fetch_prices",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no data")),
        )

        shared = {
            "project_root": __import__("pathlib").Path("."),
            "universe": _make_universe(),
            "history_df": pd.DataFrame(),
        }
        ctx = correlation.build_correlation_context(shared)
        assert ctx["correlation_fig_json"] is None


def _history_df_with_ranks(ranks: dict) -> pd.DataFrame:
    """Build a minimal history_df with region, gics_sector, rank, scan_id."""
    rows = []
    for key, rank in ranks.items():
        region, sector = key.split("|", 1)
        rows.append({
            "scan_id": 1,
            "region": region,
            "gics_sector": sector,
            "rank": rank,
            "composite_score": 1.0 / rank,
        })
    return pd.DataFrame(rows)
