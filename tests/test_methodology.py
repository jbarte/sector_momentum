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


def test_methodology_content_sections_present():
    html = _render("_methodology.html.j2")
    for heading in ["Universe", "Data sources", "Signals", "Scoring",
                    "Trajectory", "Backtest", "Research basis"]:
        assert heading in html, heading
    # Key factual anchors that must stay accurate
    assert "rs_ratio" in html
    assert "50% Level" in html and "50% Change" in html
    assert "informational only" in html.lower() or "info-only" in html.lower()


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
    for page in ["index.html.j2", "themes.html.j2", "sentiment.html.j2"]:
        src = (_TPL_DIR / page).read_text(encoding="utf-8")
        assert '_methodology.html.j2' in src, page


def test_rotation_illo_partial_markup():
    html = _render("_rotation_illo.html.j2")
    assert 'class="modal-illo"' in html
    assert 'class="arc a1"' in html
    assert 'class="sweep"' in html
    assert 'class="halo"' in html
    assert 'role="img"' in html


def test_modals_include_rotation_illo():
    for page in ["index.html.j2", "_methodology.html.j2"]:
        src = (_TPL_DIR / page).read_text(encoding="utf-8")
        assert '_rotation_illo.html.j2' in src, page
