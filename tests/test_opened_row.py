"""The opened row (spec 2026-11-16-phone-first-screen-and-opened-row, part 4b).

It was a flat monospace block with a drawn score tree (└─ ├─), an ASCII table for the
UCITS fund (which lost its ISIN on a phone to fit) and nothing on the closed row to say
it opens. Now: the composite as one line, Level and Change as counted sections, the fund
as a card, and a chevron on each row. The markup checks build real HTML through
`_build_breakdown_html`; the styling checks read the stylesheets.
"""
import html as _htmllib
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dashboard.breakdown import _build_breakdown_html, _build_instruments_html  # noqa: E402

_CSS = _ROOT / "dashboard" / "templates" / "css"

_THEMES = {
    "themes": {"Insurance": {"ticker": "IAK"}},
    "benchmark": "ACWI",
    "ucits": {"Insurance": [
        {"ticker": "SC0Y", "name": "Invesco European Insurance Sector UCITS ETF Acc",
         "ter": "0.20%", "isin": "IE00B5MTXJ97", "url": "https://example.com/sc0y",
         "match": "partial"},
    ]},
}
_SCORE = {"composite": 1.06, "data_score": 1.06, "level_score": 1.347, "change_score": 0.772}
_SIGNALS = [
    {"signal_name": "rs_ratio", "raw_value": 104.5, "z_value": 1.49},
    {"signal_name": "return_3m", "raw_value": 0.018, "z_value": 1.15},
    {"signal_name": "rs_momentum", "raw_value": 100.1, "z_value": -0.10},
]


def _weights(level=0.5, change=0.5, data=1.0, level_sigs=("rs_ratio", "return_3m"),
             change_sigs=("rs_momentum",)):
    return {"pillars": {"data": data}, "data_pillar": {"level": level, "change": change},
            "level_signals": {n: 1 for n in level_sigs}, "change_signals": {n: 1 for n in change_sigs}}


def _html(weights=None):
    return _build_breakdown_html("THEME|Insurance", _SCORE, _SIGNALS, {}, weights or _weights(),
                                 sector_etfs={}, themes_cfg=_THEMES)


def _text(html: str) -> str:
    return re.sub(r"\s+", " ", _htmllib.unescape(re.sub(r"<[^>]+>", "", html))).strip()


# --- the composite as one line --------------------------------------------------------

def test_the_composite_is_one_line_with_the_real_weights():
    text = _text(_html())
    assert "Composite 1.060 = ½ Level 1.347 + ½ Change 0.772" in text
    assert "ETF IAK · benchmark ACWI" in text


def test_the_drawn_tree_is_gone():
    html = _html()
    for gone in ("└─", "├─", "score-tree", "st-row", "bd-footer"):
        assert gone not in html, gone


def test_weights_that_are_not_half_are_shown_as_percentages():
    text = _text(_html(_weights(level=0.7, change=0.3)))
    assert "70% Level 1.347 + 30% Change 0.772" in text


def test_a_data_pillar_below_100_percent_is_not_silently_dropped():
    text = _text(_html(_weights(data=0.8)))
    assert "80% × (½ Level 1.347 + ½ Change 0.772)" in text


def test_each_term_of_the_line_stays_together_on_a_narrow_screen():
    html = _html()
    assert html.count('class="bd-term"') == 2


# --- Level and Change sections ---------------------------------------------------------

def test_the_sections_count_their_signals_from_the_data_not_a_constant():
    text = _text(_html(_weights(level_sigs=("rs_ratio", "return_3m"), change_sigs=("rs_momentum",))))
    assert "Level · 2 signals" in text
    assert re.search(r"Change · 1 signal(?!s)", text), "singular for one, not '1 signals'"


def test_the_signal_rows_keep_their_tooltips_and_values():
    html = _html()
    assert 'class="sig-tip"' in html and "104.5" in html and "+1.49" in html


# --- the UCITS fund as a card -------------------------------------------------------------

def test_the_ucits_fund_is_a_card_with_its_isin_not_a_table():
    html = _build_instruments_html("THEME|Insurance", {}, _THEMES)
    assert '<ul class="etf-list">' in html and html.count('<li class="etf-card">') == 1
    assert "<table" not in html
    text = _text(html)
    assert "SC0Y Invesco European Insurance Sector UCITS ETF Acc" in text
    assert "TER 0.20%" in text and "IE00B5MTXJ97" in text, "the ISIN is on the card"
    assert "Partial match" in text, "sentence case, not 'Partial Match'"


def test_the_fund_link_has_an_accessible_name_and_a_safe_target():
    html = _build_instruments_html("THEME|Insurance", {}, _THEMES)
    assert 'aria-label="Open Invesco European Insurance Sector UCITS ETF Acc (opens in a new tab)"' in html
    assert 'target="_blank" rel="noopener"' in html and "Open &#8599;" in html


def test_an_unsafe_fund_url_is_dropped():
    cfg = {"ucits": {"Insurance": [{"ticker": "X", "name": "x", "ter": "0.1%", "isin": "I",
                                    "url": "javascript:alert(1)", "match": "exact"}]}}
    assert "<a " not in _build_instruments_html("THEME|Insurance", {}, cfg)


def test_a_fund_name_is_escaped():
    cfg = {"ucits": {"Insurance": [{"ticker": "X", "name": "<b>bold</b>", "ter": "0.1%",
                                    "isin": "I", "url": "", "match": "exact"}]}}
    html = _build_instruments_html("THEME|Insurance", {}, cfg)
    assert "<b>bold</b>" not in html and "&lt;b&gt;bold&lt;/b&gt;" in html


# --- layout and the chevron ------------------------------------------------------------------

def _tables() -> str:
    return (_CSS / "_tables.css.j2").read_text()


def test_level_and_change_sit_side_by_side_and_the_panel_is_an_inset_card():
    tables = _tables()
    grid = re.search(r"\.breakdown-grid \{([^}]*)\}", tables).group(1)
    assert "grid-template-columns: 1fr 1fr" in grid
    inner = re.search(r"\.breakdown-inner \{([^}]*)\}", tables).group(1)
    assert "var(--bg-inset)" in inner and "var(--radius-md)" in inner
    assert "width: 0" in inner and "min-width: 100%" in inner, "keeps the columns above from moving"


def test_a_chevron_says_the_row_opens_and_turns_over_when_it_does():
    tables = _tables()
    assert '.leaderboard-row[aria-expanded] .theme-cell::after' in tables
    assert '.leaderboard-row[aria-expanded="true"] .theme-cell::after { transform: translateY(-30%) rotate(-135deg); }' in tables
    # only a row that can open has one: a past scan's rows have no breakdown and no aria-expanded
    assert not re.search(r"\.leaderboard-row \.theme-cell(::after)? \{", tables)


def test_a_phone_card_has_the_same_chevron_and_a_stacked_panel():
    resp = (_CSS / "_responsive.css.j2").read_text()
    assert ".leaderboard-card:has(.card-expand-hit) .card-line1::after" in resp
    assert ".leaderboard-card.open .card-line1::after { transform: translateY(3px) rotate(-135deg); }" in resp
    assert ".card-breakdown .sig-bar { display: none; }" in resp
    assert re.search(r"\.breakdown-grid \{ grid-template-columns: 1fr; \}", resp), "one column on a phone"


def test_odd_weights_are_never_rounded_to_look_exact():
    assert "33.3% Level 1.347 + 66.7% Change 0.772" in _text(_html(_weights(level=1 / 3, change=2 / 3)))
    assert "99.5% × (½ Level" in _text(_html(_weights(data=0.995)))


def test_a_missing_signal_is_not_counted():
    """A name with no metadata draws no row, so it is not in the count either."""
    text = _text(_html(_weights(level_sigs=("rs_ratio", "no_such_signal"), change_sigs=("rs_momentum",))))
    assert re.search(r"Level · 1 signal(?!s)", text)


def test_the_match_class_and_label_are_normalised():
    cfg = {"ucits": {"Insurance": [{"ticker": "X", "name": "x", "ter": "0.1%", "isin": "I",
                                    "url": "", "match": "PARTIAL"}]}}
    html = _build_instruments_html("THEME|Insurance", {}, cfg)
    assert 'class="etf-match etf-match-partial"' in html and "Partial match" in _text(html)
