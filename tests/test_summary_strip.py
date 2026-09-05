"""Summary strip — the three cells between the command bar and the tab bar."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

ROOT = Path(__file__).parent.parent
INDEX = (ROOT / "dashboard/templates/index.html.j2").read_text()
HEADER = (ROOT / "dashboard/templates/_header.html.j2").read_text()
AUTH = (ROOT / "dashboard/assets/auth.js").read_text()
SV = (ROOT / "dashboard/templates/i18n/_core.js.j2").read_text()


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


def test_todays_read_renders_the_theme_then_a_translatable_phrase():
    """Theme name first: it is never translated, and theme-first reads
    correctly in both EN and SV."""
    assert "{{ todays_read.lead_theme }}" in INDEX
    assert 'data-i18n="read_leads"' in INDEX


def test_all_three_drift_phrases_are_present_and_keyed():
    for drift in ("rising", "falling", "flat"):
        assert f'data-i18n="read_bottom_{drift}"' in INDEX, drift


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
    """index.html.j2's Cell C eyebrow hardcodes "vs ACWI" (EN) / "mot ACWI"
    (SV) as the track-record chips' label, while dashboard/figures.py's
    `_window_excess` computes the excess return generically against whatever
    `track["benchmark"]` the equity curve carries -- correct today only
    because config/themes.yaml's `benchmark:` happens to be ACWI. Nothing
    else pins that agreement, so a future change to the config would
    silently make the label lie about what the chips actually measure.
    Pinned here rather than threaded through the template: `benchmark` is a
    single global config value (themes.yaml's own comment), so a config
    change is the only way this could ever drift, and this test forces
    whoever makes that change to also update the hardcoded label (both
    languages) in the same commit."""
    import yaml
    themes_cfg = yaml.safe_load((ROOT / "config/themes.yaml").read_text())
    benchmark = (themes_cfg or {}).get("benchmark") or "ACWI"
    assert benchmark == "ACWI", (
        f"config/themes.yaml's benchmark is now {benchmark!r}, but "
        f"index.html.j2's Cell C eyebrow and dashboard/templates/i18n/"
        f"_core.js.j2's Swedish translation still hardcode 'vs ACWI' / "
        f"'mot ACWI' -- update strip_eyebrow_vs_bench in both places"
    )
    assert "vs ACWI" in INDEX, "the hardcoded EN label this test pins is gone"
    assert 'mot ACWI' in SV, "the hardcoded SV label this test pins is gone"


def test_mark_live_targets_the_new_cell():
    """Left pointing at #context-chips, markLive() silently takes its
    .meta-cluster fallback and the Live chip lands back in the header."""
    assert 'getElementById("context-chips")' not in AUTH
    assert 'getElementById("market-context-chips")' in AUTH


def test_every_new_key_has_a_swedish_entry():
    keys = set(re.findall(r'data-i18n="(read_[a-z_]+|strip_[a-z_]+)"', INDEX))
    assert keys, "no new strip keys found — did the markup land?"
    for key in sorted(keys):
        assert re.search(rf"\b{key}:", SV), f"{key} has no SV entry"


def test_eyebrow_labels_exist_for_all_three_cells():
    for key in ("strip_eyebrow_read", "strip_eyebrow_band", "strip_eyebrow_vs_bench"):
        assert f'data-i18n="{key}"' in INDEX


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
