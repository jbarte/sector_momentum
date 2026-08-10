"""The Enter/Hold/Exit badge: gated behind sign-in, and action-aware.

Two separate guarantees, both easy to break silently:

1. **Gating.** `setup` reaches the reader through three channels — the rendered
   span, the row's `data-setup` attribute (which the filter chips read), and
   `data.json`'s theme rows. Suppressing only the span leaves the other two.
2. **Action-awareness.** The label depends on the band AND on whether the reader
   holds the theme. That rule lives in rescore.js (one writer, four callers), so
   it is exercised here under Node rather than re-implemented in Python.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
_TPL_DIR = _PROJECT_ROOT / "dashboard" / "templates"
_RESCORE_JS = _PROJECT_ROOT / "dashboard" / "assets" / "rescore.js"


# ---------------------------------------------------------------------------
# Gating — the build must not bake a badge into the guest page
# ---------------------------------------------------------------------------

def _rows():
    """Two rows spanning both badge bands under the default horizon."""
    return [
        {"key": "THEME|Uranium", "sector_id": "THEME-Uranium", "region": "THEME",
         "sector": "Uranium", "rank": 1, "setup": "entry", "composite": "1.2",
         "level_score": "1.0", "change_score": "0.5", "data_score": "0.8",
         "sentiment_score": "—", "delta_rank": "—", "arrow": "", "arrow_class": "",
         "trajectory_label": "→", "trajectory_state": "flat", "breakdown_html": "",
         "_raw_composite": 1.2, "_raw_change": 0.5},
        {"key": "THEME|Shipping", "sector_id": "THEME-Shipping", "region": "THEME",
         "sector": "Shipping", "rank": 17, "setup": "exit", "composite": "-1.1",
         "level_score": "-1.0", "change_score": "-0.4", "data_score": "-0.7",
         "sentiment_score": "—", "delta_rank": "—", "arrow": "", "arrow_class": "",
         "trajectory_label": "→", "trajectory_state": "flat", "breakdown_html": "",
         "_raw_composite": -1.1, "_raw_change": -0.4},
    ]


def _render_index(**overrides) -> str:
    from tests.test_dashboard_js import _horizon_ctx
    from jinja2 import Environment, FileSystemLoader

    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    env.filters["js_json"] = lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    ctx = dict(
        scan_date="2026-08-10", leaderboard_rows=_rows(), cohort_list=[],
        cohorts_json=json.dumps([]), cohort_charts_json=json.dumps({}),
        sentiment_scatter_json=json.dumps({"data": [], "layout": {}}),
        rescore_data_json=json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=json.dumps({"scans": [], "scores": {}}),
        signals_list=[], plotly_bundle="assets/plotly.min.js",
        backtest_json=json.dumps({}), backtest_metrics=[], has_backtest=False,
        rotation_json=json.dumps([]), has_rotations=False, has_any_rows=True,
        **_horizon_ctx(),
    )
    ctx.update(overrides)
    return env.get_template("index.html.j2").render(**ctx)


def _leaderboard_row_tags(html: str) -> list[str]:
    return re.findall(r"<tr class=\"leaderboard-row\"[^>]*>", html)


def test_gated_build_bakes_no_badge_and_no_setup_attribute():
    """With gating on, build.py sets row['setup'] = None for every row."""
    rows = [{**r, "setup": None} for r in _rows()]
    html = _render_index(leaderboard_rows=rows, badges_gated=True)

    # Match the element, not the class name — the stylesheet and the guide prose
    # both mention .setup-badge and neither is a leak.
    assert '<span class="setup-badge' not in html, \
        "a badge span was baked into the gated leaderboard"
    tags = _leaderboard_row_tags(html)
    assert len(tags) == 2
    for tag in tags:
        assert 'data-setup=""' in tag, \
            f"data-setup leaks the badge to the filter chips: {tag}"


def test_ungated_build_renders_the_badge():
    """No auth configured (a local build) keeps badges for everyone."""
    html = _render_index(badges_gated=False)
    assert '<span class="setup-badge entry" data-i18n="badge_entry">▲ Enter</span>' in html
    assert '<span class="setup-badge exit" data-i18n="badge_exit">▼ Exit</span>' in html
    tags = _leaderboard_row_tags(html)
    assert any('data-setup="entry"' in t for t in tags)
    assert any('data-setup="exit"' in t for t in tags)


def test_missing_badges_gated_key_gates_rather_than_leaks():
    """A dropped context key must cost the feature, not publish it.

    Jinja renders an undefined name as falsy, so the obvious
    `{{ 'true' if badges_gated else 'false' }}` would emit `false` — i.e. NOT
    gated — the day someone removes the key from build.py's context.
    """
    html = _render_index()                       # badges_gated deliberately absent
    assert "var BADGES_GATED = true;" in html


def test_build_passes_badges_gated_into_the_page_context():
    """The template reads BADGES_GATED but the JS-var guard test only scans
    `{{ x | safe }}` assignments, so this key has no other coverage."""
    build_text = (_PROJECT_ROOT / "dashboard" / "build.py").read_text()
    assert '"badges_gated": badges_gated' in build_text
    assert "badges_gated = lag_active" in build_text


def test_gating_suppresses_setup_before_data_json_is_built():
    """data.json's theme rows are spread from leaderboard_rows, so gating has to
    happen where setup is computed, not at render time."""
    build_text = (_PROJECT_ROOT / "dashboard" / "build.py").read_text()
    gate = build_text.index("if badges_gated:\n            row[\"setup\"] = None")
    theme_rows = build_text.index("theme_rows = [")
    assert gate < theme_rows, "setup is gated after data.json's rows are derived"


# ---------------------------------------------------------------------------
# Action-awareness — the label rule, exercised in the language it lives in
# ---------------------------------------------------------------------------

pytestmark_node = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not available")


def _badge(rank, top_n, buffer, is_held):
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      const h = {{top_n: {top_n}, buffer: {buffer}}};
      process.stdout.write(JSON.stringify(
        api.badgeForRank({json.dumps(rank)}, h, {json.dumps(is_held)})));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         check=True).stdout
    return json.loads(out)


@pytestmark_node
def test_badge_truth_table():
    """top_n=5, buffer=3 -> buy band 1-5, silent 6-8, exit from 9.

    The two rows that motivated this change are the `held` entry band (was
    "Entry" on something already owned, which reads as a buy signal) and the
    unheld exit band (was "Exit" on something never owned).
    """
    T, B = 5, 3
    assert _badge(3, T, B, False) == "entry"      # buy it
    assert _badge(3, T, B, True) == "hold"        # was "entry" — the misleading one
    assert _badge(7, T, B, True) == "hold"        # silent band, still owned
    assert _badge(7, T, B, False) is None
    assert _badge(12, T, B, True) == "exit"
    assert _badge(12, T, B, False) is None        # cannot exit what you don't own


@pytestmark_node
def test_hold_spans_the_whole_band_so_a_holding_does_not_flicker():
    """A held theme drifting across the top_n boundary keeps one label.

    Scoping "hold" to the buy band alone would drop the badge at rank 6 — a
    visible change on a move the band rule deliberately calls noise.
    """
    T, B = 5, 3
    labels = {_badge(r, T, B, True) for r in range(1, T + B + 1)}
    assert labels == {"hold"}


@pytestmark_node
def test_unranked_row_is_never_badged():
    """A held theme with no rank must not fall through to "hold"."""
    assert _badge(None, 5, 3, True) is None
    assert _badge(float("nan"), 5, 3, True) is None


def test_badge_pass_rewrites_data_en_when_it_reuses_a_span():
    """applyLang() restores English from a cached `data-en`, and the badge span
    is reused across kinds — a span that goes Enter -> Hold and keeps
    data-en="▲ Enter" snaps back to Enter on the next English pass. Only
    verifiable in a browser, so this pins the line instead.
    """
    tpl = (_TPL_DIR / "index.html.j2").read_text()
    body = tpl[tpl.index("function applyHorizonBadges()"):]
    body = body[:body.index("\n}")]
    assert "badge.textContent = BADGE_TEXT_EN[kind];" in body
    assert "badge.setAttribute('data-en', BADGE_TEXT_EN[kind]);" in body, \
        "badge text is set without refreshing data-en; applyLang will revert it"


def test_only_the_badge_pass_writes_badge_markup():
    """Four code paths used to emit their own badge span and had already drifted
    (the rescore path was still on the pre-band heuristic). Everything else must
    delegate rather than grow a fifth copy."""
    for path in [_PROJECT_ROOT / "dashboard" / "assets" / "auth.js"]:
        assert "setup-badge" not in path.read_text(), \
            f"{path.name} renders its own badge again — use applyHorizonBadges()"


@pytestmark_node
def test_band_rule_itself_is_unchanged():
    """setupForRank still mirrors dashboard/rows.py:_compute_setup — the
    server-side consumers (alerts, badge scorecard) have no reader and must keep
    seeing the plain band."""
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      const h = {{top_n: 5, buffer: 3}};
      process.stdout.write(JSON.stringify(
        [3, 7, 12].map(r => api.setupForRank(r, h))));
    """
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True,
                         check=True).stdout
    assert json.loads(out) == ["entry", None, "exit"]
