"""Render tests for the methodology modal partial + footer link + page includes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"


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
    return _jinja_env().get_template(name).render(**ctx)


def test_methodology_modal_markup_and_a11y():
    html = _render("_methodology.html.j2")
    assert 'id="methodology-modal"' in html
    assert 'role="dialog"' in html
    assert 'aria-modal="true"' in html
    assert 'aria-labelledby="methodology-title"' in html
    assert 'id="methodology-close"' in html
    # hidden by default
    assert "methodology-modal" in html and "hidden" in html


def _prose(name: str) -> str:
    """Rendered text with tags stripped and whitespace collapsed.

    Assertions must survive re-wrapping and inline markup — a phrase like
    "excluded from the <strong>ranking</strong>" split over two source lines
    should still match, or these tests fail on formatting rather than meaning.
    """
    import re
    html = _render(name)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).lower()


def test_methodology_covers_every_concept_a_reader_needs():
    """Pins topics, not exact headings — the prose is rewritten periodically for
    readability, but a reader must never lose one of these."""
    html = _prose("_methodology.html.j2")
    for topic, needle in [
        ("what a theme is",      "theme is a group of companies"),
        ("what an ETF is",       "exchange-traded fund"),
        ("momentum",             "momentum"),
        ("relative, not absolute", "relative"),
        ("the two pillars",      "level"),
        ("z-scores explained",   "z-score"),
        ("horizon",              "horizon"),
        ("the hold band",        "hold band"),
        ("badges",               "enter"),
        # The badge is the band read against holdings, so a reader who does not
        # learn that holdings are part of it cannot explain why the same theme
        # shows Enter for one person and Hold for another.
        ("badges depend on holdings", "not held"),
        ("sentiment",            "finbert"),
        ("backtest",             "backtest"),
        ("data sources",         "yfinance"),
        ("stop-loss marker",     "stop-loss"),
    ]:
        assert needle in html, f"methodology no longer explains {topic}"


def test_methodology_explains_the_stop_loss_marker_peak_and_intent():
    """The design spec requires this explicitly: 'the guide dialog gains a
    paragraph explaining what is measured -- peak since starring, not since
    buying'. Without it the tooltip (untranslated per a separate finding, and
    unreachable on touch devices with no hover) was the ONLY explanation a
    reader had."""
    low = _prose("_methodology.html.j2")
    assert "since you starred it" in low
    assert "not since you bought it" in low
    assert "informational only" in low
    assert "does not mean the position was sold" in low


def test_methodology_keeps_its_factual_anchors():
    """These are claims the code actually implements. If the code changes, this
    test should fail and force the prose to follow."""
    low = _prose("_methodology.html.j2")
    assert "50% level" in low and "50% change" in low
    # Sentiment must stay described as excluded from the ranking.
    assert "excluded from the ranking" in low or "informational only" in low
    # The universe size must match the shipped config.
    import yaml
    from pathlib import Path
    cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config/themes.yaml").read_text())
    assert str(len(cfg["themes"])) in low, "stated universe size is out of date"


def test_methodology_states_the_backtest_caveats():
    """The backtest flatters the strategy in three known ways. A reader who
    misses that will over-trust the numbers, so the modal must say so."""
    low = _prose("_methodology.html.j2")
    assert "did not exist" in low, "survivorship/selection caveat missing"
    assert "fitted to the past" in low, "overfitting caveat missing"
    assert "not investment advice" in low


def test_methodology_script_binds_trigger():
    """Esc-to-close itself is pinned directly against _modal.js.j2's own
    source by
    test_dashboard_js.py::test_shared_modal_helper_implements_the_aria_modal_contract
    (not re-checked here): _methodology.html.j2 no longer includes _modal.js.j2
    itself when rendered (2026-08-23 sweep — the partial relies on
    window.SMModal already existing from the page's own earlier include; see
    test_every_aria_modal_dialog_is_bound_to_the_helper in test_dashboard_js.py
    for that guarantee), so rendering this partial in isolation no longer
    contains the literal word "Escape". What this test still owns: that the
    partial actually calls the shared helper rather than hand-rolling."""
    html = _render("_methodology.html.j2")
    assert "methodology-link" in html          # trigger id referenced by the script
    assert "window.SMModal.bind(" in html      # shared helper, not hand-rolled
    assert "#methodology" in html              # hash auto-open


def test_footer_has_methodology_link():
    html = _render("_footer.html.j2")
    assert 'id="methodology-link"' in html
    assert "Methodology" in html


def test_all_pages_include_methodology_partial():
    for page in ["index.html.j2", "sentiment.html.j2"]:
        src = (_TPL_DIR / page).read_text(encoding="utf-8")
        assert '_methodology.html.j2' in src, page


def test_rotation_illo_partial_markup():
    html = _render("_rotation_illo.html.j2")
    assert 'class="modal-illo"' in html
    assert 'class="arc a1 arc1"' in html
    assert 'class="sweep"' in html
    assert 'class="halo"' in html
    assert 'role="img"' in html


def test_modals_include_rotation_illo():
    for page in ["index.html.j2", "_methodology.html.j2"]:
        src = (_TPL_DIR / page).read_text(encoding="utf-8")
        assert '_rotation_illo.html.j2' in src, page


def test_guide_illo_bands_match_the_shipped_medium_preset():
    """_guide_illo.html.j2's header comment claims specific top_n/exit_rank
    numbers for the shipped Medium preset, and its 12 hardcoded bars are split
    hi/mid/lo to depict exactly those numbers. Unlike the prose partials
    rendered elsewhere in this file, the SVG has no live-rendering path to
    interpolate config values into -- both the comment and the bar split are
    hand-maintained, so this is the drift check that stands in for one. It
    caught the header comment and bar split silently going stale after
    medium's top_n moved 5 -> 4 while exit_rank (a function of top_n, buffer,
    and the theme count) happened to stay at 9."""
    import re

    import yaml

    from src.horizons import horizons

    src = (_TPL_DIR / "_guide_illo.html.j2").read_text(encoding="utf-8")

    medium = next(h for h in horizons() if h.key == "medium")
    themes_cfg = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "config/themes.yaml").read_text())
    universe_size = len(themes_cfg["themes"])
    exit_rank = medium.exit_rank(universe_size)

    # The header comment states both numbers in prose -- pin them literally so
    # a future preset change that forgets to update the comment fails loudly.
    comment = src[: src.index("#}")]
    assert f"top_n {medium.top_n}" in comment, (
        f"comment claims a stale top_n; shipped medium.top_n is {medium.top_n}")
    assert f"exit_rank {exit_rank}" in comment, (
        f"comment claims a stale exit_rank; shipped medium.exit_rank({universe_size}) "
        f"is {exit_rank}")

    # The bar split must depict the same numbers: hi bars = top_n, hi+mid
    # bars = exit_rank, and every bar accounted for (12 total, none orphaned).
    hi_count = len(re.findall(r'class="bar b\d+ bar-hi"', src))
    mid_count = len(re.findall(r'class="bar b\d+ bar-mid"', src))
    lo_count = len(re.findall(r'class="bar b\d+ bar-lo"', src))
    assert hi_count == medium.top_n, (
        f"SVG draws {hi_count} buy-band bars, but shipped top_n is {medium.top_n}")
    assert hi_count + mid_count == exit_rank, (
        f"SVG's buy+hold bars ({hi_count + mid_count}) no longer sum to "
        f"exit_rank ({exit_rank})")
    assert hi_count + mid_count + lo_count == 12, "a bar lost its band class"
