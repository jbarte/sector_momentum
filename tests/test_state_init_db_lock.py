"""init_db()'s advisory-lock guard against its own TOCTOU race.

BACKLOG.md (2026-08-23 finding, fixed 2026-09-12): every statement in
init_db() is `IF NOT EXISTS`-guarded, but that check-then-create is not
atomic across concurrent Postgres sessions. Two overlapping init_db() calls
racing to create the same not-yet-existing object could both pass the
existence check and then collide on create, aborting the loser's whole
transaction (rolling back its other ALTER TABLEs too). A transaction-scoped
advisory lock around the entire DDL block serializes concurrent callers
instead of racing them.

These are source-level pins, not a live-concurrency reproduction: this repo
already has a disposable TEST_DATABASE_URL-gated suite (test_state_smoke.py)
for behavioral coverage of init_db() against a real database, and actually
driving two overlapping sessions into a genuine collision needs a second
process/thread plus timing control that would dwarf the three-line fix it is
proving. What matters here is source order and shape, both of which a static
read of the function proves conclusively.
"""

import inspect
from pathlib import Path

from src.state import _INIT_DB_LOCK_KEY_SQL, init_db


def test_init_db_takes_the_lock_before_any_ddl_statement():
    """The lock only closes the race if it runs BEFORE the first
    CREATE/ALTER statement -- taken anywhere else in the function (e.g.
    after the DDL loop) would let the same collision happen first and be
    pointless. Checked as source order, not just presence."""
    src = inspect.getsource(init_db)
    lock_idx = src.index("cur.execute(_INIT_DB_LOCK_KEY_SQL)")
    ddl_idx = src.index("for stmt in _DDL_STATEMENTS")
    assert lock_idx != -1 and ddl_idx != -1
    assert lock_idx < ddl_idx


def test_lock_is_transaction_scoped():
    """pg_advisory_xact_lock releases automatically when the surrounding
    transaction ends -- init_db()'s existing `with conn:` (one transaction
    per call) is therefore sufficient on its own to release it. The
    session-scoped sibling (pg_advisory_lock) would instead leak the lock
    held across calls unless paired with an explicit pg_advisory_unlock,
    which nothing here calls -- confirmed absent from the whole module,
    since a stray unlock call would be a sign the wrong variant was used."""
    assert "pg_advisory_xact_lock(" in _INIT_DB_LOCK_KEY_SQL
    assert "pg_advisory_unlock" not in Path("src/state.py").read_text()


def test_lock_key_is_a_fixed_string_not_a_bare_int():
    """hashtext() of a fixed, human-readable string rather than a hand-picked
    integer: guarantees no accidental collision with some unrelated advisory
    lock this codebase might take later, without maintaining a registry of
    keys already in use."""
    assert "hashtext(" in _INIT_DB_LOCK_KEY_SQL
