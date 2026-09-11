"""Render tests for the beginner-deck partial, mirroring tests/test_methodology.py's
pattern (same _jinja_env/_render/_prose helpers, duplicated here rather than
imported -- test_methodology.py doesn't expose them as a shared module, and a
one-file test suite is easier to read standalone)."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"
_CSS_DIR = _TPL_DIR / "css"

_CTX = {"default_horizon_top_n": 4, "trailing_stop_pct": 12}


def _jinja_env():
    from jinja2 import Environment, FileSystemLoader
    from dashboard.build import register_asset_url
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
    register_asset_url(env)
    env.filters["js_json"] = (
        lambda v: v.replace("</", r"<\/") if isinstance(v, str) else v
    )
    return env


def _render(name: str, **ctx) -> str:
    merged = {**_CTX, **ctx}
    return _jinja_env().get_template(name).render(**merged)


def _prose(name: str, **ctx) -> str:
    html = _render(name, **ctx)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


def test_deck_modal_markup_and_a11y():
    html = _render("_beginner_deck.html.j2")
    assert 'id="beginner-deck-modal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'hidden' in html  # closed by default


def test_deck_has_all_four_steps():
    html = _render("_beginner_deck.html.j2")
    for n in (1, 2, 3, 4):
        assert f'data-step="{n}"' in html


def test_deck_nav_controls_exist():
    html = _render("_beginner_deck.html.j2")
    assert 'id="beginner-deck-back"' in html
    assert 'id="beginner-deck-next"' in html
    assert 'id="beginner-deck-dots"' in html


def test_deck_uses_the_shared_modal_helper_not_hand_rolled():
    html = _render("_beginner_deck.html.j2")
    assert "window.SMModal.bind(" in html


def test_deck_has_no_i18n_attributes():
    """Deliberate carve-out matching _methodology.html.j2 -- English only for
    now. A data-i18n attribute here would need a Swedish entry or
    tests/test_i18n_coverage.py fails the whole build."""
    html = _render("_beginner_deck.html.j2")
    assert "data-i18n" not in html


def test_deck_card_one_states_the_buy_band_size_live():
    low = _prose("_beginner_deck.html.j2", default_horizon_top_n=4)
    assert "top 4" in low
    low7 = _prose("_beginner_deck.html.j2", default_horizon_top_n=7)
    assert "top 7" in low7, "card 1 must read the live value, not a hardcoded 4"


def test_deck_card_two_states_it_is_not_a_stop_loss():
    low = _prose("_beginner_deck.html.j2")
    assert "not a stop-loss" in low


def test_deck_card_three_states_the_cadence():
    """Cadence is stated as prose, not interpolated (a natural-language word
    doesn't interpolate cleanly) -- protected instead by
    test_deck_cadence_matches_the_live_default_horizon below."""
    low = _prose("_beginner_deck.html.j2")
    assert "monthly" in low


def test_deck_cadence_matches_the_live_default_horizon():
    """Factual anchor, mirroring test_methodology.py::
    test_methodology_keeps_its_factual_anchors. If the default horizon's
    cadence ever stops being monthly, this must fail and force the prose
    to follow."""
    from src.horizons import default_horizon
    assert default_horizon().rebalance == "M", (
        "default horizon cadence changed -- update card 3's prose in "
        "_beginner_deck.html.j2 from 'monthly' to match, then update this assertion"
    )


def test_deck_card_four_states_the_stop_loss_pct_live():
    low = _prose("_beginner_deck.html.j2", trailing_stop_pct=12)
    assert "12%" in low
    low20 = _prose("_beginner_deck.html.j2", trailing_stop_pct=20)
    assert "20%" in low20, "card 4 must read the live value, not a hardcoded 12"


def test_deck_card_four_states_it_measures_from_peak_since_starring():
    low = _prose("_beginner_deck.html.j2")
    assert "since you starred it" in low
    assert "not what you paid" in low or "not since you bought" in low


def test_footer_has_the_new_here_link():
    html = _render("_footer.html.j2")
    assert 'id="beginner-deck-link"' in html
    assert "New here?" in html


def test_footer_link_sits_beside_methodology_link():
    """Same visual treatment as the existing link -- .footer-link class,
    not a bespoke style. Must check the NEW button's own opening tag, not a
    slice between the two ids -- since methodology-link renders first, such
    a slice would actually contain methodology-link's own class attribute
    and pass even if the deck button had no class (or a different one)."""
    html = _render("_footer.html.j2")
    idx_deck = html.index('id="beginner-deck-link"')
    tag_start = html.rindex("<button", 0, idx_deck)
    tag_end = html.index(">", idx_deck)
    deck_tag = html[tag_start:tag_end + 1]
    assert 'class="footer-link"' in deck_tag


def test_index_page_includes_the_deck_partial():
    html = _render("index.html.j2", leaderboard_rows=[], scan_date="2026-01-01",
                    active_scan_id=1, todays_read="", cohort_list=[], horizon_list=[],
                    sentiment_ranking_enabled=False, round_trip_bps=100,
                    horizons_json="[]", horizon_default_json="{}", cohorts_json="{}",
                    unbuyable_json="{}", theme_tickers_json="{}", chart_dark_json="{}",
                    has_any_rows=False, badges_gated=False, asset_versions={},
                    lag_banner_date=None, auth=False)
    assert 'id="beginner-deck-modal"' in html


def test_sentiment_page_includes_the_deck_partial():
    html = _render("sentiment.html.j2", scan_date="2026-01-01", active_scan_id=1,
                    asset_versions={}, round_trip_bps=100, chart_dark_json="{}",
                    sentiment_ranking_enabled=False, lag_days=7,
                    horizons_json="[]", horizon_default_json="{}", auth=False)
    assert 'id="beginner-deck-modal"' in html


def test_deck_script_binds_the_footer_trigger():
    html = _render("_beginner_deck.html.j2")
    assert 'getElementById("beginner-deck-link")' in html


def test_deck_links_to_the_full_methodology():
    """Reverse direction of test_methodology.py's
    test_methodology_links_back_to_the_beginner_deck. Card 4's "Read the
    full Methodology" link must actually open window.SMMethodologyModal, not
    just mutate the URL hash -- so the click handler must be wired to the
    link's id and reference the methodology modal's exposed handle."""
    html = _render("_beginner_deck.html.j2")
    assert 'id="beginner-deck-methodology-link"' in html
    assert 'getElementById("beginner-deck-methodology-link")' in html
    assert "window.SMMethodologyModal" in html


def test_next_button_stays_right_aligned_with_no_back_button():
    """Regression test for a real bug, confirmed live in a browser (not just
    reasoned about): #beginner-deck-back[hidden] used to declare only
    `visibility: hidden`, never `display`. CSS cascades per property, so the
    UA stylesheet's own `[hidden] { display: none }` still won for `display`
    specifically -- Back kept display:none, took no space in the
    .beginner-deck-nav flex row, and Next (the only remaining flex child)
    sat at flex-start on Card 1 instead of flex-end. Pin both declarations
    so a future edit can't drop the display override and reintroduce this."""
    css = (_CSS_DIR / "_chrome.css.j2").read_text()
    m = re.search(r"#beginner-deck-back\[hidden\]\s*\{([^}]*)\}", css)
    assert m, "expected a #beginner-deck-back[hidden] rule in _chrome.css.j2"
    body = m.group(1)
    assert re.search(r"display\s*:\s*(?!none\b)\S+", body), (
        "must override `display` to something other than none -- the bare "
        "UA [hidden] { display: none } rule otherwise still wins that "
        "property and the button takes no space in the flex row"
    )
    assert re.search(r"visibility\s*:\s*hidden\b", body), (
        "must also stay invisible via `visibility: hidden`, not just take space"
    )


def test_each_card_has_its_own_illustration():
    html = _render("_beginner_deck.html.j2")
    for cls in ("illo-buy-band", "illo-lines", "illo-cadence", "illo-stop-loss"):
        assert f'class="beginner-deck-illo {cls}"' in html
    # decorative but labelled, matching _rotation_illo.html.j2/_guide_illo.html.j2
    assert html.count('role="img"') == 4
    assert html.count("aria-label=") >= 4


def test_buy_band_illustration_highlights_the_live_top_n_not_a_fixed_count():
    """The card-1 illustration must never repeat _guide_illo.html.j2's own
    drift bug (a hardcoded "top_n 5" comment left stale against the live
    Medium preset's actual 4) -- the highlighted-bar count has to move with
    whatever default_horizon_top_n actually is, not a number baked into the
    template."""
    for n in (3, 4, 6):
        html = _render("_beginner_deck.html.j2", default_horizon_top_n=n)
        assert html.count('class="bar bar-buy"') == n
        assert html.count('class="bar bar-rest"') == 10 - n


def test_card_four_link_is_not_the_browser_default_blue():
    """beginner-deck-methodology-link is a plain in-text <a> with no class
    of its own -- static regex check over the CSS source, this codebase's
    established convention for pinning a computed style without a real
    browser (see tests/test_typography_floor.py). Scoped broadly to
    `.methodology-modal a` since that's what both this link and
    methodology-to-deck-link (tests/test_methodology.py) actually rely on."""
    css = (_CSS_DIR / "_chrome.css.j2").read_text()
    assert re.search(r"\.methodology-modal\s+a\s*\{[^}]*color\s*:\s*var\(--brand-strong\)", css)
