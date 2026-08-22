"""Sub-12px typography floor — the always-visible-chrome remainder of the
2026-08-09 design review audit's "474 elements render under 12px" finding
(BACKLOG.md, "Design review findings (2026-08-09 audit) — P1/P2 remainder").

Static regex checks over the CSS source, not real browser rendering — this
codebase's established convention (see test_market_context_chips.py,
test_a11y_landmarks.py). Each selector's resolved px value uses the SAME
reference context measured live in a browser during the design spec's
investigation (sector_momentum-notes/specs/2026-08-15-sub-12px-typography-design.md):
root/body font-size is a fixed 14px (_foundation.css.j2, no responsive
override); .traj-badge's `em` is relative to its parent table cell, measured
at exactly 12px; .chevron and the sort-direction arrows are `em` relative to
their own `thead th`/table-cell ancestor.

Several selectors are DELIBERATELY excluded from the floor: icon glyphs read
by shape rather than parsed as text (the thead sort-direction arrows,
.cc-info's info-circle), and quiet secondary labels that must not compete
with what they qualify (.alpha-badge — its own code comment: "Deliberately
the quietest thing in the command bar" — plus .lc-label, .theme-ticker and,
since the 2026-08-19 leaderboard restructure, `thead th`). Each is pinned
below with its own assertion, not silently skipped, so a future change to any
of them is a deliberate edit to this test, not a silent gap.
"""
import re
from pathlib import Path

_CSS_DIR = Path(__file__).parent.parent / "dashboard" / "templates" / "css"
_ROOT_PX = 14  # html, body { font-size: 14px } — _foundation.css.j2


def _css(name: str) -> str:
    return (_CSS_DIR / name).read_text()


def _rule_font_size(css: str, selector: str) -> str:
    """Extract the literal `font-size: X;` value from `selector { ... }`.
    Requires `{` (whitespace-tolerant) immediately after the selector text,
    so `.chip` cannot accidentally match `.chip-up { ... }` and `.health-panel`
    cannot match `.health-panel summary { ... }` — deliberately dumb regex,
    not a CSS parser, matching this codebase's existing test style."""
    pattern = re.escape(selector) + r"\s*\{[^}]*font-size:\s*([0-9.]+(?:px|rem|em));"
    m = re.search(pattern, css)
    assert m, f"could not find `font-size` in `{selector} {{ ... }}`"
    return m.group(1)


def _resolved_px(value: str, context_px: float = _ROOT_PX) -> float:
    if value.endswith("px"):
        return float(value[:-2])
    if value.endswith("rem"):
        return float(value[:-3]) * _ROOT_PX
    if value.endswith("em"):
        return float(value[:-2]) * context_px
    raise ValueError(f"unhandled unit: {value!r}")


# ---------------------------------------------------------------------------
# In scope: always-visible chrome, floor = 12px
# ---------------------------------------------------------------------------


def test_summary_strip_subline_meets_floor():
    """The strip's scan-meta line (scan id + date) is read as text, not
    skimmed as a qualifier, so it sits on the floor rather than below it."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".strip-subline")) >= 12


def test_summary_strip_band_pill_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".band-pill")) >= 12


def test_summary_strip_band_note_meets_floor():
    """The empty-band note is the cell's only content when it shows, so it is
    primary text at that moment, not a qualifier."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".band-note")) >= 12


def test_rank_delta_arrows_meet_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".arrow.up")) >= 12
    assert _resolved_px(_rule_font_size(css, ".arrow.down")) >= 12


def test_trend_badge_meets_floor():
    css = _css("_tables.css.j2")
    # em relative to its parent table cell, measured live at 12px — see spec.
    assert _resolved_px(_rule_font_size(css, ".traj-badge"), context_px=12) >= 12


def test_filter_bar_meets_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".filter-chip")) >= 12
    assert _resolved_px(_rule_font_size(css, ".filter-clear")) >= 12
    assert _resolved_px(_rule_font_size(css, ".filter-count")) >= 12


def test_guide_modal_subsection_heading_meets_floor():
    css = _css("_guides.css.j2")
    assert _resolved_px(_rule_font_size(css, ".tab-guide-body h3")) >= 12


def test_lang_toggle_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".lang-toggle")) >= 12


def test_market_context_chip_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".chip")) >= 12


def test_site_footer_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".site-footer")) >= 12


def test_gate_modal_continue_link_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".gate-modal-continue")) >= 12


def test_lag_banner_button_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".lag-banner-btn")) >= 12


def test_health_panel_meets_floor():
    css = _css("_health.css.j2")
    assert _resolved_px(_rule_font_size(css, ".health-panel")) >= 12


def test_tab_note_meets_floor():
    css = _css("_sentiment.css.j2")
    assert _resolved_px(_rule_font_size(css, ".tab-note")) >= 12


def test_cohort_select_label_meets_floor():
    css = _css("_charts.css.j2")
    assert _resolved_px(_rule_font_size(css, ".cohort-select-row label")) >= 12


def test_drilldown_controls_label_meets_floor():
    css = _css("_charts.css.j2")
    assert _resolved_px(_rule_font_size(css, ".drilldown-controls label")) >= 12


def test_scan_meta_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".scan-meta")) >= 12


def test_auth_form_email_input_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".auth-form input[type=\"email\"]")) >= 12


def test_auth_status_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".auth-status")) >= 12


def test_lag_banner_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".lag-banner")) >= 12


def test_alert_prefs_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alert-prefs")) >= 12


def test_alert_prefs_topic_code_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alert-prefs-topic code")) >= 12


def test_alert_prefs_warn_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alert-prefs-warn")) >= 12


def test_alert_prefs_status_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alert-prefs-status")) >= 12


def test_alerts_hz_note_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alerts-hz-note")) >= 12


def test_alerts_hz_warn_meets_floor():
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alerts-hz-warn")) >= 12


def test_methodology_body_code_meets_floor():
    css = _css("_chrome.css.j2")
    # em relative to .methodology-body p/li ancestor, measured live at 12.6px
    assert _resolved_px(_rule_font_size(css, ".methodology-body code"), context_px=12.6) >= 12


def test_horizon_note_meets_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".horizon-note")) >= 12


def test_review_status_meets_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".review-status")) >= 12


def test_band_legend_rule_is_gone_with_its_element():
    """The #band-legend swatch legend was removed by the Stage 2 leaderboard
    redesign (2026-08-20) — the band-cut rows self-label now, making a
    separate legend redundant. Pins the DELETION rather than the old floor
    assertion, matching test_chevron_rule_is_gone_with_its_element's
    precedent for the same situation; a reintroduced `.band-legend` should be
    a deliberate edit to this test."""
    css = _css("_tables.css.j2")
    assert not re.search(r"\.band-legend\s*[,{\[]", css)


def test_setup_badge_meets_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".setup-badge")) >= 12


def test_unbuyable_badge_meets_floor():
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".unbuyable-badge")) >= 12


def test_showing_badge_meets_floor():
    css = _css("_tables.css.j2")
    # em relative to table cell ancestor, measured live at 13px
    assert _resolved_px(_rule_font_size(css, ".showing-badge"), context_px=13) >= 12


# ---------------------------------------------------------------------------
# Deliberately exempt — pinned, not ignored
# ---------------------------------------------------------------------------

def test_chevron_rule_is_gone_with_its_element():
    """The row-expand chevron was removed by the 2026-08-19 leaderboard
    restructure — the row itself is the disclosure control now. Its CSS rule
    went with it, so this pins the DELETION rather than the old exemption; a
    reintroduced `.chevron` should be a deliberate edit to this test."""
    css = _css("_tables.css.j2")
    assert not re.search(r"\.chevron\s*[,{]", css)


def test_table_header_is_deliberately_exempt_from_the_floor():
    """Column headers dropped to 11px in the 2026-08-19 leaderboard restructure
    (sector_momentum-notes/specs/2026-08-18-leaderboard-redesign-design.md).
    They are uppercase, wide-tracked labels for columns whose CONTENT carries
    the reading weight — the redesign deliberately quiets the header so the row
    doesn't compete with it. Same category as .alpha-badge: below the floor on
    purpose, pinned here rather than silently skipped.

    NOTE: this reverses the 2026-08-15 sub-12px audit, which raised `thead th`
    from 10px to 12px. Flagged for review with the restructure."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, "thead th")) < 12


def test_theme_ticker_is_deliberately_exempt_from_the_floor():
    """The US ETF ticker beside a theme name — a quiet secondary identifier
    that must not compete with the 15px theme name it qualifies. Same
    reasoning as .alpha-badge."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".theme-ticker")) < 12


def test_sort_direction_arrows_are_deliberately_exempt_from_the_floor():
    """▲/▼ appended after a column header's own text via ::after — a
    direction glyph, not a second piece of reading text. em is relative to
    `thead th`'s OWN font-size, 11px since the 2026-08-19 restructure."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, "thead th.sort-asc::after"), context_px=11) < 12
    assert _resolved_px(_rule_font_size(css, "thead th.sort-desc::after"), context_px=11) < 12


def test_market_context_info_glyph_is_deliberately_exempt_from_the_floor():
    """The ⓘ icon on the market-context chips — same category as .chevron."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".context-chips .cc-info")) < 12


def test_alpha_badge_is_deliberately_exempt_from_the_floor():
    """Existing code comment: "Deliberately the quietest thing in the
    command bar — it qualifies a nav item, it should not compete with it."
    A 12px floor would defeat that documented intent."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".alpha-badge")) < 12


def test_level_change_label_is_deliberately_exempt_from_the_floor():
    """Row headers for the Level/Change merged cell — tiny caps label, same
    10px precedent as .sig-title elsewhere in the table. Deliberately small
    to fit the compact cell layout."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".lc-label")) < 12


def test_band_cut_eyebrow_is_deliberately_exempt_from_the_floor():
    """BUY BAND ENDS / SELL LINE — an uppercase, wide-tracked eyebrow label,
    same treatment as the table header and other quiet secondary labels this
    redesign introduced. Below the floor on purpose, pinned here rather than
    silently skipped. Design spec:
    sector_momentum-notes/specs/2026-08-18-leaderboard-redesign-design.md."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".band-cut-row .bcr-eyebrow")) < 12


def test_band_cut_note_is_deliberately_exempt_from_the_floor():
    """The trailing explanatory note ('below this line: not a new buy') —
    quiet supporting text next to the eyebrow it belongs to."""
    css = _css("_tables.css.j2")
    assert _resolved_px(_rule_font_size(css, ".band-cut-row .bcr-note")) < 12


def test_summary_strip_eyebrow_is_deliberately_exempt_from_the_floor():
    """The strip's three eyebrow labels ("TODAY'S READ", "IN THE BUY BAND",
    "MARKET CONTEXT") are uppercase tracked-out category labels that name the
    cell they sit above — the same "quiet secondary label that must not
    compete with what it qualifies" case as .alpha-badge. The design spec
    (2026-08-18 leaderboard redesign, Screen 1 item 2) specifies 11px with
    .10em tracking; uppercase at that tracking reads larger than its nominal
    size. Pinned here rather than silently skipped."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".strip-eyebrow")) == 11


def test_market_context_trailing_words_are_deliberately_exempt():
    """"above 200-DMA" / "calm" qualify the chip number beside them — the
    number is the content, the word is its gloss. Spec Screen 1 item 2 gives
    these as 11px against the chip's 12px, deliberately subordinate."""
    css = _css("_chrome.css.j2")
    assert _resolved_px(_rule_font_size(css, ".market-chips .chip-word")) == 11
