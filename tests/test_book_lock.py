"""The position lock: friction and a record, never enforcement.

The lock has a one-click override by design -- a lock that could NOT be
cleared would let the dashboard stop the reader recording a trade they had
actually made at the broker, leaving the board wrong, which is a worse failure
than the impulsive trade it prevents. These tests pin the friction and the
override, and the RLS that keeps one user's lock private.
"""
import re
from pathlib import Path

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
