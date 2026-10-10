"""The site can be put on an iPhone home screen with a real icon.

Before this, "Add to Home Screen" gave a generic screenshot tile: no touch
icon, no favicon, no manifest. The artwork is drawn by scripts/make_home_icons.py
(stdlib only) and committed; these tests keep the committed files, the manifest,
the page heads and the build copy step in step.

The manifest deliberately stays `display: browser` and the heads omit
`apple-mobile-web-app-capable`: an iOS standalone web app has its own storage,
separate from Safari, so the emailed magic link (opened in Safari) could never
sign the reader in inside it.
"""
import importlib.util
import json
import re
import struct
import zlib
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).parent.parent
ASSETS = ROOT / "dashboard" / "assets"
ICONS = ASSETS / "icons"
TEMPLATES = ROOT / "dashboard" / "templates"

_spec = importlib.util.spec_from_file_location("make_home_icons", ROOT / "scripts" / "make_home_icons.py")
make_home_icons = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_home_icons)


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])


def _site_file(url: str) -> Path:
    """Where a site-root-relative URL's file lives in the source tree: the build
    copies dashboard/assets/ to docs/assets/ and the manifest to docs/."""
    return ASSETS / url.removeprefix("assets/")


def _png_pixels(path: Path) -> tuple[bytes, bytes]:
    """(IHDR, inflated pixel rows). The compressed IDAT bytes depend on the
    local zlib build (Mac vs the Ubuntu CI image), so staleness is judged on
    what the image *is*, never on how it happens to be compressed."""
    data, pos, ihdr, idat = path.read_bytes(), 8, b"", b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag, body = data[pos + 4:pos + 8], data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            ihdr = body
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    return ihdr, zlib.decompress(idat)


def test_committed_icons_are_what_the_generator_draws(tmp_path):
    make_home_icons.write_all(tmp_path)
    stale = f"{{}} is stale: re-run `python3 scripts/make_home_icons.py` and commit"
    assert (ICONS / "favicon.svg").read_bytes() == (tmp_path / "favicon.svg").read_bytes(), \
        stale.format("favicon.svg")
    for name in make_home_icons.PNG_SIZES:
        assert _png_pixels(ICONS / name) == _png_pixels(tmp_path / name), stale.format(name)


def test_png_icons_have_the_sizes_their_names_and_the_manifest_promise():
    for name, size in make_home_icons.PNG_SIZES.items():
        assert _png_size(ICONS / name) == (size, size), name
    assert make_home_icons.PNG_SIZES["apple-touch-icon.png"] == 180   # what iOS asks for


def test_manifest_is_valid_and_every_icon_it_names_exists():
    manifest = json.loads((ASSETS / "manifest.webmanifest").read_text())
    assert manifest["name"] == "ETF Momentum"
    assert manifest["short_name"] == "Momentum"            # fits under an iPhone icon
    assert manifest["display"] == "browser"                # NOT standalone: see module docstring
    assert manifest["icons"], "no icons declared"
    for icon in manifest["icons"]:
        path = _site_file(icon["src"])
        assert path.exists(), f"manifest names {icon['src']} but it does not exist"
        w, h = (int(n) for n in icon["sizes"].split("x"))
        assert _png_size(path) == (w, h), icon["src"]


def _partial() -> str:
    env = Environment(loader=FileSystemLoader(str(TEMPLATES)))
    return env.get_template("_head_icons.html.j2").render()


def test_head_links_point_at_files_that_exist():
    html = _partial()
    hrefs = re.findall(r'<link [^>]*href="([^"]+)"', html)
    assert {h for h in hrefs} == {
        "assets/icons/favicon.svg", "assets/icons/apple-touch-icon.png", "manifest.webmanifest"}
    for href in hrefs:
        assert _site_file(href).exists(), f"{href} would 404"
    assert 'rel="apple-touch-icon"' in html and 'rel="manifest"' in html and 'rel="icon"' in html


def test_head_does_not_make_the_page_a_standalone_web_app():
    assert "apple-mobile-web-app-capable" not in _partial()


def test_every_page_head_includes_the_icons():
    for name in ("index.html.j2", "sentiment.html.j2"):
        text = (TEMPLATES / name).read_text()
        head = text.split("</head>")[0]
        assert '{% include "_head_icons.html.j2" %}' in head, f"{name} has no icon links"


def test_build_copies_the_icons_and_the_manifest_into_docs():
    src = (ROOT / "dashboard" / "build.py").read_text()
    assert re.search(r'copytree\(\s*icons_src,\s*docs_assets\s*/\s*"icons"', src)
    assert re.search(r'copy2\(\s*manifest_src,\s*out_dir\s*/\s*"manifest.webmanifest"', src)
    # The pages link these unconditionally, so a missing source must fail the
    # build, not be skipped: no exists()/is_dir() guard around either copy.
    assert "icons_src.is_dir()" not in src and "manifest_src.exists()" not in src
