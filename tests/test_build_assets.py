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

# Every asset — plotly included — now goes through `asset_url('name.js')`,
# so there is no longer a reference form this scan cannot see. The old
# `{{ plotly_bundle }}` context-variable special case was removed on
# 2026-08-29 when cache-busting made one uniform mechanism worthwhile.
_TEMPLATED_ASSET_REFS: set[str] = set()


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
        text = path.read_text()
        # Cache-busted form (the only one since 2026-08-29): every script tag
        # goes through asset_url('name.js') so build.py can append a content
        # hash. See the cache-busting tests at the bottom of this file.
        for m in re.finditer(r"asset_url\(\s*['\"]([^'\"]+)['\"]\s*\)", text):
            names.add(m.group(1))
        # Literal form, still matched so a regression back to a hardcoded
        # (and therefore un-bustable) src is caught rather than ignored.
        for m in re.finditer(r'src="assets/([^"]+)"', text):
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
    from plotly.offline import get_plotlyjs_version

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


# ---------------------------------------------------------------------------
# Cache busting
#
# Found live 2026-08-29, the day the book-lock feature shipped: the lock
# silently did not lock. Root cause was not the lock code (correct, and
# verified against the deployed artifacts) but the deploy — GitHub Pages
# serves these assets with `cache-control: max-age=600` and NOTHING in the
# URL changed between deploys, so a reader whose browser had cached the
# previous `positions.js` ran it against the freshly-deployed page. The page
# rendered the new lock UI while the old script, which knew nothing about
# `SMBookLock`, handled the clicks: new HTML, old JS.
#
# That mixed-version page is the general failure these tests prevent. The
# lock was just the instance that made it visible — and the worst kind,
# because the UI claimed a safety feature was on while the code enforcing
# it was absent.
# ---------------------------------------------------------------------------

def _js_srcs(html: str) -> list[str]:
    """Every `src="assets/....js..."` value in a rendered page."""
    return re.findall(r'src="(assets/[^"]+\.js[^"]*)"', html)


def test_rendered_js_assets_all_carry_a_cache_busting_query(tmp_path):
    """Every JS asset URL must carry a version query, so a browser cannot
    pair a new page with a stale script. Rendered for real rather than
    grepped from template source: the point is what a reader's browser
    actually receives."""
    import json as _json
    import sys
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dashboard.build import _render
    from tests.test_dashboard_js import (
        _horizon_ctx, _make_mock_plotly_json, _TEMPLATE,
    )

    # Derived from the templates themselves: a script tag added later
    # WITHOUT asset_url() gets no version here and shows up unversioned in
    # the assertion below, which is exactly the regression to catch.
    versions = {name: "v%08d" % i for i, name in enumerate(sorted(_referenced_assets()))}

    out = tmp_path / "index.html"
    _render(_TEMPLATE, out, dict(
        scan_date="2026-08-29",
        leaderboard_rows=[], us_leaderboard_rows=[], eu_leaderboard_rows=[],
        cohort_list=[], grouped_rows=[], has_any_rows=False,
        cohorts_json=_json.dumps([]), **_horizon_ctx(),
        cohort_charts_json=_json.dumps({}),
        sentiment_scatter_json=_make_mock_plotly_json(),
        rescore_data_json=_json.dumps({"scans": [], "sectors": [], "data": {}, "sentiment": {}}),
        scan_history_json=_json.dumps({"scans": [], "scores": {}}),
        signals_list=[],
        backtest_json=_json.dumps({}), backtest_metrics=[], has_backtest=False,
        rotation_json=_json.dumps([]), has_rotations=False,
        # Auth on, so the gated scripts (positions.js and book-lock.js — the
        # two the live failure actually involved) are in the rendered output.
        auth={"url": "https://example.supabase.co", "key": "anon"},
        auth_config_json=_json.dumps({"url": "https://example.supabase.co", "key": "anon"}),
        asset_versions=versions,
    ))
    html = out.read_text()

    srcs = _js_srcs(html)
    assert srcs, "rendered page referenced no JS assets at all — fixture is wrong"
    unversioned = [s for s in srcs if "?v=" not in s]
    assert not unversioned, (
        "these JS assets have no cache-busting query, so a stale cached copy "
        f"can be paired with a newer page: {unversioned}"
    )


def test_asset_version_is_content_derived(tmp_path):
    """The version must change when a file's CONTENT changes, and stay put
    when it doesn't — a build-timestamp version would re-bust plotly.min.js
    (1.4 MB) on every daily scan for a file that never changes."""
    import sys
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dashboard.build import _asset_versions

    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "a.js").write_text("console.log(1);")
    first = _asset_versions(assets, ["a.js"])

    # Unchanged content -> identical version (readers keep their cache).
    assert _asset_versions(assets, ["a.js"]) == first

    # Changed content -> different version (readers are forced to refetch).
    (assets / "a.js").write_text("console.log(2);")
    assert _asset_versions(assets, ["a.js"])["a.js"] != first["a.js"]


def test_asset_version_skips_files_that_do_not_exist(tmp_path):
    """The copy block is existence-gated (an un-fetched plotly bundle is a
    normal local state), so the version map must tolerate the same."""
    import sys
    sys.path.insert(0, str(_PROJECT_ROOT))
    from dashboard.build import _asset_versions

    assets = tmp_path / "assets"
    assets.mkdir()
    assert _asset_versions(assets, ["missing.js"]) == {}
