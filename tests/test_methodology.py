"""Render tests for the methodology modal partial + footer link + page includes."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

_TPL_DIR = Path(__file__).parent.parent / "dashboard" / "templates"


def _jinja_env():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader(str(_TPL_DIR)), keep_trailing_newline=True)
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
    ]:
        assert needle in html, f"methodology no longer explains {topic}"


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
    html = _render("_methodology.html.j2")
    assert "methodology-link" in html          # trigger id referenced by the script
    assert "Escape" in html                    # Esc-to-close
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
