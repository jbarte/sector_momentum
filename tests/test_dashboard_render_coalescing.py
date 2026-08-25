"""Behavioral tests for mobile card render coalescing.

Source-text assertions (see test_dashboard_js.py's
test_apply_horizon_badges_direct_band_boundaries_call_is_guarded and its
neighbors) can pin the *shape* of a guard but not whether the actual DOM
rebuild ran once or three times -- that class of bug (BACKLOG.md, "Mobile
card list still double/triple-renders from several other entry points",
2026-08-24 review) was only ever caught by live, uninstrumented browser
counting. These tests automate that: load the real rendered dashboard in
headless Chromium, count actual writes to #leaderboard-cards' innerHTML
(the one DOM operation every real rebuild performs exactly once --
dashboard/templates/index.html.j2:799), and assert the count directly,
so this bug class is re-checked on every CI run.

See sector_momentum-notes/specs/2026-08-25-mobile-render-coalescing-design.md.
"""
import json
import shutil
import sys
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.build import _build_leaderboard_rows, _render
from dashboard.figures import _build_scan_history_data
from src.cohorts import Cohort

from tests.test_dashboard_js import (
    _horizon_ctx, _grouped_rows_for, _make_mock_plotly_json, _TEMPLATE,
)

_ASSETS_DIR = Path(__file__).parent.parent / "dashboard" / "assets"

# Assets the template references unconditionally (no `auth` context passed
# below, so the {% if auth %} block -- and auth.js/supabase.min.js -- never
# render; nothing needs them).
_REQUIRED_ASSETS = ["rescore.js", "theme.js", "scan-history.js", "scan-digest.js"]


def _render_leaderboard_html(tmp_path):
    """Render the real dashboard template with two THEME scans (2 rows each)
    and copy the JS assets it references alongside it, so a browser can load
    it via a file:// URL exactly like the built docs/index.html.

    Returns the Path to the rendered index.html.
    """
    df = pd.DataFrame([
        dict(scan_id=2, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Robotics", level_score=0.7, change_score=0.4,
             data_score=0.55, sentiment_score=0.2, composite=0.55, rank=1.0),
        dict(scan_id=2, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Semiconductors", level_score=-0.3, change_score=-0.1,
             data_score=-0.2, sentiment_score=0.0, composite=-0.2, rank=2.0),
        dict(scan_id=1, run_at="2026-08-17T06:00:00", region="THEME",
             gics_sector="Robotics", level_score=0.5, change_score=0.3,
             data_score=0.4, sentiment_score=0.1, composite=0.4, rank=2.0),
        dict(scan_id=1, run_at="2026-08-17T06:00:00", region="THEME",
             gics_sector="Semiconductors", level_score=0.1, change_score=0.0,
             data_score=0.05, sentiment_score=0.0, composite=0.05, rank=1.0),
    ])
    latest_df = df[df["scan_id"] == 2]
    lb_rows, scan_date = _build_leaderboard_rows(latest_df)

    universe = {"us_sectors": {}, "us_benchmark": "RSP",
                "eu_sectors": {}, "eu_benchmark": "EXSA.DE"}
    weights = {"pillars": {"data": 1.0}, "data_pillar": {"level": 0.5, "change": 0.5},
               "level_signals": {"rs_ratio": 1}, "change_signals": {"rs_momentum": 1}}
    for r in lb_rows:
        key = f"{r['region']}|{r['sector']}"
        r["key"] = key
        r["sector_id"] = key.replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "->"
        r["trajectory_state"] = "flat"
        r["setup"] = None
        r["breakdown_html"] = "<div>breakdown</div>"

    grouped_rows = _grouped_rows_for(lb_rows)
    scan_history = _build_scan_history_data(df)
    scan_index = [
        {"scan_id": 2, "run_at_display": "2026-08-24 06:00 UTC",
         "run_at_raw": "2026-08-24T06:00:00", "sector_count": 2,
         "top_sector": "Robotics", "top_region": "THEME"},
        {"scan_id": 1, "run_at_display": "2026-08-17 06:00 UTC",
         "run_at_raw": "2026-08-17T06:00:00", "sector_count": 2,
         "top_sector": "Semiconductors", "top_region": "THEME"},
    ]

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        us_leaderboard_rows=[], eu_leaderboard_rows=[],
        cohort_list=[c for c, _ in grouped_rows], grouped_rows=grouped_rows,
        has_any_rows=any(rs for _, rs in grouped_rows),
        cohorts_json=json.dumps([{"region": "THEME", "label": "Themes"}]),
        **_horizon_ctx(), cohort_charts_json=json.dumps({}),
        sentiment_scatter_json=_make_mock_plotly_json(),
        rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=json.dumps(scan_history),
        scan_index=scan_index, active_scan_id=2,
        signals_list=[], plotly_bundle="assets/plotly.min.js",
        backtest_json=json.dumps({}), backtest_metrics=[], has_backtest=False,
        rotation_json=json.dumps([]), has_rotations=False,
    ))

    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(exist_ok=True)
    for name in _REQUIRED_ASSETS:
        src = _ASSETS_DIR / name
        if src.exists():
            shutil.copy2(src, assets_dir / name)

    return out


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment-dependent
            pytest.skip(f"Chromium not available (run `playwright install "
                        f"chromium`): {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, tmp_path):
    html_path = _render_leaderboard_html(tmp_path)
    pg = browser.new_page()
    pg.goto(html_path.as_uri())
    yield pg
    pg.close()


def _arm_card_rewrite_counter(page):
    """Count writes to #leaderboard-cards' innerHTML -- the one DOM op every
    real renderMobileCards() rebuild performs exactly once, regardless of
    internal function names on either side of the coalescing fix."""
    page.evaluate("""() => {
        window.__cardRewriteCount = 0;
        const container = document.getElementById('leaderboard-cards');
        const desc = Object.getOwnPropertyDescriptor(Element.prototype, 'innerHTML');
        Object.defineProperty(container, 'innerHTML', {
            configurable: true,
            get() { return desc.get.call(this); },
            set(v) { window.__cardRewriteCount++; desc.set.call(this, v); },
        });
    }""")


def _flush_microtasks(page):
    """Let any Promise.resolve().then(...) queued by the coalescer run."""
    page.evaluate("() => new Promise(resolve => setTimeout(resolve, 0))")


def test_multiple_synchronous_calls_coalesce_to_one_render(page):
    """Calling renderMobileCards() three times in the same synchronous tick
    -- what every one of the four buggy entry points in BACKLOG.md's
    "Mobile card list still double/triple-renders..." item effectively does
    -- must rebuild the card list exactly once, not three times."""
    _arm_card_rewrite_counter(page)
    page.evaluate("""() => {
        window.renderMobileCards();
        window.renderMobileCards();
        window.renderMobileCards();
    }""")
    _flush_microtasks(page)
    count = page.evaluate("() => window.__cardRewriteCount")
    assert count == 1, (
        f"expected exactly 1 real card rebuild for 3 synchronous "
        f"renderMobileCards() calls, got {count}"
    )
