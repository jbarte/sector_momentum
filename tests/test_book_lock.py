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
