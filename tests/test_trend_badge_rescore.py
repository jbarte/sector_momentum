"""Regression: client-side rescoring must not flatten the Trend badge.

`updateRows()` (index.html.j2) used `textContent`, which replaces the
`.traj-glyph`/`.traj-word` span pair every trajectory builder emits with a
single bare-glyph text node — destroying the word AND the tooltip. Dormant in
production today (its only caller is the sentiment-blend ranking control,
withdrawn while sentiment is alpha — see BACKLOG.md), so this is exercised
here by rendering with the control force-enabled and driving a real browser,
the only way to catch a bug in how the DOM gets rebuilt rather than in the
data feeding it (see test_dashboard_render_coalescing.py for the same
reasoning about a different bug class).
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

from tests.test_dashboard_js import _horizon_ctx, _grouped_rows_for, _make_mock_plotly_json, _TEMPLATE
from tests.test_dashboard_render_coalescing import _REQUIRED_ASSETS

_ASSETS_DIR = Path(__file__).parent.parent / "dashboard" / "assets"


def _render_with_sentiment_control(tmp_path):
    """Two THEME rows, sentiment_ranking_enabled=True, and a two-scan
    RESCORE_DATA constructed so toggling the control changes Alpha's rank
    from 2 to 1 -- a real, non-flat trajectory (state "up"), distinct from
    the "flat" state every row is baked with, so a passing test can only mean
    updateRows() actually ran and rebuilt the badge from Rescore.rescore()'s
    output, not that the pre-existing baked badge was left untouched."""
    df = pd.DataFrame([
        dict(scan_id=1, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Alpha", level_score=0.1, change_score=0.1,
             data_score=0.1, sentiment_score=0.0, composite=0.1, rank=1.0),
        dict(scan_id=1, run_at="2026-08-24T06:00:00", region="THEME",
             gics_sector="Beta", level_score=0.5, change_score=0.5,
             data_score=0.5, sentiment_score=0.0, composite=0.5, rank=2.0),
    ])
    lb_rows, scan_date = _build_leaderboard_rows(df)
    for r in lb_rows:
        key = f"{r['region']}|{r['sector']}"
        r["key"] = key
        r["sector_id"] = key.replace("|", "-").replace(" ", "_")
        r["trajectory_label"] = "→"
        r["trajectory_state"] = "flat"
        r["trajectory_word"] = "flat"
        r["setup"] = None
        r["breakdown_html"] = "<div>breakdown</div>"

    grouped_rows = _grouped_rows_for(lb_rows)
    scan_history = _build_scan_history_data(df)

    # scan 1 (older): Alpha behind Beta. scan 2 (newer): Alpha overtakes.
    rescore_data = {
        "scans": [{"scan_id": 1, "run_at": "2026-08-17T00:00:00"},
                 {"scan_id": 2, "run_at": "2026-08-24T00:00:00"}],
        "sectors": ["THEME|Alpha", "THEME|Beta"],
        "data": {"THEME|Alpha": [0.1, 0.9], "THEME|Beta": [0.9, 0.1]},
        "sentiment": {"THEME|Alpha": [0.0, 0.0], "THEME|Beta": [0.0, 0.0]},
    }

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date=scan_date, leaderboard_rows=lb_rows,
        us_leaderboard_rows=[], eu_leaderboard_rows=[],
        cohort_list=[c for c, _ in grouped_rows], grouped_rows=grouped_rows,
        has_any_rows=any(rs for _, rs in grouped_rows),
        cohorts_json=json.dumps([{"region": "THEME", "label": "Themes"}]),
        **_horizon_ctx(), cohort_charts_json=json.dumps({}),
        sentiment_scatter_json=_make_mock_plotly_json(),
        rescore_data_json=json.dumps(rescore_data),
        sentiment_ranking_enabled=True,
        scan_history_json=json.dumps(scan_history),
        scan_index=[{"scan_id": 1, "run_at_display": "2026-08-24 06:00 UTC",
                    "run_at_raw": "2026-08-24T06:00:00", "sector_count": 2,
                    "top_sector": "Beta", "top_region": "THEME"}],
        active_scan_id=1,
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
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment-dependent
            pytest.skip(f"Chromium not available (run `playwright install "
                        f"chromium`): {exc}")
        yield b
        b.close()


@pytest.fixture
def page(browser, tmp_path):
    html_path = _render_with_sentiment_control(tmp_path)
    pg = browser.new_page()
    pg.goto(html_path.as_uri())
    yield pg
    pg.close()


def _alpha_traj(page):
    row = page.locator('tr.leaderboard-row[data-sector="Alpha"]')
    return row.locator(".traj-badge")


def test_baked_badge_has_glyph_and_word_before_any_rescore(page):
    """Sanity: the server-rendered badge is already the two-span shape."""
    badge = _alpha_traj(page)
    assert badge.locator(".traj-glyph").count() == 1
    assert badge.locator(".traj-word").count() == 1
    assert badge.locator(".traj-word").inner_text() == "flat"


def test_toggling_the_sentiment_control_rebuilds_the_badge_with_both_spans(page):
    """The regression: a rescore must not collapse the badge to a bare glyph."""
    badge = _alpha_traj(page)
    page.locator(".rank-settings summary").click()  # <details> starts collapsed
    page.locator("#sentiment-toggle").check()
    page.wait_for_function(
        "document.querySelector('tr[data-sector=\"Alpha\"] .traj-word')"
        "?.textContent === 'rising'"
    )
    assert badge.locator(".traj-glyph").count() == 1, \
        "textContent would have destroyed this span"
    assert badge.locator(".traj-word").count() == 1, \
        "textContent would have destroyed this span"
    assert badge.locator(".traj-glyph").inner_text() == "↑"
    assert badge.locator(".traj-word").inner_text() == "rising"
    assert "traj-up" in (badge.get_attribute("class") or "")


def test_the_badge_still_has_its_tooltip_after_rescoring(page):
    """innerHTML on the existing span (not outerHTML replacement) must leave
    the element's own attributes -- title, data-i18n-title -- untouched."""
    page.locator(".rank-settings summary").click()
    page.locator("#sentiment-toggle").check()
    page.wait_for_function(
        "document.querySelector('tr[data-sector=\"Alpha\"] .traj-word')"
        "?.textContent === 'rising'"
    )
    assert _alpha_traj(page).get_attribute("title") == "Rank slope over last 3–5 scans"


def test_rescoring_updates_data_trend_so_the_trend_filter_stays_in_sync(page):
    """Review finding: updateRows() repainted the badge but left the row's
    data-trend attribute stale, which _matchesTrend() reads for the Trend
    filter chips. A reader with a filter active would see a badge disagree
    with what the filter shows or hides."""
    row = page.locator('tr.leaderboard-row[data-sector="Alpha"]')
    assert row.get_attribute("data-trend") == "flat"
    page.locator(".rank-settings summary").click()
    page.locator("#sentiment-toggle").check()
    page.wait_for_function(
        "document.querySelector('tr[data-sector=\"Alpha\"] .traj-word')"
        "?.textContent === 'rising'"
    )
    assert row.get_attribute("data-trend") == "up"
