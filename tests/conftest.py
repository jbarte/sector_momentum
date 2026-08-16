"""Shared pytest fixtures.

Structural guard against live network calls escaping the unit suite: see
`_no_live_gdelt_downloads` below.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_live_gdelt_downloads(monkeypatch):
    """Fail loudly if any test reaches the real GDELT bulk downloader.

    `src.data.gdelt_gkg._download` is the one place that hits the network
    (`data.gdeltproject.org`) for the bulk GKG feed. Every test that exercises
    `fetch_theme_headlines_bulk` (directly or via `fetch_headlines`) is
    expected to inject its own `fetcher=`/mock rather than touch the real
    thing — a test that doesn't do that should fail fast and obviously
    instead of silently firing dozens of live HTTP requests (see the
    incident this guard was added for: a mis-targeted `unittest.mock.patch`
    in test_state_smoke.py let 96 real GETs through while the test still
    passed, because its assertions never depended on the network call).

    No test in this suite legitimately needs live network access, so there
    is no opt-out — a test that needs `_download` for real should not exist
    in the unit suite.

    Recording-and-failing-at-teardown, rather than raising straight out of
    the stub, is deliberate: `_safe_slice` in gdelt_gkg.py wraps every
    fetcher call in a broad `except Exception` (by design, so one bad slice
    doesn't take down the other 95) which would silently swallow a raise
    from inside `_download` itself — a fail-fast guard implemented that way
    would fail to fail. Raising still matters here (it guarantees the stub
    never lets a real `requests.get` happen), but the guard's loudness comes
    from checking afterwards, in fixture teardown, whether the stub was ever
    called at all.
    """
    calls: list[str] = []

    def _blocked(url: str, timeout: int = 60):
        calls.append(url)
        raise RuntimeError(f"blocked by test guard, no live GET made: {url}")

    monkeypatch.setattr("src.data.gdelt_gkg._download", _blocked)

    yield

    if calls:
        pytest.fail(
            f"{len(calls)} call(s) reached src.data.gdelt_gkg._download "
            f"during this test (e.g. {calls[0]!r}) — patch/inject a fetcher "
            "instead of relying on the real network. See "
            "tests/conftest.py:_no_live_gdelt_downloads."
        )
