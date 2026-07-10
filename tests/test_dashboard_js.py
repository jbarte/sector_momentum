"""Tests that guard against broken JavaScript in the built dashboard.

The dashboard embeds Plotly figures as inline JS variables. If any variable
is missing from the build.py render context, Jinja2 renders it as an empty
string, producing `var X = ;` — a syntax error that kills ALL interactivity
(tab switching, row expansion, everything). These tests catch that class of bug.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import _build_sentiment_scatter_figure, _build_leaderboard_rows, _render

_TEMPLATE = Path(__file__).parent.parent / "dashboard" / "templates" / "index.html.j2"
_PROJECT_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _template_js_vars() -> set[str]:
    """Parse the template and return every Jinja2 variable used in a JS
    `var NAME = {{ var_name | safe }};` assignment."""
    text = _TEMPLATE.read_text()
    return set(re.findall(r"var\s+[A-Z_]+\s*=\s*\{\{\s*(\w+)\s*\|?\s*safe\s*\}\}", text))


def _render_context_keys() -> set[str]:
    """Extract the keys passed to _render(context=dict(...)) in build.py's main().

    Uses parenthesis depth-counting to handle nested calls like json.dumps(...).
    """
    text = (Path(__file__).parent.parent / "dashboard" / "build.py").read_text()
    marker = "context=dict("
    start = text.find(marker)
    if start == -1:
        return set()
    # Walk from the opening '(' counting depth to find the matching ')'
    paren_start = start + len(marker) - 1  # position of '('
    depth = 0
    context_block = ""
    for i, ch in enumerate(text[paren_start:], paren_start):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                context_block = text[paren_start + 1 : i]
                break
    # Each kwarg starts a line with `    key=`
    return set(re.findall(r"^\s*(\w+)\s*=", context_block, re.MULTILINE))


def _minimal_history_df() -> pd.DataFrame:
    """One scan, two sectors — enough to exercise all figure builders."""
    rows = []
    for region, sector in [("US", "Technology"), ("EU", "Financials")]:
        rows.append({
            "scan_id": 1,
            "run_at": "2026-06-23T12:00:00",
            "region": region,
            "gics_sector": sector,
            "level_score": 0.5,
            "change_score": 0.3,
            "data_score": 0.6,
            "sentiment_score": 0.1,
            "composite": 0.4,
            "rank": 1.0,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Test 1 — sentiment scatter builder returns valid non-empty JSON
# ---------------------------------------------------------------------------

def test_leaderboard_rows_include_sentiment_score():
    """Each leaderboard row must carry a formatted, non-empty sentiment_score so
    the Sentiment column renders (regression: the column was previously blank
    because the row dict never set this key)."""
    df = pd.DataFrame([
        {"scan_id": 1, "run_at": "2026-06-24T12:00:00", "region": "US",
         "gics_sector": "Technology", "level_score": 0.5, "change_score": 0.3,
         "data_score": 0.6, "sentiment_score": 0.42, "composite": 0.6, "rank": 1.0},
        {"scan_id": 1, "run_at": "2026-06-24T12:00:00", "region": "EU",
         "gics_sector": "Energy", "level_score": -0.2, "change_score": -0.1,
         "data_score": -0.15, "sentiment_score": float("nan"), "composite": -0.15, "rank": 2.0},
    ])
    rows, _ = _build_leaderboard_rows(df)
    by_sector = {r["sector"]: r for r in rows}
    assert "sentiment_score" in by_sector["Technology"]
    assert by_sector["Technology"]["sentiment_score"] == "0.420"
    # NaN sentiment falls back to the em-dash placeholder, never blank
    assert by_sector["Energy"]["sentiment_score"] == "—"
    assert by_sector["Energy"]["sentiment_score"] != ""


def test_sentiment_scatter_empty_df_returns_valid_json():
    empty = pd.DataFrame(columns=[
        "scan_id", "region", "gics_sector",
        "data_score", "sentiment_score",
    ])
    result = _build_sentiment_scatter_figure(empty)
    assert result, "returned empty string for empty DataFrame"
    parsed = json.loads(result)
    assert "data" in parsed
    assert "layout" in parsed


def test_sentiment_scatter_populated_df_returns_valid_json():
    df = _minimal_history_df()
    result = _build_sentiment_scatter_figure(df)
    assert result, "returned empty string for populated DataFrame"
    parsed = json.loads(result)
    assert "data" in parsed
    assert "layout" in parsed


# ---------------------------------------------------------------------------
# Test 2 — every template JS variable is in the render context
# ---------------------------------------------------------------------------

def test_render_context_covers_all_template_js_vars():
    template_vars = _template_js_vars()
    context_keys = _render_context_keys()
    missing = template_vars - context_keys
    assert not missing, (
        f"Template JS variables not in _render() context: {missing}\n"
        f"This causes `var X = ;` syntax errors that break all dashboard interactivity."
    )


# ---------------------------------------------------------------------------
# Test 3 — rendered template has no empty JS variable assignments
# ---------------------------------------------------------------------------

def _make_mock_plotly_json() -> str:
    return json.dumps({"data": [], "layout": {}})


def test_rendered_template_has_no_empty_js_vars(tmp_path):
    """Render the template with minimal mock data and verify no var X = ; patterns."""
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-06-23",
            leaderboard_rows=[],
            rrg_data_json=_make_mock_plotly_json(),
            drilldown_data=json.dumps({}),
            sector_keys=[],
            movers_json=_make_mock_plotly_json(),
            history_json=_make_mock_plotly_json(),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            signals_list=[],
            plotly_bundle="assets/plotly.min.js",
            backtest_json=json.dumps({}),
            backtest_metrics=[],
            has_backtest=False,
            rotation_json=json.dumps([]),
            has_rotations=False,
        ),
    )
    html = out.read_text()
    empty_var_pattern = re.compile(r"var\s+\w+\s*=\s*;")
    matches = empty_var_pattern.findall(html)
    assert not matches, (
        f"Empty JS variable assignments found: {matches}\n"
        "A Jinja2 variable is missing from the _render() context."
    )


def test_build_rescore_data_shape():
    import pandas as pd
    from dashboard.build import _build_rescore_data

    rows = []
    for scan_id, run_at in [(1, "2026-06-22T00:00:00"), (2, "2026-06-23T00:00:00")]:
        for region, sector, dscore, sscore in [
            ("US", "Technology", 0.6, 0.2),
            ("EU", "Energy", -0.3, float("nan")),  # NaN sentiment -> 0.0
        ]:
            rows.append({
                "scan_id": scan_id, "run_at": run_at, "region": region,
                "gics_sector": sector, "data_score": dscore, "sentiment_score": sscore,
            })
    df = pd.DataFrame(rows)

    out = _build_rescore_data(df)

    assert [s["scan_id"] for s in out["scans"]] == [1, 2]
    assert set(out["sectors"]) == {"US|Technology", "EU|Energy"}
    # arrays aligned to scans length
    for key in out["sectors"]:
        assert len(out["data"][key]) == 2
        assert len(out["sentiment"][key]) == 2
    # NaN sentiment coerced to 0.0
    assert out["sentiment"]["EU|Energy"] == [0.0, 0.0]
    assert out["data"]["US|Technology"] == [0.6, 0.6]


def test_rendered_template_includes_rescore_data_and_control(tmp_path):
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-06-23",
            leaderboard_rows=[],
            rrg_data_json=_make_mock_plotly_json(),
            drilldown_data=json.dumps({}),
            sector_keys=[],
            movers_json=_make_mock_plotly_json(),
            history_json=_make_mock_plotly_json(),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            signals_list=[],
            plotly_bundle="assets/plotly.min.js",
            backtest_json=json.dumps({}),
            backtest_metrics=[],
            has_backtest=False,
            rotation_json=json.dumps([]),
            has_rotations=False,
        ),
    )
    html = out.read_text()
    assert "var RESCORE_DATA =" in html
    assert 'assets/rescore.js' in html
    assert 'id="sentiment-toggle"' in html
    assert 'id="sentiment-weight"' in html
    # no empty JS var assignments
    assert not re.compile(r"var\s+\w+\s*=\s*;").findall(html)


def test_history_tab_has_scan_index(tmp_path):
    """The built dashboard renders the scan-index list with the active scan marked."""
    import json as _json
    import pandas as pd
    from dashboard.build import _render, build_scan_index

    scan_index = build_scan_index(pd.DataFrame([
        dict(scan_id=2, run_at="2026-06-02T06:00:00", region="US", gics_sector="Technology",
             level_score=0.7, change_score=0.7, data_score=0.7, sentiment_score=0.0,
             composite=0.7, rank=1.0),
        dict(scan_id=1, run_at="2026-06-01T06:00:00", region="US", gics_sector="Energy",
             level_score=0.5, change_score=0.5, data_score=0.5, sentiment_score=0.0,
             composite=0.5, rank=1.0),
    ]))
    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date="2026-06-02 06:00 UTC", active_scan_id=2, scan_index=scan_index,
        leaderboard_rows=[], rrg_data_json="{}", drilldown_data="{}",
        sector_keys=[], movers_json="{}", history_json="{}", sentiment_scatter_json="{}",
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
    ))
    html = out.read_text()
    assert "scan-index" in html                       # the list container
    assert "reports/report_2.md" in html              # download link
    assert "● Showing" in html                         # active marker on MAX scan_id


def test_built_html_has_no_composite_toggle(tmp_path):
    """The rendered leaderboard has no composite view toggle."""
    import json as _json
    from dashboard.build import _render, _build_leaderboard_rows

    import pandas as pd
    rows_df = pd.DataFrame([
        dict(scan_id=1, run_at="2026-06-01 00:00", region="US", gics_sector="Technology",
             composite=0.8, data_score=0.8, level_score=0.7, change_score=0.9,
             sentiment_score=0.0, rank=1.0),
    ])
    lb_rows, scan_date = _build_leaderboard_rows(rows_df)
    for r in lb_rows:
        r["key"] = f"{r['region']}|{r['sector']}"
        r["sector_id"] = r["key"].replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "→"; r["trajectory_state"] = "flat"
        r["breakdown_html"] = "<div>PANEL</div>"

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        rrg_data_json="{}", drilldown_data="{}", sector_keys=[], movers_json="{}",
        history_json="{}", sentiment_scatter_json="{}",
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
    ))
    html = out.read_text()
    assert 'data-view=' not in html
    assert 'sector-view-toggle' not in html
    assert 'data-sector-key="US|Technology"' in html
