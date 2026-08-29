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
         "sector": "Uranium", "ticker": "URA", "rank": 1, "setup": "entry",
         "composite": "1.2", "composite_bar": "", "level_change_bars": "",
         "delta_rank": "—", "arrow": "", "arrow_class": "",
         "trajectory_label": "→", "trajectory_state": "flat",
         "trajectory_word": "flat", "breakdown_html": "",
         "_raw_composite": 1.2, "_raw_change": 0.5, "_raw_level": 1.0},
        {"key": "THEME|Shipping", "sector_id": "THEME-Shipping", "region": "THEME",
         "sector": "Shipping", "ticker": "BOAT", "rank": 17, "setup": "exit",
         "composite": "-1.1", "composite_bar": "", "level_change_bars": "",
         "delta_rank": "—", "arrow": "", "arrow_class": "",
         "trajectory_label": "→", "trajectory_state": "flat",
         "trajectory_word": "flat", "breakdown_html": "",
         "_raw_composite": -1.1, "_raw_change": -0.4, "_raw_level": -1.0},
    ]


def _render_index(**overrides) -> str:
    from tests.test_dashboard_js import _horizon_ctx
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url

    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    register_asset_url(env)
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
    """No auth configured (a local build) keeps badges for everyone.

    This only proves the BAKE. applyHorizonBadges() then runs over these rows on
    load and could strip them — see
    test_unknown_holdings_falls_back_to_the_band_not_to_silence for the half
    that keeps them.
    """
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


def test_in_buy_band_is_computed_even_when_badges_are_gated():
    """The highlighted rank badge is NOT a signed-in feature and must survive
    gating.

    It regressed the moment it was introduced: `_compute_setup` also sets
    `in_buy_band`, but build.py only called it on the ungated branch, so the
    shipped (gated) build rendered every rank badge unhighlighted while the
    buy-band cut line was drawn right below the fourth row — the exact
    disagreement this replaced the hardcoded `rank <= 3` to fix.

    Asserted against build.py's structure because the bug was in which branch
    the call sat on, not in _compute_setup itself.
    """
    build_text = (_PROJECT_ROOT / "dashboard" / "build.py").read_text()
    call = build_text.index("_compute_setup(row, _default_horizon)")
    gate = build_text.index('if badges_gated:\n            row["setup"] = None')
    assert call < gate, (
        "_compute_setup must run before the gate, or in_buy_band is never set "
        "on a gated build"
    )
    assert build_text.count("_compute_setup(row, _default_horizon)") == 1, (
        "two call sites means one of them can drift behind the gate again"
    )


def test_gated_row_still_marks_the_buy_band():
    """End to end through the template: gated rows carry no setup but do carry
    the highlight."""
    rows = _rows()
    for row in rows:
        row["setup"] = None
    rows[0]["in_buy_band"] = True
    html = _render_index(leaderboard_rows=rows, badges_gated=True)
    # rank 1 also picks up the `rank-1` ring class, so match the prefix rather
    # than the whole attribute.
    assert 'class="rank-badge in-buy-band' in html
    # The in-band left rail on the rank cell tracks the same fact.
    assert 'class="rank-cell in-band-rail"' in html
    assert 'data-setup=""' in html
    # No BAKED badge span. The literal "setup-badge" also appears in the page's
    # JavaScript, which builds them at runtime, so match the rendered markup.
    assert '<span class="setup-badge' not in html


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

    Since the self-describing-suffix / Swedish-suffix fix (fix round 1 on
    task-2-report.md), `data-en` is refreshed to the BARE kind text (no
    suffix) — the suffix now lives in a separate `data-suffix` attribute that
    _i18n.html.j2's apply() appends to whichever base (data-en, or SV[key] on
    the Swedish branch) it picks. A suffix baked directly into data-en, as
    this pinned before the fix, made the Swedish branch permanently unable to
    show it (SV[key] never reads data-en at all) — see
    test_badge_i18n_playwright.py for the browser-verified regression test of
    that actual behaviour; this test only pins the source shape.
    """
    tpl = (_TPL_DIR / "index.html.j2").read_text()
    body = tpl[tpl.index("function applyHorizonBadges()"):]
    body = body[:body.index("\n}")]
    assert "badge.textContent = BADGE_TEXT_EN[kind] + suffix;" in body
    assert "badge.setAttribute('data-en', BADGE_TEXT_EN[kind]);" in body, \
        "badge text is set without refreshing data-en; applyLang will revert it"
    assert "badge.setAttribute('data-suffix', suffix);" in body, \
        "suffix is not carried on data-suffix; the Swedish branch cannot show it"
    assert "badge.removeAttribute('data-suffix');" in body, \
        "a stale data-suffix from a prior render can persist onto a badge with no suffix"


def test_only_the_badge_pass_writes_badge_markup():
    """Four code paths used to emit their own badge span and had already drifted
    (the rescore path was still on the pre-band heuristic). Everything else must
    delegate rather than grow a fifth copy."""
    for path in [_PROJECT_ROOT / "dashboard" / "assets" / "auth.js"]:
        assert "setup-badge" not in path.read_text(), \
            f"{path.name} renders its own badge again — use applyHorizonBadges()"


def _badge_for(rank, state, is_held, top_n=5, buffer=3):
    script = f"""
      const api = require({json.dumps(str(_RESCORE_JS))});
      const h = {{top_n: {top_n}, buffer: {buffer}}};
      process.stdout.write(JSON.stringify(api.badgeFor(
        {json.dumps(rank)}, h, {json.dumps(state)}, {json.dumps(is_held)})));
    """
    return json.loads(subprocess.run(["node", "-e", script], capture_output=True,
                                     text=True, check=True).stdout)


@pytestmark_node
def test_unknown_holdings_falls_back_to_the_band_not_to_silence():
    """The action-aware rule needs holdings. Without them "nothing is held" is
    indistinguishable from "nothing can be exited", so applying it blind deletes
    every Exit badge — on an ungated build that means stripping badges the
    server just rendered, and for a signed-in user whose holdings read failed it
    means hiding a real sell signal.
    """
    assert _badge_for(3, "unknown", False) == "entry"
    assert _badge_for(12, "unknown", False) == "exit"     # NOT None
    assert _badge_for(7, "unknown", False) is None        # middle band, as before


@pytestmark_node
def test_loading_holdings_shows_nothing_rather_than_guessing():
    """Mid-fetch, `isHeld` answers false for everything. Badging from that would
    flash "Enter" on a theme the reader holds — the exact misread this change
    exists to remove."""
    for rank in (3, 7, 12):
        assert _badge_for(rank, "loading", False) is None


@pytestmark_node
def test_ready_holdings_uses_the_action_aware_rule():
    assert _badge_for(3, "ready", False) == "entry"
    assert _badge_for(3, "ready", True) == "hold"
    assert _badge_for(12, "ready", True) == "exit"
    assert _badge_for(12, "ready", False) is None


def test_positions_records_a_failed_holdings_read():
    """`held` fails open to an empty Set for the ★ toggles. That is fine there
    and wrong for badges, so the failure has to be recorded separately."""
    src = (_PROJECT_ROOT / "dashboard" / "assets" / "positions.js").read_text()
    assert "loadFailed = true" in src, "a failed holdings read is indistinguishable from owning nothing"
    assert "function holdingsState()" in src
    assert 'if (!signedIn || loadFailed) return "unknown";' in src


def test_filter_group_has_an_explicit_hidden_rule():
    """`.filter-group { display: flex }` is an author rule and beats the UA
    stylesheet's `[hidden] { display: none }`, so setting the `hidden` property
    alone leaves the chips on screen for guests."""
    css = (_TPL_DIR / "css" / "_tables.css.j2").read_text()
    assert ".filter-group[hidden] { display: none; }" in css


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
