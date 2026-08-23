"""Every client-side asset a template loads must actually reach docs/assets/.

`theme.js` was referenced by `<script src="assets/theme.js">` in both pages
from the dark theme feature's first commit, but never added to
`dashboard/build.py`'s per-asset copy block — so it 404'd on every real build
and the whole feature was inert for 3 commits, undetected because nothing
checks the built *output*, only that the build script exits 0. This is the
regression test the backlog asked for after that incident: static analysis
over the source files, not a full build, so it runs in milliseconds and
needs no DB/config.
"""
import re
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_TPL_DIR = _PROJECT_ROOT / "dashboard" / "templates"
_BUILD_PY = _PROJECT_ROOT / "dashboard" / "build.py"

# The one asset reference that isn't a literal `src="assets/…"` string: both
# pages load it via `{{ plotly_bundle }}`, and build.py pins that variable to
# the literal string "assets/plotly.min.js" (see build.py:451). Hardcoded
# here rather than resolved by rendering the template, since this test is
# deliberately static/DB-free — see the module docstring.
_TEMPLATED_ASSET_REFS = {"plotly.min.js"}


def _referenced_assets() -> set[str]:
    """Every asset filename loaded via `<script src="assets/…">` across all
    templates, plus the one templated exception above.

    Recursive glob, not `_TPL_DIR.glob("*.j2")`: partials under `css/` and
    `i18n/` are rendered via `{% include %}` from `_style.html.j2` /
    `_i18n.html.j2` just as much as the 14 top-level files are, and a
    non-recursive scan would silently miss a script reference added to one
    of those 15 subdirectory files — the same failure mode this test exists
    to catch, just one directory level deeper.
    """
    names = set(_TEMPLATED_ASSET_REFS)
    for path in _TPL_DIR.rglob("*.j2"):
        for m in re.finditer(r'src="assets/([^"]+)"', path.read_text()):
            names.add(m.group(1))
    return names


def _copied_assets() -> set[str]:
    """Every asset filename `build.py`'s copy block writes into
    `docs/assets/` — parsed from its `shutil.copy2(..., docs_assets / "X")`
    call sites, regardless of what condition (if any) gates each one. The
    theme.js bug was "copied under no condition at all", not "copied under
    the wrong condition" — this test targets that simpler, actually-occurred
    failure mode."""
    text = _BUILD_PY.read_text()
    return set(re.findall(r'docs_assets\s*/\s*"([^"]+)"', text))


def test_every_referenced_asset_is_copied_by_build_py():
    referenced = _referenced_assets()
    copied = _copied_assets()
    missing = referenced - copied
    assert not missing, (
        f"{sorted(missing)} referenced by a <script src=\"assets/…\"> tag but "
        f"never copied into docs/assets/ by dashboard/build.py — the page will "
        f"404 on this script in a real build. Add a shutil.copy2(...) line "
        f"for it, mirroring the existing entries."
    )


def test_referenced_and_copied_sets_are_not_trivially_empty():
    """A parsing regression in either helper above would make the main test
    pass vacuously (empty minus empty is still empty) — this guards against
    that by pinning known-present entries on both sides."""
    referenced = _referenced_assets()
    copied = _copied_assets()
    assert "theme.js" in referenced, "template parsing found nothing — regex likely broken"
    assert "theme.js" in copied, "build.py parsing found nothing — regex likely broken"
    assert len(referenced) >= 5
    assert len(copied) >= 5


# ---------------------------------------------------------------------------
# Vendored browser bundles: version skew + integrity
# ---------------------------------------------------------------------------
#
# Added 2026-08-23 with the Plotly 2.27.0 -> 3.7.0 bump. Two separate defects
# shared these ~40 lines of build.py, and each needs its own guard:
#
#   1. The served plotly.js had drifted TWO MAJORS behind the plotly.py that
#      writes the figure JSON (2.27.0, from 2023, against plotly.py 6.9.0 /
#      plotly.js 3.7.0). Nothing errored — an older runtime silently ignores
#      attributes it does not know — so the skew was invisible until measured.
#   2. Neither bundle's bytes were verified. Pinning a version pins a URL, not
#      an artifact.


def _build_py_text() -> str:
    return _BUILD_PY.read_text()


def test_plotly_cdn_version_matches_the_installed_plotly_py():
    """The served plotly.js must be the version the installed plotly.py targets.

    This is the guard that makes the skew self-reporting: bumping `plotly` in
    requirements.lock without bumping PLOTLY_CDN now fails here, instead of
    quietly shipping figure JSON to a runtime two majors behind it.
    """
    from plotly.offline.offline import get_plotlyjs_version

    from dashboard.build import PLOTLY_CDN

    expected = get_plotlyjs_version()
    m = re.search(r"plotly-\w+-([0-9]+\.[0-9]+\.[0-9]+)\.min\.js", PLOTLY_CDN)
    assert m, f"could not parse a version out of PLOTLY_CDN={PLOTLY_CDN!r}"
    assert m.group(1) == expected, (
        f"PLOTLY_CDN serves plotly.js {m.group(1)} but the installed plotly.py "
        f"generates figure JSON for plotly.js {expected}. Bump PLOTLY_CDN (and "
        f"PLOTLY_SHA256 with it) to match, then rebuild and eyeball every chart "
        f"in both themes — a major bump changes defaults silently."
    )


def test_every_vendored_bundle_url_has_a_pinned_sha256():
    """Each *_CDN constant must have a 64-hex *_SHA256 beside it.

    Pins the shape rather than the values: a third vendored bundle added later
    without a digest is the regression this catches.
    """
    text = _build_py_text()
    urls = set(re.findall(r"^(\w+?)_CDN\b", text, re.M))
    assert urls, "no *_CDN constants found — regex likely broken"
    for name in sorted(urls):
        m = re.search(rf'^{name}_SHA256 = "([0-9a-f]{{64}})"$', text, re.M)
        assert m, (
            f"{name}_CDN has no 64-hex {name}_SHA256 beside it. Every bundle "
            f"downloaded at build time and served to readers must be pinned by "
            f"content, not just by URL."
        )


def test_bundles_are_verified_by_hash_not_by_file_existence():
    """The cached copy must be hash-checked, not merely existence-checked.

    Sabotage-verified: reverting `_ensure_bundle` to `if not bundle.exists()`
    fails this. That regression is why the 2.27.0 -> 3.7.0 bump would otherwise
    have landed only in CI (cold asset cache every run) and never on a machine
    that already had the old file — `dashboard/assets/` is gitignored.
    """
    import inspect

    from dashboard.build import _ensure_bundle

    src = inspect.getsource(_ensure_bundle)
    assert "_sha256(cached)" in src, (
        "_ensure_bundle does not hash its cached copy — a stale bundle from "
        "before a version bump would be served forever"
    )
