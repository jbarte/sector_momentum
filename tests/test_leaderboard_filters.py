"""Tests for the leaderboard filter data attributes rendered on each row.

The client-side filter engine reads these attributes; if the template stops
emitting them (or emits wrong values) every filter silently matches nothing.
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.cohorts import cohorts as _cohorts_fn

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


def _render_index(grouped_rows, cohort_list=None):
    """Render index.html.j2 from a list of (Cohort, [row_dict]) tuples —
    the shape build.py's `grouped_rows` context var takes. `has_any_rows` is
    derived the same way build.py derives it, so tests don't have to keep it
    in sync by hand."""
    return _jinja_env().get_template("index.html.j2").render(
        grouped_rows=grouped_rows,
        cohort_list=cohort_list if cohort_list is not None else [c for c, _ in grouped_rows],
        has_any_rows=any(rows for _, rows in grouped_rows),
        sector_keys=[], scan_index=[], backtest_metrics=[], badge_scorecard=[],
    )


def _render_leaderboard(rows):
    """Render index.html.j2 with a single US-labelled cohort group — the
    shape these pre-cohort-unification tests were written against, now
    expressed as a one-entry `grouped_rows` list."""
    from types import SimpleNamespace
    return _render_index([(SimpleNamespace(region="US", label="US Sectors"), rows)])


# Three real cohorts (one member each) built through src.cohorts.cohorts()
# rather than hand-rolled, so this fixture can't drift from the real shape.
_TEST_UNIVERSE = {
    "us_sectors": {"Technology": "XLK"},
    "eu_sectors": {"Technology": "EXV3.DE"},
}
_TEST_THEMES_CFG = {"themes": {"Space": {"ticker": "UFO"}}}


def _rows_for_cohort(cohort):
    rows = []
    for key in cohort.instruments:
        region, name = key.split("|", 1)
        rows.append(_row(key=key, sector_id=f"{region}-{name}", sector=name, region=region))
    return rows


_THREE_COHORT_GROUPS = [
    (c, _rows_for_cohort(c)) for c in _cohorts_fn(_TEST_UNIVERSE, _TEST_THEMES_CFG)
]


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


def test_leaderboard_renders_every_cohort_group():
    html = _render_index(grouped_rows=_THREE_COHORT_GROUPS)
    assert "US Sectors" in html
    assert "EU Sectors" in html
    assert "Themes" in html


def test_theme_rows_carry_filter_data_attributes():
    """Themes must be filterable like sectors — the chips read these."""
    html = _render_index(grouped_rows=_THREE_COHORT_GROUPS)
    theme_row = [l for l in html.splitlines() if 'data-sector-key="THEME|Space"' in l]
    assert theme_row, "theme row missing"
    assert 'data-setup=' in theme_row[0]
    assert 'data-trend=' in theme_row[0]


def test_cohort_filter_chips_present():
    html = _render_index(grouped_rows=_THREE_COHORT_GROUPS)
    for value in ("US", "EU", "THEME"):
        assert f'data-filter-value="{value}"' in html
