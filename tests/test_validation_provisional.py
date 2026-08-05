"""Tests for the validation panel's provisional-mode gate.

The forward-return panel colours cells green/red as if hit rate and mean
excess were conclusive findings. With only a few weeks of scan history the
observations overlap too heavily to support that — this module gates the
colouring on calendar span (not scan count) and adds a caveat when the span
is too short.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.validation import build_validation_context, CONCLUSIVE_SPAN_DAYS, MIN_SCANS

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"


def _history(rows: list[tuple]) -> pd.DataFrame:
    """Build a scan-history DataFrame from
    (scan_id, run_at, region, sector, composite, change_score, rank)."""
    return pd.DataFrame(
        rows,
        columns=["scan_id", "run_at", "region", "gics_sector", "composite", "change_score", "rank"],
    )


def _shared_with_span(total_days: int, n_scans: int = 20) -> dict:
    """A minimal shared dict whose scans span exactly `total_days` calendar
    days (first scan on day 0, last scan on day `total_days`), with enough
    scans to clear MIN_SCANS."""
    n_scans = max(n_scans, MIN_SCANS)
    base = pd.Timestamp("2025-01-01")
    rows = []
    for i in range(n_scans):
        # Spread scans evenly across [0, total_days]; last one lands exactly
        # on total_days so the span is exact.
        offset_days = round(i * total_days / (n_scans - 1)) if n_scans > 1 else 0
        run_at = (base + pd.Timedelta(days=offset_days)).strftime("%Y-%m-%d")
        rows.append((i + 1, run_at, "THEME", "Space", 0.9, 0.1, 2))
    df = _history(rows)
    return {
        "all_scores_df": df,
        "themes_cfg": {"benchmark": "ACWI", "themes": {"Space": {"ticker": "UFO"}}},
        "project_root": Path("/tmp"),
    }


@patch("dashboard.validation.fetch_prices")
def _flat_prices(shared: dict, mock_prices) -> dict:
    """Run build_validation_context with a flat-price fetch stub (price level
    is irrelevant to span/conclusive computation) and return the context."""
    trading_days = pd.bdate_range("2024-12-01", periods=600)
    flat = pd.DataFrame({"Close": [100.0] * len(trading_days)}, index=trading_days)
    mock_prices.return_value = {t: flat for t in ("XLE", "RSP", "EXSA.DE")}
    return build_validation_context(shared)


class TestSpanAndConclusive:
    def test_span_days_computed_from_first_and_last_scan(self):
        shared = _shared_with_span(total_days=100)
        ctx = _flat_prices(shared)
        assert ctx["validation_span_days"] == 100

    def test_not_conclusive_under_threshold(self):
        # The real-world case observed 2026-08-01: 37-day span.
        shared = _shared_with_span(total_days=37)
        ctx = _flat_prices(shared)
        assert ctx["validation_conclusive"] is False

    def test_conclusive_at_threshold(self):
        shared = _shared_with_span(total_days=CONCLUSIVE_SPAN_DAYS)
        ctx = _flat_prices(shared)
        assert ctx["validation_span_days"] == CONCLUSIVE_SPAN_DAYS
        assert ctx["validation_conclusive"] is True

    def test_conclusive_above_threshold(self):
        shared = _shared_with_span(total_days=CONCLUSIVE_SPAN_DAYS + 35)
        ctx = _flat_prices(shared)
        assert ctx["validation_conclusive"] is True

    def test_just_under_threshold_is_not_conclusive(self):
        shared = _shared_with_span(total_days=CONCLUSIVE_SPAN_DAYS - 1)
        ctx = _flat_prices(shared)
        assert ctx["validation_conclusive"] is False

    def test_early_return_sets_conclusive_false(self):
        """Fewer than MIN_SCANS scans: the context must still carry
        validation_conclusive (False) so the template never reads an
        undefined value, and must not raise."""
        rows = [
            (i, f"2026-01-{i:02d}", "THEME", "Space", 0.9, 0.1, 1)
            for i in range(1, MIN_SCANS)
        ]
        shared = {
            "all_scores_df": _history(rows),
            "themes_cfg": {"benchmark": "ACWI", "themes": {"Space": {"ticker": "UFO"}}},
            "project_root": Path("/tmp"),
        }
        ctx = build_validation_context(shared)
        assert ctx["validation_min_scans_met"] is False
        assert ctx["validation_conclusive"] is False

    def test_empty_scores_sets_conclusive_false(self):
        shared = {
            "all_scores_df": _history([]),
            "themes_cfg": {"benchmark": "ACWI", "themes": {}},
            "project_root": Path("/tmp"),
        }
        ctx = build_validation_context(shared)
        assert ctx["validation_min_scans_met"] is False
        assert ctx["validation_conclusive"] is False


def _jinja_env():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    env.filters["js_json"] = (
        lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    )
    return env


def _render_partial(name: str, **ctx) -> str:
    return _jinja_env().get_template(name).render(**ctx)


_NEGATIVE_ROW = {
    "region": "US", "horizon": "1m", "obs": 24,
    "hit_rate": 0.25, "mean_excess": -0.03, "median_excess": -0.02,
}
_HOLDING_ROW = {
    "region": "US", "runs": 3, "ongoing": 0,
    "median": 5, "mean": 5.3, "min": 2, "max": 9,
}


class TestValidationPartialRendering:
    def test_coloring_suppressed_when_provisional(self):
        html = _render_partial(
            "_validation.html.j2",
            validation_min_scans_met=True,
            validation_conclusive=False,
            validation_span_days=37,
            validation_first_scan="2026-06-25",
            validation_fwd_returns=[_NEGATIVE_ROW],
            validation_holding=[_HOLDING_ROW],
        )
        assert "signal-lo" not in html
        assert "signal-hi" not in html
        assert "val-provisional" in html

    def test_coloring_present_when_conclusive(self):
        html = _render_partial(
            "_validation.html.j2",
            validation_min_scans_met=True,
            validation_conclusive=True,
            validation_span_days=400,
            validation_first_scan="2025-06-01",
            validation_fwd_returns=[_NEGATIVE_ROW],
            validation_holding=[_HOLDING_ROW],
        )
        assert "signal-lo" in html
        assert "val-provisional" not in html
