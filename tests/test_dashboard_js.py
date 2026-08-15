"""Tests that guard against broken JavaScript in the built dashboard.

The dashboard embeds Plotly figures as inline JS variables. If any variable
is missing from the build.py render context, Jinja2 renders it as an empty
string, producing `var X = ;` — a syntax error that kills ALL interactivity
(tab switching, row expansion, everything). These tests catch that class of bug.
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from src.horizons import (horizons as _horizons, default_horizon as _default_horizon,
                          round_trip_bps as _round_trip_bps, review_dates as _review_dates)

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import (
    _build_sentiment_scatter_figure,
    _build_leaderboard_rows,
    _build_breakdown_html,
    _build_rrg_figure,
    _build_movers_figure,
    _build_history_figure,
    _compute_rank_trajectories,
    _format_raw_value,
    _safe_float,
    _render,
)
from src.cohorts import Cohort


def _horizon_ctx(dumps=json.dumps):
    """The context vars every index.html.j2 render needs, sourced from the real
    config so these fixtures can't drift from what build.py actually passes.

    Named for the horizon block it started as; it has since become the shared
    "things the template reads that aren't figures or rows" bundle. Anything
    added here must also be added to build.py's sectors_ctx — a var missing
    from the context renders as `var X = ;` and takes every script on the page
    down with it, which is what test_rendered_template_has_no_empty_js_vars
    exists to catch.
    """
    hs = _horizons()
    d = _default_horizon()
    return dict(
        horizon_list=hs,
        round_trip_bps=_round_trip_bps(),
        # since is fixed, not datetime.now(): these fixtures feed deterministic
        # render tests, and a real clock would make review_dates flake once a
        # day when "today" crosses a review boundary mid-test-run.
        horizons_json=dumps([
            {"key": h.key, "label": h.label, "rebalance": h.rebalance,
             "top_n": h.top_n, "buffer": h.buffer,
             "trades_per_year": h.trades_per_year,
             "median_holding_days": h.median_holding_days,
             "review_dates": _review_dates(h, since="2026-01-15")} for h in hs
        ]),
        horizon_default_json=dumps({
            "key": d.key, "label": d.label, "top_n": d.top_n, "buffer": d.buffer}),
        unbuyable_json=dumps([]),
        chart_dark_json=dumps({}),
    )


def _grouped_rows_for(rows):
    """Group already-built leaderboard row dicts by region into the
    (Cohort, [row_dict]) shape build.py's `grouped_rows` context var takes."""
    by_region = {}
    for r in rows:
        by_region.setdefault(r["region"], []).append(r)
    labels = {"US": "US Sectors", "EU": "EU Sectors", "THEME": "Themes"}
    return [
        (Cohort(region=region, label=labels.get(region, region), benchmark="", instruments={}), rs)
        for region, rs in by_region.items()
    ]

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


def _dict_literal_keys(text: str, marker_re: "re.Pattern[str]") -> set[str]:
    """Find every `{ "key": value, ... }` dict literal whose opening brace is
    matched by ``marker_re``, and return the union of their string keys.

    Uses brace depth-counting so nested dicts/lists inside a value don't
    confuse the block boundary.
    """
    keys: set[str] = set()
    for m in marker_re.finditer(text):
        start = m.end() - 1  # position of the opening "{"
        depth = 0
        block = None
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    block = text[start + 1:i]
                    break
        if block is not None:
            keys.update(re.findall(r'"(\w+)"\s*:', block))
    return keys


def _render_context_keys() -> set[str]:
    """Collect the union of keys that end up in the three page render contexts.

    Post hub-file-split refactor, build.py no longer builds one flat
    `context=dict(...)` call per page — it assembles a `{...}` dict literal
    (`sectors_ctx`, `sentiment_ctx`) and merges in the keys returned by each
    module's `build_page_context` / `build_sectors_context` via
    `.update(...)`. This walks both sources:
      1. The literal `xxx_ctx = { ... }` dicts declared in build.py's main().
      2. The `return { ... }` dict literals of the context-builder functions
         in dashboard/figures.py, sentiment.py, badges.py, and macro.py.
    """
    dashboard_dir = Path(__file__).parent.parent / "dashboard"
    all_keys: set[str] = set()

    build_text = (dashboard_dir / "build.py").read_text()
    all_keys |= _dict_literal_keys(build_text, re.compile(r"\w+_ctx\s*=\s*\{"))

    for module_name in ("figures.py", "sentiment.py", "badges.py", "macro.py"):
        module_text = (dashboard_dir / module_name).read_text()
        # Grabs every `return { ... }` dict literal in the module — a superset
        # of just the context-builder functions is fine here since extra keys
        # (e.g. macro.build_macro_context's nested "spy_last" etc.) only widen
        # the set, never cause a false "missing" report below.
        all_keys |= _dict_literal_keys(module_text, re.compile(r"return \{"))
    return all_keys


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
            leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
            cohort_list=[], cohorts_json=json.dumps([]), **_horizon_ctx(), cohort_charts_json=json.dumps({}),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            scan_history_json=json.dumps({"scans": [], "scores": {}}),
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
            leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
            cohort_list=[], cohorts_json=json.dumps([]), **_horizon_ctx(), cohort_charts_json=json.dumps({}),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            scan_history_json=json.dumps({"scans": [], "scores": {}}),
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
    # The blend control is gated on sentiment_ranking_enabled, which this context
    # does not set — so it renders absent, and Undefined is falsy. RESCORE_DATA
    # and rescore.js stay unconditional: the sentiment slider is only one of
    # their consumers (the badge rules and the trajectory maths are the others),
    # so gating the control must not strip the data or the module.
    # tests/test_sentiment_alpha_gate.py covers both directions of the flag.
    assert 'id="sentiment-toggle"' not in html
    assert 'id="sentiment-weight"' not in html
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
        leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
        cohort_list=[], cohorts_json=_json.dumps([]), **_horizon_ctx(_json.dumps), cohort_charts_json=_json.dumps({}), sentiment_scatter_json="{}",
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=_json.dumps({"scans": [], "scores": {}}),
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
    grouped_rows = _grouped_rows_for(lb_rows)
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        us_leaderboard_rows=[r for r in lb_rows if r["region"] == "US"],
        eu_leaderboard_rows=[r for r in lb_rows if r["region"] == "EU"],
        cohort_list=[c for c, _ in grouped_rows], grouped_rows=grouped_rows,
        has_any_rows=any(rs for _, rs in grouped_rows),
        cohorts_json=_json.dumps([]), **_horizon_ctx(_json.dumps),
        cohort_charts_json=_json.dumps({}), sentiment_scatter_json="{}",
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=_json.dumps({"scans": [], "scores": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
    ))
    html = out.read_text()
    assert 'data-view=' not in html
    assert 'sector-view-toggle' not in html
    assert 'data-sector-key="US|Technology"' in html


# ---------------------------------------------------------------------------
# Render-based tests: call build functions, check output HTML
# ---------------------------------------------------------------------------

def test_leaderboard_render_with_breakdown_panel(tmp_path):
    """Render a full leaderboard row with breakdown HTML and verify structure."""
    import json as _json

    df = pd.DataFrame([
        dict(scan_id=1, run_at="2026-07-01T12:00:00", region="US",
             gics_sector="Technology", level_score=0.7, change_score=0.4,
             data_score=0.55, sentiment_score=0.2, composite=0.55, rank=1.0),
        dict(scan_id=1, run_at="2026-07-01T12:00:00", region="US",
             gics_sector="Energy", level_score=-0.3, change_score=-0.1,
             data_score=-0.2, sentiment_score=0.0, composite=-0.2, rank=2.0),
    ])
    lb_rows, scan_date = _build_leaderboard_rows(df)

    # Enrich rows with breakdown HTML as main() does
    universe = {"us_sectors": {"Technology": "XLK", "Energy": "XLE"},
                "us_benchmark": "RSP", "eu_sectors": {}, "eu_benchmark": "EXSA.DE"}
    weights = {"pillars": {"data": 1.0}, "data_pillar": {"level": 0.5, "change": 0.5},
               "level_signals": {"rs_ratio": 1}, "change_signals": {"rs_momentum": 1}}
    for r in lb_rows:
        key = f"{r['region']}|{r['sector']}"
        r["key"] = key
        r["sector_id"] = key.replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "->"; r["trajectory_state"] = "flat"
        r["setup"] = None
        r["breakdown_html"] = _build_breakdown_html(
            key, {"composite": r.get("_raw_composite", 0), "data_score": 0.5,
                  "level_score": 0.4, "change_score": 0.3},
            [], universe, weights,
        )

    out = tmp_path / "index.html"
    grouped_rows = _grouped_rows_for(lb_rows)
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        us_leaderboard_rows=[r for r in lb_rows if r["region"] == "US"],
        eu_leaderboard_rows=[r for r in lb_rows if r["region"] == "EU"],
        cohort_list=[c for c, _ in grouped_rows], grouped_rows=grouped_rows,
        has_any_rows=any(rs for _, rs in grouped_rows),
        cohorts_json=_json.dumps([]), **_horizon_ctx(_json.dumps),
        cohort_charts_json=_json.dumps({}),
        sentiment_scatter_json=_make_mock_plotly_json(),
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=_json.dumps({"scans": [], "scores": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
        backtest_json=_json.dumps({}), backtest_metrics=[], has_backtest=False,
        rotation_json=_json.dumps([]), has_rotations=False,
    ))
    html = out.read_text()

    # Both sectors rendered
    assert "Technology" in html
    assert "Energy" in html
    # Breakdown panel present
    assert "breakdown-inner" in html
    assert "score-tree" in html
    # Rank column rendered
    assert ">1<" in html or ">1 <" in html or "rank" in html.lower()
    # No empty JS vars
    assert not re.compile(r"var\s+\w+\s*=\s*;").findall(html)


def test_figure_builders_produce_valid_plotly_json():
    """All figure builders should return valid Plotly JSON (data + layout)."""
    df = _minimal_history_df()

    # RRG
    rrg_json = _build_rrg_figure(df.assign(rs_ratio=100.5, rs_momentum=99.8))
    parsed = json.loads(rrg_json)
    assert "data" in parsed and "layout" in parsed

    # Movers (need 2 scans for a meaningful chart)
    two_scan_df = pd.concat([
        df.assign(scan_id=1),
        df.assign(scan_id=2, composite=lambda x: x["composite"] + 0.1, rank=lambda x: x["rank"]),
    ], ignore_index=True)
    movers_json = _build_movers_figure(two_scan_df)
    parsed = json.loads(movers_json)
    assert "data" in parsed and "layout" in parsed

    # History
    hist_json = _build_history_figure(df)
    parsed = json.loads(hist_json)
    assert "data" in parsed and "layout" in parsed


def test_rank_trajectories_computation():
    """_compute_rank_trajectories returns correct trajectory states."""
    rows = []
    for scan_id in range(1, 6):
        # Technology improving (rank going from 5 down to 1)
        rows.append(dict(scan_id=scan_id, region="US", gics_sector="Technology",
                         rank=6 - scan_id))
        # Energy worsening (rank going from 1 up to 5)
        rows.append(dict(scan_id=scan_id, region="US", gics_sector="Energy",
                         rank=scan_id))
    df = pd.DataFrame(rows)
    result = _compute_rank_trajectories(df)

    assert "US|Technology" in result
    assert "US|Energy" in result
    # Improving rank (going down) = negative slope = up/strong_up
    assert result["US|Technology"]["state"] in ("up", "strong_up")
    # Worsening rank (going up) = positive slope = down/strong_down
    assert result["US|Energy"]["state"] in ("down", "strong_down")


def test_safe_float_handles_edge_cases():
    """_safe_float correctly handles None, NaN, and valid floats."""
    assert _safe_float(None) is None
    assert _safe_float(float("nan")) is None
    assert _safe_float(0.5) == 0.5
    assert _safe_float(0) == 0.0
    assert _safe_float("invalid") is None


def test_format_raw_value_formatting():
    """_format_raw_value applies correct formatting per signal type."""
    # rs_ratio/rs_momentum: 1 decimal
    assert _format_raw_value("rs_ratio", 102.5) == "102.5"
    assert _format_raw_value("rs_momentum", 98.3) == "98.3"
    # breadth: percentage
    assert _format_raw_value("breadth_above_50dma", 0.65) == "65%"
    # slopes: signed 3 decimals
    assert _format_raw_value("ma50_slope", 0.003) == "+0.003"
    # returns: percentage
    assert "%" in _format_raw_value("return_1m", 0.05)
    # NaN/None: em-dash
    assert _format_raw_value("rs_ratio", None) == "—"
    assert _format_raw_value("rs_ratio", float("nan")) == "—"


def test_render_context_keys_finds_all_render_calls():
    """_render_context_keys should find keys from all _render() calls, not just the first."""
    keys = _render_context_keys()
    # index.html context keys
    assert "scan_date" in keys
    assert "leaderboard_rows" in keys
    assert "cohort_charts_json" in keys
    # sentiment.html context keys
    assert "sentiment_scatter_json" in keys
    assert "sentiment_signal_rows" in keys
    # index.html cohort keys. The leaderboard renders one ungrouped cohort,
    # so there is no grouped_rows any more — just the flat row list above.
    assert "cohort_list" in keys
    assert "grouped_rows" not in keys


# ---------------------------------------------------------------------------
# Sentiment signal row builder tests
# ---------------------------------------------------------------------------

def test_sentiment_row_includes_finbert_columns():
    """FinBERT news signals are formatted into the row dict; sorted by polarity."""
    from dashboard.sentiment import _build_sentiment_signal_rows

    df = pd.DataFrame([
        {"region": "US", "gics_sector": "Energy", "signal_name": "news_polarity", "value": -0.10},
        {"region": "US", "gics_sector": "Energy", "signal_name": "news_count", "value": 12.0},
        {"region": "US", "gics_sector": "Energy", "signal_name": "news_positive_pct", "value": 0.25},
        {"region": "US", "gics_sector": "Energy", "signal_name": "news_negative_pct", "value": 0.40},
        {"region": "US", "gics_sector": "Technology", "signal_name": "news_polarity", "value": 0.30},
        {"region": "US", "gics_sector": "Technology", "signal_name": "news_count", "value": 8.0},
        {"region": "US", "gics_sector": "Technology", "signal_name": "news_positive_pct", "value": 0.60},
        {"region": "US", "gics_sector": "Technology", "signal_name": "news_negative_pct", "value": 0.10},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 2
    # Sorted by polarity descending: Technology (+0.30) first.
    assert rows[0]["sector"] == "Technology"
    assert rows[0]["news_polarity"] == "+0.30"
    assert rows[0]["news_count"] == "8"
    assert rows[0]["news_positive_pct"] == "60%"
    assert rows[1]["sector"] == "Energy"
    assert rows[1]["news_polarity"] == "-0.10"


def test_sentiment_row_missing_count_shows_dash():
    """A sector with no news_count value shows an em-dash for Articles."""
    from dashboard.sentiment import _build_sentiment_signal_rows

    df = pd.DataFrame([
        {"region": "EU", "gics_sector": "Banks", "signal_name": "news_polarity", "value": 0.05},
    ])
    rows = _build_sentiment_signal_rows(df)
    assert len(rows) == 1
    assert rows[0]["news_count"] == "—"


# ---------------------------------------------------------------------------
# _build_scan_history_data tests
# ---------------------------------------------------------------------------

def test_build_scan_history_data_shape():
    """_build_scan_history_data returns scans and scores with correct structure."""
    from dashboard.figures import _build_scan_history_data

    rows = []
    for scan_id, run_at in [(1, "2026-06-22T00:00:00"), (2, "2026-06-23T00:00:00")]:
        for region, sector, comp, lvl, chg, data, sent, rank in [
            ("US", "Technology", 0.8, 0.7, 0.4, 0.55, 0.2, 1.0),
            ("EU", "Energy", 0.3, 0.2, 0.1, 0.15, 0.0, 2.0),
        ]:
            rows.append({
                "scan_id": scan_id, "run_at": run_at, "region": region,
                "gics_sector": sector, "level_score": lvl, "change_score": chg,
                "data_score": data, "sentiment_score": sent, "composite": comp,
                "rank": rank,
            })
    df = pd.DataFrame(rows)
    result = _build_scan_history_data(df)

    assert "scans" in result
    assert "scores" in result
    assert len(result["scans"]) == 2
    # Newest first
    assert result["scans"][0]["id"] == 2
    assert result["scans"][1]["id"] == 1
    # Each scan entry has required fields
    for s in result["scans"]:
        assert "id" in s and "date" in s and "sectors" in s and "top" in s
    # Scores keyed by string scan_id
    assert "2" in result["scores"]
    assert "1" in result["scores"]
    # Each sector present
    assert "US|Technology" in result["scores"]["2"]
    assert "EU|Energy" in result["scores"]["2"]
    # Required score fields
    for key, sc in result["scores"]["2"].items():
        for field in ("rank", "composite", "level", "change", "data", "sentiment"):
            assert field in sc, f"Missing {field} in {key}"


def test_build_scan_history_data_empty():
    """Empty DataFrame returns empty structure."""
    from dashboard.figures import _build_scan_history_data

    df = pd.DataFrame(columns=[
        "scan_id", "run_at", "region", "gics_sector", "level_score",
        "change_score", "data_score", "sentiment_score", "composite", "rank",
    ])
    result = _build_scan_history_data(df)
    assert result == {"scans": [], "scores": {}}


# ---------------------------------------------------------------------------
# Renderable scan history — template render test
# ---------------------------------------------------------------------------

def test_scan_history_json_in_rendered_output(tmp_path):
    """Rendered index.html contains SCAN_HISTORY variable with valid JSON."""
    scan_history = {
        "scans": [{"id": 2, "date": "2026-07-12 06:00 UTC", "sectors": 22, "top": "Technology (US)"}],
        "scores": {"2": {"US|Technology": {"rank": 1, "composite": 0.8, "level": 0.7, "change": 0.4, "data": 0.55, "sentiment": 0.2}}},
    }
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-07-12",
            scan_index=[{"scan_id": 2, "run_at_display": "2026-07-12 06:00 UTC",
                         "run_at_raw": "2026-07-12T06:00:00", "sector_count": 22,
                         "top_sector": "Technology", "top_region": "US"}],
            active_scan_id=2,
            leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
            cohort_list=[], cohorts_json=json.dumps([]), **_horizon_ctx(), cohort_charts_json=json.dumps({}),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            scan_history_json=json.dumps(scan_history),
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
    assert "var SCAN_HISTORY =" in html
    assert "scan-history-banner" in html
    assert 'data-scan-id="2"' in html
    # Extract and parse the JSON
    start = html.index("var SCAN_HISTORY =") + len("var SCAN_HISTORY =")
    end = html.index(";", start)
    parsed = json.loads(html[start:end].strip())
    assert "scans" in parsed
    assert "scores" in parsed
    assert parsed["scans"][0]["id"] == 2


def test_scan_digest_markup_in_rendered_output(tmp_path):
    """Rendered index.html contains the scan-digest banner, script tag, and i18n keys."""
    scan_history = {
        "scans": [{"id": 2, "date": "2026-07-12 06:00 UTC", "sectors": 22, "top": "Technology (US)"}],
        "scores": {"2": {"US|Technology": {"rank": 1, "composite": 0.8, "level": 0.7, "change": 0.4, "data": 0.55, "sentiment": 0.2}}},
    }
    out = tmp_path / "index.html"
    _render(
        template_path=_TEMPLATE,
        out_path=out,
        context=dict(
            scan_date="2026-07-12",
            scan_index=[{"scan_id": 2, "run_at_display": "2026-07-12 06:00 UTC",
                         "run_at_raw": "2026-07-12T06:00:00", "sector_count": 22,
                         "top_sector": "Technology", "top_region": "US"}],
            active_scan_id=2,
            leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
            cohort_list=[], cohorts_json=json.dumps([]), **_horizon_ctx(), cohort_charts_json=json.dumps({}),
            sentiment_scatter_json=_make_mock_plotly_json(),
            rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
            scan_history_json=json.dumps(scan_history),
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
    assert 'id="scan-digest-banner"' in html
    assert "assets/scan-digest.js" in html
    assert 'data-i18n="digest_new_top5"' in html
    assert 'data-i18n="digest_gains"' in html
    assert 'data-i18n="digest_drops"' in html
    assert 'id="digest-chips-entries"' in html
    assert 'id="digest-chips-up"' in html
    assert 'id="digest-chips-down"' in html


# ---------------------------------------------------------------------------
# Per-cohort chart context (Task 3 — chart tabs get a cohort selector)
# ---------------------------------------------------------------------------

def test_cohort_charts_context_is_keyed_by_cohort():
    """One chart context per configured cohort, so the selector can switch
    between them without the page holding three differently-named blobs."""
    import pandas as pd
    from pathlib import Path
    from dashboard.figures import build_cohort_chart_context

    rows = []
    for scan in range(1, 7):
        for region, name in (("US", "Energy"), ("EU", "Banks"), ("THEME", "Space")):
            rows.append({
                "scan_id": scan, "run_at": f"2026-07-{scan:02d}",
                "region": region, "gics_sector": name,
                "level_score": 0.5, "change_score": 0.4, "data_score": 0.45,
                "sentiment_score": None, "composite": 0.45, "rank": 1.0,
            })
    df = pd.DataFrame(rows)
    shared = {
        "all_scores_df": df,
        "history_df": df,
        "rrg_df": df.assign(rs_ratio=101.0, rs_momentum=99.0),
        "themes_cfg": {"benchmark": "ACWI", "themes": {"Space": {"ticker": "UFO"}}},
        "project_root": Path("/tmp"),
    }
    charts = build_cohort_chart_context(shared)["cohort_charts"]

    assert set(charts) == {"THEME"}
    for region in ("THEME",):
        assert {"rrg", "movers", "history"} <= set(charts[region])


# ---------------------------------------------------------------------------
# Cohort-aware JS assets (Task 4 — auth.js live upgrade + scan-history.js)
# ---------------------------------------------------------------------------

def test_scan_history_does_not_dump_unknown_regions_into_us():
    src = (Path(__file__).parent.parent / "dashboard/assets/scan-history.js").read_text()
    assert "regionGroups.US.push" not in src, (
        "unknown regions fall into the US group — a THEME row would render as a sector"
    )
    assert "{ US: [], EU: [] }" not in src, "region groups are still hardcoded"


def test_auth_js_region_labels_are_not_hardcoded():
    src = (Path(__file__).parent.parent / "dashboard/assets/auth.js").read_text()
    assert '[["US", "US Sectors"], ["EU", "EU Sectors"]]' not in src


# ---------------------------------------------------------------------------
# positions.js — itemForRow row classification (theme star-toggle corruption)
# ---------------------------------------------------------------------------

def _extract_item_for_row_js():
    """Pull the itemForRow() function body verbatim out of positions.js so the
    test executes the real production code, not a re-implementation of it."""
    src = (Path(__file__).parent.parent / "dashboard/assets/positions.js").read_text()
    match = re.search(r"function itemForRow\(tr\) \{.*?\n  \}", src, re.S)
    assert match, "itemForRow() not found in dashboard/assets/positions.js"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_item_for_row_classifies_by_region_not_dataset_shape():
    """Regression for the theme-star corruption bug: itemForRow must key off
    data-region === "THEME", not off which of data-sector/data-theme happens
    to be present on the row. auth.js's live-upgrade path (renderLatestRows)
    sets tr.dataset.sector on EVERY rebuilt row, including THEME ones, so a
    classifier keyed on "has data-sector" misfiled themes as
    item_type="sector", region="THEME" — silently breaking held-theme exit
    alerts and orphaning positions written by the old themes.html.j2 (which
    keyed themes as item_type="theme", region="")."""
    fn_src = _extract_item_for_row_js()
    script = f"""
        {fn_src}
        const cases = [
          // Live-upgraded THEME row: auth.js sets data-sector, not data-theme.
          {{ dataset: {{ region: "THEME", sector: "Space" }} }},
          // Static (pre-login) THEME row: index.html.j2 also sets data-sector.
          {{ dataset: {{ region: "THEME", sector: "Space" }} }},
          // Legacy markup shape (old deleted themes.html.j2): only data-theme set.
          {{ dataset: {{ region: "THEME", theme: "Space" }} }},
          // Ordinary sector row must still classify as a sector.
          {{ dataset: {{ region: "US", sector: "Energy" }} }},
          {{ dataset: {{ region: "EU", sector: "Banks" }} }},
          // Region header / breakdown row: neither identity attribute set.
          {{ dataset: {{}} }},
        ];
        process.stdout.write(JSON.stringify(cases.map(itemForRow)));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    results = json.loads(res.stdout)

    assert results[0] == {"item_type": "theme", "region": "", "name": "Space"}
    assert results[1] == {"item_type": "theme", "region": "", "name": "Space"}
    assert results[2] == {"item_type": "theme", "region": "", "name": "Space"}
    assert results[3] == {"item_type": "sector", "region": "US", "name": "Energy"}
    assert results[4] == {"item_type": "sector", "region": "EU", "name": "Banks"}
    assert results[5] is None


# ---------------------------------------------------------------------------
# Leaderboard column structure — three builders must agree
# ---------------------------------------------------------------------------

def _count_cells(fragment: str) -> int:
    """Number of <td ...> openings in a row-building fragment."""
    import re
    return len(re.findall(r"<td\b", fragment))


def test_leaderboard_row_builders_emit_the_same_column_count():
    """The server template, the signed-in live upgrade (auth.js) and the
    scan-history view each build leaderboard rows independently. A column
    added or removed in one and not the others silently misaligns every row
    against the header, so pin them together.
    """
    root = Path(__file__).parent.parent

    auth = (root / "dashboard/assets/auth.js").read_text()
    body = auth.split("tr.innerHTML =", 1)[1].split("tbody.appendChild(tr)", 1)[0]
    auth_cells = _count_cells(body)

    hist = (root / "dashboard/assets/scan-history.js").read_text()
    hbody = hist.split('html += \'<tr class="leaderboard-row">\'', 1)[1].split('"</tr>"', 1)[0]
    hist_cells = _count_cells(hbody)

    tpl = (root / "dashboard/templates/index.html.j2").read_text()
    tbody = tpl.split('{% for row in leaderboard_rows %}', 1)[1].split("{% endfor %}", 1)[0]
    # The template's row also contains the breakdown <td>; count only the data row.
    tpl_row = tbody.split('<tr class="breakdown-row"', 1)[0]
    tpl_cells = _count_cells(tpl_row)

    assert auth_cells == hist_cells == tpl_cells, (
        f"leaderboard column count drifted: template={tpl_cells} "
        f"auth.js={auth_cells} scan-history.js={hist_cells}"
    )


def test_leaderboard_colspans_match_the_column_count():
    """A stale colspan leaves the breakdown panel and empty-state rows spanning
    the wrong width once a column is added or removed."""
    import re
    root = Path(__file__).parent.parent
    tpl = (root / "dashboard/templates/index.html.j2").read_text()

    tbody = tpl.split('{% for row in leaderboard_rows %}', 1)[1].split("{% endfor %}", 1)[0]
    tpl_row = tbody.split('<tr class="breakdown-row"', 1)[0]
    n_cols = _count_cells(tpl_row)

    header = tpl.split("<thead>", 1)[1].split("</thead>", 1)[0]
    n_headers = len(re.findall(r"<th\b", header))
    assert n_headers == n_cols, f"{n_headers} headers vs {n_cols} body cells"

    # Every colspan in the leaderboard table must equal the column count.
    table = tpl.split('id="leaderboard-table"', 1)[1].split("</table>", 1)[0]
    for span in re.findall(r'colspan="(\d+)"', table):
        assert int(span) == n_cols, f"colspan={span} but table has {n_cols} columns"


def test_composite_bar_python_and_js_agree():
    """`_composite_bar` (build) and `compositeBar` (rescore.js) render the same
    cell. The static build, the signed-in upgrade and the scan-history view
    would otherwise disagree on the same number."""
    import re
    from dashboard.rows import _composite_bar, COMPOSITE_FULL_SCALE

    js = (Path(__file__).parent.parent / "dashboard/assets/rescore.js").read_text()
    m = re.search(r"var COMPOSITE_FULL_SCALE = ([\d.]+);", js)
    assert m, "COMPOSITE_FULL_SCALE missing from rescore.js"
    assert float(m.group(1)) == COMPOSITE_FULL_SCALE, "full-scale constant drifted"

    # Positive grows right from centre, negative grows left, both half-width max.
    pos = _composite_bar(1.5)
    neg = _composite_bar(-1.5)
    assert "left:50%" in pos and "width:50.0%" in pos
    assert "right:50%" in neg and "width:50.0%" in neg
    assert "cbar pos" in pos and "cbar neg" in neg

    # Equal magnitudes must NOT render identically (the defect being fixed).
    assert _composite_bar(2.0) != _composite_bar(-2.0)

    # Beyond full scale clamps rather than overflowing the track.
    assert "width:50.0%" in _composite_bar(99.0)
    assert _composite_bar(None) == (
        '<span class="cbar-wrap"></span><span class="cbar-val">—</span>'
    )


def test_z_bar_is_centre_origin():
    """The breakdown z-bar encoded sign only as colour: -2.5 and +2.5 both
    filled from the left, so direction carried no meaning."""
    from dashboard.breakdown import _z_bar

    up, _ = _z_bar(2.5)
    down, _ = _z_bar(-2.5)
    assert "left:50%" in up, "positive z should grow right from centre"
    assert "right:50%" in down, "negative z should grow left from centre"
    assert up != down, "equal magnitudes must not render identically"

    # Magnitude still encoded, and clamped at the track edge.
    small, _ = _z_bar(0.75)
    assert "width:12.5%" in small
    big, _ = _z_bar(9.0)
    assert "width:50.0%" in big

    # The neutral band keeps its muted chip.
    _, chip = _z_bar(0.1)
    assert "neut" in chip

def test_gate_modal_uses_the_shared_modal_helper():
    """The gate modal declared aria-modal="true" while implementing none of it:
    no focus move, no trap, no Escape, no backdrop close. It must go through
    window.SMModal (templates/_modal.js.j2) like every other modal, and must
    fail open if that helper is missing rather than taking sign-in down."""
    auth = (Path(__file__).parent.parent / "dashboard/assets/auth.js").read_text()

    assert "SMModal.bind(modal" in auth, "gate modal not bound to the shared helper"
    assert "closeBtn: continueBtn" in auth, "dismiss button not wired as the close control"
    # Fail-open path preserved.
    assert "modal.hidden = !show" in auth, "plain-toggle fallback removed"


def test_shared_modal_helper_implements_the_aria_modal_contract():
    """A dialog claiming aria-modal must actually trap focus, close on Escape
    and on backdrop click, and restore focus to whatever opened it."""
    js = (Path(__file__).parent.parent
          / "dashboard/templates/_modal.js.j2").read_text()

    assert 'e.key === "Escape"' in js, "no Escape handling"
    assert 'e.key !== "Tab"' in js and "shiftKey" in js, "no focus trap"
    assert "e.target === overlay" in js, "no backdrop close"
    assert "lastFocus = document.activeElement" in js, "focus not captured on open"
    assert "lastFocus.focus()" in js, "focus not restored on close"


def test_every_aria_modal_dialog_is_bound_to_the_helper():
    """Guard against a future modal re-introducing the same defect: anything
    declaring aria-modal must be reachable by SMModal.bind."""
    import re
    root = Path(__file__).parent.parent
    bound = set()
    for f in ("dashboard/assets/auth.js",
              "dashboard/templates/_methodology.html.j2",
              "dashboard/templates/index.html.j2",
              "dashboard/templates/sentiment.html.j2"):
        bound |= set(re.findall(r"SMModal\.bind\(\s*(\w+)", (root / f).read_text()))
    assert bound, "no modal is bound to the shared helper at all"

    # Every page that renders an aria-modal dialog must also include the helper.
    for page in ("dashboard/templates/index.html.j2",
                 "dashboard/templates/sentiment.html.j2"):
        src = (root / page).read_text()
        if 'aria-modal="true"' in src:
            assert "_modal.js.j2" in src, f"{page} has a modal but never includes the helper"
