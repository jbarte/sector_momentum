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


def _market_context_cell() -> str:
    start = INDEX.index('id="cell-market-context"')
    return INDEX[start:INDEX.index("</section>", start)]


def test_strip_sits_between_the_command_bar_and_the_tab_bar():
    """Order matters: the strip frames the board, so it must precede the tabs."""
    assert INDEX.index('class="summary-strip"') < INDEX.index('class="tabs"')


def test_all_three_cells_exist():
    for cell_id in ("cell-todays-read", "cell-buy-band", "cell-market-context"):
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


def _render_header(active_segment, macro=None, auth=False):
    """_header.html.j2 is shared by index.html.j2 and sentiment.html.j2, and
    since Cell C (Stage 4) is index-only, whether the header's own SPY/VIX
    chips render now depends on active_segment — a raw text scan of the
    .j2 source can't distinguish "renders for sentiment" from "renders for
    both", since both branches' markup is present in the source
    unconditionally. Same minimal-render technique
    test_mobile_scan_meta_survives_missing_scan_date (test_dashboard_js.py)
    uses for this exact template."""
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    env = Environment(loader=FileSystemLoader(str(ROOT / "dashboard" / "templates")))
    register_asset_url(env)
    return env.get_template("_header.html.j2").render(
        active_segment=active_segment, macro=macro, auth=auth)


_MACRO = {"spy_above": True, "spy_distance_pct": 1.0, "vix_band": "Calm", "vix_last": 12.0}


def test_market_context_chips_are_not_duplicated_on_the_leaderboard_page():
    """Stage 4 moves them into Cell C for index.html.j2 (active_segment ==
    "sectors"). Rendering the header for that page must not also carry
    them, or desktop would show SPY/VIX twice — Cell C and the header
    would both display the same numbers at once. No scan_date is passed
    here, so .mobile-scan-meta (which also carries SPY/VIX, on purpose —
    see test_the_mobile_scan_meta_row_keeps_its_own_spy_vix_echo below)
    never renders in this call, meaning any "SPY" found below can only
    have come from the desktop chip this test forbids."""
    assert 'id="context-chips"' not in HEADER
    html = _render_header("sectors", macro=_MACRO)
    assert "mobile-scan-meta" not in html, "unexpected: scan_date was not passed"
    assert "SPY" not in html, \
        "the desktop SPY chip must be gone from the command bar on the leaderboard page"


def test_market_context_chips_are_restored_on_the_sentiment_page():
    """Found in whole-branch review: sentiment.html.j2 shares this header but
    has no summary strip of its own (Stage 4's plan never listed it in Task
    2's file scope). Without a header echo, SPY/VIX/Live existed nowhere on
    that page at all — a real regression, not the "duplicate on desktop"
    case the sibling test above guards against."""
    html = _render_header("sentiment", macro=_MACRO)
    assert "SPY" in html
    assert 'id="market-context-chips"' in html, (
        "markLive() (auth.js) targets this id — it must exist on the "
        "sentiment page's header too, not only inside index.html.j2's Cell C"
    )


def test_market_context_chips_need_macro_or_auth_on_the_sentiment_page():
    """Same guard the pre-Stage-4 header block used, and the one Cell C was
    found missing in this same review: a build with neither macro data nor
    auth configured must render no trigger at all, not a guide button with
    nothing beside it to explain."""
    html = _render_header("sentiment", macro=None, auth=False)
    assert "tab-guide-btn" not in html


def test_the_mobile_scan_meta_row_keeps_its_own_spy_vix_echo():
    """Stage 3's mobile row is a separate element and stays — it is the phone's
    only view of these numbers once Cell C is hidden at that width."""
    assert 'class="mobile-scan-meta"' in HEADER
    assert "macro.spy_distance_pct" in HEADER


def test_market_context_cell_keeps_the_guide_reachable():
    cell = _market_context_cell()
    assert 'data-guide="guide_body_market_context"' in cell
    assert "tab-guide-btn" in cell
    assert 'class="cc-label"' in cell, \
        "the dispatch prefers .cc-label over live numbers as the dialog heading"


def test_market_context_cell_is_guarded_on_macro():
    """A build with no FRED data has macro = None."""
    assert "{% if macro %}" in _market_context_cell()


def test_market_context_cell_has_an_outer_guard_too():
    """Found in whole-branch review: the inner `{% if macro %}` above only
    hides the chips — without an outer guard, the eyebrow button (and its
    "what these mean" guide trigger) still rendered on a build with neither
    macro data nor auth configured, with nothing beside it to explain. The
    old _header.html.j2 block this replaced had exactly this guard
    (`{% if macro or auth %}`) before Stage 4 deleted it.

    Strips Jinja comments before searching — a `{#- ... -#}` comment
    explaining the guard sits directly above it and itself contains the
    words "macro"/"auth" in prose, which a naive substring check over the
    raw text would satisfy even with the actual `{% if %}` tag removed
    (caught live: this test passed against a sabotaged copy on the first
    draft, for exactly that reason)."""
    start = INDEX.index('<div class="strip-cell" id="cell-market-context"')
    preceding = re.sub(r"\{#-.*?-#\}", "", INDEX[:start], flags=re.DOTALL)
    tags = list(re.finditer(r"\{%\s*(if|endif)\s*([^%]*)%\}", preceding))
    assert tags, "expected a `{% if %}` tag before #cell-market-context"
    last = tags[-1]
    assert last.group(1) == "if", (
        "the last Jinja tag before #cell-market-context is `{% endif %}`, "
        "meaning no `{% if %}` block is still open — the cell is unguarded"
    )
    assert "macro" in last.group(2) and "auth" in last.group(2), (
        "expected an `{% if macro or auth %}`-shaped guard, found: " + last.group(0)
    )


def test_market_context_reuses_the_existing_macro_i18n_keys():
    """These already exist as title attributes; Stage 4 promotes them to
    visible words. Reusing them keeps one Swedish string per fact."""
    cell = _market_context_cell()
    assert "macro_chip_spy_above" in cell and "macro_chip_spy_below" in cell
    assert "macro_vix_" in cell


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
    for key in ("strip_eyebrow_read", "strip_eyebrow_band", "strip_eyebrow_market"):
        assert f'data-i18n="{key}"' in INDEX


def test_mobile_hides_what_the_scan_meta_row_already_says():
    """At 375px .mobile-scan-meta (Stage 3) already prints the scan id, date,
    SPY and VIX. Cell C and the strip's subline repeat exactly that, within a
    couple of hundred pixels of it — so both are hidden at this width rather
    than printing the same facts twice on one screen. Found at the browser
    gate; caught nowhere else, since both are correct in isolation."""
    css = (ROOT / "dashboard/templates/css/_responsive.css.j2").read_text()
    mobile = css[css.index("@media (max-width: 600px)"):]
    assert re.search(r"#cell-market-context\s*\{\s*display:\s*none", mobile)
    assert re.search(r"\.strip-subline\s*\{\s*display:\s*none", mobile)
