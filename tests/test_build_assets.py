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
