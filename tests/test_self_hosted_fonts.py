"""The dashboard serves its own fonts (dashboard/assets/fonts/) instead of
loading them from Google Fonts, so no third party sees a reader's IP address.
These tests keep it that way: the build copies the files, every @font-face
points at a file that exists, and no template loads anything from a host other
than the project's own Supabase.
"""
import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_TEMPLATES = _PROJECT_ROOT / "dashboard" / "templates"
_FONTS = _PROJECT_ROOT / "dashboard" / "assets" / "fonts"

_SHIPPED = [
    "familjen-grotesk-latin-wght.woff2",
    "inter-latin-wght.woff2",
    "jetbrains-mono-latin-400.woff2",
    "jetbrains-mono-latin-500.woff2",
]


def _template_text() -> str:
    files = list(_TEMPLATES.rglob("*.j2"))
    # A glob that found nothing would make every check below pass vacuously.
    assert len(files) > 10, files
    return "\n".join(f.read_text() for f in files)


def test_build_copies_the_fonts_and_their_licence(tmp_path):
    from dashboard.build import _copy_fonts

    _copy_fonts(tmp_path)
    copied = sorted(p.name for p in (tmp_path / "fonts").iterdir())
    assert copied == sorted(_SHIPPED + ["FONTS.md"])


def test_every_font_face_points_at_a_shipped_file():
    css = (_TEMPLATES / "css" / "_foundation.css.j2").read_text()
    urls = re.findall(r"url\('assets/fonts/([^']+)'\)", css)
    assert sorted(urls) == sorted(_SHIPPED)
    for name in urls:
        assert (_FONTS / name).is_file(), name


def test_no_template_loads_from_a_third_party_host():
    hosts = set(re.findall(r"https?://([a-z0-9.-]+)", _template_text()))
    # The project's own Supabase is the one origin the page talks to.
    foreign = {h for h in hosts if not h.endswith(".supabase.co")}
    assert foreign == set(), sorted(foreign)
