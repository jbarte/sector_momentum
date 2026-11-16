"""One button system and one type scale (2026-11-16 UI audit).

The audit found five button styles with no primary, nine near-duplicate font
sizes (12, 12.25, 12.6 and 13px side by side), pop-up headings in Inter 700 when
every other heading is the display font at 600, click targets of 15 to 23px,
a "How to read this tab" button in two different places, and tickers and labels
at 3.66:1. These tests keep each fixed. They read the stylesheets and templates.
"""
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

_CSS = _ROOT / "dashboard" / "templates" / "css"
_TPL = _ROOT / "dashboard" / "templates"

# 10 micro labels, 11 small labels, 12 secondary text, 13 tables and small body,
# 14 body, 15 theme names, 16 headlines, 18 headings, 20 display.
_SCALE = {10, 11, 12, 13, 14, 15, 16, 18, 20}


def _css(name: str) -> str:
    return (_CSS / name).read_text()


def _rule(css: str, selector: str) -> str:
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*(?:,[^{}]*)?\{([^}]*)\}", css)
    assert m, f"no rule starting with {selector!r}"
    return m.group(1)


def _lum(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    chans = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _contrast(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


# --- buttons ---------------------------------------------------------------------

def test_there_is_one_primary_button_and_it_is_white_on_solid_green():
    chrome = _css("_chrome.css.j2")
    primary = re.search(r"\.pill-button\.auth-send,\s*\.beginner-deck-nav #beginner-deck-next\s*\{([^}]*)\}", chrome)
    assert primary, "Send link and Next / Got it share one primary rule"
    assert "background: var(--solid)" in primary.group(1)
    assert "color: var(--on-solid)" in primary.group(1)
    hover = re.search(r"\.pill-button\.auth-send:hover,[^{]*\{([^}]*)\}", chrome).group(1)
    assert "var(--solid-hover)" in hover


def test_white_on_the_primary_buttons_clears_aa_in_both_states():
    # --solid / --solid-hover are the green ramp (the same in dark, like --green-600)
    root = _css("_foundation.css.j2")
    assert re.search(r"--solid:\s*var\(--green-600\)", root) and re.search(r"--solid-hover:\s*var\(--green-700\)", root)
    assert re.search(r"--green-600:\s*#5A6F49", root) and re.search(r"--green-700:\s*#455636", root)
    assert re.search(r"--on-solid:\s*#FFFFFF", root)
    assert _contrast("#FFFFFF", "#5A6F49") >= 4.5
    assert _contrast("#FFFFFF", "#455636") >= 4.5


def test_the_sign_in_field_and_its_button_are_one_height():
    chrome = _css("_chrome.css.j2")
    assert "min-height: var(--control-h)" in _rule(chrome, '.auth-form input[type="email"]')
    assert "min-height: var(--control-h)" in _rule(chrome, ".auth-form .pill-button,\n.gate-modal .pill-button,\n.methodology-modal .pill-button")
    assert re.search(r"--control-h:\s*32px", _css("_foundation.css.j2"))


def test_dialog_and_banner_buttons_are_pills_like_the_rest():
    chrome = _css("_chrome.css.j2")
    assert "border-radius: var(--radius-pill)" in _rule(chrome, ".beginner-deck-nav button")
    assert "border-radius: var(--radius-pill)" in _rule(chrome, ".lag-banner-btn")


# --- type --------------------------------------------------------------------------

def test_every_font_size_is_on_the_scale():
    stray = {}
    for path in _CSS.glob("*.j2"):
        for m in re.finditer(r"font-size:\s*([0-9.]+)(px|rem|em)\b", path.read_text()):
            value, unit = float(m.group(1)), m.group(2)
            if unit == "px" and value in _SCALE:
                continue
            if unit in ("rem", "em") and value == 1:      # inherit-size: 1rem / 1em
                continue
            stray.setdefault(f"{value:g}{unit}", []).append(path.name)
    assert not stray, f"font sizes off the 10/11/12/13/14/15/16/18/20 scale: {stray}"


def test_popup_headings_are_the_display_font_at_600():
    chrome = _css("_chrome.css.j2")
    body = _rule(chrome, ".gate-modal h2,\n.methodology-modal h2,\n.guide-modal h2")
    assert "font-family: var(--font-display)" in body and "font-weight: 600" in body
    assert "font-family: var(--font-display)" in _rule(chrome, ".methodology-modal h3")
    assert re.search(r"b,\s*strong\s*\{\s*font-weight:\s*600", _css("_foundation.css.j2")), "bold is 600, not 700"


def test_tickers_and_level_change_labels_are_readable_text():
    """fg5 is decoration only (3.66:1 on the card); these two were text in it."""
    tables = _css("_tables.css.j2")
    assert "color: var(--fg4)" in _rule(tables, ".theme-ticker")
    assert "color: var(--fg4)" in _rule(tables, ".lc-label")
    assert "color: var(--fg5)" not in tables
    for surface in ("#FAF7F0", "#F4EFE4", "#FDFBF6"):
        assert _contrast("#6F674F", surface) >= 4.5, surface


# --- targets and placement -----------------------------------------------------------

def test_small_controls_are_at_least_24px_tall():
    chrome = _css("_chrome.css.j2")
    assert "min-height: 24px" in _rule(chrome, ".footer-link")
    assert "min-height: 24px" in _rule(chrome, "button.strip-eyebrow")
    assert "min-height: 24px" in _rule(_css("_health.css.j2"), ".health-panel summary")
    assert "min-height: 28px" in _rule(chrome, ".pill-button"), "the header Sign in was 23px"


def test_how_to_read_this_tab_is_at_the_top_of_every_tab():
    page = (_TPL / "index.html.j2").read_text()
    for tab in ("leaderboard", "rrg", "drilldown", "movers", "history", "backtest", "correlation"):
        start = page.index(f'id="tab-{tab}"')
        end = page.index("</section>", start)
        panel = page[start:end]
        button = panel.index(f'data-guide="guide_body_{tab}"')
        # before any chart, table or note: only the section tag and a utility row precede it
        head = panel[:button]
        assert "chart-container" not in head and "<table" not in head and "tab-note" not in head, tab
        if tab != "leaderboard":     # the leaderboard has it in its filter row, also top right
            assert 'class="utility-row"' in head, f"{tab}: the button sits in the shared top-right row"


def test_a_hidden_walkthrough_slide_does_not_run_its_animation():
    """Hidden slides are rendered (one dialog height), so without this their
    illustrations finished animating while slide 1 was showing."""
    chrome = _css("_chrome.css.j2")
    assert re.search(r"\.beginner-deck-card\[hidden\] \*\s*\{\s*animation:\s*none\s*!important", chrome)
