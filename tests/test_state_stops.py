"""State-layer contracts for the stop-loss latch.

These assert the SQL each function issues rather than round-tripping a real
database -- the DB-backed suites skip without DATABASE_URL, and the properties
that matter here (which columns are selected, and that the insert is
idempotent) are exactly the ones a mocked cursor can pin.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from src import state


def _conn_returning(rows: list[dict], monkeypatch) -> MagicMock:
    monkeypatch.setattr(state, "_read_sql",
                        lambda conn, query, params=None: pd.DataFrame(rows))
    return MagicMock()


def test_get_all_positions_now_returns_created_at(monkeypatch):
    """The stop needs the star date; without created_at there is no entry
    date and no peak window."""
    captured = {}

    def _fake(conn, query, params=None):
        captured["query"] = query
        return pd.DataFrame([{"user_id": "u1", "item_type": "theme",
                              "region": "", "name": "Uranium & Nuclear",
                              "created_at": pd.Timestamp("2026-08-01")}])

    monkeypatch.setattr(state, "_read_sql", _fake)
    rows = state.get_all_positions(MagicMock())
    assert "created_at" in captured["query"]
    assert rows[0]["created_at"] == pd.Timestamp("2026-08-01")


def test_get_stop_loss_users_filters_to_opted_in_and_enabled(monkeypatch):
    captured = {}

    def _fake(conn, query, params=None):
        captured["query"] = query
        return pd.DataFrame([{"user_id": "u1", "ntfy_topic": "sm-" + "a" * 32,
                              "stop_loss_since": pd.Timestamp("2026-09-01")}])

    monkeypatch.setattr(state, "_read_sql", _fake)
    rows = state.get_stop_loss_users(MagicMock())
    assert "stop_loss_since IS NOT NULL" in captured["query"]
    assert "enabled = true" in captured["query"]
    assert rows[0]["user_id"] == "u1"


def test_get_position_stops_returns_empty_list_when_no_rows(monkeypatch):
    monkeypatch.setattr(state, "_read_sql",
                        lambda conn, query, params=None: pd.DataFrame())
    assert state.get_position_stops(MagicMock()) == []


def test_insert_position_stop_is_idempotent():
    """ON CONFLICT DO NOTHING is what makes the latch latch: a second scan over
    a still-breached position must not overwrite the original breach date."""
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    state.insert_position_stop(
        conn, user_id="u1", item_type="theme", region="", name="Uranium & Nuclear",
        stopped_on="2026-09-07", peak_price=52.1, peak_on="2026-08-18",
        drawdown=-0.143, notified=True)
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO position_stops" in sql
    assert "ON CONFLICT" in sql and "DO NOTHING" in sql


def test_migration_cascades_the_latch_when_a_position_is_deleted():
    """Unstarring must clear the latch, and it must do so WITHOUT application
    code -- that is the whole reason the FK is composite and cascading. This
    reads the migration rather than a live DB (the DB-backed suites skip
    without DATABASE_URL), because what is being pinned is the DDL choice."""
    from pathlib import Path
    sql = Path("scripts/position_stops_migration.sql").read_text().lower()
    assert "references public.positions(user_id, item_type, region, name)" in sql
    assert "on delete cascade" in sql


def test_migration_grants_select_only_to_clients():
    """A client that could INSERT here could fabricate a stop it was never
    sent; one that could DELETE could clear a latch without unstarring."""
    from pathlib import Path
    sql = Path("scripts/position_stops_migration.sql").read_text().lower()
    assert "grant select on public.position_stops to authenticated;" in sql
    for forbidden in ("grant insert", "grant update", "grant delete", "grant all"):
        assert forbidden not in sql


def test_upsert_position_stop_distance_upserts_not_inserts():
    """Unlike the latch's ON CONFLICT DO NOTHING, this must overwrite the
    previous reading every scan -- it's a live gauge, not a one-time record."""
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    state.upsert_position_stop_distance(
        conn, user_id="u1", item_type="theme", region="", name="Uranium & Nuclear",
        as_of="2026-09-11", peak_price=52.1, peak_on="2026-08-18",
        latest_price=48.3, drawdown=-0.073)
    sql = cur.execute.call_args[0][0]
    assert "INSERT INTO position_stop_distance" in sql
    assert "ON CONFLICT" in sql and "DO UPDATE" in sql
    assert "DO NOTHING" not in sql


def test_upsert_position_stop_distance_passes_the_right_values():
    conn = MagicMock()
    cur = conn.cursor.return_value.__enter__.return_value
    state.upsert_position_stop_distance(
        conn, user_id="u1", item_type="theme", region="", name="Uranium & Nuclear",
        as_of="2026-09-11", peak_price=52.1, peak_on="2026-08-18",
        latest_price=48.3, drawdown=-0.073)
    params = cur.execute.call_args[0][1]
    assert params == ("u1", "theme", "", "Uranium & Nuclear",
                      "2026-09-11", 52.1, "2026-08-18", 48.3, -0.073)


def test_position_stop_distance_migration_cascades_on_delete():
    from pathlib import Path
    sql = Path("scripts/position_stop_distance_migration.sql").read_text().lower()
    assert "references public.positions(user_id, item_type, region, name)" in sql
    assert "on delete cascade" in sql


def test_position_stop_distance_migration_grants_select_only():
    from pathlib import Path
    sql = Path("scripts/position_stop_distance_migration.sql").read_text().lower()
    assert "grant select on public.position_stop_distance to authenticated;" in sql
    for forbidden in ("grant insert", "grant update", "grant delete", "grant all"):
        assert forbidden not in sql


def test_position_stop_distance_migration_upserts_not_latches():
    """The whole point of this table vs. reusing position_stops: it must be
    writable more than once for the same key."""
    from pathlib import Path
    sql = Path("scripts/position_stop_distance_migration.sql").read_text()
    assert "primary key (user_id, item_type, region, name)" in sql
