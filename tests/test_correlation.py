"""Tests for dashboard.correlation — rolling correlation heatmap."""
from __future__ import annotations

import numpy as np
import pandas as pd


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


def _make_themes_cfg(n: int = 25) -> dict:
    """Build a themes config dict matching the ticker count."""
    return {"benchmark": "ACWI",
            "themes": {f"Theme{i}": {"ticker": f"TICK{i}"} for i in range(n)}}


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
    def test_ordered_by_rank_within_cohort(self):
        from dashboard.correlation import _order_labels
        from src.cohorts import cohorts

        themes_cfg = _make_themes_cfg(3)
        ranks = {"THEME|Theme0": 2, "THEME|Theme1": 1, "THEME|Theme2": 3}
        labels, tickers, _ = _order_labels(cohorts(themes_cfg), ranks)
        assert labels[0] == "Theme1 (THEME)"   # rank 1
        assert labels[1] == "Theme0 (THEME)"   # rank 2
        assert labels[2] == "Theme2 (THEME)"   # rank 3

    def test_top5_bold(self):
        from dashboard.correlation import _order_labels
        from src.cohorts import cohorts

        themes_cfg = _make_themes_cfg(25)
        ranks = {f"THEME|{name}": i + 1
                 for i, name in enumerate(themes_cfg["themes"])}
        labels, _, _ = _order_labels(cohorts(themes_cfg), ranks)
        bold_count = sum(1 for l in labels if l.startswith("<b>"))
        assert bold_count == 5  # top 5 of the one cohort


class TestBuildCorrelationContext:
    def test_context_keys(self, monkeypatch):
        from dashboard import correlation

        prices = _make_prices(25, 80)
        themes_cfg = _make_themes_cfg()
        tickers = [c["ticker"] for c in themes_cfg["themes"].values()]
        # Map synthetic tickers to match the configured cohort
        mapped_prices = {}
        for i, t in enumerate(tickers):
            mapped_prices[t] = prices[f"TICK{i}"]

        monkeypatch.setattr(
            correlation, "fetch_prices",
            lambda tickers, start, end, cache_dir: mapped_prices,
        )

        ranks = {f"THEME|{name}": i + 1
                 for i, name in enumerate(themes_cfg["themes"])}
        shared = {
            "project_root": __import__("pathlib").Path("."),
            "themes_cfg": themes_cfg,
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
            "themes_cfg": _make_themes_cfg(),
            "history_df": pd.DataFrame(),
        }
        ctx = correlation.build_correlation_context(shared)
        assert ctx["correlation_fig_json"] is None


def test_order_labels_includes_theme_cohort_when_themes_cfg_supplied():
    """cohort-unification PR 5: correlation heatmap joins THEME when the
    caller passes themes_cfg through, per src/cohorts.py's docstring."""
    from dashboard.correlation import _order_labels
    from src.cohorts import cohorts

    themes_cfg = {"benchmark": "ACWI", "themes": {
        "Space": {"ticker": "UFO"}, "Biotech": {"ticker": "XBI"},
    }}
    ranks = {"THEME|Space": 1, "THEME|Biotech": 2}
    labels, tickers, block_sizes = _order_labels(cohorts(themes_cfg), ranks)

    assert tickers == ["UFO", "XBI"]
    assert block_sizes == [2]
    assert labels[-1].endswith("(THEME)")


def test_build_correlation_context_raises_on_duplicate_ticker_across_cohorts():
    """A ticker shared by two cohorts would duplicate heatmap rows/columns —
    build_correlation_context must guard it rather than silently corrupt the
    figure (caught by the function's own try/except, degrading to none_ctx)."""
    from dashboard import correlation

    themes_cfg = {"benchmark": "ACWI", "themes": {
        "Space": {"ticker": "DUPE"}, "Biotech": {"ticker": "DUPE"},
    }}

    shared = {
        "project_root": __import__("pathlib").Path("."),
        "themes_cfg": themes_cfg,
        "history_df": _history_df_with_ranks({"THEME|Space": 1, "THEME|Biotech": 2}),
    }
    ctx = correlation.build_correlation_context(shared)
    assert ctx["correlation_fig_json"] is None


def test_order_labels_follows_cohorts_and_reports_block_sizes():
    from dashboard.correlation import _order_labels
    from src.cohorts import cohorts

    themes_cfg = {"benchmark": "ACWI", "themes": {
        "Semiconductors": {"ticker": "SOXX"}, "Space": {"ticker": "UFO"},
    }}
    ranks = {"THEME|Semiconductors": 2, "THEME|Space": 1}
    labels, tickers, block_sizes = _order_labels(cohorts(themes_cfg), ranks)

    assert tickers == ["UFO", "SOXX"], "ranked within cohort"
    assert block_sizes == [2]
    assert labels[0].endswith("(THEME)")


def test_block_boundaries_match_the_previous_single_divider():
    """With two cohorts the generalized boundary must land exactly where the
    old `n_us - 0.5` divider did, or the heatmap shifts."""
    from dashboard.correlation import _block_boundaries
    assert _block_boundaries([11, 14]) == [10.5]
    assert _block_boundaries([2, 3, 4]) == [1.5, 4.5]
    assert _block_boundaries([5]) == []


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
