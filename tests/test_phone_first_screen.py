"""Phone first screen, guest banner, Data Health and the Backtest table
(spec 2026-11-16-phone-first-screen-and-opened-row, part 4a).

Measured at 375x812 as a guest, the first theme row started at 1,115px; it now starts
at about 750px (about 715px signed in or with the banner dismissed), with the 44px
touch-target rules on. These tests pin each part of that; the numbers themselves were
measured in a real build, not here.
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dashboard.health import _badge, build_health_context  # noqa: E402

_TPL = _ROOT / "dashboard" / "templates"
_CSS = _TPL / "css"


def _css(name: str) -> str:
    return (_CSS / name).read_text()


def _phone_block() -> str:
    """The 'Phone first screen' @media (max-width: 600px) block of _responsive.css.j2."""
    css = _css("_responsive.css.j2")
    start = css.index("Phone first screen (spec 2026-11-16")
    start = css.index("@media (max-width: 600px) {", start)
    end = css.index("Touch targets", start)
    return css[start:end]


# --- guest banner -------------------------------------------------------------------

def test_the_guest_banner_has_a_short_wording_and_a_dismiss_control():
    page = (_TPL / "index.html.j2").read_text()
    assert 'class="lag-long"' in page and 'class="lag-short"' in page
    assert 'id="lag-banner-dismiss"' in page and 'aria-label="Dismiss this notice"' in page
    assert 'data-lag-date="{{ lag_banner_date }}"' in page
    block = _phone_block()
    assert ".lag-long { display: none; }" in block and ".lag-short { display: inline; }" in block
    assert "flex-wrap: nowrap" in block, "one line on a phone"


def test_a_dismissed_banner_stays_dismissed_until_the_data_date_changes():
    page = (_TPL / "index.html.j2").read_text()
    assert 'var KEY = "sm.lagBannerDismissed";' in page
    assert 'localStorage.getItem(KEY) === banner.getAttribute("data-lag-date")' in page
    assert 'localStorage.setItem(KEY, banner.getAttribute("data-lag-date"))' in page
    auth = (_ROOT / "dashboard" / "assets" / "auth.js").read_text()
    assert "window.SMLagBanner.dismissed()" in auth, (
        "resolving a guest session must not bring a dismissed banner back")


def test_the_scan_line_gives_way_to_a_visible_guest_banner_on_a_phone():
    """The banner names the data date already; the scan line returns when it is dismissed."""
    assert "body:has(.lag-banner:not([hidden])) .command-bar .mobile-scan-meta { display: none; }" in _phone_block()


# --- header, strip and controls on a phone -----------------------------------------------

def test_the_phone_header_wraps_instead_of_overlapping():
    """The Sentiment toggle with its badge is too wide to share a row with the scan date at
    375px: they overlapped. Flex-wrap lets the date drop to its own line."""
    block = _phone_block()
    assert re.search(r"\.command-bar \.mobile-scan-meta \{[^}]*order: 3;[^}]*width: auto;", block)
    assert ".auth-root { position: static; }" in block, "the sign-in form spans the header"
    assert re.search(r"\.auth-form \{[^}]*left: var\(--space-4\);[^}]*right: var\(--space-4\);", block)


def test_the_buy_band_scrolls_on_one_line_without_widening_the_page():
    block = _phone_block()
    assert re.search(r"\.band-pills \{[^}]*flex-wrap: nowrap;[^}]*overflow-x: auto;", block, re.S)
    # a grid track grows to its widest child: without these the page was 504px wide at 375px
    assert ".summary-strip { grid-template-columns: minmax(0, 1fr); }" in block
    assert re.search(r"\.strip-cell \{[^}]*min-width: 0;", block)


def test_a_phone_keeps_one_filters_control_beside_the_guide_link():
    block = _phone_block()
    assert ".control-chips .control-chip { display: none; }" in block
    assert ".horizon-row > .tab-guide-btn { margin-left: auto; }" in block
    # the chips that are hidden are the same filters as the ones inside More filters
    page = (_TPL / "index.html.j2").read_text()
    for value in ("top5", "rising", "composite_pos"):
        assert page.count(f'data-filter-value="{value}"') == 2, f"{value}: quick chip and full-set chip"


def test_the_track_record_label_keeps_a_44px_target_but_not_44px_of_layout():
    assert "#cell-track-record .strip-eyebrow { margin: -14px 0; }" in _phone_block()
    assert "min-height: 44px" in _css("_responsive.css.j2")


# --- Data Health ---------------------------------------------------------------------------

def _health(finbert_scored=16, finbert_total=18, prices_failed=0, produced=18, expected=18):
    return {
        "sectors_produced": produced, "sectors_expected": expected,
        "prices_failed": prices_failed, "asof_dropped_count": 0,
        "finbert_scored": finbert_scored, "finbert_total": finbert_total,
        "run_at": "2026-11-09T15:58:52+00:00", "duration_s": 511.0,
    }


def test_partial_sentiment_coverage_does_not_open_data_health_by_itself():
    """16 of 18 scored is the usual state, sentiment is experimental and excluded from the
    ranking, and it kept the panel open (with a warning sign) every day."""
    ctx = build_health_context(_health(finbert_scored=16, finbert_total=18))
    assert _badge("finbert", 16, 18) == "amber"
    assert ctx["health_badges"]["finbert"] == "amber", "still shown amber inside the panel"
    assert ctx["health_any_warn"] is False


def test_a_real_problem_still_opens_data_health():
    assert build_health_context(_health(finbert_scored=4, finbert_total=18))["health_any_warn"] is True, "sentiment outage"
    assert build_health_context(_health(prices_failed=2))["health_any_warn"] is True, "a price fetch failed"
    assert build_health_context(_health(produced=16, expected=18))["health_any_warn"] is True, "themes missing"


def test_data_health_is_one_line_and_aligned_to_the_page_edge():
    footer = (_TPL / "_footer.html.j2").read_text()
    assert "Data Health &mdash; last scan {{ health.run_at[:10] }} {{ health.run_at[11:16] }} UTC" in footer
    assert "{{ health.sectors_produced }}/{{ health.sectors_expected }} themes" in footer
    assert 'title="The scan took' in footer, "the run time moved to a tooltip"
    health_css = _css("_health.css.j2")
    assert re.search(r"\.health-panel \{ margin: var\(--space-4\) var\(--space-7\) 0;", health_css)


# --- Backtest -----------------------------------------------------------------------------------

def test_backtest_cells_are_one_line_and_the_table_fits_1024px():
    tables = _css("_tables.css.j2")
    assert ".bt-row td { white-space: nowrap; }" in tables
    assert "#tab-backtest thead th { white-space: normal;" in tables
    assert re.search(r"#tab-backtest thead th, \.bt-row td \{ padding-left: var\(--space-2\); padding-right: var\(--space-2\); \}", tables)


def test_section_headings_share_one_class_instead_of_three_inline_styles():
    css = _css("_sentiment.css.j2")
    rule = re.search(r"\.section-heading \{([^}]*)\}", css).group(1)
    for needle in ("font-family: var(--font-display)", "font-size: 15px", "font-weight: 600",
                   "letter-spacing: var(--ls-heading)"):
        assert needle in rule, needle
    index = (_TPL / "index.html.j2").read_text()
    validation = (_TPL / "_validation.html.j2").read_text()
    assert index.count('class="section-heading"') == 1 and validation.count('class="section-heading"') == 2
    assert "font-family:var(--font-display);font-size:15px" not in index + validation
