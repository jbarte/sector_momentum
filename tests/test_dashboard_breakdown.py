"""Breakdown-panel rendering of the info-only max-drawdown signal."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.breakdown import _build_breakdown_html
from dashboard.rows import _format_raw_value

_TPL = Path(__file__).parent.parent / "dashboard/templates/index.html.j2"


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


def test_score_tree_key_attribute_matches_sentiment_toggle_selector():
    """The `.score-tree` panel and the sentiment-toggle's `updateTrees()` in
    index.html.j2 are two independently-maintained producers/consumers of the
    same DOM attribute -- they must agree on its name for the toggle to find
    and re-render the panel at all. Found undertested: reverting breakdown.py
    alone (to `data-sector-key`, pre-rename) during the theme-key DOM rename
    did not fail any existing test, since nothing pinned this specific join.
    Pins both sides so a future rename of one without the other fails loudly
    instead of silently breaking sentiment-toggle re-rendering of the tree."""
    score_row = {"composite": 0.5, "data_score": 0.5, "level_score": 0.4,
                 "change_score": 0.6, "sentiment_score": None}
    universe = {"us_sectors": {"Technology": "XLK"}, "us_benchmark": "RSP"}
    weights = {"pillars": {"data": 1.0}, "data_pillar": {"level": 0.5, "change": 0.5},
               "level_signals": {}, "change_signals": {}}
    html = _build_breakdown_html("US|Technology", score_row, [], universe, weights)
    assert 'data-theme-key="US|Technology"' in html, (
        "breakdown panel no longer emits data-theme-key -- the sentiment "
        "toggle's updateTrees() selector will silently stop matching it"
    )

    js = _TPL.read_text()
    start = js.index("function updateTrees(")
    end = js.index("\n  }\n", start)
    body = js[start:end]
    assert '.score-tree[data-theme-key="' in body, (
        "updateTrees() no longer selects .score-tree by data-theme-key -- "
        "it will silently stop finding the breakdown panel to re-render"
    )
