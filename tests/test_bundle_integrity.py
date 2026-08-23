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
    with patch.object(B, "_ensure_bundle", return_value=None) as ens:
        B._ensure_plotly_bundle()
        assert ens.call_args.args == ("plotly.min.js", B.PLOTLY_CDN, B.PLOTLY_SHA256)
        assert ens.call_args.kwargs == {"required": True}

        ens.reset_mock()
        B._ensure_supabase_bundle()
        assert ens.call_args.args == (
            "supabase.min.js", B.SUPABASE_JS_CDN, B.SUPABASE_JS_SHA256,
        )
        assert ens.call_args.kwargs == {"required": False}
