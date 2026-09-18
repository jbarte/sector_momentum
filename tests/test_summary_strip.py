"""Summary strip — the three cells between the command bar and the tab bar."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
INDEX = (ROOT / "dashboard/templates/index.html.j2").read_text()
HEADER = (ROOT / "dashboard/templates/_header.html.j2").read_text()
AUTH = (ROOT / "dashboard/assets/auth.js").read_text()


def _track_record_cell() -> str:
    """Cell C -- SPY/VIX market-context chips until 2026-09-05, when the
    track-record chip replaced them with 1M/12M performance vs ACWI. The id
    changed (cell-market-context -> cell-track-record) but the cell's role
    (third strip-cell, tappable guide trigger) did not."""
    start = INDEX.index('id="cell-track-record"')
    return INDEX[start:INDEX.index("</section>", start)]


def test_strip_sits_between_the_command_bar_and_the_tab_bar():
    """Order matters: the strip frames the board, so it must precede the tabs."""
    assert INDEX.index('class="summary-strip"') < INDEX.index('class="tabs"')


def test_all_three_cells_exist():
    for cell_id in ("cell-todays-read", "cell-buy-band", "cell-track-record"):
        assert f'id="{cell_id}"' in INDEX


def test_todays_read_cell_is_guarded_on_the_derived_facts():
    """todays_read is None on a build with no scan rows; the cell must then not
    render at all rather than render an empty sentence."""
    assert "{% if todays_read %}" in INDEX


def test_todays_read_renders_the_theme_then_the_lead_phrase():
    """Theme name first, then the fixed phrase that follows it."""
    assert "{{ todays_read.lead_theme }}" in INDEX
    assert "leads the board." in INDEX


def test_all_three_drift_phrases_are_present():
    phrases = {
        "rising": "The bottom half is picking up.",
        "falling": "The bottom half keeps sliding.",
        "flat": "The bottom half is holding flat.",
    }
    for drift, phrase in phrases.items():
        assert phrase in INDEX, drift


def _render_header(active_segment, auth=False):
    """_header.html.j2 is shared by index.html.j2 and sentiment.html.j2.
    Same minimal-render technique test_mobile_scan_meta_survives_missing_
    scan_date (test_dashboard_js.py) uses for this exact template."""
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    env = Environment(loader=FileSystemLoader(str(ROOT / "dashboard" / "templates")))
    register_asset_url(env)
    return env.get_template("_header.html.j2").render(
        active_segment=active_segment, auth=auth)


def test_market_context_chips_are_gone_from_the_sentiment_page_too():
    """Task 3 (2026-09-05) removed the market-context chips outright, not just
    from index.html.j2's Cell C — the sentiment page has no track-record cell
    to explain, so it gets no chips and no guide trigger at all, on any build
    (with or without auth configured). Also covers the leaderboard page's own
    header (active_segment="sectors"): with `macro` gone from the template
    entirely, there is nothing left that could render the desktop SPY/VIX
    chips there either — see test_the_chips_are_one_tappable_control in
    test_market_context_chips.py for the companion assertion that
    'id="context-chips"' is gone from _header.html.j2 for good."""
    for segment in ("sectors", "sentiment"):
        for auth in (False, True):
            html = _render_header(segment, auth=auth)
            assert "SPY" not in html
            assert 'id="market-context-chips"' not in html
            assert "tab-guide-btn" not in html


def test_the_mobile_scan_meta_row_has_no_spy_vix_echo_left():
    """Stage 3's mobile row survives (scan id/date still need a phone-width
    home), but its SPY/VIX echo went with the rest of the macro stack."""
    assert 'class="mobile-scan-meta"' in HEADER
    assert "macro" not in HEADER


def test_track_record_cell_keeps_the_guide_reachable():
    cell = _track_record_cell()
    assert 'data-guide="guide_body_track_record"' in cell
    assert "tab-guide-btn" in cell
    assert 'class="cc-label"' in cell, \
        "the dispatch prefers .cc-label over live numbers as the dialog heading"


def test_track_record_cell_has_an_outer_guard():
    """The SPY/VIX version of this cell (Stage 4) had both an outer guard
    (`{% if macro or auth %}`) and an inner one (`{% if macro %}`) hiding just
    the chips. The 2026-09-05 track-record chip collapsed that to a single
    outer guard (`{% if has_backtest or auth %}`): the chips' values are
    always filled by renderHorizonStats() (an em dash when the horizon has no
    live stats yet), so there is no server-rendered inner branch left to
    guard. Without the outer guard, the eyebrow button (and its "what this
    compares" trigger) would still render on a build with neither a backtest
    artifact nor auth configured, with nothing beside it to explain — see the
    old _header.html.j2 block this replaced, pre-Stage-4.

    Strips Jinja comments before searching — a `{#- ... -#}` comment
    explaining the guard sits directly above it and itself contains the
    words "has_backtest"/"auth" in prose, which a naive substring check over
    the raw text would satisfy even with the actual `{% if %}` tag removed
    (caught live: this test passed against a sabotaged copy on the first
    draft, for exactly that reason)."""
    start = INDEX.index('<div class="strip-cell" id="cell-track-record"')
    preceding = re.sub(r"\{#-.*?-#\}", "", INDEX[:start], flags=re.DOTALL)
    tags = list(re.finditer(r"\{%\s*(if|endif)\s*([^%]*)%\}", preceding))
    assert tags, "expected a `{% if %}` tag before #cell-track-record"
    last = tags[-1]
    assert last.group(1) == "if", (
        "the last Jinja tag before #cell-track-record is `{% endif %}`, "
        "meaning no `{% if %}` block is still open — the cell is unguarded"
    )
    assert "has_backtest" in last.group(2) and "auth" in last.group(2), (
        "expected an `{% if has_backtest or auth %}`-shaped guard, found: "
        + last.group(0)
    )


def test_vs_acwi_label_matches_the_configured_benchmark():
    """index.html.j2's Cell C eyebrow hardcodes "vs ACWI" as the track-record
    chips' label, while dashboard/figures.py's `_window_excess` computes the
    excess return generically against whatever `track["benchmark"]` the
    equity curve carries -- correct today only because config/themes.yaml's
    `benchmark:` happens to be ACWI. Nothing else pins that agreement, so a
    future change to the config would silently make the label lie about what
    the chips actually measure. Pinned here rather than threaded through the
    template: `benchmark` is a single global config value (themes.yaml's own
    comment), so a config change is the only way this could ever drift, and
    this test forces whoever makes that change to also update the hardcoded
    label in the same commit."""
    import yaml
    themes_cfg = yaml.safe_load((ROOT / "config/themes.yaml").read_text())
    benchmark = (themes_cfg or {}).get("benchmark") or "ACWI"
    assert benchmark == "ACWI", (
        f"config/themes.yaml's benchmark is now {benchmark!r}, but "
        f"index.html.j2's Cell C eyebrow still hardcodes 'vs ACWI' -- "
        f"update strip_eyebrow_vs_bench"
    )
    assert "vs ACWI" in INDEX, "the hardcoded EN label this test pins is gone"


def test_mark_live_targets_the_new_cell():
    """Left pointing at #context-chips, markLive() silently takes its
    .meta-cluster fallback and the Live chip lands back in the header."""
    assert 'getElementById("context-chips")' not in AUTH
    assert 'getElementById("market-context-chips")' in AUTH


def test_eyebrow_labels_exist_for_all_three_cells():
    for phrase in ("Today's read", "In the buy band", "vs ACWI"):
        assert phrase in INDEX


def test_mobile_hides_the_subline_that_the_scan_meta_row_repeats():
    """At 375px .mobile-scan-meta (Stage 3) already prints the scan id and
    date; the strip's subline repeats exactly that within a couple of hundred
    pixels of it, so it is hidden at this width rather than printing the same
    facts twice on one screen. Found at the browser gate; caught nowhere
    else, since it is correct in isolation."""
    css = (ROOT / "dashboard/templates/css/_responsive.css.j2").read_text()
    mobile = css[css.index("@media (max-width: 600px)"):]
    assert re.search(r"\.strip-subline\s*\{\s*display:\s*none", mobile)


def test_mobile_does_not_hide_the_track_record_cell():
    """Cell C used to be hidden here, and the reason was DE-DUPLICATION: the
    SPY/VIX chips it carried were already printed by .mobile-scan-meta a few
    hundred pixels above. The track-record chips that replaced them have no
    such echo anywhere on a phone, so the same rule would remove the feature
    outright instead of de-duplicating it — including its staleness warning.

    (The comment that survived the swap justified the hide as "a third ~125px
    cell doesn't fit". That was never true at this breakpoint: the rule right
    below sets grid-template-columns: 1fr, which stacks the cells, so width
    was never the constraint. Restored 2026-09-05.)"""
    css = (ROOT / "dashboard/templates/css/_responsive.css.j2").read_text()
    mobile = css[css.index("@media (max-width: 600px)"):]
    assert not re.search(r"#cell-track-record\s*\{\s*display:\s*none", mobile), (
        "Cell C is hidden on mobile again -- that removes the feature on "
        "phones rather than de-duplicating it; see this test's docstring"
    )


# ---------------------------------------------------------------------------
# Signed-in upgrade: "Today's read" must follow the live board.
#
# Found 2026-09-18: the cell is baked from the GATED scan, and nothing updated
# it when auth.js swapped the table to the live one -- so a signed-in reader saw
# "AgTech & Food Innovation leads the board. Scan #187 · 2026-09-10" above a
# live table (scan #195) led by Shipping, beside a green "Live" chip claiming
# the page showed the latest scan. auth.js's own comment said there was no
# scan-date element to update; that stopped being true when the strip and the
# scan-meta rows were added, and the decision was never revisited.
# ---------------------------------------------------------------------------

import json as _json
import shutil as _shutil
import subprocess as _subprocess

import pytest as _pytest

_TPL_DIR = ROOT / "dashboard" / "templates"
_RESCORE_JS = ROOT / "dashboard" / "assets" / "rescore.js"


def _render_index(todays_read, active_scan_id=187, scan_date="2026-09-10 11:09 UTC"):
    """Minimal real render of index.html.j2 -- same context shape
    tests/test_leaderboard_filters.py::_render_index uses, plus the three
    values this cell reads."""
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    from src.horizons import round_trip_bps
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    register_asset_url(env)
    env.filters["js_json"] = lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    return env.get_template("index.html.j2").render(
        leaderboard_rows=[], round_trip_bps=round_trip_bps(), cohort_list=[],
        has_any_rows=False, theme_keys=[], scan_index=[], backtest_metrics=[],
        badge_scorecard=[], todays_read=todays_read,
        active_scan_id=active_scan_id, scan_date=scan_date,
    )


def _todays_read_cell(html):
    start = html.index('id="cell-todays-read"')
    return html[start:html.index('id="cell-buy-band"', start)]


@_pytest.mark.parametrize("drift", ["rising", "falling", "flat"])
def test_every_drift_sentence_is_rendered_but_only_the_current_one_shows(drift):
    """All three sentences must be in the DOM so the live upgrade can switch
    between them WITHOUT JS owning any prose (digest.py: every user-visible
    word lives in this template). Exactly one may be visible -- a guest, who
    never gets the upgrade, must still read one sentence, not three."""
    cell = _todays_read_cell(_render_index({"lead_theme": "Shipping", "drift": drift}))
    for d in ("rising", "falling", "flat"):
        span = re.search(r'<span data-drift="%s"([^>]*)>' % d, cell)
        assert span, f"no data-drift={d} sentence rendered"
        assert ("hidden" in span.group(1)) == (d != drift), (
            f"drift={drift}: the {d} sentence has the wrong visibility"
        )


def test_the_lead_theme_and_scan_facts_are_addressable():
    """The upgrade rewrites these by hook, so each must be its own element --
    in the strip's subline AND the mobile header row, which both print the
    scan id and date (the subline is hidden at <=600px, so on a phone the
    header row is the only one a reader sees)."""
    html = _render_index({"lead_theme": "AgTech & Food Innovation", "drift": "falling"})
    cell = _todays_read_cell(html)
    assert '<span class="todays-read-lead">AgTech &amp; Food Innovation</span>' in cell \
        or '<span class="todays-read-lead">AgTech & Food Innovation</span>' in cell
    assert html.count('<span class="scan-id">187</span>') == 2, (
        "scan id must be hookable in both the strip subline and .mobile-scan-meta"
    )
    assert html.count('<span class="scan-date">2026-09-10</span>') == 2


def test_the_upgrade_event_carries_the_live_rows():
    """auth.js has the live rows; the page owns the strip. The event is the
    seam between them, so it must carry the rows rather than make the page
    re-query v_recent_scores."""
    assert re.search(
        r'new CustomEvent\("sm:leaderboard-upgraded",\s*\{\s*detail:\s*\{\s*rows:\s*latest',
        AUTH,
    ), "sm:leaderboard-upgraded no longer carries the live rows"


def test_the_page_listens_for_the_upgrade():
    assert "document.addEventListener('sm:leaderboard-upgraded', applyTodaysRead)" in INDEX


def test_the_stale_no_scan_date_claim_is_gone():
    """The comment above markLive() said no scan-date element existed to
    update. That false premise is how this bug survived; leaving it in place
    invites the next reader to trust it."""
    assert "scan_date isn't used anywhere in" not in AUTH


def _extract_function(src, signature):
    """Brace-balanced extraction, same technique as test_dashboard_js.py's
    _apply_horizon_badges_js() -- a naive search for the closing brace would
    stop at the first nested block."""
    start = src.index(signature)
    i = src.index("{", start)
    depth = 0
    while True:
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
        i += 1


_FAKE_DOM = """
function el(props) {
  var e = { textContent: "", hidden: false, dataset: {}, children: [] };
  for (var k in props) { e[k] = props[k]; }
  return e;
}
var lead = el({ textContent: "AgTech & Food Innovation" });
var drifts = ["rising", "falling", "flat"].map(function (d) {
  return el({ dataset: { drift: d }, hidden: d !== "falling" });
});
var cell = el({});
cell.querySelector = function (sel) { return sel === ".todays-read-lead" ? lead : null; };
cell.querySelectorAll = function (sel) { return sel === "[data-drift]" ? drifts : []; };
var ids = [el({ textContent: "187" }), el({ textContent: "187" })];
var dates = [el({ textContent: "2026-09-10" }), el({ textContent: "2026-09-10" })];
global.document = {
  getElementById: function (id) { return id === "cell-todays-read" ? cell : null; },
  querySelectorAll: function (sel) {
    return sel === ".scan-id" ? ids : (sel === ".scan-date" ? dates : []);
  }
};
global.window = { COHORTS: [{ region: "THEME" }] };
window.Rescore = require(%(rescore)r);
var Rescore = window.Rescore;
"""


def _run_apply_todays_read(detail):
    fn = _extract_function(INDEX, "function applyTodaysRead(")
    script = (_FAKE_DOM % {"rescore": str(_RESCORE_JS)}) + fn + """
      applyTodaysRead({ detail: %s });
      console.log(JSON.stringify({
        lead: lead.textContent,
        visible: drifts.filter(function (d) { return !d.hidden; })
                       .map(function (d) { return d.dataset.drift; }),
        ids: ids.map(function (e) { return e.textContent; }),
        dates: dates.map(function (e) { return e.textContent; })
      }));
    """ % _json.dumps(detail)
    res = _subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node failed: {res.stderr}"
    return _json.loads(res.stdout)


def _live_rows(changes_by_theme):
    rows = []
    for i, (theme, change) in enumerate(changes_by_theme, start=1):
        rows.append({"scan_id": 195, "run_at": "2026-09-18T11:05:52.187557+00:00",
                     "region": "THEME", "gics_sector": theme, "rank": i,
                     "change_score": change})
    return rows


@_pytest.mark.skipif(_shutil.which("node") is None, reason="node not available")
def test_upgrade_rewrites_the_cell_from_the_live_board():
    """Behavioural, not source-pinned: runs the page's real handler against
    the exact disagreement from the bug report -- baked AgTech/falling/#187,
    live Shipping-led board with a rising bottom half."""
    rows = _live_rows([("Shipping", 0.0), ("Cybersecurity", 0.0),
                       ("AgTech & Food Innovation", 0.3), ("Biotech", 0.3)])
    out = _run_apply_todays_read({"rows": rows})
    assert out["lead"] == "Shipping"
    assert out["visible"] == ["rising"], "exactly one drift sentence must show"
    assert out["ids"] == ["195", "195"]
    assert out["dates"] == ["2026-09-18", "2026-09-18"]


@_pytest.mark.skipif(_shutil.which("node") is None, reason="node not available")
def test_retired_cohort_rows_cannot_lead():
    """v_recent_scores has no region filter and retired sector rows are still
    in the table (see auth.js's COHORTS comment). The headline must be computed
    from the rows the table actually renders, or a dead sector could lead."""
    rows = _live_rows([("Shipping", 0.0), ("Biotech", 0.0)])
    rows.insert(0, dict(rows[0], region="US", gics_sector="Technology", rank=0.5))
    out = _run_apply_todays_read({"rows": rows})
    assert out["lead"] == "Shipping"


@_pytest.mark.skipif(_shutil.which("node") is None, reason="node not available")
def test_an_upgrade_without_rows_leaves_the_baked_cell_alone():
    """A dispatch with no detail must not blank the headline or write
    'undefined' into the scan line -- leaving the baked cell is the honest
    fallback."""
    out = _run_apply_todays_read(None)
    assert out["lead"] == "AgTech & Food Innovation"
    assert out["visible"] == ["falling"]
    assert out["ids"] == ["187", "187"]
