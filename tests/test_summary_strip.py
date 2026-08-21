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


def test_market_context_chips_are_no_longer_in_the_header():
    """Stage 4 moves them into Cell C. Leaving the header copy behind would
    render SPY/VIX twice on desktop."""
    assert 'id="context-chips"' not in HEADER
    before_mobile_row = HEADER.split("mobile-scan-meta")[0]
    assert "macro.spy_distance_pct" not in before_mobile_row, \
        "the desktop SPY chip must be gone from the command bar"


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
