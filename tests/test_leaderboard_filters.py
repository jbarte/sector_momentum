"""Tests for the leaderboard filter data attributes rendered on each row.

The client-side filter engine reads these attributes; if the template stops
emitting them (or emits wrong values) every filter silently matches nothing.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"


def _jinja_env():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    env.filters["js_json"] = (
        lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    )
    return env


def _row(**over):
    """A leaderboard row dict shaped like _build_leaderboard_rows output."""
    row = {
        "key": "US|Technology", "sector_id": "US-Technology",
        "sector": "Technology", "region": "US", "rank": 1,
        "composite": "0.900", "level_score": "0.5", "change_score": "0.4",
        "data_score": "0.3", "sentiment_score": "—",
        "delta_rank": "+1.0", "arrow": "▲", "arrow_class": "up",
        "setup": "entry", "trajectory_state": "strong_up",
        "trajectory_label": "↑↑", "breakdown_html": "<div></div>",
        "_raw_composite": 0.9, "_raw_change": 0.4,
    }
    row.update(over)
    return row


def _render_leaderboard(rows):
    """Render index.html.j2. Only the looped context vars are required;
    everything else renders as Jinja's default empty Undefined."""
    return _jinja_env().get_template("index.html.j2").render(
        us_leaderboard_rows=rows, eu_leaderboard_rows=[],
        sector_keys=[], scan_index=[], backtest_metrics=[], badge_scorecard=[],
    )


def _row_tag(html):
    m = re.search(r'<tr class="leaderboard-row"[^>]*>', html)
    assert m, "no leaderboard row rendered"
    return m.group(0)


def test_row_has_all_filter_attributes():
    tag = _row_tag(_render_leaderboard([_row()]))
    for attr in ("data-setup", "data-trend", "data-composite",
                 "data-change", "data-rank"):
        assert attr in tag, f"{attr} missing from {tag}"


def test_row_attribute_values_match_row_dict():
    tag = _row_tag(_render_leaderboard([_row()]))
    assert 'data-setup="entry"' in tag
    assert 'data-trend="strong_up"' in tag
    assert 'data-composite="0.9"' in tag
    assert 'data-change="0.4"' in tag
    assert 'data-rank="1"' in tag


def test_exit_and_flat_row_values():
    tag = _row_tag(_render_leaderboard(
        [_row(setup="exit", trajectory_state="flat")]))
    assert 'data-setup="exit"' in tag
    assert 'data-trend="flat"' in tag


def test_row_without_setup_renders_empty_setup_attr():
    # setup is None for most rows; the attribute must still be present and empty
    # so the engine's membership test cleanly fails instead of throwing.
    tag = _row_tag(_render_leaderboard([_row(setup=None)]))
    assert 'data-setup=""' in tag


def test_row_with_missing_scores_renders_empty_numeric_attrs():
    # Empty -> parseFloat("") is NaN -> threshold predicates correctly fail.
    tag = _row_tag(_render_leaderboard(
        [_row(_raw_composite=None, _raw_change=None)]))
    assert 'data-composite=""' in tag
    assert 'data-change=""' in tag
