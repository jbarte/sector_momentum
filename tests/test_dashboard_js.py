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
             "top_n": h.top_n, "buffer_frac": h.buffer_frac,
             "trades_per_year": h.trades_per_year,
             "median_holding_days": h.median_holding_days,
             "review_dates": _review_dates(h, since="2026-01-15")} for h in hs
        ]),
        horizon_default_json=dumps({
            "key": d.key, "label": d.label, "top_n": d.top_n, "buffer_frac": d.buffer_frac}),
        unbuyable_json=dumps([]),
        theme_tickers_json=dumps({}),
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

def test_leaderboard_rows_carry_the_level_change_cell_and_its_sort_key():
    """The 6-column restructure (2026-08-19) dropped the Sentiment column and
    merged Level and Change into one stacked-bar cell. The row dict must carry
    the rendered cell plus `_raw_level`, the numeric sort key sortTable() reads
    from that cell's data-sort-value — and must no longer carry the three
    pre-formatted score strings the old separate columns printed."""
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

    tech = by_sector["Technology"]
    assert "lc-cell" in tech["level_change_bars"]
    assert tech["_raw_level"] == pytest.approx(0.5)
    assert tech["_raw_change"] == pytest.approx(0.3)
    assert by_sector["Energy"]["_raw_level"] == pytest.approx(-0.2)

    # The old per-column strings are gone from the dict entirely, not merely
    # unused by the template.
    for gone in ("level_score", "change_score", "sentiment_score"):
        assert gone not in tech, f"{gone} still in the leaderboard row dict"


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
# positions.js — ★/☆ toggle's title/aria-label must be translatable
# ---------------------------------------------------------------------------

def test_position_toggle_tooltips_are_i18n_keyed_not_hardcoded_english():
    """applyRowState() used to set btn.title / aria-label to a literal English
    string with no i18n hookup, so the toggle stayed "Held — click to remove"
    for Swedish readers even after a language switch, unlike every other
    control on the page. It must carry the same data-i18n-title/-aria pattern
    auth.js uses for its own dynamically-inserted markup (UNBUYABLE_BADGE)."""
    src = (Path(__file__).parent.parent / "dashboard/assets/positions.js").read_text()
    assert 'setAttribute("data-i18n-title", key)' in src
    assert 'setAttribute("data-i18n-aria", key)' in src
    assert '"position_held_tip"' in src
    assert '"position_mark_held_tip"' in src


def test_position_toggle_calls_applylangtoel_after_state_changes():
    """The translation only takes effect once something calls
    window.applyLangToEl (positions.js has no access to the SV dict itself —
    it lives in the per-page inline i18n IIFE). Guards against the fix
    landing half-done: keys set on the element but nothing ever re-running
    the i18n pass to apply them.

    Scoped, not the page-wide window.applyLang: a full rescan on every star
    click would also re-run applyLang()'s own applyFilters() side effect for
    no reason, and this button is the only thing that changed."""
    src = (Path(__file__).parent.parent / "dashboard/assets/positions.js").read_text()
    assert "window.applyLangToEl" in src
    assert "window.applyLang(" not in src, (
        "positions.js should use the scoped applyLangToEl(), not a "
        "page-wide applyLang() rescan, for a single button's retranslation"
    )


def test_i18n_pass_exposes_the_scoped_element_translator():
    """applyLangToEl must exist (positions.js depends on it) and must read
    data-i18n-title/-aria the same way apply()'s own document-wide passes do,
    so a key translated by one path is translated identically by the other."""
    js = (Path(__file__).parent.parent
          / "dashboard/templates/_i18n.html.j2").read_text()
    assert "window.applyLangToEl" in js
    assert 'el.hasAttribute("data-i18n-title")' in js
    assert 'el.hasAttribute("data-i18n-aria")' in js


@pytest.mark.parametrize("key", ["position_held_tip", "position_mark_held_tip"])
def test_swedish_has_the_position_toggle_strings(key):
    sv = (Path(__file__).parent.parent
          / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert f"{key}:" in sv


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


def _row_builder_fragments() -> dict[str, str]:
    """The three independent leaderboard row-building fragments, as source text."""
    root = Path(__file__).parent.parent

    auth = (root / "dashboard/assets/auth.js").read_text()
    hist = (root / "dashboard/assets/scan-history.js").read_text()
    tpl = (root / "dashboard/templates/index.html.j2").read_text()
    tbody = tpl.split("{% for row in leaderboard_rows %}", 1)[1].split("{% endfor %}", 1)[0]

    return {
        "auth.js": auth.split("tr.innerHTML =", 1)[1].split("tbody.appendChild(tr)", 1)[0],
        "scan-history.js": hist.split(
            'html += \'<tr class="leaderboard-row">\'', 1)[1].split('"</tr>"', 1)[0],
        "index.html.j2": tbody.split('<tr class="breakdown-row"', 1)[0],
    }


def test_leaderboard_row_builders_emit_the_same_cell_classes():
    """Stronger than the column COUNT above: the three builders must put the
    same class on the same cell index, in the same order. Every consumer of a
    leaderboard row addresses cells positionally and by class —
    applyHorizonBadges() reads `tr.cells[0] .rank-badge` and `tr.cells[1]`,
    sortTable() special-cases column 3's `data-sort-value`, and the CSS styles
    `.rank-cell` / `.theme-cell` / `.composite-cell` / `.delta-cell`. A builder
    that keeps the right number of cells but drops a class (or reorders two)
    passes the count test and still breaks those consumers silently.
    """
    import re

    def classes(fragment: str) -> list[str]:
        out = []
        for td in re.findall(r"<td\b([^>]*)>", fragment):
            m = re.search(r'class=\\?["\']([^"\'\\]*)', td)
            out.append(m.group(1).strip() if m else "")
        return out

    frags = _row_builder_fragments()
    got = {name: classes(frag) for name, frag in frags.items()}

    # The template's rank/theme cells carry Jinja conditionals inside the class
    # attribute (`in-band-rail`, and the setup/unbuyable spans); compare only
    # the leading literal class name, which is what the consumers match on.
    def head(vals):
        return [v.split("{")[0].strip() for v in vals]

    # Five cells since the Trend column was removed and its badge moved into
    # the theme cell; the trailing "" is the unclassed Level/Change cell.
    expected = ["rank-cell", "theme-cell", "composite-cell", "", "delta-cell"]
    for name, vals in got.items():
        assert head(vals) == expected, f"{name} cell classes drifted: {head(vals)}"


def test_every_row_builder_carries_the_level_change_sort_key():
    """sortTable()'s column-3 branch reads `data-sort-value` because the merged
    Level/Change cell holds two numbers its innerText parse cannot
    disambiguate. A builder that omits the attribute makes that column sort
    every one of its rows as NaN."""
    for name, frag in _row_builder_fragments().items():
        assert "data-sort-value" in frag, f"{name} omits the Level/Change sort key"


# ---------------------------------------------------------------------------
# Trend badge placement — the methodology says it must be read WITH the setup
# badge, so it lives in the theme cell rather than a far-right column.
# ---------------------------------------------------------------------------

_needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not available")


def _rescore_traj_badge_source() -> str:
    """The Trend badge's actual RENDERED markup, from running the real
    functions under node — not a guess from slicing rescore.js's source text.

    auth.js's `trendInner` calls Rescore.trajBadgeHTML(...) rather than
    building the markup inline (both it and index.html.j2's updateRows() share
    this one function — see BACKLOG.md, "Rescore path flattens the Trend badge
    to a bare glyph"). Tests that used to grep trendInner's own definition for
    "traj-badge" etc. must follow that call into its actual output or they
    pass on a one-line delegation that builds nothing.

    Code review (2026-08-30) flagged the original version of this helper,
    which extracted the two functions' bodies by `str.split` on their source
    text — fragile to reformatting (a reindent silently mis-slices the body)
    and, more importantly, never actually RUNS the code: it only proves a
    substring occurs somewhere in the file, matching test_rescore_parity.py's
    complaint about source-text checks in general. This calls the real
    exported functions under `node` instead, the same pattern that file
    already uses, with representative non-empty arguments so every literal
    class/attribute this module cares about appears in the output.
    """
    rescore_js = Path(__file__).parent.parent / "dashboard" / "assets" / "rescore.js"
    script = f"""
        const R = require({json.dumps(str(rescore_js))});
        process.stdout.write(
          R.trajBadgeHTML("up", "\\u2191", "rising")
          + R.trajBadgeInner("\\u2191", "rising")
        );
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return res.stdout


def _auth_trend_badge_source() -> str:
    """auth.js's `trendInner` definition, resolved through its indirection.

    `trendInner` delegates to Rescore.trajBadgeHTML rather than building the
    markup inline — a caller checking `trendInner`'s own text alone would pass
    on a bare function call that builds nothing. Shared by every test that
    needs "the markup auth.js's Trend badge actually renders" so the
    indirection-following logic exists in one place, not one per test.
    """
    auth = (Path(__file__).parent.parent / "dashboard/assets/auth.js").read_text()
    trend_def = auth.split("var trendInner", 1)[1].split(";", 1)[0]
    if "trajBadgeHTML" in trend_def:
        return trend_def + _rescore_traj_badge_source()
    return trend_def


def _theme_cell_of(fragment: str) -> str:
    """The theme cell's span of a row-building fragment.

    Delimited by the two cell classes that bracket it rather than by a closing
    `</td>`: the template's theme cell is whitespace-controlled Jinja whose
    tags and comment markers make tag-matching unreliable, while
    `theme-cell` -> `composite-cell` is a fixed adjacency that
    test_leaderboard_row_builders_emit_the_same_cell_classes already pins.
    """
    assert "theme-cell" in fragment, "fragment has no theme cell"
    after = fragment.split("theme-cell", 1)[1]
    return after.split("composite-cell", 1)[0]


@_needs_node
def test_trend_badge_sits_in_the_theme_cell_beside_the_setup_badge():
    """The methodology tells the reader to read the setup badge and the Trend
    badge together: the badge describes the position band, and Trend is what
    says the theme is deteriorating underneath it. They used to sit at opposite
    ends of the table -- measured 651-763px apart at a desktop width, four
    columns between them -- which is not a pairing anyone reads as one.

    Both badges now live in the theme cell. Guards the builders that render a
    real trajectory; scan-history.js is exempt because a past scan has never
    carried a trend value (it rendered a literal placeholder).
    """
    frags = _row_builder_fragments()

    # The template inlines the badge markup; auth.js interpolates a variable
    # built further up. Follow that one indirection rather than accepting the
    # bare variable name, which would pass even if trendInner stopped being a
    # trajectory badge.
    tpl_cell = _theme_cell_of(frags["index.html.j2"])
    assert "traj-badge" in tpl_cell, (
        "index.html.j2: the Trend badge is not in the theme cell -- the setup "
        "badge and Trend must stay adjacent to be read together"
    )

    auth_cell = _theme_cell_of(frags["auth.js"])
    assert "trendInner" in auth_cell, (
        "auth.js: the theme cell does not render the trend badge -- the setup "
        "badge and Trend must stay adjacent to be read together"
    )
    assert "traj-badge" in _auth_trend_badge_source(), (
        "auth.js: trendInner is in the theme cell but no longer builds a "
        "trajectory badge"
    )


def test_leaderboard_has_no_separate_trend_column():
    """The Trend column was removed when its badge moved into the theme cell.
    A header left behind produces an empty sixth column in every row."""
    root = Path(__file__).parent.parent
    tpl = (root / "dashboard/templates/index.html.j2").read_text()
    header = tpl.split("<thead>", 1)[1].split("</thead>", 1)[0]
    assert "col_trend" not in header, (
        "a Trend column header survives, but its badge now renders in the "
        "theme cell -- the column would be empty"
    )


@_needs_node
def test_trend_badge_keeps_its_explanation_in_every_builder():
    """The removed `<th>` carried the only on-page explanation of what Trend
    measures (`title="Rank slope over last 3-5 scans"`). Dropping the column
    without rehoming that tooltip would delete the explanation outright, so it
    moves onto the badge itself -- translated, like every other tooltip.

    Checked in BOTH builders that render a real badge. Checking only the
    template would leave signed-in readers -- the only readers who ever see a
    setup badge to pair Trend with -- with no explanation at all.
    """
    root = Path(__file__).parent.parent

    tpl = (root / "dashboard/templates/index.html.j2").read_text()
    tpl_cell = _theme_cell_of(
        tpl.split("{% for row in leaderboard_rows %}", 1)[1].split("{% endfor %}", 1)[0]
    )

    auth_badge = _auth_trend_badge_source()

    for name, frag in (("index.html.j2", tpl_cell), ("auth.js", auth_badge)):
        assert 'data-i18n-title=' in frag.replace("\\", ""), (
            f"{name}: the Trend badge carries no translated tooltip -- the "
            f"explanation the column header used to provide is gone"
        )
        assert "trend_tip" in frag, f"{name}: tooltip does not use the trend_tip key"
        assert "title=" in frag.replace("\\", ""), (
            f"{name}: the Trend badge carries no English title fallback"
        )


@_needs_node
def test_signed_in_rows_render_no_trend_placeholder_in_the_theme_cell():
    """auth.js's trend value is optional -- `latestRowMeta` returns {} when the
    query gives it nothing to work with. That fallback used to land in its own
    <td>, where an em dash reads correctly as "no value". Spliced into the theme
    cell it becomes a bare text node right after the ticker, with no margin
    (`.theme-cell > * + *` only spaces ELEMENTS), rendering as `URA-`. The
    absence of a badge is already the correct way to say "no trend" -- the same
    reasoning that removed scan-history.js's placeholder in this change.

    The fallback now lives inside Rescore.trajBadgeHTML, not in auth.js's own
    trendInner assignment -- checked by actually CALLING it with a missing
    state under node, not by grepping either file's source text (auth.js no
    longer contains the fallback logic at all, and a source-text check of
    trajBadgeHTML's body would only prove a substring exists, not what the
    function actually returns).
    """
    rescore_js = Path(__file__).parent.parent / "dashboard" / "assets" / "rescore.js"
    script = f"""
        const R = require({json.dumps(str(rescore_js))});
        process.stdout.write(JSON.stringify(R.trajBadgeHTML(null, "up", "rising")));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(res.stdout)
    assert out == "", (
        "trajBadgeHTML no longer returns empty for a missing trajectory state "
        "-- a missing trend must render nothing, not a placeholder like an em "
        "dash, which reads as a stray character beside the ticker"
    )


def test_horizon_badge_pass_inserts_the_setup_badge_before_the_trend_badge():
    """applyHorizonBadges() writes the setup badge client-side, and on the build
    that actually ships it is the ONLY writer: `badges_gated` bakes no badge, so
    every signed-in reader gets their badge from this function. Appending it to
    the theme cell puts it AFTER the Trend badge, silently reversing the
    action-then-health-check order the template bakes -- so the shipped build
    and an ungated build would disagree about the order of the two badges this
    change exists to pair.
    """
    tpl = (Path(__file__).parent.parent
           / "dashboard/templates/index.html.j2").read_text()
    fn = tpl.split("function applyHorizonBadges", 1)[1].split("\nfunction ", 1)[0]
    fn = _strip_line_comments(fn)
    assert "insertBefore(" in fn, (
        "applyHorizonBadges no longer positions the setup badge relative to the "
        "Trend badge -- appending puts the action badge after the health check"
    )
    assert "traj-badge" in fn, (
        "applyHorizonBadges positions the badge without reference to the Trend "
        "badge, so the ordering is incidental rather than pinned"
    )


def test_trend_tip_i18n_key_exists():
    """data-i18n-title resolves against the Swedish table; a missing key leaves
    the tooltip silently English."""
    sv = (Path(__file__).parent.parent
          / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert re.search(r'trend_tip:\s*"[^"]+"', sv), "trend_tip missing from the SV table"


def test_mobile_cards_find_both_badges_by_class_not_by_cell_index():
    """renderMobileCards() reads the badges off the table row with
    querySelector on their classes, which is why moving the Trend badge between
    cells needed no change there at all. Pinned because the obvious
    "simplification" -- addressing the cells positionally -- would couple the
    card renderer to the column layout and break on the next column change.
    (Searches for _renderMobileCardsNow, not renderMobileCards, which is now
    a thin coalescing wrapper as of 2026-08-25.)
    """
    tpl = (Path(__file__).parent.parent
           / "dashboard/templates/index.html.j2").read_text()
    fn = tpl.split("function _renderMobileCardsNow()", 1)[1].split("\nfunction ", 1)[0]
    fn = _strip_line_comments(fn)
    assert "querySelector('.traj-badge')" in fn.replace('"', "'"), (
        "renderMobileCards no longer selects the Trend badge by class"
    )
    assert "querySelector('.setup-badge')" in fn.replace('"', "'"), (
        "renderMobileCards no longer selects the setup badge by class"
    )


def test_theme_cell_is_not_a_flex_table_cell():
    """`.theme-cell` and `.composite-cell` are ADJACENT columns. A <td> whose
    display is flex stops being a table-internal box, and two adjacent such
    boxes share ONE anonymous table cell — which stacks them vertically, so the
    theme name lands on top of the composite bar in a single double-width
    column. `.composite-cell` is flex, so `.theme-cell` must not be. Verified in
    a browser when this was written: the six cells' left offsets collapsed to
    four. See the rule's own comment in _tables.css.j2.

    The original regex here (`^\\.theme-cell\\s*\\{...`) only matched a bare
    `.theme-cell { ... }` rule. The actual rule is `.theme-cell > * + * { ... }`
    (a descendant-combinator selector), so that regex never matched — `m` was
    always `None` and the `if m:` body, the only place the assertion lived,
    never ran. This was the sole automated guard against the exact regression
    the module docstring above describes, and it passed vacuously no matter
    what `.theme-cell` declared."""
    import re
    css = (Path(__file__).parent.parent
           / "dashboard/templates/css/_tables.css.j2").read_text()

    # Every CSS rule whose selector contains `.theme-cell` as a class token
    # (not just an exact `.theme-cell { }` rule) — covers `.theme-cell > * + *`
    # and any future selector variant (`.theme-cell.foo`, `#id .theme-cell`, …).
    rules = re.findall(r"([^{}]+)\{([^}]*)\}", css)
    theme_cell_bodies = [
        body for selector, body in rules if re.search(r"\.theme-cell\b", selector)
    ]
    assert theme_cell_bodies, (
        "no CSS rule matched `.theme-cell` at all — the regex itself is "
        "broken, which is exactly how this test passed vacuously before"
    )
    for body in theme_cell_bodies:
        assert not re.search(r"display\s*:\s*flex", body), (
            ".theme-cell declares display:flex — it is adjacent to the flex "
            ".composite-cell and the two will collapse into one anonymous cell"
        )

    # `.composite-cell` is the other half of the adjacency risk: it is
    # display:flex BY DESIGN (deferred elsewhere to become a wrapper <div>,
    # see BACKLOG.md), and that is precisely what makes a flex `.theme-cell`
    # dangerous. Confirming it here means a regression that silently drops
    # `.composite-cell`'s flex (changing the premise this test relies on
    # without anyone updating this test) gets caught too, not just the
    # `.theme-cell` side.
    composite_cell_bodies = [
        body for selector, body in rules if re.search(r"\.composite-cell\b", selector)
    ]
    assert composite_cell_bodies, "no CSS rule matched `.composite-cell`"
    assert any(re.search(r"display\s*:\s*flex", body) for body in composite_cell_bodies), (
        ".composite-cell no longer declares display:flex — the adjacency risk "
        "this test guards against has changed shape; re-derive the guard "
        "rather than deleting it"
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
    assert COMPOSITE_FULL_SCALE == 1.6, "scale should be 1.6 per the redesign spec"

    # Positive grows right from centre, negative grows left, both half-width max.
    pos = _composite_bar(1.6)
    neg = _composite_bar(-1.6)
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

    # Signed, 2 decimals, U+2212 (not ASCII hyphen) for negatives, and the
    # value's ink class follows its own sign (spec: ink #3F4F34 positive,
    # #8E4B31 negative, --fg3 at exactly zero).
    assert '<span class="cbar-val pos">+1.06</span>' in _composite_bar(1.061)
    assert '<span class="cbar-val neg">−0.87</span>' in _composite_bar(-0.869)
    assert "-0.87" not in _composite_bar(-0.869), "must use U+2212, not ASCII hyphen"
    assert '<span class="cbar-val">+0.00</span>' in _composite_bar(0.0), \
        "exactly zero gets no pos/neg class -- falls back to --fg3 via the base rule"


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

def test_level_change_bars_python_and_js_agree():
    """_level_change_bars (build) and levelChangeBars (rescore.js) render the
    same two-row cell — the same three-way duplication risk as the composite
    bar, for the new merged Level/Change column.

    Only checking `"function levelChangeBars" in js` (the previous version of
    this test) proves the function exists, not that it agrees with the Python
    side — a JS-only divergence in class names, the LEVEL/CHANGE label text,
    or row order would still pass. Following `test_composite_bar_python_and_js_agree`'s
    pattern of reading the JS source directly, this pulls the exact function
    body (brace-balanced, not a regex guess at where it ends) and checks the
    specific class strings and label text it must share with the Python
    output for the two row-builders to render the same markup."""
    from dashboard.rows import _level_change_bars, COMPOSITE_FULL_SCALE

    js = (Path(__file__).parent.parent / "dashboard/assets/rescore.js").read_text()
    assert "function levelChangeBars" in js

    # Extract the exact function body (brace-balanced) so the checks below are
    # scoped to levelChangeBars itself, not to the whole file.
    start = js.index("function levelChangeBars")
    brace_start = js.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if js[i] == "{":
            depth += 1
        elif js[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    js_fn = js[start:i + 1]

    # The Python side's class names and label text, cross-checked against the
    # JS source text directly — a divergence here (renamed class, retyped
    # label, swapped row order) is exactly what would make the two builders
    # disagree while every Python-only assertion below kept passing.
    for cls in ("lc-cell", "lc-row", "lc-label", "lc-track", "lc-bar", "lc-val"):
        assert cls in js_fn, f"levelChangeBars is missing the '{cls}' class the Python side emits"
    assert '"LEVEL"' in js_fn or "'LEVEL'" in js_fn, "levelChangeBars must label the first row LEVEL"
    assert '"CHANGE"' in js_fn or "'CHANGE'" in js_fn, "levelChangeBars must label the second row CHANGE"
    # Row order: LEVEL must be built (and therefore appended) before CHANGE,
    # matching _level_change_bars' _row("LEVEL", ...) then _row("CHANGE", ...).
    assert js_fn.index("LEVEL") < js_fn.index("CHANGE"), \
        "levelChangeBars must render LEVEL before CHANGE, like the Python side"

    html = _level_change_bars(1.58, 0.53)
    assert html.count('class="lc-row"') == 2
    assert html.count('class="lc-track"') == 2
    assert "−" not in html  # both positive here
    assert "+1.58" in html and "+0.53" in html
    assert 'class="lc-bar pos"' in html

    # Negative change, positive level — both signs must render independently.
    html2 = _level_change_bars(1.58, -0.53)
    assert "+1.58" in html2
    assert "−0.53" in html2
    assert 'class="lc-bar neg"' in html2

    # Both missing — two dashes, no bars.
    html3 = _level_change_bars(None, None)
    assert html3.count("—") == 2
    assert 'class="lc-bar' not in html3

    # Full-scale clamp matches the composite bar's own scale.
    full = _level_change_bars(COMPOSITE_FULL_SCALE, COMPOSITE_FULL_SCALE)
    assert full.count("width:50.0%") == 2


def _apply_band_boundaries_js():
    """applyBandBoundaries()'s exact function body (brace-balanced), the same
    technique test_level_change_bars_python_and_js_agree above uses for
    levelChangeBars — a naive regex would truncate on the first nested `}`."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function applyBandBoundaries()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_old_band_edge_classes_are_gone():
    """The invisible border-cut mechanism (toggling a class on the boundary
    row) is replaced entirely by inserted rows — see test_inserts_a_row_not_a_class
    below. Stage 2 of the leaderboard redesign
    (sector_momentum-notes/specs/2026-08-18-leaderboard-redesign-design.md,
    Screen 1 point 7)."""
    js = _apply_band_boundaries_js()
    assert "band-edge-buy" not in js
    assert "band-edge-hold" not in js


def test_inserts_a_row_not_a_class():
    js = _apply_band_boundaries_js()
    assert "band-cut-row" in js
    assert ("insertAdjacentElement" in js or "insertAdjacentHTML" in js
            or "insertBefore" in js or ".after(" in js)


def test_removes_existing_cut_rows_before_reinserting():
    """Idempotency: applyBandBoundaries() runs again after every sort, filter,
    and horizon switch. Without removing what it inserted last time, repeated
    calls accumulate duplicate cut rows."""
    js = _apply_band_boundaries_js()
    assert re.search(r"querySelectorAll\(['\"]\.band-cut-row['\"]\)", js), (
        "applyBandBoundaries() must remove any existing .band-cut-row elements "
        "before inserting new ones, or repeated calls (every sort/filter does "
        "this) will accumulate duplicate rows"
    )


def test_band_cut_rank_text_is_not_hardcoded():
    """The exit note ('a holding that falls past rank N is sold') must read N
    from the active horizon preset, never a literal number — a Medium-preset
    literal would be silently wrong on Long."""
    js = _apply_band_boundaries_js()
    assert "h.top_n" in js and "h.buffer" in js, (
        "the exit-rank text must be computed from h.top_n + h.buffer "
        "(the active horizon preset), not a hardcoded number"
    )
    assert not re.search(r"rank\s+\d", js), (
        "found a hardcoded rank number in applyBandBoundaries() — "
        "it must be interpolated from h.top_n + h.buffer"
    )


def test_band_legend_markup_is_gone():
    """The #band-legend swatch legend is redundant once the band-cut rows
    self-label — Stage 2 Task 1 deletes it rather than keep two descriptions
    of the same fact."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert "band-legend" not in text


def test_band_legend_css_is_gone():
    css = (Path(__file__).parent.parent / "dashboard/templates/css/_tables.css.j2").read_text()
    assert ".band-legend" not in css


def test_band_cut_i18n_keys_updated():
    i18n = (Path(__file__).parent.parent / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert "band_buy:" not in i18n
    assert "band_exit:" not in i18n
    for key in ("band_buy_ends", "band_buy_note", "band_sell_line",
                "band_sell_note_prefix", "band_sell_note_suffix"):
        assert f"{key}:" in i18n, f"missing SV translation for new key {key}"


def test_band_cut_text_bakes_in_the_current_language():
    """applyBandBoundaries() reruns on every sort/filter/horizon-switch, not
    once per row-rebuild — hardcoding English + data-i18n and waiting for the
    next language toggle (auth.js's UNBUYABLE_BADGE pattern) would visibly
    reset a Swedish reader's translated band-cut text back to English on
    their very next interaction. Browser-verified live by whole-branch
    review: localStorage.lang='sv', sort/filter the table, watch 'BUY BAND
    ENDS' reappear in English. buildBandCutRowHtml() must bake in the
    correct text for the current language at build time via
    window.translate(), not just tag it data-i18n and hope."""
    js = _apply_band_boundaries_js()
    # _apply_band_boundaries_js() only captures applyBandBoundaries() itself;
    # buildBandCutRowHtml() is defined just above it in the same script block.
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function buildBandCutRowHtml")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = text[start:i + 1]
    assert "window.translate" in fn_body, (
        "buildBandCutRowHtml() must call window.translate() to bake in the "
        "current language's text, not just emit English + data-i18n"
    )


def test_apply_horizon_badges_calls_apply_band_boundaries():
    """applyHorizonBadges() must keep calling applyBandBoundaries() at its own
    end — this is what already gives the signed-in path (sm:leaderboard-upgraded/
    sm:positions-changed, both wired to applyHorizonBadges) a band cut with no
    separate listener needed. A Stage 2 plan draft assumed this call was
    missing and added a redundant second listener; whole-branch review found
    the call already there on main and the addition was reverted. This test
    guards the fact the (correct) revert depends on."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function applyHorizonBadges()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = text[start:i + 1]
    assert "applyBandBoundaries();" in fn_body


def _apply_horizon_badges_js():
    """applyHorizonBadges()'s exact function body (brace-balanced), same
    technique as _apply_band_boundaries_js() above."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function applyHorizonBadges()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_apply_horizon_badges_direct_band_boundaries_call_is_guarded():
    """Code review, 2026-08-24 (on the mobile-holdings-toggle fix), found
    this pre-existing: applyFilters() (called below, when it exists) ends
    with its own applyBandBoundaries() call -- an unconditional
    DIRECT call here too meant every sm:positions-changed event (a mobile
    star tap included) ran the full renderMobileCards() rebuild (a
    container.innerHTML wipe + rebuild of every card) TWICE in the same
    tick, for an identical end state: the second call's own "remove any
    band-cut rows I inserted last time" step (applyBandBoundaries()'s own
    comment) discards the first call's output before anything ever paints
    it. The direct call must fire only in the fallback branch, where
    applyFilters() is unavailable and nothing else in this function will
    call it -- the previous test confirms a call still always happens
    somewhere; this one confirms it does not ALSO happen unconditionally
    here.

    NOT load-bearing for the double/triple-render bug any more: since
    renderMobileCards() itself now coalesces repeated calls within one tick
    (dashboard/templates/index.html.j2, the wrapper added in the
    mobile-render-coalescing fix), removing this guard would no longer
    bring that bug back -- it would only make applyBandBoundaries()'s
    cheaper cut-row/renderBuyBand() work run twice. Kept as a minor
    efficiency, not a correctness requirement."""
    fn_body = _apply_horizon_badges_js()
    assert "if (typeof applyFilters !== 'function') { applyBandBoundaries(); }" in fn_body, (
        "the direct applyBandBoundaries() call is not guarded on applyFilters() "
        "being unavailable -- it now looks unconditional again"
    )
    # Guards against a second, still-unconditional call being added
    # elsewhere in the function while leaving the guarded one in place.
    assert fn_body.count("applyBandBoundaries();") == 1, (
        f"expected exactly one applyBandBoundaries() call site in "
        f"applyHorizonBadges(), found {fn_body.count('applyBandBoundaries();')}"
    )


def test_apply_horizon_badges_trailing_apply_filters_call_is_guarded():
    """Live-browser-verified gap in the fix above: window.applyLang()
    (_i18n.html.j2's apply()) ends with its OWN window.applyFilters() call
    ("the leaderboard filter count is built in
    JS... re-render it in the new language", its own comment says). Calling
    applyFilters() again right after window.applyLang(lang), unconditionally,
    was a SECOND source of the identical double-renderMobileCards() bug the
    previous test fixes the first source of -- confirmed live: instrumenting
    window.renderMobileCards() and calling applyHorizonBadges() still showed
    2 renders after only the first guard was in place, 1 after this one was
    added too. The explicit applyFilters() call must fire only when
    window.applyLang did not already run.

    NOT load-bearing for the double/triple-render bug any more, same as the
    guard above: renderMobileCards() itself now coalesces repeated calls
    within one tick, so this guard is a minor efficiency, not a correctness
    requirement."""
    fn_body = _apply_horizon_badges_js()
    tail = fn_body[fn_body.index("if (window.applyLang)"):]
    assert "} else if (typeof applyFilters === 'function') {" in tail, (
        "the trailing applyFilters() call is not guarded on window.applyLang "
        "being unavailable -- window.applyLang(lang) already calls "
        "applyFilters() internally, so calling it again here unconditionally "
        "redoes applyBandBoundaries()'s cut-row/renderBuyBand() work for no "
        "benefit (renderMobileCards() itself now coalesces repeated calls "
        "within one tick, so this no longer double-renders the card list -- "
        "see the NOT load-bearing comment in dashboard/templates/index.html.j2)"
    )
    # window.applyLang(lang) must still be the call that actually fires in
    # the normal case (real pages always define window.applyLang) -- this
    # guards against the guard silently inverting (e.g. skipping applyLang
    # instead of the redundant applyFilters call).
    assert "window.applyLang(lang);" in tail


def test_scan_history_also_draws_band_boundaries():
    """Same gap as the signed-in view: showScan() rebuilt rows with no band
    cut of any kind. Cannot call applyBandBoundaries() directly the way the
    signed-in view does — that function is a DOM-attribute-driven pass keyed
    on tr.dataset.rank, and scan-history rows deliberately carry no
    data-rank attribute (railClass's comment in this same file explains
    why). showScan() must compute its own cut positions from its in-memory
    rank data and build the row markup via the shared
    window.buildBandCutRowHtml() instead."""
    js = (Path(__file__).parent.parent / "dashboard/assets/scan-history.js").read_text()
    assert "buildBandCutRowHtml" in js, (
        "showScan() must build band-cut-row markup via "
        "window.buildBandCutRowHtml(), the same function applyBandBoundaries() "
        "uses, computing its own cut positions since it cannot rely on that "
        "shared DOM pass (see this test's docstring)"
    )


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


def test_modal_helper_include_precedes_footer_and_methodology():
    """_footer.html.j2 and _methodology.html.j2 stopped including
    _modal.js.j2 themselves (2026-08-23 sweep — it was the third of three
    copies inlined into every page) and now rely on window.SMModal already
    existing from the PAGE's own earlier include. That traded an idempotent,
    order-independent safety net (each partial self-included the guarded
    `window.SMModal = window.SMModal || (...)` definition) for a document-
    order dependency previously documented only in a comment — code review
    the same day flagged that nothing enforced it, so a future reorder (or a
    new page including either partial without _modal.js.j2 first) would
    throw `TypeError: Cannot read properties of undefined (reading 'bind')`
    at runtime with every test here still green. This is that enforcement."""
    root = Path(__file__).parent.parent
    for page in ("dashboard/templates/index.html.j2",
                 "dashboard/templates/sentiment.html.j2"):
        src = (root / page).read_text()
        modal_at = src.find('{% include "_modal.js.j2" %}')
        assert modal_at != -1, f"{page} never includes _modal.js.j2 directly"
        for partial in ("_footer.html.j2", "_methodology.html.j2"):
            partial_at = src.find(f"{{% include '{partial}' %}}")
            if partial_at == -1:
                partial_at = src.find(f'{{% include "{partial}" %}}')
            assert partial_at != -1, f"{page} never includes {partial}"
            assert modal_at < partial_at, (
                f"{page}: _modal.js.j2 is included AFTER {partial} — "
                f"{partial}'s window.SMModal.bind(...) call would run before "
                f"window.SMModal is defined"
            )


# ---------------------------------------------------------------------------
# One shared Supabase client — auth.js / positions.js / alert-prefs.js
# ---------------------------------------------------------------------------

def test_only_one_file_creates_the_supabase_client():
    """auth.js, positions.js and alert-prefs.js each used to call
    window.supabase.createClient() independently — three clients pointed at
    the same project, which makes Supabase log a 'Multiple GoTrueClient
    instances detected' console warning for every extra one. Only
    supabase-client.js may call createClient(); the other three must reuse
    window.SMSupabase."""
    root = Path(__file__).parent.parent / "dashboard/assets"
    consumers = ["auth.js", "positions.js", "alert-prefs.js"]

    creator_src = (root / "supabase-client.js").read_text()
    assert "window.supabase.createClient(cfg.url, cfg.key)" in creator_src
    assert "window.SMSupabase =" in creator_src

    for name in consumers:
        src = (root / name).read_text()
        assert "createClient" not in src, (
            f"{name} still calls createClient() itself — should reuse "
            f"window.SMSupabase from supabase-client.js instead"
        )
        assert "window.SMSupabase" in src, (
            f"{name} does not reference the shared client at all"
        )


def test_supabase_client_script_loads_before_its_consumers():
    """Load-order matters: window.SMSupabase must exist before auth.js,
    positions.js or alert-prefs.js run, or their first-load guard sees it
    unset and fails open (auth silently disabled)."""
    footer = (Path(__file__).parent.parent
              / "dashboard/templates/_footer.html.j2").read_text()
    # asset_url('x.js'), not a literal src="assets/x.js": every script tag
    # goes through it since cache-busting shipped (2026-08-29). Same
    # ordering property, new syntax.
    creator_idx = footer.index("asset_url('supabase-client.js')")
    for name in ("auth.js", "positions.js", "alert-prefs.js"):
        consumer_idx = footer.index(f"asset_url('{name}')")
        assert creator_idx < consumer_idx, (
            f"supabase-client.js must be loaded before {name}"
        )


def test_supabase_client_asset_is_copied_when_auth_is_configured():
    """Mirrors the build_assets regression test's own incident (theme.js
    referenced but never copied, 404 in production) for this specific file —
    checked directly here too since supabase-client.js is only copied inside
    the same `if auth_ctx["auth"]:` block as its three consumers, and that
    block-scoping is easy to get right for the existing three files while
    missing the new fourth one."""
    build_py = (Path(__file__).parent.parent / "dashboard/build.py").read_text()
    assert 'docs_assets / "supabase-client.js"' in build_py


def _render_mobile_cards_js():
    """_renderMobileCardsNow()'s exact function body (brace-balanced), the same
    technique test_level_change_bars_python_and_js_agree /
    _apply_band_boundaries_js use elsewhere in this file. (Searches for
    _renderMobileCardsNow, not renderMobileCards, which is now a thin coalescing
    wrapper around it as of 2026-08-25.)"""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function _renderMobileCardsNow()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_leaderboard_cards_container_exists():
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert 'class="leaderboard-cards"' in text
    # Must be a sibling of the table inside the same .table-wrap, not a
    # separate top-level element — CSS visibility toggling assumes this.
    wrap_start = text.index('class="table-wrap"')
    cards_start = text.index('class="leaderboard-cards"', wrap_start)
    assert wrap_start < cards_start, "cards container must be inside .table-wrap"


def test_render_mobile_cards_reads_the_table_not_a_fourth_data_source():
    """Cards are a projection of the table's live DOM, not an independently
    built fourth row-format — this is what keeps sort/filter/band-cuts/badges
    working on mobile with no card-specific reimplementation of any of them."""
    js = _render_mobile_cards_js()
    assert "querySelectorAll" in js
    assert "leaderboard-row" in js
    # Must read the CURRENT DOM (rank badge text, theme name text, etc.), not
    # a data source like RESCORE_DATA or SCAN_HISTORY that duplicates the
    # table's own state.
    assert "RESCORE_DATA" not in js
    assert "SCAN_HISTORY" not in js


def test_render_mobile_cards_reflects_band_cut_rows_too():
    js = _render_mobile_cards_js()
    assert "band-cut-row" in js


def test_cards_embed_their_own_breakdown_copy_not_toggle_breakdown():
    """Resolved during planning: toggleBreakdown() only toggles a CSS class
    on the table's own breakdown <tr>, and the table is fully hidden on
    mobile (display:none), so calling it from a card's tap handler would
    toggle a class with no visible effect. Cards must embed their own copy
    of the breakdown content and toggle it independently — this is the
    regression a future 'simplify by reusing toggleBreakdown()' edit would
    silently reintroduce."""
    js = _render_mobile_cards_js()
    assert "card-breakdown" in js
    assert "getElementById('bd-'" in js or 'getElementById("bd-"' in js


def test_render_mobile_cards_wired_into_apply_band_boundaries():
    """sortTable(), applyFilters(), and applyHorizonBadges() all already call
    applyBandBoundaries() at their own end (verified before this test was
    written — the same class of unverified claim Stage 2's whole-branch
    review had to catch and revert). One call inside applyBandBoundaries()
    itself covers all of them, plus the signed-in path (auth.js dispatches
    sm:leaderboard-upgraded after rebuilding rows, and that event already
    triggers applyHorizonBadges()) — no direct listener needed in auth.js."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function applyBandBoundaries(")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = text[start:i + 1]
    assert "renderMobileCards" in fn_body


def test_render_mobile_cards_wired_into_scan_history_only():
    """scan-history.js's showScan() is the one path that genuinely needs a
    direct call — it deliberately never calls applyHorizonBadges() or
    applyBandBoundaries() (its rows carry no data-rank, so that DOM pass
    would find nothing regardless), the identical reason Stage 2 had to give
    it its own local band-cut logic instead of reusing the shared pass."""
    scan_history_js = (Path(__file__).parent.parent
                        / "dashboard/assets/scan-history.js").read_text()
    assert "renderMobileCards" in scan_history_js, (
        "showScan() must call renderMobileCards() after rebuilding rows"
    )


def test_pinned_column_css_is_gone():
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    assert "#leaderboard-table th:nth-child(1)" not in css
    assert "#leaderboard-table td:nth-child(1)" not in css
    assert "#leaderboard-table th:nth-child(2)" not in css
    assert "#leaderboard-table td:nth-child(2)" not in css


def test_card_and_table_visibility_toggle_by_viewport():
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    assert "max-width: 600px" in css
    assert ".leaderboard-cards" in css


def test_card_line2_is_a_flex_container():
    """Found live: renderMobileCards() copies compositeCell.innerHTML — the
    .cbar-wrap/.cbar-val children only, not .composite-cell itself, whose
    `display: flex` (_tables.css.j2) is what gives .cbar-wrap's `flex: 1` a
    container to size against. Without .card-line2 also being a flex
    container, the bar rendered at 0 width — measured live via
    getBoundingClientRect() before this was caught."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.card-line2\s*\{[^}]*\}", css)
    assert m, ".card-line2 rule not found"
    assert "display: flex" in m.group(0)


def test_card_position_held_css_rule_exists():
    """Mirrors .leaderboard-row.position-held (_tables.css.j2) at card scale.
    Without this, renderMobileCards() could copy the class onto the card
    correctly and it would still LOOK identical to an unheld card -- the
    other half of the "can't tell a holding from an ordinary row" report."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.leaderboard-card\.position-held\s*\{[^}]*\}", css)
    assert m, ".leaderboard-card.position-held rule not found"
    assert "background" in m.group(0)


def test_card_position_held_rule_declared_after_in_band_rule():
    """Both classes are whole-card backgrounds that can genuinely co-occur
    (a holding often stays inside the buy band while held), unlike on
    desktop where in-band is a rank-cell rail and held is a row background
    — independent channels with no precedence question. Source order is
    what decides the winner here, so pin it: position-held must come after
    in-band, so a held+in-band card reads as held (the more actionable fact
    once you already own it), not silently reverting to the in-band tint."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    in_band_idx = css.index(".leaderboard-card.in-band {")
    held_idx = css.index(".leaderboard-card.position-held {")
    assert in_band_idx < held_idx


def test_card_position_warn_css_rule_exists():
    """Mirrors .leaderboard-row.position-warn's inset box-shadow AND its
    td::before "⚠ " prefix (_tables.css.j2), retargeted at .card-theme
    since cards have no <td> to attach the pseudo-element to."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    warn_m = re.search(r"\.leaderboard-card\.position-warn\s*\{[^}]*\}", css)
    assert warn_m, ".leaderboard-card.position-warn rule not found"
    assert "box-shadow" in warn_m.group(0)
    glyph_m = re.search(
        r"\.leaderboard-card\.position-warn \.card-theme::before\s*\{[^}]*\}", css,
    )
    assert glyph_m, ".leaderboard-card.position-warn .card-theme::before rule not found"
    assert "⚠" in glyph_m.group(0)


def test_card_position_surplus_css_rule_exists():
    """Mirrors .leaderboard-row.position-surplus's inset accent (_tables.css.j2)
    at card scale -- same gap as position-held/position-warn above: without
    styling, _renderMobileCardsNow() could copy the class correctly and the
    card would still look identical to any other held card."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.leaderboard-card\.position-surplus\s*\{[^}]*\}", css)
    assert m, ".leaderboard-card.position-surplus rule not found"
    assert "box-shadow" in m.group(0)


def test_card_position_toggle_touch_target_scoped_to_cards_only():
    """44px WCAG touch target, but scoped to `.leaderboard-card
    .position-toggle` rather than added to the file's shared
    `@media (pointer: coarse)` touch-target block: cards only ever exist
    below the same 600px breakpoint, but the desktop TABLE's own
    .position-toggle can still be reached by a touchscreen device wide
    enough to show the table (a touchscreen laptop, a landscape tablet) --
    widening it there too would inflate the star inside an already-dense
    table row for no reason this fix needs. Confirms the rule is scoped,
    not merely present, and confirms it sits in the max-width block."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.leaderboard-card \.position-toggle\s*\{[^}]*\}", css)
    assert m, ".leaderboard-card .position-toggle rule not found"
    assert "min-height: 44px" in m.group(0)

    # The exact declaration, brace included -- a bare "@media (pointer:
    # coarse)" substring search would also match this test's own docstring-
    # style prose mentioning the block by name in a comment above the new
    # card rule (found by running this test: it matched there first).
    coarse_start = css.index("@media (pointer: coarse) {")
    coarse_end = css.index("\n}\n", coarse_start)
    coarse_block = css[coarse_start:coarse_end]
    assert ".position-toggle" not in coarse_block, (
        "bare .position-toggle must not appear in the shared pointer:coarse "
        "block -- that would also widen the desktop table's star button"
    )


def test_card_breakdown_scrolls_instead_of_clipping():
    """Found live: the embedded breakdown content is intrinsically wider
    than a mobile card even in .breakdown-grid's single-column mode (~130px
    of overflow measured at 375px via scrollWidth vs clientWidth). Without
    overflow-x, that content is silently clipped rather than scrollable."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.card-breakdown\s*\{[^}]*\}", css)
    assert m, ".card-breakdown rule not found"
    assert "overflow-x: auto" in m.group(0)


# ---------------------------------------------------------------------------
# Stage 3 Task 2: mobile header scan-meta row, scrollable control row,
# stacked footer.
# ---------------------------------------------------------------------------


def test_render_mobile_cards_reads_position_state():
    """Found live 2026-08-24: cards had no star toggle and no held/warn
    styling at all -- positions.js's decorateRow() inserts .position-toggle
    as .theme-name's SIBLING inside the theme-cell <td>, not inside
    .theme-name itself, so the existing themeName.innerHTML read never
    picked it up. A signed-in mobile reader could not mark or see
    holdings."""
    js = _render_mobile_cards_js()
    assert "querySelector('.position-toggle')" in js
    assert "classList.contains('position-held')" in js
    assert "classList.contains('position-warn')" in js
    assert "position-held" in js and "position-warn" in js


def test_render_mobile_cards_reads_position_surplus():
    """Whole-branch review finding: applyHorizonBadges() marks the extra
    holding in an over-held book with .position-surplus on the table row
    (the same fact the review panel counts as 'Book: 5 / 4 -- too many'),
    but _renderMobileCardsNow() copied position-held/position-warn onto the
    card and not this one -- so a mobile reader had no way to see which
    holding on screen was the surplus one. Same read as isHeld/isWarn just
    above it, and the same class copied onto the card's own class list."""
    js = _render_mobile_cards_js()
    assert "classList.contains('position-surplus')" in js
    assert "isSurplus" in js
    # The read and the write must be the SAME variable, not a re-derivation:
    # confirms isSurplus is actually threaded into the card's class string,
    # not just read and discarded.
    m = re.search(r"isSurplus\s*\?\s*'[^']*position-surplus", js)
    assert m, "isSurplus is read but never written into the card's class list"


def test_render_mobile_cards_position_toggle_uses_outerHTML():
    """positionBtn must be read via outerHTML, matching the read-projection
    pattern the rest of this function already uses for rankBadge/trendBadge/
    unbuyableBadge/setupBadge -- .innerHTML/.textContent would drop the
    button's own tag (and its aria-pressed/title/data-i18n-* attributes)
    entirely, not just mis-escape it."""
    js = _render_mobile_cards_js()
    assert "positionBtn.outerHTML" in js


def test_render_mobile_cards_position_toggle_click_delegates_to_table_row():
    """The card's button is freshly parsed from outerHTML -- serialization
    never carries listeners, so without delegation the star would render
    but do nothing on tap (a silent no-op, arguably worse than not
    rendering it at all: it looks interactive and isn't). Must forward to
    the real table row's button rather than reimplementing positions.js's
    held/persist/revert state machine for cards -- the same
    no-reimplementation rule this function already follows for sorting,
    filtering, band cuts and badges (see
    test_render_mobile_cards_reads_the_table_not_a_fourth_data_source)."""
    js = _render_mobile_cards_js()
    assert "querySelectorAll('.position-toggle')" in js
    assert "stopPropagation" in js
    assert "closest('.leaderboard-card')" in js
    assert "dataset.sectorId" in js
    assert ".leaderboard-row[data-sector-id=" in js
    assert "tableBtn.click()" in js


def test_render_mobile_cards_position_toggle_click_does_not_bubble_to_card():
    """Sabotage-guarding note for the reviewer, not a runtime assertion:
    stopPropagation() must sit INSIDE the position-toggle click handler,
    not the card's own — a tap on the star would otherwise also toggle the
    card's breakdown-disclosure open/closed, since that listener sits on an
    ancestor of the button. Verified live in the browser (2026-08-24): a
    tap on the star with a fake table-row listener attached fired exactly
    once and left the card's aria-expanded unchanged."""
    js = _render_mobile_cards_js()
    toggle_start = js.index("container.querySelectorAll('.position-toggle')")
    toggle_block = js[toggle_start:]
    assert "stopPropagation" in toggle_block.split("});", 1)[0]


def test_render_mobile_cards_preserves_open_state_across_rebuild():
    """Code review, 2026-08-24 (confirmed independently by four of the eight
    review angles): the position-toggle click delegation is the FIRST thing
    that can trigger a renderMobileCards() rebuild from inside a card.
    Every previous trigger (sort/filter/badge-refresh) fired from outside
    any card, so nothing before this needed to preserve a card's own 'open'
    state across the container.innerHTML wipe -- every rebuilt card always
    hardcoded aria-expanded="false". Without this capture/restore, tapping
    a star silently collapsed whatever breakdown the user had open,
    including on an unrelated card."""
    js = _render_mobile_cards_js()
    capture_idx = js.index("var openSectorIds = {};")
    innerHTML_idx = js.index("container.innerHTML = html;")
    restore_idx = js.index("if (Object.keys(openSectorIds).length)")
    assert capture_idx < innerHTML_idx < restore_idx, (
        "open-state must be captured BEFORE the wipe and restored AFTER it"
    )
    capture_block = js[capture_idx:innerHTML_idx]
    assert "leaderboard-card.open" in capture_block
    assert "dataset.sectorId" in capture_block
    restore_block = js[restore_idx:]
    assert "classList.add('open')" in restore_block
    assert "setAttribute('aria-expanded', 'true')" in restore_block


def test_render_mobile_cards_restores_focus_by_sector_id_not_position():
    """Sort/filter can reorder cards between rebuilds -- restoring focus (or
    open state) by DOM index instead of data-sector-id would land on
    whichever card happens to occupy the old position, not the one the
    user was actually using. Also guards the keyboard-focus-loss half of
    the same review finding: a keyboard/AT user activating the star via
    Enter/Space had the focused button destroyed and replaced mid-click,
    with focus silently reverting to <body> and never restored."""
    js = _render_mobile_cards_js()
    capture_idx = js.index("var activeEl = document.activeElement;")
    innerHTML_idx = js.index("container.innerHTML = html;")
    restore_idx = js.index("if (focusSectorId)")
    assert capture_idx < innerHTML_idx < restore_idx
    capture_block = js[capture_idx:innerHTML_idx]
    assert "closest('.leaderboard-card')" in capture_block
    assert "classList.contains('position-toggle')" in capture_block
    restore_block = js[restore_idx:]
    assert '.leaderboard-card[data-sector-id="' in restore_block
    assert ".focus()" in restore_block


def test_leaderboard_cards_have_keyboard_activation():
    """Found by whole-branch review: cards get role="button"/tabindex="0"
    (renderMobileCards()) but only a click listener, so Enter/Space did
    nothing for keyboard/AT users — unlike #leaderboard-table's own
    delegated keydown handler for the identical role="button" pattern on
    .leaderboard-row, which this mirrors on #leaderboard-cards."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    idx = text.index("getElementById('leaderboard-cards')?.addEventListener('keydown'")
    block_end = text.index("});", idx)
    block = text[idx:block_end]
    assert "'.leaderboard-card'" in block
    assert "t.click()" in block


def test_mobile_scan_meta_markup_exists():
    """The mobile echo of the desktop meta-cluster (scan id, date, SPY/VIX)
    — a distinct class from the pre-existing .scan-meta on #auth-email-label,
    which is an unrelated element (the signed-in user's email)."""
    header = (Path(__file__).parent.parent
              / "dashboard/templates/_header.html.j2").read_text()
    assert 'class="mobile-scan-meta"' in header
    assert 'data-i18n="scan_prefix"' in header
    assert "{{ active_scan_id }}" in header
    assert "{{ scan_date[:10] }}" in header
    # Guarded by the same `{% if macro %}` the desktop chips use — a build
    # with no FRED data must not crash rendering this row.
    meta_start = header.index('class="mobile-scan-meta"')
    assert "{%- if macro %}" in header[meta_start:]


def test_scan_prefix_has_sv_translation():
    """data-i18n="scan_prefix" without an SV entry would silently fall
    back to English on language switch — the same gap the horizon
    control's own SV keys were added to close (see the comment above
    horizon_label in this same file). Shared by the mobile echo, the
    desktop summary-strip subline, and the sentiment page's own desktop
    indicator (see test_desktop_scan_meta_* below) — one key, one
    translation, three renderers."""
    core_i18n = (Path(__file__).parent.parent
                 / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert re.search(r"\bscan_prefix:\s*\"\S+\"", core_i18n)



def test_mobile_scan_meta_survives_missing_scan_date():
    """Found by the full suite, not by this task's own tests above:
    test_leaderboard_filters.py's _render_index() renders index.html.j2
    (which includes this header) with a minimal context that carries no
    scan_date at all. `scan_date[:10]` slices Jinja's default Undefined,
    which raises — unlike a bare `{{ scan_date }}`, which just renders
    blank — so the whole block must be guarded on scan_date, not only the
    macro half of it."""
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    tpl_dir = Path(__file__).parent.parent / "dashboard" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)))
    register_asset_url(env)
    html = env.get_template("_header.html.j2").render(active_segment="sectors")
    assert "mobile-scan-meta" not in html


# ---------------------------------------------------------------------------
# Desktop scan indicator (sentiment.html.j2 has no summary strip of its own)
# ---------------------------------------------------------------------------

def _render_header_full(active_segment, scan_date=None, active_scan_id=None,
                         macro=None, auth=False):
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    tpl_dir = Path(__file__).parent.parent / "dashboard" / "templates"
    env = Environment(loader=FileSystemLoader(str(tpl_dir)))
    register_asset_url(env)
    return env.get_template("_header.html.j2").render(
        active_segment=active_segment, scan_date=scan_date,
        active_scan_id=active_scan_id, macro=macro, auth=auth)


def test_desktop_scan_meta_markup_exists():
    """index.html.j2's own summary strip already prints the scan id/date on
    desktop (.strip-subline); sentiment.html.j2 shares this header but has no
    strip. Without a header-level echo, the Sentiment page shows no scan date
    anywhere on desktop once data IS present — the fix landed 2026-08-22 only
    covers the empty state, this covers the populated one.
    """
    header = (Path(__file__).parent.parent
              / "dashboard/templates/_header.html.j2").read_text()
    assert 'class="desktop-scan-meta"' in header
    meta_start = header.index('class="desktop-scan-meta"')
    block = header[meta_start:meta_start + 400]
    assert 'data-i18n="scan_prefix"' in block
    assert "{{ active_scan_id }}" in block
    assert "{{ scan_date[:10] }}" in block


def test_desktop_scan_meta_renders_on_the_sentiment_page():
    html = _render_header_full("sentiment", scan_date="2026-08-15 06:00 UTC",
                               active_scan_id=162)
    assert 'class="desktop-scan-meta"' in html
    assert "#162" in html
    assert "2026-08-15" in html


def test_desktop_scan_meta_absent_on_the_leaderboard_page():
    """index.html.j2's own summary strip already covers this on desktop
    (.strip-subline) — a second copy from the shared header would print the
    scan id and date twice on the same page."""
    html = _render_header_full("sectors", scan_date="2026-08-15 06:00 UTC",
                               active_scan_id=162)
    assert "desktop-scan-meta" not in html


def test_desktop_scan_meta_survives_missing_scan_date():
    """Same Undefined-slicing hazard as the mobile echo above:
    `scan_date[:10]` on Jinja's default Undefined raises rather than
    rendering blank."""
    html = _render_header_full("sentiment")
    assert "desktop-scan-meta" not in html


def _media_blocks(css: str, kind: str):
    """Every top-level `@media (KIND: Npx) { ... }` block, matched to its OWN
    close: a media block's closing brace sits at column 0, while every rule
    inside it is indented — so a newline immediately followed by `}` (no
    leading space) finds the block's real end even when an inner rule
    (e.g. `.mobile-scan-meta { ... }`)
    closes its own brace first. `[^}]*`, tried first, cannot cross that inner
    brace and silently matches nothing whenever a sibling rule sits between
    the query and the selector being checked — the same block-boundary trap
    CLAUDE.md documents for BACKLOG.md's `## ` scan, here in CSS instead of
    Markdown. Returns [(breakpoint_px, block_body), ...] for every such block,
    since this file has four separate `@media (max-width: 600px)` sections and
    a check must land in the right one, not merely find "a" 600px block.
    """
    pattern = re.compile(
        r"@media\s*\(" + re.escape(kind) + r":\s*(\d+)px\)\s*\{(.*?)\n\}",
        re.S,
    )
    return [(int(m.group(1)), m.group(2)) for m in pattern.finditer(css)]


def test_desktop_scan_meta_hidden_at_and_below_600px():
    """The exact complement of .mobile-scan-meta's own boundary (hidden at
    >=601px). Reusing .scan-meta instead of a distinct class was the trap
    here: that rule lives in `@media (max-width: 420px)`, not 600px, so
    reusing it would print the scan date TWICE between 421px and 600px —
    once from .mobile-scan-meta, once from a wrongly-still-visible
    .scan-meta."""
    css = (Path(__file__).parent.parent
           / "dashboard/templates/css/_responsive.css.j2").read_text()
    blocks = _media_blocks(css, "max-width")
    assert any(
        bp == 600 and re.search(r"\.desktop-scan-meta\s*\{\s*display:\s*none", body)
        for bp, body in blocks
    ), ".desktop-scan-meta is not hidden at <=600px"


def test_desktop_scan_meta_shares_its_style_with_scan_meta_not_a_copy():
    """`.desktop-scan-meta` was introduced as a BYTE-IDENTICAL copy of the
    pre-existing `.scan-meta` (font-size, color, font-variant-numeric,
    white-space) -- flagged independently by three code-review angles
    (2026-08-23): nothing ties the two rule bodies together, so a future
    visual tweak (e.g. the dark-theme retint already on the backlog) can be
    made to one and silently miss the other. The two classes exist ONLY
    because they need different visibility breakpoints (see
    _responsive.css.j2) -- that is a reason to keep separate display rules
    there, not a reason to duplicate the typography here. Merged into one
    comma-selector declaration.
    """
    css = (Path(__file__).parent.parent
           / "dashboard/templates/css/_chrome.css.j2").read_text()
    assert re.search(r"\.scan-meta\s*,\s*\.desktop-scan-meta\s*\{", css), (
        ".scan-meta and .desktop-scan-meta declare their typography "
        "separately -- merge into one comma-selector rule"
    )
    # And .desktop-scan-meta must not ALSO still have its own standalone rule
    # left behind (a merge that adds the comma selector without deleting the
    # old block would pass the check above while leaving the duplicate).
    assert not re.search(r"(?<!,\n)\.desktop-scan-meta\s*\{", css), (
        ".desktop-scan-meta still has a separate declaration block of its "
        "own alongside the merged comma-selector rule"
    )


def test_meta_cluster_page_scoping_guard_is_not_duplicated():
    """The desktop scan-meta row and the market-context chips both exist only
    on pages sharing this header without their own summary strip -- today that
    means `active_segment != "sectors"`, checked twice: once per block. Code
    review (2026-08-23, three independent angles) flagged this as the kind of
    duplication that drifts silently -- a third segment added later would need
    the same edit made twice to opt out correctly, and nothing forces that.
    Consolidated into one wrapping guard; each inner block keeps only the
    condition specific to it (`scan_date` vs `macro or auth`).
    """
    header = (Path(__file__).parent.parent
              / "dashboard/templates/_header.html.j2").read_text()
    assert header.count('active_segment != "sectors"') == 1, (
        'the page-scoping guard is checked more than once in this file -- '
        'consolidate into a single wrapping {% if %}'
    )


def test_desktop_scan_meta_and_mobile_scan_meta_boundaries_are_complementary():
    """Pins the pairing directly: whatever breakpoint .mobile-scan-meta uses
    for "hidden above", .desktop-scan-meta must use for "hidden at or below"
    — same number, so there is no width where both or neither is visible."""
    css = (Path(__file__).parent.parent
           / "dashboard/templates/css/_responsive.css.j2").read_text()

    mobile_bp = next(
        (bp for bp, body in _media_blocks(css, "min-width")
         if re.search(r"\.mobile-scan-meta\s*\{\s*display:\s*none", body)),
        None,
    )
    desktop_bp = next(
        (bp for bp, body in _media_blocks(css, "max-width")
         if re.search(r"\.desktop-scan-meta\s*\{\s*display:\s*none", body)),
        None,
    )
    assert mobile_bp is not None, "could not find .mobile-scan-meta's own hide rule"
    assert desktop_bp is not None, "could not find .desktop-scan-meta's hide rule"
    assert mobile_bp == desktop_bp + 1, (
        f".mobile-scan-meta hides above {mobile_bp}px but .desktop-scan-meta "
        f"hides at/below {desktop_bp}px — a gap or overlap between them means "
        f"a width where both or neither element is visible"
    )


def test_site_footer_stacks_on_mobile():
    """Desktop's `justify-content: space-between` on one row (_chrome.css.j2)
    squeezes the disclaimer text against the Methodology/Alerts buttons at
    375px with no wrap; stack them instead."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.site-footer\s*\{[^}]*\}", css)
    assert m, ".site-footer rule not found"
    assert "flex-direction: column" in m.group(0)


def test_cards_are_only_disclosures_when_they_have_a_breakdown():
    """Found in review, verified live at 375px on scan #159: scan-history.js's
    past-scan rows are bare `<tr class="leaderboard-row">` with no
    data-sector-id and no .breakdown-row sibling, so bdContent is '' for every
    card on that path — the one path this stage newly wired renderMobileCards()
    into. Emitting role="button"/tabindex/aria-expanded unconditionally made all
    18 cards announce themselves as expandable and then reveal a 0px-tall empty
    panel. role, tabindex, aria-expanded and .card-breakdown must all hang off
    the same `expandable` condition, or they drift apart again."""
    fn_body = _render_mobile_cards_js()
    assert "var expandable = bdContent !== ''" in fn_body, (
        "renderMobileCards() must decide expandability from bdContent"
    )
    # The disclosure affordances are gated, not unconditional.
    assert "expandable ? ' role=\"button\" tabindex=\"0\" aria-expanded=\"false\"'" in fn_body
    assert "expandable ? '<div class=\"card-breakdown\">'" in fn_body
    # ...and nothing emits them unconditionally any more.
    assert "+ ' role=\"button\" tabindex=\"0\" aria-expanded=\"false\"'" not in fn_body
    assert "+ '<div class=\"card-breakdown\">'" not in fn_body


def test_card_click_handler_is_scoped_to_expandable_cards():
    """A non-expandable card has no breakdown to toggle, so a click handler on
    it would only set an 'open' class nothing reads — and would still feel like
    a dead tap target. Pairs with
    test_cards_are_only_disclosures_when_they_have_a_breakdown."""
    fn_body = _render_mobile_cards_js()
    assert "querySelectorAll('.leaderboard-card[role=\"button\"]')" in fn_body


def test_breakdown_lookup_skips_empty_sector_id():
    """getElementById('bd-') on a row with no data-sector-id is a lookup that
    can only ever miss; guard it so the intent reads as deliberate rather than
    as an accidental miss that happens to return null."""
    fn_body = _render_mobile_cards_js()
    assert "sectorId ? document.getElementById('bd-' + sectorId) : null" in fn_body


# ---------------------------------------------------------------------------
# Stage 4: summary strip Cell B — the buy band, read from the table's live DOM.
# ---------------------------------------------------------------------------


def _strip_line_comments(js: str) -> str:
    """Drop // comment lines before asserting on code.

    A comment that names the very thing a test forbids will match a bare
    substring assertion — the same trap that had a leaderboard-row tag in a
    Stage 3 comment matching test_badge_gating's row regex.
    """
    return "\n".join(
        line for line in js.splitlines() if not line.strip().startswith("//")
    )


def _render_buy_band_js():
    """renderBuyBand()'s exact body, same brace-balanced extraction as
    _render_mobile_cards_js()."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function renderBuyBand()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_buy_band_reads_the_rail_class_not_a_recomputed_rank():
    """.in-band-rail is written by applyHorizonBadges() from the ACTIVE
    horizon's top_n. Re-deriving the band here would be a second source of
    truth that goes stale the moment the reader switches horizon."""
    fn = _render_buy_band_js()
    assert "in-band-rail" in fn
    assert "top_n" not in fn, "read the rail class, do not recompute the band"


def test_buy_band_skips_filtered_out_rows():
    """applyFilters() hides rows with style.display = 'none'; a hidden row is
    not on the board the reader is looking at."""
    assert "style.display === 'none'" in _render_buy_band_js()


def test_buy_band_is_called_from_the_band_boundaries_funnel():
    """One call point covers sortTable(), applyFilters() and
    applyHorizonBadges() — the same funnel renderMobileCards() uses."""
    body = _apply_band_boundaries_js()
    assert "renderBuyBand" in body


def test_buy_band_is_called_from_scan_history():
    """showScan() bypasses applyBandBoundaries() entirely — same reason
    renderMobileCards() needed its own call there."""
    js = (Path(__file__).parent.parent / "dashboard/assets/scan-history.js").read_text()
    assert "renderBuyBand" in js


def test_buy_band_empty_state_is_translatable():
    """A filter can hide every in-band theme, and a past scan carries no rail
    at all — empty is a real state, so its copy needs a Swedish entry like any
    other."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    sv = (Path(__file__).parent.parent / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert 'data-i18n="band_empty"' in text
    assert re.search(r"\bband_empty:", sv), "band_empty has no SV entry"


def test_buy_band_pills_are_rank_ordered_not_table_ordered():
    """Every pill prints its own rank, so following a Theme or Composite sort
    would render "1 2 4 3" — which reads as a bug rather than as the table's
    sort. Found at the browser gate. The band is a set; rank is its order."""
    fn = _render_buy_band_js()
    assert "band.sort(" in fn
    assert "a.rank - b.rank" in fn
    assert "isNaN(a.rank)" in fn, \
        "auth.js writes data-rank='' for a null rank; those must not scramble the rest"


def test_buy_band_never_calls_applylang():
    """applyLang() re-enters applyFilters() -> applyBandBoundaries() ->
    renderBuyBand(), so calling it from inside renderBuyBand() recurses until
    the stack blows. Reachable only by filtering the band empty, which is why
    the empty-state note is baked into the template and toggled rather than
    injected and translated at runtime. Found at the browser gate."""
    fn = _strip_line_comments(_render_buy_band_js())
    assert "applyLang(" not in fn, "renderBuyBand() must not call applyLang()"
    assert "getElementById('band-empty')" in fn


# ---------------------------------------------------------------------------
# Stage 5: horizon segmented control.
# ---------------------------------------------------------------------------


def test_horizon_toggle_buttons_exist_for_every_horizon():
    """One .horizon-btn per horizon_list entry via a Jinja loop over the
    same horizon_list the <select>'s <option>s already loop over — a
    source scan sees the loop body once, not once per rendered horizon, so
    this checks the loop is over horizon_list, not that the text repeats."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert 'class="horizon-toggle"' in text
    toggle_start = text.index('class="horizon-toggle"')
    toggle_block = text[toggle_start:text.index("</div>", toggle_start)]
    assert "{% for h in horizon_list %}" in toggle_block
    assert 'class="horizon-btn"' in toggle_block
    assert 'data-horizon-choice="{{ h.key }}"' in toggle_block


def test_horizon_select_is_still_in_the_dom_but_hidden():
    """Spec: keep <select id="horizon-select">, visually hidden —
    switchHorizon() reads document.getElementById('horizon-select') back
    internally at two points, not only via its own onchange."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert 'id="horizon-select"' in text
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    m = re.search(r"\.horizon-row select\s*\{[^}]*\}", css)
    assert m, ".horizon-row select rule not found"
    assert "display: none" in m.group(0)


def test_horizon_buttons_are_wired_to_switch_horizon():
    """A click must call the SAME switchHorizon() the <select>'s onchange
    calls — a second, independent state-setter would let the two controls
    disagree."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    idx = text.index("function initHorizonSelect()")
    brace_start = text.index("{", idx)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = text[idx:i + 1]
    assert "switchHorizon(" in fn_body
    assert "horizon-btn" in fn_body


def test_switch_horizon_keeps_the_toggle_buttons_in_sync():
    """A programmatic switchHorizon() call (a deep link, a test, the
    <select>'s own onchange) must update the buttons' aria-pressed too —
    the same reasoning the existing _sel.value sync comment gives for the
    hidden <select> itself.

    Strips comments before asserting — caught live: a first sabotage-verify
    pass commented out the call and this test still passed, because the
    substring "updateHorizonToggleUI" survives in the commented-out line
    itself. Same trap Stage 3/4's own comments hit twice already
    (test_buy_band_never_calls_applylang uses the same _strip_line_comments
    helper for exactly this reason)."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    idx = text.index("function switchHorizon(key)")
    brace_start = text.index("{", idx)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = _strip_line_comments(text[idx:i + 1])
    assert "updateHorizonToggleUI(key)" in fn_body


def test_horizon_label_has_no_trailing_colon():
    """Restyled as a bare eyebrow (matching .strip-eyebrow's "TODAY'S READ" /
    "IN THE BUY BAND" — no trailing punctuation), not a form <label> — the
    element it used to label is now display:none and no longer the focus
    target; the segmented buttons are."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    idx = text.index('data-i18n="horizon_label"')
    line = text[max(0, idx - 80):idx + 80]
    assert "Horizon:" not in line
    assert "<label" not in text[max(0, idx - 40):idx]


def test_horizon_row_label_css_is_gone():
    """Found while reviewing this plan: `.horizon-row label` styled the
    <label> Task 1 Step 3 removes in favour of a plain <span> — left behind,
    it would be dead CSS with no matching element."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    assert ".horizon-row label" not in css


def test_horizons_json_carries_buffer_frac_not_a_precomputed_exit_rank():
    """exit_rank now depends on a RUNTIME universe size (the fraction is
    fixed, the universe isn't), so baking a single static number into the
    build-time JSON would go stale the moment a signed-in reader's rebuild
    sees a different universe size than the guest-baked page did. buffer_frac
    travels instead; every consumer resolves exit_rank fresh via the one
    shared Rescore.exitRank() function (Task 6/7 of the fractional-band plan)
    rather than each recomputing the formula inline."""
    build_text = (Path(__file__).parent.parent / "dashboard" / "build.py").read_text()
    assert '"buffer_frac": h.buffer_frac' in build_text
    assert '"exit_rank"' not in build_text, (
        "a precomputed exit_rank reappeared in the exported JSON — it cannot "
        "be correct for both a guest-baked page and a signed-in rebuild with "
        "a different observed universe size"
    )


def test_horizon_stats_render_all_four_in_spec_order():
    """Spec order (Screen 1 item 5): held, sell past rank, trades/yr,
    median hold — the pre-Stage-5 markup had held/median-hold/trades."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    strip = text[text.index('id="horizon-stats"'):text.index("</span>\n    </span>", text.index('id="horizon-stats"'))]
    ids_in_order = re.findall(r'id="(hz-[a-z]+)"', strip)
    assert ids_in_order == ["hz-held", "hz-exit", "hz-trades", "hz-hold"]


def _render_horizon_stats_js():
    """renderHorizonStats()'s exact function body (brace-balanced), the same
    technique test_render_mobile_cards_wired_into_apply_band_boundaries and
    _render_mobile_cards_js() use elsewhere in this file."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function renderHorizonStats(")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_render_horizon_stats_sets_the_exit_rank_stat():
    js = _render_horizon_stats_js()
    assert "hz-exit" in js
    assert "h.exit_rank" in js


def test_horizon_btn_has_a_touch_target_not_the_dead_select():
    """Found in whole-branch review: #horizon-select was listed in the
    pointer:coarse 44px touch-target rule (_responsive.css.j2) before this
    stage, and stayed listed after the swap even though it is now
    permanently display:none — dead weight on the control that no longer
    needs it, while .horizon-btn, the real tap target, was never added.

    Strips /* */ block comments before asserting — caught live, twice: the
    fix's own explanatory comment names both "#horizon-select" (explaining
    why it's gone) and ".horizon-btn" (explaining why it's added), so a
    bare substring check on either assertion is satisfied by the comment
    alone regardless of the actual CSS rule. Same "comments are page
    content" trap as _strip_line_comments elsewhere in this file, for CSS
    block comments instead of // line comments."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    m = re.search(r"@media \(pointer: coarse\) \{.*?\n\}\n", css_no_comments, re.DOTALL)
    assert m, "pointer:coarse block not found"
    block = m.group(0)
    assert "#horizon-select" not in block
    assert ".horizon-btn" in block


# ---------------------------------------------------------------------------
# Desktop controls row: curated filter chips + a "More filters" disclosure.
# ---------------------------------------------------------------------------


def test_control_chips_share_data_attributes_with_the_full_set():
    """The curated chips are duplicates that share state with their
    full-set counterparts, not a physical relocation — same
    data-filter-group/data-filter-value pair on both, so one sync
    mechanism (Step 5 below) drives both from the same click."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert text.count('class="control-chip"') == 3
    for group, value in [("thresholds", "top5"), ("trend", "rising"),
                          ("thresholds", "composite_pos")]:
        pattern = ('class="control-chip" data-filter-group="%s" '
                   'data-filter-value="%s"' % (group, value))
        assert pattern in text, "missing curated chip: %s/%s" % (group, value)


def test_full_filter_set_moved_into_a_details_disclosure():
    """The nine-chip #leaderboard-filter-bar is unmodified in shape — only
    relocated inside a <details class="more-filters">, the same native
    pattern .rank-settings already uses one control over."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    details_start = text.index('class="more-filters"')
    details_block_start = text.rindex("<details", 0, details_start)
    bar_pos = text.index('id="leaderboard-filter-bar"')
    filter_group_setup_pos = text.index('id="filter-group-setup"')
    assert details_block_start < bar_pos < filter_group_setup_pos, (
        "expected <details class=\"more-filters\"> to wrap #leaderboard-filter-bar"
    )


def test_horizon_row_and_utility_row_are_merged():
    """One "Controls row" per the spec, not two separate rows, on the
    LEADERBOARD tab specifically — its former .utility-row's children
    (filter bar, rank-settings, guide button) now live inside .horizon-row
    alongside the horizon control.

    Does NOT assert `.utility-row` is gone from the whole document: RRG,
    Drill-down, Movers, and History each have their own unrelated
    `.utility-row` (a bare "How to read this tab" button, nothing this
    task touches) — a global absence check would fail against a correct
    implementation. Caught live while executing this plan, before writing
    the actual test."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    horizon_row_start = text.index('class="horizon-row"')
    horizon_row_end = text.index('id="scan-digest-banner"')
    row = text[horizon_row_start:horizon_row_end]
    assert 'class="utility-row"' not in row, (
        "the leaderboard tab's own .utility-row must be gone — merged into .horizon-row"
    )
    assert 'id="leaderboard-filter-bar"' in row
    assert 'data-guide="guide_body_leaderboard"' in row


def _wire_filter_chips_js():
    """_wireFilterChips()'s exact IIFE body (brace-balanced), the same
    technique _render_horizon_stats_js() uses elsewhere in this file."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("(function _wireFilterChips()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_wire_filter_chips_matches_curated_chips_too():
    """Scoped to #leaderboard-filter-bar .filter-chip before this task —
    a curated .control-chip outside that container would never receive a
    click listener at all, let alone stay in sync."""
    js = _wire_filter_chips_js()
    assert "leaderboard-filter-bar" not in js, (
        "must not scope the chip query to the filter bar's id — "
        "curated chips live outside it"
    )
    assert "data-filter-group" in js and "data-filter-value" in js


def _sync_chip_state_js():
    """_syncChipState()'s exact function body (brace-balanced) — the actual
    sync contract lives here, not in _wireFilterChips() itself, which only
    calls it."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function _syncChipState(")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_clicking_a_curated_chip_syncs_its_full_set_counterpart():
    """The actual sync contract: _syncChipState must update EVERY element
    sharing a data-filter-group/data-filter-value pair, not just the one
    clicked — otherwise the curated Top 5 chip and the full set's Top 5
    chip could show different pressed states for the same underlying
    filter. _wireFilterChips() only needs to call it (checked separately
    below) — the selector construction itself lives in _syncChipState's
    own body, a sibling function, not inside _wireFilterChips()."""
    wire_js = _wire_filter_chips_js()
    assert "_syncChipState(" in wire_js, \
        "_wireFilterChips() must call _syncChipState after updating _filterState"
    sync_js = _sync_chip_state_js()
    assert "querySelectorAll" in sync_js
    assert 'data-filter-group="' in sync_js and 'data-filter-value="' in sync_js, (
        "expected a selector built from both attributes, matching every "
        "element sharing them — not just the one clicked"
    )


def test_clear_filters_resets_curated_chips_too():
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function clearFilters()")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    fn_body = text[start:i + 1]
    assert "leaderboard-filter-bar" not in fn_body, (
        "clearFilters()'s chip query must not be scoped to the filter bar's "
        "id — curated chips live outside it and must be reset too"
    )
    assert "_setChipPressed(" in fn_body, (
        "clearFilters() must delegate the pressed-state toggle to the "
        "shared _setChipPressed helper, not inline its own copy of "
        "classList.toggle/aria-pressed — found in whole-branch review, "
        "this used to be an independent duplicate of _syncChipState's logic"
    )


def test_control_chip_css_matches_spec_padding():
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    m = re.search(r"\.control-chip\s*\{[^}]*\}", css)
    assert m, ".control-chip rule not found"
    assert "padding: 5px 12px" in m.group(0)


def test_more_filters_chip_has_a_dashed_border():
    """Spec: "a dashed More filters chip" — same border style this codebase
    already uses for a hint-style chip (_tables.css.j2's tooltip-cursor
    badge), reused here rather than inventing a new dash pattern."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    m = re.search(r"\.more-filters summary\s*\{[^}]*\}", css)
    assert m, ".more-filters summary rule not found"
    assert "dashed" in m.group(0)


def test_more_filters_i18n_key_exists():
    """The [^"]+ (not \\S+) matters: the Swedish translation is two words
    ("Fler filter") — a \\S+-based pattern stops at the first space and
    never reaches the closing quote, failing against a correct
    translation. Caught live while executing this plan."""
    core_i18n = (Path(__file__).parent.parent
                 / "dashboard/templates/i18n/_core.js.j2").read_text()
    assert re.search(r'\bmore_filters:\s*"[^"]+"', core_i18n)
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert 'data-i18n="more_filters"' in text


def _set_filter_bar_visible_js():
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("function setFilterBarVisible(")
    brace_start = text.index("{", start)
    depth = 0
    i = brace_start
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    return text[start:i + 1]


def test_set_filter_bar_visible_also_hides_curated_chips():
    """Found while planning Task 2: scan-history.js calls this to hide
    filters when past-scan rows carry no filter data-attributes. Before
    this fix it only hid the disclosed full set, leaving the curated Top
    5/Rising/Composite>0 chips tappable with no effect while viewing
    history."""
    js = _set_filter_bar_visible_js()
    assert "control-chips" in js


def test_filter_count_and_clear_live_outside_the_disclosure():
    """A reader who picks a full-set-only chip (e.g. Enter) then collapses
    More filters must still see that a filter is active and have a way to
    clear it — Task 1 left both inside the <details>, invisible once
    collapsed."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    control_chips_start = text.index('class="control-chips"')
    more_filters_start = text.index('class="more-filters"', control_chips_start)
    between = text[control_chips_start:more_filters_start]
    assert 'id="filter-clear"' in between
    assert 'id="filter-count"' in between
    disclosure_body = text[more_filters_start:text.index('</details>', more_filters_start)]
    assert 'id="filter-clear"' not in disclosure_body
    assert 'id="filter-count"' not in disclosure_body


def test_filter_bar_mobile_scroll_hack_is_gone():
    """Superseded: the full set is behind a disclosure now, not always
    visible, so forcing it into a horizontally-scrolling strip at 375px is
    worse than letting it wrap. BACKLOG.md predicted this exact reversal
    when Stage 3 shipped the stopgap."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    m = re.search(r"\.filter-bar\s*\{[^}]*\}", css)
    if m:
        assert "overflow-x: auto" not in m.group(0)


def test_more_filters_gets_the_mobile_popover_treatment():
    """Same static/full-width override .rank-settings already gets at
    600px (_responsive.css.j2) — .more-filters is the same kind of control
    in the same row and must not run off the side of a 375px screen.

    Matches on the specific selector pair rather than delimiting the whole
    @media block: this file has several separate `@media (max-width:
    600px)` blocks, and a non-greedy regex trying to bound just one of
    them matched clean across a block boundary into an unrelated
    `@media (pointer: coarse)` block instead (caught live — it found
    `.rank-settings label`, a different rule, before ever finding this
    block's own closing brace)."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    assert re.search(
        r"\.rank-settings,\s*\n\s*\.more-filters\s*\{\s*position:\s*static;",
        css
    ), "expected `.rank-settings,\\n  .more-filters { position: static; }`"
    assert re.search(
        r"\.rank-settings \.sentiment-control,\s*\n\s*\.more-filters \.filter-bar\s*\{",
        css
    ), "expected the popover-positioning rule to cover both .more-filters and .rank-settings"


def test_horizon_row_is_the_new_popover_anchor():
    """.utility-row { position: relative } anchored .rank-settings's
    popover (_sentiment.css.j2's `position: absolute; right: 0`) before
    Task 1 deleted .utility-row from the markup — the anchor must move to
    .horizon-row, the row .rank-settings now lives inside, or the popover
    positions against the wrong ancestor."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    assert ".utility-row { position: relative; }" not in css
    assert re.search(r"\.horizon-row\s*\{\s*position:\s*relative;\s*\}", css)


def test_control_chips_wraps_instead_of_overflowing():
    """Found live at 375px, not by any test: .control-chips is a flex
    child of .horizon-row (which does wrap), but had no flex-wrap of its
    own — its six children (3 curated chips, Clear, count, More filters)
    were forced onto one line and overflowed past the row's right edge
    instead of wrapping, measured via getBoundingClientRect() (right:
    382.66 against a 375px viewport) even though
    document.documentElement.scrollWidth still read 375, which is why a
    static "no horizontal overflow" check on the document alone would
    have missed this."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    m = re.search(r"\.control-chips\s*\{[^}]*\}", css)
    assert m, ".control-chips rule not found"
    assert "flex-wrap: wrap" in m.group(0)


def test_control_chips_is_right_aligned():
    """Found in whole-branch review (three independent reviewers, one
    verified live): .horizon-row is a plain flex-wrap row with no
    justify-content and .control-chips had no margin-left:auto — despite
    the class's own code comment and BACKLOG.md both already asserting
    "Right-aligned controls". Measured live at 1280px before the fix: an
    811px gap between .control-chips's right edge and the row's own."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_tables.css.j2").read_text()
    m = re.search(r"\.control-chips\s*\{[^}]*\}", css)
    assert m, ".control-chips rule not found"
    assert "margin-left: auto" in m.group(0)


def test_control_chip_and_more_filters_get_touch_targets():
    """Found in whole-branch review: every sibling control in the same row
    (.filter-chip, .rank-settings summary) already gets the 44px
    pointer:coarse bump — the new .control-chip and .more-filters summary
    did not, despite the latter's own comment calling it "the same family"
    as .rank-settings."""
    css = (Path(__file__).parent.parent / "dashboard/templates/css"
           / "_responsive.css.j2").read_text()
    css_no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    m = re.search(r"@media \(pointer: coarse\) \{.*?\n\}\n", css_no_comments, re.DOTALL)
    assert m, "pointer:coarse block not found"
    block = m.group(0)
    assert ".control-chip" in block
    assert ".more-filters summary" in block


# ---------------------------------------------------------------------------
# escapeHtml — auth.js / scan-digest.js / scan-history.js interpolation hardening
# ---------------------------------------------------------------------------
#
# Found in the 2026-08-23 sweep: renderLatestRows() (auth.js) and fmtChip()
# (scan-digest.js) both build row/chip HTML by string concatenation,
# interpolating theme/sector names unescaped. Not exploitable today — the
# names come from config/themes.yaml via the pipeline, never from a reader —
# but hardening against the day any row field stops being repo-controlled.
#
# scan-history.js's renderScanLeaderboard() has the identical pattern (its
# own comment even cites auth.js's r.gics_sector by name) but was missed by
# the original sweep — caught in code review the same day, fixed alongside.

def _extract_escape_html_js(filename: str) -> str:
    """Pull escapeHtml() verbatim out of the named asset file."""
    src = (Path(__file__).parent.parent / "dashboard/assets" / filename).read_text()
    match = re.search(r"function escapeHtml\(s\) \{.*?\n  \}", src, re.S)
    assert match, f"escapeHtml() not found in dashboard/assets/{filename}"
    return match.group(0)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("filename", ["auth.js", "scan-digest.js", "scan-history.js"])
def test_escape_html_neutralizes_markup(filename):
    """Executes the real production function, not a re-implementation —
    same discipline as test_item_for_row_classifies_by_region_not_dataset_shape
    above."""
    fn_src = _extract_escape_html_js(filename)
    script = f"""
        {fn_src}
        const out = escapeHtml('<script>alert(1)</script> & "quoted" \\'text\\'');
        process.stdout.write(out);
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    out = res.stdout
    assert "<script>" not in out and "</script>" not in out, (
        f"escapeHtml did not neutralize a <script> tag: {out!r}"
    )
    assert out == (
        "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;quoted&quot; &#39;text&#39;"
    )


def test_auth_js_row_builder_escapes_the_theme_name():
    """Pins the CALL SITE, not just that escapeHtml() exists somewhere in the
    file — confirmed by sabotage: removing only the escapeHtml(...) wrapper
    around r.gics_sector (leaving the function definition untouched) must
    fail this test."""
    src = (Path(__file__).parent.parent / "dashboard/assets/auth.js").read_text()
    assert "escapeHtml(r.gics_sector)" in src, (
        "renderLatestRows no longer escapes r.gics_sector before interpolating "
        "it into innerHTML"
    )
    # And the function must actually be defined in this file, not just called.
    assert "function escapeHtml(" in src


def test_auth_js_row_builder_escapes_the_ticker_too():
    """The ticker symbol is built into the SAME innerHTML string as
    r.gics_sector, from the same config/themes.yaml source, but was left
    unescaped when the theme name was first hardened — caught in a second
    review pass, 2026-08-23."""
    src = (Path(__file__).parent.parent / "dashboard/assets/auth.js").read_text()
    assert "escapeHtml(ticker)" in src, (
        "renderLatestRows no longer escapes ticker before interpolating it "
        "into tickerHtml"
    )


def test_scan_digest_js_chip_builder_escapes_sector_and_region():
    """Pins the CALL SITE — same shape as the auth.js test above."""
    src = (Path(__file__).parent.parent / "dashboard/assets/scan-digest.js").read_text()
    assert "escapeHtml(item.sector)" in src, (
        "fmtChip no longer escapes item.sector before interpolating it into innerHTML"
    )
    assert "escapeHtml(item.region)" in src, (
        "fmtChip no longer escapes item.region before interpolating it into innerHTML"
    )
    assert "function escapeHtml(" in src


def test_scan_history_js_row_builder_escapes_the_theme_name():
    """Pins the CALL SITE — same shape as the auth.js test above. Added in
    code review, 2026-08-23: this file was the one call site the original
    sweep missed, despite its own comment citing auth.js's identical
    pattern by name."""
    src = (Path(__file__).parent.parent / "dashboard/assets/scan-history.js").read_text()
    assert "escapeHtml(sector)" in src, (
        "renderScanLeaderboard no longer escapes sector before interpolating "
        "it into innerHTML"
    )
    assert "function escapeHtml(" in src


def test_scan_history_js_row_builder_escapes_the_ticker_too():
    """Same second-review-pass gap as auth.js: the ticker symbol shares the
    tickerHtml/innerHTML sink but was left unescaped when `sector` was
    hardened."""
    src = (Path(__file__).parent.parent / "dashboard/assets/scan-history.js").read_text()
    assert "escapeHtml(ticker)" in src, (
        "renderScanLeaderboard no longer escapes ticker before interpolating "
        "it into tickerHtml"
    )


def test_mobile_card_theme_name_uses_innerhtml_not_textcontent():
    """The severer finding from the same review round: renderMobileCards()
    (index.html.j2) re-derives the mobile card view from the LEADERBOARD
    TABLE's own rendered DOM. `.textContent` on the theme-name span DECODES
    entities back to plain text ("&lt;img..." -> literal "<img...") before
    that string gets concatenated into a NEW innerHTML assignment a few
    lines below — silently undoing whatever escaping the table builder
    (auth.js's escapeHtml()/scan-history.js's escapeHtml()) already did.

    Confirmed live in a browser 2026-08-23: escapeHtml() itself verified
    correct in isolation (a debug-instrumented rebuild showed it correctly
    producing "&lt;img ...&gt;"), yet an injected theme name still executed
    as a real <img onerror=...> element — through exactly this read
    projection, on the mobile card view, at a 375px viewport. `.innerHTML`
    returns the SAME text RE-SERIALIZED with entities intact, safe to
    reinject; `.theme-name` holds only a single text node in every table
    builder, so this is a pure fix with no behavior change for real data.

    Source-pinned rather than run under Node: renderMobileCards() reads
    throughout from `tr.querySelector(...)`, so a real test needs a DOM
    (jsdom or a browser), which is not part of this project's JS test
    infrastructure — see the live browser verification above for the
    behavioral proof this source assertion cannot provide on its own."""
    src = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert "themeName.innerHTML" in src, (
        "renderMobileCards() reads themeName.textContent (or similar) instead "
        "of .innerHTML -- .textContent decodes HTML entities back to plain "
        "text, which then gets reinjected unescaped into the card's innerHTML, "
        "undoing whatever the table builder's escapeHtml() already did"
    )
    assert "themeName.textContent" not in src, (
        "themeName.textContent still appears in index.html.j2 -- the fixed "
        "call site must be the only reference"
    )


def test_apply_filters_assigned_before_i18n_include():
    """Code review, 2026-08-24 (removed-behavior angle): applyHorizonBadges()'s
    exactly-once applyBandBoundaries() guarantee holds today only because
    `window.applyFilters = applyFilters;` executes before _i18n.html.j2's
    include -- nothing structural enforces that order, only where the two
    lines happen to sit in the file. If a future edit moved the include
    earlier (or the assignment later), _i18n.html.j2's apply() would see
    window.applyFilters as undefined and skip its own trailing
    applyFilters() call, while applyHorizonBadges()'s direct-call guard
    (gated on the LOCAL hoisted `applyFilters` identifier, always defined)
    would also skip -- applyBandBoundaries()/renderMobileCards() would not
    run at all for that invocation, a silent regression from "renders
    twice" to "never renders," which neither of this file's other two
    guard tests can catch (both are scoped to applyHorizonBadges()'s own
    body). This test pins the ordering itself."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assign_at = text.index("window.applyFilters = applyFilters;")
    include_at = text.index('{% include "_i18n.html.j2" %}')
    assert assign_at < include_at, (
        "window.applyFilters must be assigned before _i18n.html.j2 is "
        "included, or applyHorizonBadges() can silently render zero times "
        "instead of the intended once"
    )


def test_short_date_formats_for_both_languages():
    """Badge suffixes name a real date. Intl handles the month name, so no new
    i18n keys are needed -- but it must actually differ by language, or Swedish
    readers get English months (the bug class that shipped on horizon_note)."""
    import json as _json
    import shutil, subprocess
    if shutil.which("node") is None:
        pytest.skip("node not available")
    js = _PROJECT_ROOT / "dashboard/assets/rescore.js"
    script = (
        f"const R = require({str(js)!r});"
        "console.log(JSON.stringify(["
        "R.shortDate('2026-08-31','en'), R.shortDate('2026-08-31','sv')]));"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    en, sv = _json.loads(out.stdout)
    assert "31" in en and "Aug" in en, f"unexpected English format: {en!r}"
    assert "31" in sv, f"unexpected Swedish format: {sv!r}"


def test_badge_carries_no_slot_when_the_book_is_full():
    """The regression that started this work: a board with four healthy
    holdings rendered a plain green Enter, which reads as 'buy now' when the
    strategy would buy nothing. freeSlots === 0 must be stated on the badge."""
    js = _apply_horizon_badges_js()
    assert "badge_no_slot" in js, (
        "applyHorizonBadges never references the no-slot suffix -- an Enter "
        "badge on a full book still reads as actionable"
    )
    assert "freeSlots" in js, "the badge pass does not consult selectBook's freeSlots"


def test_badge_carries_the_review_date_between_reviews():
    js = _apply_horizon_badges_js()
    assert "shortDate" in js, (
        "applyHorizonBadges does not stamp the next review date onto badges -- "
        "muting alone was already tried and did not work"
    )


def test_no_slot_and_review_suffixes_have_swedish():
    """Swedish has shipped missing twice on this exact surface."""
    sv = (Path(__file__).parent.parent
          / "dashboard/templates/i18n/_core.js.j2").read_text()
    for key in ("badge_no_slot",):
        assert f"{key}:" in sv, f"{key} has no Swedish translation"


def test_surplus_rows_are_marked_when_over_held():
    """Over-holding is never trimmed by the strategy (free goes negative), so
    the board must name the surplus rather than wait it out. The choice of the
    WORST-ranked holding is our rule, not the strategy's -- simulate() never
    over-holds, so the backtest has no opinion. Recorded in the spec."""
    js = _apply_horizon_badges_js()
    assert "position-surplus" in js, (
        "no surplus marking -- an over-held book gives the reader no way to "
        "see which position is the extra one"
    )
    assert "surplus" in js


def test_surplus_lookup_falls_back_to_sector_id():
    """Rows rebuilt client-side by auth.js's renderLatestRows() -- the
    signed-in path, the only path where book-state exists at all -- carry
    data-sector-id but not data-sector-key (see that function's own comment
    at auth.js). A lookup that only tries data-sector-key silently fails to
    mark the surplus row for every signed-in reader, which is this feature's
    entire audience. The fix is a shared helper with a data-sector-id
    fallback, used here instead of a raw single-selector querySelector."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    assert "function _leaderboardRowForKey(" in text, (
        "no shared row-lookup helper -- the data-sector-id fallback must live "
        "in one place so both the surplus mark and the review panel use it"
    )
    helper_start = text.index("function _leaderboardRowForKey(")
    helper_brace = text.index("{", helper_start)
    depth = 0
    i = helper_brace
    while True:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                break
        i += 1
    helper_body = text[helper_start:i + 1]
    assert "data-sector-key" in helper_body and "data-sector-id" in helper_body, (
        "_leaderboardRowForKey does not fall back to data-sector-id"
    )

    js = _apply_horizon_badges_js()
    assert "_leaderboardRowForKey(k)" in js, (
        "the surplus-marking lookup does not call the shared helper -- it is "
        "still a raw data-sector-key-only querySelector, which misses every "
        "signed-in reader's rebuilt rows"
    )
    assert 'querySelector(\'.leaderboard-row[data-sector-key="\' + k + \'"]\')' not in js, (
        "the surplus-marking lookup still has the raw single-selector query "
        "inline, alongside (or instead of) the shared helper call"
    )


def test_surplus_style_exists():
    css = (Path(__file__).parent.parent
           / "dashboard/templates/css/_tables.css.j2").read_text()
    assert ".position-surplus" in css, "surplus rows have no visual treatment"


# ---------------------------------------------------------------------------
# Whole-branch review finding: selectBook()'s rankedKeys must be RANK order,
# not raw querySelectorAll() DOM order (sortTable()-reorderable).
# ---------------------------------------------------------------------------

def _book_collection_js():
    """The row-collection block inside applyHorizonBadges() that builds
    _rankedKeys/_heldKeys/_unbuyableKeys and calls selectBook(), extracted
    verbatim so the test exercises the real production code."""
    text = (Path(__file__).parent.parent / "dashboard/templates/index.html.j2").read_text()
    start = text.index("var _rankedKeys = [], _heldKeys = [], _unbuyableKeys = [];")
    end_marker = "_bookState = window.Rescore.selectBook(_rankedKeys, _heldKeys, h, _unbuyableKeys);"
    end = text.index(end_marker) + len(end_marker)
    return text[start:end]


def test_book_collection_sorts_by_data_rank():
    """Source pin for the fix itself: the collection must sort on the actual
    rank value, not rely on `rows` already being in rank order."""
    snippet = _book_collection_js()
    assert "parseFloat(a.dataset.rank) - parseFloat(b.dataset.rank)" in snippet, (
        "row collection no longer sorts by data-rank before building "
        "_rankedKeys -- selectBook()'s result again depends on whatever "
        "order sortTable() last left the DOM in"
    )
    assert ".sort(" in snippet


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_book_state_is_independent_of_dom_order():
    """Behavioral proof, not just source-pinned: selectBook() reads
    rankedKeys[] POSITION as rank order (index 0 = best rank) -- it never
    looks at data-rank itself. Rows are collected via querySelectorAll in
    whatever order sortTable() (Composite, Theme, Rank Δ, ...) last left the
    DOM in. Feeds the SAME three rows to the real, verbatim-extracted
    collection code in two different orders -- already rank-sorted, and
    reversed (as a reader who just sorted by Theme name would leave them) --
    and asserts selectBook() returns the IDENTICAL sells/buys/picks either
    way. Before this fix this would fail: the reversed input fed a
    differently-ordered _rankedKeys straight into selectBook(), which would
    keep the wrong holding and buy the wrong name."""
    snippet = _book_collection_js()
    rescore_path = _PROJECT_ROOT / "dashboard/assets/rescore.js"
    script = f"""
        global.window = {{}};
        window.Rescore = require({str(rescore_path)!r});

        function mkRow(rank, key) {{
          return {{
            dataset: {{ sectorKey: key, rank: String(rank) }},
            hasAttribute: function (name) {{ return name === 'data-rank'; }}
          }};
        }}
        // A ranked 1 (best), B ranked 2 and HELD, C ranked 3.
        var A = mkRow(1, 'A'), B = mkRow(2, 'B'), C = mkRow(3, 'C');
        var pos = {{ isHeld: function (tr) {{ return tr === B; }} }};
        // band = top_n(1) + buffer(0) = 1: B's rank (index 1) sits OUTSIDE
        // the band, so it must be sold and A (rank index 0) bought instead --
        // but only if rankOf is built from RANK order, not from whichever
        // array position each row happens to occupy below.
        var h = {{ top_n: 1, buffer: 0 }};

        function run(rows) {{
          var _bookState = null;
          {snippet}
          return _bookState;
        }}

        process.stdout.write(JSON.stringify({{
          rankOrder: run([A, B, C]),
          domReordered: run([C, A, B])
        }}));
    """
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["rankOrder"]["sells"] == ["B"]
    assert out["rankOrder"]["buys"] == ["A"]
    assert out["rankOrder"]["picks"] == ["A"]
    assert out["domReordered"] == out["rankOrder"], (
        "selectBook()'s result changed when the SAME rows were fed in a "
        "different (sortTable()-style reordered) DOM order -- the "
        "collection is still order-dependent"
    )
