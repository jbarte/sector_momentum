"""Breakdown-panel rendering of the info-only max-drawdown signal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.breakdown import _build_breakdown_html
from dashboard.rows import _format_raw_value


def test_format_max_dd_1y_as_percent():
    assert _format_raw_value("max_dd_1y", -0.324) == "-32.4%"
    assert _format_raw_value("max_dd_1y", 0.0) == "0.0%"
    assert _format_raw_value("max_dd_1y", None) == "—"


def test_breakdown_renders_max_drawdown_line():
    score_row = {"composite": 0.5, "data_score": 0.5, "level_score": 0.4,
                 "change_score": 0.6, "sentiment_score": None}
    signals = [{"signal_name": "max_dd_1y", "raw_value": -0.312, "z_value": None}]
    universe = {"us_sectors": {"Technology": "XLK"}, "us_benchmark": "RSP"}
    weights = {"pillars": {"data": 1.0}, "data_pillar": {"level": 0.5, "change": 0.5},
               "level_signals": {}, "change_signals": {}}
    html = _build_breakdown_html("US|Technology", score_row, signals, universe, weights)
    assert "Max Drawdown (1y)" in html
    assert "-31.2%" in html
