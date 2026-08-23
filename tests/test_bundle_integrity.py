"""Behavioural tests for `_ensure_bundle` — the vendored-bundle supply chain.

`dashboard/build.py` downloads plotly.js and supabase-js at build time and
serves them to readers; `dashboard/assets/` is gitignored, so CI re-downloads
both on every cold run. Before 2026-08-23 the bytes were never verified: the
pinned *URL* was the only thing standing between a re-published tag (or a
compromised CDN) and arbitrary JS running on the page — including, for
supabase.min.js, with access to a signed-in reader's auth session.

The static checks in test_build_assets.py pin the shape (a digest exists, the
cache is hashed). These pin the behaviour, which is where the failure modes
actually live: what gets written to disk, and what exits non-zero.
"""
import hashlib
from unittest.mock import MagicMock, patch

import pytest

from dashboard import build as B


GOOD = b"console.log('pinned bundle');"
GOOD_SHA = hashlib.sha256(GOOD).hexdigest()
EVIL = b"console.log('substituted');"
URL = "https://cdn.example/bundle.min.js"


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "_ASSETS_DIR", tmp_path)
    return tmp_path


def _resp(content: bytes) -> MagicMock:
    r = MagicMock()
    r.content = content
    r.raise_for_status = MagicMock()
    return r


def test_matching_cache_is_reused_without_any_download(assets):
    (assets / "b.js").write_bytes(GOOD)
    with patch("requests.get") as get:
        out = B._ensure_bundle("b.js", URL, GOOD_SHA, required=True)
    get.assert_not_called()
    assert out == assets / "b.js"


def test_stale_cache_is_replaced_rather_than_served(assets):
    """The bump path. An existence check would keep the old bytes forever."""
    (assets / "b.js").write_bytes(b"the previous major version")
    with patch("requests.get", return_value=_resp(GOOD)) as get:
        out = B._ensure_bundle("b.js", URL, GOOD_SHA, required=True)
    get.assert_called_once()
    assert out.read_bytes() == GOOD


def test_downloaded_bytes_failing_the_digest_are_fatal_and_never_written(assets):
    """A mismatch is not "the network was down" — it is the substitution this
    whole mechanism exists to stop, so it exits non-zero for BOTH bundles.

    The `not exists()` assertion is the load-bearing half: writing the bad
    bytes first and exiting after would leave them cached, and the next build
    would serve them from the `_sha256(cached)` fast path without ever
    re-downloading.
    """
    with patch("requests.get", return_value=_resp(EVIL)):
        with pytest.raises(SystemExit) as exc:
            B._ensure_bundle("b.js", URL, GOOD_SHA, required=True)
    assert exc.value.code == 1
    assert not (assets / "b.js").exists(), "rejected bytes were cached to disk"


def test_digest_mismatch_is_fatal_even_for_the_fail_open_bundle(assets):
    """required=False softens a *download failure*, not a substitution."""
    with patch("requests.get", return_value=_resp(EVIL)):
        with pytest.raises(SystemExit):
            B._ensure_bundle("b.js", URL, GOOD_SHA, required=False)
    assert not (assets / "b.js").exists()


def test_download_failure_is_fatal_when_required(assets):
    with patch("requests.get", side_effect=RuntimeError("CDN down")):
        with pytest.raises(SystemExit) as exc:
            B._ensure_bundle("b.js", URL, GOOD_SHA, required=True)
    assert exc.value.code == 1


def test_download_failure_degrades_gracefully_when_not_required(assets):
    """A missing auth bundle means a dashboard without login, not a failed build."""
    with patch("requests.get", side_effect=RuntimeError("CDN down")):
        out = B._ensure_bundle("b.js", URL, GOOD_SHA, required=False)
    assert out is None
    assert not (assets / "b.js").exists()


def test_the_two_real_bundles_wire_the_right_digest_and_failure_mode(assets):
    """Guards the call sites, not the helper: swapping the two digests, or
    flipping `required`, is invisible to every test above."""
    # Returns a Path, not None: _ensure_plotly_bundle asserts non-None (its
    # required=True contract), so a None stub would fail on the stub rather
    # than on the wiring this test is about.
    with patch.object(B, "_ensure_bundle", return_value=Path("stub")) as ens:
        B._ensure_plotly_bundle()
        assert ens.call_args.args == ("plotly.min.js", B.PLOTLY_CDN, B.PLOTLY_SHA256)
        assert ens.call_args.kwargs == {"required": True}

        ens.reset_mock()
        B._ensure_supabase_bundle()
        assert ens.call_args.args == (
            "supabase.min.js", B.SUPABASE_JS_CDN, B.SUPABASE_JS_SHA256,
        )
        assert ens.call_args.kwargs == {"required": False}


# ---------------------------------------------------------------------------
# Is each digest the digest of the RIGHT artifact?
# ---------------------------------------------------------------------------
#
# Code review (2026-08-23) found the gap these close. Every test above pins a
# digest to whatever constant the call site passes, so SWAPPING THE TWO
# CONSTANTS' VALUES moves both sides together and all of them still pass —
# confirmed by running that exact sabotage. Nothing tied a pinned digest to the
# bytes its own URL actually serves.
#
# The bytes are the only ground truth, so these check against them. They are
# offline-safe: each skips when the artifact it needs is not on disk (a clean
# checkout, before the first build), which is the same condition under which
# `_ensure_bundle` would download and verify anyway — a wrong pin still fails
# the build loudly there. What these add is catching it in the test suite, on
# any machine that has already built once.

import re
from pathlib import Path

_ASSETS = Path(__file__).parent.parent / "dashboard" / "assets"


@pytest.mark.parametrize(
    "filename, digest_name",
    [("plotly.min.js", "PLOTLY_SHA256"), ("supabase.min.js", "SUPABASE_JS_SHA256")],
)
def test_pinned_digest_is_the_digest_of_the_vendored_artifact(filename, digest_name):
    path = _ASSETS / filename
    if not path.exists():
        pytest.skip(f"{filename} not vendored yet — nothing to compare against")
    expected = getattr(B, digest_name)
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    assert actual == expected, (
        f"{digest_name} does not match the vendored {filename}.\n"
        f"  pinned   {expected}\n"
        f"  on disk  {actual}\n"
        f"Either the constant is wrong (e.g. two digests swapped), or the file "
        f"on disk is stale/tampered. `_ensure_bundle` would re-download it — "
        f"delete it and rebuild to find out which."
    )


def test_vendored_plotly_is_the_version_plotly_cdn_names():
    """Ties the URL, the digest and the bytes into one chain.

    The digest test above proves PLOTLY_SHA256 matches the file; this proves the
    file is the version PLOTLY_CDN advertises. Without it, a self-consistent but
    wrong pin (right hash of the wrong bundle) reads as healthy.
    """
    path = _ASSETS / "plotly.min.js"
    if not path.exists():
        pytest.skip("plotly.min.js not vendored yet")
    want = re.search(r"plotly-\w+-([0-9.]+)\.min\.js", B.PLOTLY_CDN).group(1)
    head = path.read_bytes()[:400].decode("utf-8", "replace")
    got = re.search(r"plotly\.js \(\w+ - minified\) v([0-9.]+)", head)
    assert got, f"no version banner in the first 400 bytes of {path}"
    assert got.group(1) == want, (
        f"PLOTLY_CDN names v{want} but the vendored bundle is v{got.group(1)}"
    )
