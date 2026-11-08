"""The dashboard serves its own fonts (dashboard/assets/fonts/) instead of
loading them from Google Fonts, so no third party sees a reader's IP address.
These tests keep it that way: the build copies exactly the font files, every
@font-face and preload points at a shipped file through the cache-busting
asset_url(), and nothing the page loads comes from a host other than the
project's own Supabase.
"""
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_PROJECT_ROOT = Path(__file__).parent.parent
_DASHBOARD = _PROJECT_ROOT / "dashboard"
_TEMPLATES = _DASHBOARD / "templates"
_FONTS = _DASHBOARD / "assets" / "fonts"

_SHIPPED = sorted(p.name for p in _FONTS.glob("*.woff2"))
_ASSET_URL_FONT = re.compile(r"""asset_url\(\s*'fonts/([^']+)'\s*\)""")


def test_the_font_folder_holds_the_expected_files():
    # Pins the glob above, so the checks below cannot pass on an empty list.
    assert _SHIPPED == [
        "familjen-grotesk-latin-wght.woff2",
        "inter-greek-wght.woff2",
        "inter-latin-wght.woff2",
        "jetbrains-mono-latin-400.woff2",
        "jetbrains-mono-latin-500.woff2",
    ]


def test_build_copies_only_the_fonts_and_their_licence(tmp_path):
    from dashboard.build import _copy_fonts

    src = tmp_path / "src"
    (src / "fonts").mkdir(parents=True)
    for name in ["a.woff2", "b.woff2", "FONTS.md", ".DS_Store", "old.ttf"]:
        (src / "fonts" / name).write_bytes(b"x")
    out = tmp_path / "out"
    (out / "fonts").mkdir(parents=True)
    (out / "fonts" / "renamed-away.woff2").write_bytes(b"stale")

    _copy_fonts(out, assets_dir=src)

    assert sorted(p.name for p in (out / "fonts").iterdir()) == [
        "FONTS.md", "a.woff2", "b.woff2",
    ]


def test_every_font_face_points_at_a_shipped_file_via_asset_url():
    css = (_TEMPLATES / "css" / "_foundation.css.j2").read_text()
    assert "url('assets/fonts/" not in css and 'url("assets/fonts/' not in css, (
        "a literal font path skips the content-hash cache-buster"
    )
    assert sorted(_ASSET_URL_FONT.findall(css)) == _SHIPPED


def test_the_greek_file_is_scoped_to_greek_by_unicode_range():
    # Without unicode-range every page would download it, and it would claim
    # Latin glyphs too. The range is Fontsource's own for inter-greek.
    css = (_TEMPLATES / "css" / "_foundation.css.j2").read_text()
    # Split on the at-rule, not a brace pattern: the Jinja {{ }} in src: has braces.
    face = next(c for c in css.split("@font-face") if "inter-greek-wght" in c)
    assert "unicode-range: U+0370-0377" in face and "U+03A3-03FF" in face
    assert "\u0394" in (_TEMPLATES / "index.html.j2").read_text(), (
        "the Delta this file exists for is no longer on the page; "
        "drop the Greek font if so"
    )


def test_preloads_are_the_same_urls_as_the_font_faces():
    css = (_TEMPLATES / "css" / "_foundation.css.j2").read_text()
    preload = (_TEMPLATES / "_font_preload.html.j2").read_text()
    # Every face except Greek: that file is fetched on demand (unicode-range),
    # so preloading it would download it on every page for nothing.
    preloaded = sorted(_ASSET_URL_FONT.findall(preload))
    assert preloaded == sorted(
        n for n in _ASSET_URL_FONT.findall(css) if "greek" not in n)
    assert len(preloaded) == 4
    for page in ("index.html.j2", "sentiment.html.j2"):
        text = (_TEMPLATES / page).read_text()
        assert '{% include "_font_preload.html.j2" %}' in text, page
        # Before the first script, or the preload starts no earlier than the CSS.
        assert text.index("_font_preload.html.j2") < text.index("<script"), page


def test_font_urls_render_with_the_content_hash(tmp_path):
    from dashboard.build import _asset_versions, register_asset_url

    env = Environment(loader=FileSystemLoader(str(_TEMPLATES)))
    names = [f"fonts/{n}" for n in _SHIPPED]
    register_asset_url(env, _asset_versions(_DASHBOARD / "assets", names))
    html = env.get_template("_font_preload.html.j2").render()
    hrefs = re.findall(r'href="([^"]+)"', html)
    assert len(hrefs) == 4
    assert all(re.fullmatch(r"assets/fonts/[a-z0-9-]+\.woff2\?v=[0-9a-f]{8}", h) for h in hrefs), hrefs


def _page_sources():
    """What reaches a reader's browser: templates, the dashboard's own scripts
    (not the vendored *.min.js bundles), and the Python that emits page HTML.
    build.py is left out: its URLs are build-time downloads, pinned by digest."""
    files = list(_TEMPLATES.rglob("*.j2"))
    files += [p for p in (_DASHBOARD / "assets").glob("*.js") if ".min." not in p.name]
    files += [p for p in _DASHBOARD.glob("*.py") if p.name != "build.py"]
    # An empty glob would make the host check pass vacuously.
    assert len(files) > 30, files
    return files


def test_nothing_the_page_loads_comes_from_a_third_party_host():
    # Case-insensitive, and protocol-relative `//host` counts too.
    url = re.compile(r"(?:https?:)?//([a-z0-9-]+(?:\.[a-z0-9-]+)+)", re.I)
    # The XML namespace every inline SVG declares. It names a vocabulary and is
    # never fetched, so it is not a third-party load; only this exact string is
    # exempt, so a real www.w3.org URL would still be caught.
    svg_namespace = "http://www.w3.org/2000/svg"
    hosts = set()
    for f in _page_sources():
        hosts |= {h.lower() for h in url.findall(f.read_text().replace(svg_namespace, ""))}
    # ntfy.sh is a link a signed-in reader clicks to open their alert topic
    # (alert-prefs.js), not something the page loads.
    links = {"ntfy.sh"}
    foreign = {h for h in hosts if not h.endswith(".supabase.co")} - links
    assert foreign == set(), sorted(foreign)


def test_a_missing_font_source_fails_the_build_and_leaves_the_output_alone(tmp_path):
    from dashboard.build import _copy_fonts

    src = tmp_path / "src"
    (src / "fonts").mkdir(parents=True)
    (src / "fonts" / "a.woff2").write_bytes(b"x")          # FONTS.md is missing
    out = tmp_path / "out"
    (out / "fonts").mkdir(parents=True)
    (out / "fonts" / "kept.woff2").write_bytes(b"old")

    try:
        _copy_fonts(out, assets_dir=src)
    except FileNotFoundError as e:
        assert "FONTS.md" in str(e)
    else:
        raise AssertionError("a missing FONTS.md must fail the build")
    assert [p.name for p in (out / "fonts").iterdir()] == ["kept.woff2"]
