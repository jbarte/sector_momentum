"""The position lock: friction and a record, never enforcement.

The lock has a one-click override by design -- a lock that could NOT be
cleared would let the dashboard stop the reader recording a trade they had
actually made at the broker, leaving the board wrong, which is a worse failure
than the impulsive trade it prevents. These tests pin the friction and the
override, and the RLS that keeps one user's lock private.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_MIGRATION = _ROOT / "scripts/book_locks_migration.sql"
_LOCK_JS = _ROOT / "dashboard/assets/book-lock.js"
_BUILD = _ROOT / "dashboard/build.py"


def test_migration_exists_and_is_idempotent():
    assert _MIGRATION.exists(), "book_locks migration was not created"
    sql = _MIGRATION.read_text()
    assert "create table if not exists public.book_locks" in sql, (
        "migration is not idempotent -- it is run by hand in the Supabase SQL "
        "editor, same as positions_migration.sql"
    )


def test_migration_enables_rls_and_scopes_to_the_owner():
    """Without this a signed-in user could read every other user's lock."""
    sql = _MIGRATION.read_text()
    assert "enable row level security" in sql
    assert "auth.uid() = user_id" in sql
    assert "with check" in sql, "insert/update is not constrained to the owner"


def test_migration_grants_update_unlike_positions():
    """positions is insert/delete only; a lock is toggled in place."""
    sql = _MIGRATION.read_text()
    grant = [l for l in sql.splitlines() if l.strip().startswith("grant")]
    assert grant, "no grant statement"
    assert "update" in " ".join(grant)


def test_migration_records_the_override():
    """The audit value must survive the friction being bypassed."""
    assert "unlocked_at" in _MIGRATION.read_text(), (
        "nothing records that the book was unlocked mid-period, so an "
        "overridden lock leaves no trace"
    )


def test_lock_asset_exists_and_exposes_the_api():
    assert _LOCK_JS.exists(), "book-lock.js was not created"
    js = _LOCK_JS.read_text()
    for fn in ("isLocked", "lockedUntil", "lock", "unlock", "load"):
        assert fn in js, f"book-lock.js does not expose {fn}"
    assert "window.SMBookLock" in js


def test_lock_asset_is_copied_by_the_build():
    """Assets not copied are 404s on Pages -- the same gap that has bitten
    every other asset added to this dashboard."""
    build = _BUILD.read_text()
    assert "book-lock.js" in build, (
        "dashboard/build.py never copies book-lock.js into docs/assets/"
    )


# ---------------------------------------------------------------------------
# lock()/unlock() must fail SAFE, never fail LOCKED (review finding, round 1)
# ---------------------------------------------------------------------------
#
# The original lock()/unlock() set the module's local `state` optimistically
# BEFORE the Supabase write resolved, with no rollback on failure. A
# rejected, unpersisted lock() call then left isLocked() reporting true for
# the rest of the page session -- exactly the "fail locked" outcome this
# module's own header comment forbids ("must never ... fail locked"). These
# tests execute the real book-lock.js under Node against a mocked
# window.SMSupabase whose write call rejects, proving the fix rather than
# just the code's presence.

def _run_node(script: str) -> dict:
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node script failed: {res.stderr}"
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_lock_fails_safe_not_locked_when_write_rejects():
    js_src = _LOCK_JS.read_text()
    script = f"""
        global.window = {{}};
        window.Rescore = {{ localISODate: function () {{ return "2026-08-29"; }} }};
        window.SMSupabase = {{
          from: function (table) {{
            return {{
              select: function () {{
                return {{ maybeSingle: function () {{
                  return Promise.resolve({{ data: null, error: null }});
                }} }};
              }},
              upsert: function () {{
                return Promise.reject(new Error("offline"));
              }},
              update: function () {{
                return {{ not: function () {{
                  return Promise.reject(new Error("offline"));
                }} }};
              }}
            }};
          }}
        }};

        {js_src}

        window.SMBookLock.load().then(function () {{
          var before = window.SMBookLock.isLocked();
          return window.SMBookLock.lock("2099-01-01", "weekly")
            .catch(function (err) {{ return err.message; }})
            .then(function (caught) {{
              process.stdout.write(JSON.stringify({{
                before: before,
                afterFailedLock: window.SMBookLock.isLocked(),
                lockedUntilAfterFailedLock: window.SMBookLock.lockedUntil(),
                caught: caught
              }}));
            }});
        }});
    """
    out = _run_node(script)
    assert out["caught"] == "offline", "lock() no longer propagates the write rejection"
    assert out["before"] is False
    assert out["afterFailedLock"] is False, (
        "lock() left isLocked()==true after a rejected, unpersisted write -- "
        "fail-LOCKED instead of fail-safe"
    )
    assert out["lockedUntilAfterFailedLock"] is None


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_unlock_fails_safe_stays_locked_when_write_rejects():
    """Same class of bug, on unlock()'s write path: state.unlocked_at must
    not be cleared locally until the update() is confirmed. On a rejected
    write, isLocked() must keep reporting the PRE-call value (still
    locked) -- the override recorded nowhere is worse than no override."""
    js_src = _LOCK_JS.read_text()
    script = f"""
        global.window = {{}};
        window.Rescore = {{ localISODate: function () {{ return "2026-08-29"; }} }};
        window.SMSupabase = {{
          from: function (table) {{
            return {{
              select: function () {{
                return {{ maybeSingle: function () {{
                  return Promise.resolve({{ data: {{
                    horizon_key: "weekly", locked_until: "2099-01-01", unlocked_at: null
                  }}, error: null }});
                }} }};
              }},
              upsert: function () {{
                return Promise.reject(new Error("offline"));
              }},
              update: function () {{
                return {{ not: function () {{
                  return Promise.reject(new Error("offline"));
                }} }};
              }}
            }};
          }}
        }};

        {js_src}

        window.SMBookLock.load().then(function () {{
          var before = window.SMBookLock.isLocked();
          return window.SMBookLock.unlock()
            .catch(function (err) {{ return err.message; }})
            .then(function (caught) {{
              process.stdout.write(JSON.stringify({{
                before: before,
                afterFailedUnlock: window.SMBookLock.isLocked(),
                caught: caught
              }}));
            }});
        }});
    """
    out = _run_node(script)
    assert out["caught"] == "offline", "unlock() no longer propagates the write rejection"
    assert out["before"] is True
    assert out["afterFailedUnlock"] is True, (
        "unlock() cleared the lock locally after a rejected, unpersisted write"
    )


# ---------------------------------------------------------------------------
# load() must actually be CALLED somewhere, not just correct in isolation
# (whole-branch review, Critical finding). Every test above calls load()
# itself, which proves load() works but not that anything in production
# code ever invokes it -- and nothing did: `state` stayed null for the whole
# page lifetime, so isLocked() reported false on every fresh page load, even
# with a real persisted lock in Supabase. These tests never call load()
# directly -- only the auth-state callback and then isLocked()/lockedUntil()/
# isLoaded(), exactly as a real caller would -- so they can only pass if the
# auth-state hook actually wires load() in.

def _run_node_async(script: str) -> dict:
    """Like _run_node, but for a script whose final output is written from
    inside a setTimeout (load()'s .then() needs a tick to resolve)."""
    res = subprocess.run(["node", "-e", script], capture_output=True, text=True)
    assert res.returncode == 0, f"node script failed: {res.stderr}"
    return json.loads(res.stdout)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_load_is_wired_to_auth_state_change():
    js_src = _LOCK_JS.read_text()
    script = f"""
        global.window = {{}};
        global.document = {{ dispatchEvent: function () {{}} }};
        window.Rescore = {{ localISODate: function () {{ return "2026-08-29"; }} }};
        var selectCalls = 0;
        var authCallback = null;
        window.SMSupabase = {{
          auth: {{
            onAuthStateChange: function (cb) {{ authCallback = cb; }}
          }},
          from: function (table) {{
            return {{
              select: function () {{
                selectCalls++;
                return {{ maybeSingle: function () {{
                  return Promise.resolve({{ data: {{
                    horizon_key: "weekly", locked_until: "2099-01-01", unlocked_at: null
                  }}, error: null }});
                }} }};
              }}
            }};
          }}
        }};

        {js_src}

        // No window.SMBookLock.load() call anywhere in this script -- the
        // whole point is that the auth-state hook must call it on its own.
        var beforeAuth = window.SMBookLock.isLocked();
        authCallback("SIGNED_IN", {{ user: {{ id: "u1" }} }});
        setTimeout(function () {{
          process.stdout.write(JSON.stringify({{
            beforeAuth: beforeAuth,
            afterAuth: window.SMBookLock.isLocked(),
            lockedUntilAfterAuth: window.SMBookLock.lockedUntil(),
            loadedAfterAuth: window.SMBookLock.isLoaded(),
            selectCalls: selectCalls
          }}));
        }}, 50);
    """
    out = _run_node_async(script)
    assert out["selectCalls"] == 1, (
        "load() was never invoked when a session became available -- "
        "onAuthStateChange is not wired to load()"
    )
    assert out["beforeAuth"] is False, "isLocked() should start false (nothing loaded yet)"
    assert out["loadedAfterAuth"] is True
    assert out["afterAuth"] is True, (
        "isLocked() does not reflect a real persisted lock after the "
        "auth-state hook fires -- reload, or a second device, would show "
        "the book as unlocked even though Supabase holds an active lock"
    )
    assert out["lockedUntilAfterAuth"] == "2099-01-01"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
def test_sign_out_resets_stale_lock_state():
    """A previous user's lock (or a previous session's, on a shared device)
    must not keep reporting locked after sign-out."""
    js_src = _LOCK_JS.read_text()
    script = f"""
        global.window = {{}};
        global.document = {{ dispatchEvent: function () {{}} }};
        window.Rescore = {{ localISODate: function () {{ return "2026-08-29"; }} }};
        var authCallback = null;
        window.SMSupabase = {{
          auth: {{ onAuthStateChange: function (cb) {{ authCallback = cb; }} }},
          from: function (table) {{
            return {{
              select: function () {{
                return {{ maybeSingle: function () {{
                  return Promise.resolve({{ data: {{
                    horizon_key: "weekly", locked_until: "2099-01-01", unlocked_at: null
                  }}, error: null }});
                }} }};
              }}
            }};
          }}
        }};

        {js_src}

        authCallback("SIGNED_IN", {{ user: {{ id: "u1" }} }});
        setTimeout(function () {{
          var whileSignedIn = window.SMBookLock.isLocked();
          authCallback("SIGNED_OUT", null);
          process.stdout.write(JSON.stringify({{
            whileSignedIn: whileSignedIn,
            afterSignOut: window.SMBookLock.isLocked(),
            loadedAfterSignOut: window.SMBookLock.isLoaded()
          }}));
        }}, 50);
    """
    out = _run_node_async(script)
    assert out["whileSignedIn"] is True
    assert out["afterSignOut"] is False, (
        "isLocked() still reports true right after sign-out -- state was "
        "not reset, so a stale lock can leak into the next signed-out view"
    )
    assert out["loadedAfterSignOut"] is False, (
        "isLoaded() still reports true after sign-out -- a later sign-in "
        "for a different user could be skipped for looking already-loaded"
    )


def test_star_toggle_is_blocked_while_locked():
    """The friction. Without this the lock is decoration."""
    js = (_ROOT / "dashboard/assets/positions.js").read_text()
    assert "SMBookLock" in js, (
        "positions.js never consults the lock, so a locked book can still be "
        "edited by tapping a star"
    )
    assert "isLocked" in js


def test_blocked_click_explains_itself():
    """A star that silently does nothing reads as a broken page."""
    js = (_ROOT / "dashboard/assets/positions.js").read_text()
    assert "lock_blocked" in js, (
        "a refused toggle gives the reader no reason"
    )


def test_live_region_exists_for_the_blocked_click_announcement():
    """announceLive() (positions.js) reads #sm-live-region by id and
    no-ops -- fail-open, same as the rest of this file -- if the element is
    absent. Review round 1 found that NO template defined that id, so every
    blocked star click announced nothing: the visible `title` attribute
    doesn't show until a hover-dwell, so the reader got no immediate
    visible OR announced feedback at all. Pin both ends of the contract: the
    id positions.js looks up, and that _footer.html.j2 -- the shared,
    auth-gated partial positions.js's own <script> tag is loaded from, on
    every page that loads it -- actually defines a live region under that
    id, kept in the accessibility tree (not `hidden`) so screen readers can
    reach it."""
    js = (_ROOT / "dashboard/assets/positions.js").read_text()
    assert 'getElementById("sm-live-region")' in js, (
        "announceLive() no longer targets #sm-live-region -- update this "
        "test and the template together if the id changes"
    )
    footer = (_ROOT / "dashboard/templates/_footer.html.j2").read_text()
    m = re.search(r'<div id="sm-live-region"([^>]*)>', footer)
    assert m, (
        "no live region for announceLive() to reach -- blocked clicks "
        "announce nothing, visibly or otherwise"
    )
    attrs = m.group(1)
    assert 'aria-live="polite"' in attrs, "#sm-live-region is not a live region"
    assert "hidden" not in attrs, (
        "#sm-live-region is `hidden` -- assistive tech ignores updates to a "
        "hidden live region, so it must stay reachable via clipping (.sr-only) "
        "instead"
    )
    # Ahead of the <script> tag that reads it: the more defensive order, even
    # though announceLive() looks the element up lazily on each call today
    # rather than once at load time.
    assert footer.index('id="sm-live-region"') < footer.index('assets/positions.js')


def test_blocked_click_has_a_visible_channel_not_just_hover_title():
    """Whole-branch review finding: the ONLY visible cue a blocked tap had
    was the `title` attribute, which needs a hover-dwell no touch device
    ever produces -- so on a phone, a blocked tap produced no visible change
    at all (#sm-live-region, the other channel, is .sr-only by design). Pins
    both ends of the fix: positions.js writes into #rp-block-note on a
    blocked tap, and _review_panel.html.j2 actually defines that element
    (hidden by default, so it doesn't occupy space until a tap happens)."""
    js = (_ROOT / "dashboard/assets/positions.js").read_text()
    assert 'getElementById("rp-block-note")' in js, (
        "no visible-cue element is targeted for a blocked tap -- the title "
        "attribute (hover-only) and the sr-only live region are still the "
        "only feedback a blocked tap gives"
    )
    # The blocked-click handler must actually call the function that writes
    # to it, not just define the function unused.
    blocked_click = js[js.index("SMBookLock.isLocked()"):]
    assert "showBlockNote(" in blocked_click[:600], (
        "showBlockNote() is defined but the blocked-click handler never "
        "calls it"
    )

    panel = (_ROOT / "dashboard/templates/_review_panel.html.j2").read_text()
    assert 'id="rp-block-note"' in panel, (
        "no template element for positions.js's visible blocked-tap cue to reach"
    )
    assert 'hidden' in panel[panel.index('id="rp-block-note"') - 40:
                             panel.index('id="rp-block-note"') + 40], (
        "#rp-block-note is not hidden by default -- it would show empty "
        "space on every page load, not just after a blocked tap"
    )


def test_panel_has_a_lock_checkbox_and_an_override():
    panel = (_ROOT / "dashboard/templates/_review_panel.html.j2").read_text()
    assert 'id="rp-lock"' in panel, "no lock control in the review panel"
    tpl = (_ROOT / "dashboard/templates/index.html.j2").read_text()
    assert "SMBookLock.unlock" in tpl, (
        "no override path -- a lock with no escape can leave the board unable "
        "to record a real trade"
    )


def test_done_auto_locks_until_the_next_review():
    # Slice from the LAST "review-done-btn", not the first: the first hit is
    # renderReviewPanel()'s own `doneBtn.hidden = ...` lookup (Task 3), far
    # from the click-binding block this test targets. The last hit is that
    # click-binding block itself (`getElementById('review-done-btn')` inside
    # initHorizonSelect()), so the window below only has to reach the Done
    # handler's own `SMBookLock.lock` call (~546 chars past it, measured
    # directly against this file) -- not the *other* SMBookLock.lock call
    # site in this file, the #rp-lock checkbox's own handler (~1095 chars
    # past it). A window of 800 clears the first with room to spare and
    # falls well short of the second, so a regression that dropped the Done
    # handler's own call while leaving the checkbox's intact would still
    # fail this test.
    tpl = (_ROOT / "dashboard/templates/index.html.j2").read_text()
    done = tpl[tpl.rindex("review-done-btn"):]
    assert "SMBookLock.lock" in done[:800], (
        "ticking Done does not re-lock the book, so the cycle does not close"
    )


def test_lock_strings_have_swedish():
    sv = (_ROOT / "dashboard/templates/i18n/_core.js.j2").read_text()
    for key in ("rp_lock_label", "lock_blocked", "rp_unlock"):
        assert f"{key}:" in sv, f"{key} has no Swedish translation"
