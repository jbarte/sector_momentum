"""Unit tests for the destructive-DB-test SAFETY GUARD.

These do NOT touch any database. They verify that the guard in
tests/test_state_smoke.py correctly recognises when two Postgres URLs point at
the SAME underlying database — including the dangerous case where the same
Supabase project is reached via the pooler (`:6543`) and the direct
(`:5432`) connection, whose URL strings differ but whose data is identical.

Context: on 2026-06-25 the production DB was wiped by the smoke-test fixture.
The previous guard compared URL strings, so a prod-equivalent TEST_DATABASE_URL
in a different form would have slipped through. These tests pin the stronger,
identity-based guard.
"""
import pytest

from tests.test_state_smoke import _db_identity, _same_database, _assert_disposable

# Same Supabase project (ref "abcdefghij"), two URL forms.
POOLER = "postgresql://postgres.abcdefghij:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
DIRECT = "postgresql://postgres:pw@db.abcdefghij.supabase.co:5432/postgres"
# A genuinely different Supabase project.
OTHER = "postgresql://postgres.zzzzzzzzzz:pw@aws-0-eu-west-1.pooler.supabase.com:6543/postgres"
# A local throwaway DB.
LOCAL = "postgresql://postgres:pw@localhost:5432/sm_test"


def test_db_identity_extracts_supabase_ref_from_both_forms():
    assert _db_identity(POOLER)[0] == "abcdefghij"
    assert _db_identity(DIRECT)[0] == "abcdefghij"


def test_pooler_and_direct_same_project_are_same_database():
    # The exact bypass that wiped prod: same project, different URL string.
    assert _same_database(POOLER, DIRECT) is True


def test_different_projects_are_distinct():
    assert _same_database(POOLER, OTHER) is False


def test_local_db_is_distinct_from_supabase():
    assert _same_database(LOCAL, POOLER) is False


def test_empty_or_unparseable_treated_as_same_for_safety():
    # Can't determine identity → assume same → refuse to wipe (fail safe).
    assert _same_database("", POOLER) is True
    assert _same_database(POOLER, "") is True


class _FakeConn:
    def __init__(self, params):
        self._p = params

    def get_dsn_parameters(self):
        return self._p


def test_assert_disposable_raises_when_connection_resolves_to_production():
    # Live connection reaches the prod project via the DIRECT host, while the
    # captured prod URL is the POOLER form — string-different, same database.
    conn = _FakeConn({"user": "postgres", "host": "db.abcdefghij.supabase.co",
                      "port": "5432", "dbname": "postgres"})
    with pytest.raises(RuntimeError, match="production"):
        _assert_disposable(conn, POOLER)


def test_assert_disposable_allows_distinct_test_db():
    conn = _FakeConn({"user": "postgres", "host": "localhost",
                      "port": "5432", "dbname": "sm_test"})
    _assert_disposable(conn, POOLER)  # must not raise
