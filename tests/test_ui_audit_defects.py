"""Defects from the 2026-11-16 UI audit that are fixed in code rather than taste.

Each test names the thing a reader saw. The CSS and template checks read the
sources (as test_a11y_landmarks.py and test_typography_floor.py do); the chart
checks build real figures.
"""
import json
import re
import sys
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dashboard.figures import (  # noqa: E402
    _base_layout, _build_drilldown_data, _build_history_figure, _build_movers_figure,
)

_CSS = _ROOT / "dashboard" / "templates" / "css"
_TPL = _ROOT / "dashboard" / "templates"


def _css(name: str) -> str:
    return (_CSS / name).read_text()


def _rule(css: str, selector: str) -> str:
    """The `{ ... }` body of the rule that STARTS with `selector` (so `.tabs` is not
    `.card > .tabs`, and `.x-card` is not `.x-cards`)."""
    m = re.search(r"(?:^|\n)" + re.escape(selector) + r"\s*(?:,[^{}]*)?\{([^}]*)\}", css)
    assert m, f"no rule starting with {selector!r}"
    return m.group(1)


# --- controls --------------------------------------------------------------

def test_every_control_inherits_the_page_font():
    """A bare <button> is 13.3px Arial: the walkthrough's Back/Next were."""
    assert re.search(r"button,\s*input,\s*select,\s*textarea\s*\{\s*font-family:\s*inherit;",
                     _css("_foundation.css.j2"))


def test_every_popup_close_button_is_one_32px_target():
    chrome = _css("_chrome.css.j2")
    body = _rule(chrome, ".methodology-close,\n.guide-close")
    assert "width: 32px" in body and "height: 32px" in body
    assert "font-family: inherit" in body
    # .guide-close no longer carries its own, smaller copy
    assert not re.search(r"\.guide-close\s*\{[^}]*padding:\s*4px 8px", _css("_guides.css.j2"))


def test_plain_links_in_table_cells_are_not_browser_blue():
    tables = _css("_tables.css.j2")
    assert ":where(tbody td > a[href]:not([class]))" in tables
    body = _rule(tables, ":where(tbody td > a[href]:not([class]))")
    assert "var(--brand-strong)" in body


# --- the walkthrough --------------------------------------------------------

def test_the_walkthrough_keeps_one_height_on_every_slide():
    """Slides were 381, 423, 381, 444px tall: the buttons moved under the pointer."""
    deck = (_TPL / "_beginner_deck.html.j2").read_text()
    assert deck.count('<div class="beginner-deck-cards">') == 1
    inside = deck.split('<div class="beginner-deck-cards">')[1].split('id="beginner-deck-dots"')[0]
    assert inside.count('class="beginner-deck-card"') == 4, "all four cards sit inside the wrapper"
    chrome = _css("_chrome.css.j2")
    assert "display: grid" in _rule(chrome, ".beginner-deck-cards")
    assert "grid-area: 1 / 1" in _rule(chrome, ".beginner-deck-card")
    hidden = _rule(chrome, ".beginner-deck-card[hidden]")
    assert "visibility: hidden" in hidden and "display: block" in hidden


# --- layout -----------------------------------------------------------------

def test_the_guest_banner_has_the_page_side_margin():
    assert "var(--space-7)" in _rule(_css("_chrome.css.j2"), ".lag-banner")


def test_opening_a_row_does_not_move_the_columns():
    body = _rule(_css("_tables.css.j2"), ".breakdown-inner")
    assert "width: 0" in body and "min-width: 100%" in body


def test_the_to_stop_column_is_hidden_until_a_row_has_a_stop():
    tables = _css("_tables.css.j2")
    assert re.search(r"#leaderboard-table:not\(:has\(\.stop-cell > \*\)\)\s*:is\(\.col-stop, \.stop-cell\)\s*\{\s*display:\s*none",
                     tables)


def test_the_tab_bar_shows_no_scrollbar():
    chrome = _css("_chrome.css.j2")
    assert "scrollbar-width: none" in _rule(chrome, ".tabs")
    assert ".tabs::-webkit-scrollbar { display: none; }" in chrome


def test_wrapped_horizon_stats_start_flush_left_on_a_phone():
    resp = _css("_responsive.css.j2")
    assert re.search(r"\.hz-stat \+ \.hz-stat\s*\{[^}]*border-left:\s*none", resp)


def test_an_opened_card_breakdown_drops_only_the_signal_bars():
    """Part 4b: the UCITS fund is a stacked card now, so the ISIN fits and is no longer
    dropped on a phone (it was hidden by #333 to make the old table fit)."""
    resp = _css("_responsive.css.j2")
    assert ".card-breakdown .sig-bar { display: none; }" in resp
    assert ".etf-isin" not in resp.split("Phone first screen")[0].split("The opened breakdown inside a card")[1].split("A chevron at the end")[0]


# --- chart labels and states --------------------------------------------------

def _history(ranks_by_scan: dict[int, dict[str, int]]) -> pd.DataFrame:
    rows = []
    for scan_id, ranks in ranks_by_scan.items():
        for theme, rank in ranks.items():
            rows.append({
                "scan_id": scan_id, "run_at": f"2026-11-0{scan_id}T12:00:00+00:00",
                "region": "THEME", "gics_sector": theme, "rank": rank,
                "composite": 1.0 - rank * 0.1, "level_score": 0.4,
                "change_score": 0.3, "data_score": 0.6,
            })
    return pd.DataFrame(rows)


def test_chart_labels_do_not_carry_the_internal_region_suffix():
    hist = _history({1: {"Space": 1, "Biotech": 2}, 2: {"Space": 2, "Biotech": 1}})
    movers = json.loads(_build_movers_figure(hist))
    assert sorted(movers["data"][0]["y"]) == ["Biotech", "Space"]
    history = json.loads(_build_history_figure(hist))
    assert sorted(t["name"] for t in history["data"]) == ["Biotech", "Space"]
    drill, keys, _ = _build_drilldown_data(hist)
    title = json.loads(drill["THEME|Space"])["layout"]["title"]["text"] if isinstance(drill["THEME|Space"], str) \
        else drill["THEME|Space"]["layout"]["title"]["text"]
    assert title == "Space — score components"
    assert "(THEME)" not in json.dumps([movers, history, title])


def test_movers_says_so_when_nobody_changed_rank():
    """It drew eighteen empty rows on a -1..1 axis, which reads as broken."""
    hist = _history({1: {"Space": 1, "Biotech": 2}, 2: {"Space": 1, "Biotech": 2}})
    fig = json.loads(_build_movers_figure(hist))
    assert not fig["data"], "no bars to draw"
    note = fig["layout"]["annotations"][0]["text"]
    assert note == "No rank changes since the previous scan."
    assert fig["layout"]["xaxis"]["visible"] is False


def test_movers_does_not_claim_nobody_moved_when_there_is_nothing_to_compare():
    """Two scans with no theme in common merge to nothing; .all() on that is True."""
    hist = _history({1: {"Space": 1}, 2: {"Biotech": 1}})
    fig = json.loads(_build_movers_figure(hist))
    assert not fig["layout"].get("annotations"), "no message about rank changes"


def test_charts_grow_their_margin_for_long_labels_and_axis_titles():
    layout = _base_layout()
    assert layout["xaxis"]["automargin"] is True and layout["yaxis"]["automargin"] is True
    assert layout["margin"]["b"] >= 64, "room under an axis title, not flush with the edge"
    # a caller's own axis settings still merge over it
    merged = _base_layout(xaxis=dict(title="RS-Ratio"))
    assert merged["xaxis"]["title"] == "RS-Ratio" and merged["xaxis"]["automargin"] is True


# --- scripts ------------------------------------------------------------------

def test_the_drilldown_menu_shows_the_theme_not_its_internal_key():
    page = (_TPL / "index.html.j2").read_text()
    assert page.count("function _themeOf(key)") == 1
    assert "opt.textContent = _themeOf(k);" in page
    assert "'No data for ' + _themeOf(key)" in page
    assert "opt.value = k;" in page, "the value stays REGION|theme: the figure lookup keys on it"
    assert ".innerHTML =\n      '<p style" not in page, "the theme name is set as text, not parsed as HTML"


def test_a_modal_returns_focus_even_where_a_click_does_not_focus_a_button():
    """Safari leaves document.activeElement on <body> after clicking a button."""
    modal = (_TPL / "_modal.js.j2").read_text()
    assert "var lastPressed = null, lastPressedAt = 0;" in modal
    assert "lastFocus = lastPressed;" in modal
    assert "Date.now() - lastPressedAt < 1000" in modal, "a stale press must not take focus"
    assert "!hit.closest('[role=\"dialog\"]')" in modal, "a press inside a dialog is not a trigger"
