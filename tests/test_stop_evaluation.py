"""Scan-time stop evaluation: latching, the day-one guard, and fail-open."""
from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock, patch

import pandas as pd

from src import alerts

_TOPIC = "sm-" + "a" * 32
_CFG = {"themes": {"Uranium & Nuclear": {"ticker": "URA"}}}


def _prices(closes, start="2026-08-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return {"URA": pd.DataFrame({"Close": closes}, index=idx)}


def _position(created="2026-08-01"):
    return [{"user_id": "u1", "item_type": "theme", "region": "",
             "name": "Uranium & Nuclear", "created_at": pd.Timestamp(created)}]


def _user(since="2026-08-01"):
    return [{"user_id": "u1", "ntfy_topic": _TOPIC,
             "stop_loss_since": pd.Timestamp(since)}]


def _run(prices, positions, users, existing, insert=None):
    with patch.object(alerts, "get_all_positions", return_value=positions), \
         patch.object(alerts, "get_stop_loss_users", return_value=users), \
         patch.object(alerts, "get_position_stops", return_value=existing), \
         patch.object(alerts, "insert_position_stop", insert or MagicMock()):
        return alerts.collect_stop_events(MagicMock(), prices, _CFG)


def test_new_breach_produces_an_event_and_a_latch_row():
    insert = MagicMock()
    out = _run(_prices([100.0, 130.0, 105.0]), _position(), _user(), [], insert)
    assert [e["name"] for e in out["u1"]] == ["Uranium & Nuclear"]
    assert insert.call_count == 1
    assert insert.call_args.kwargs["notified"] is True


def test_already_latched_position_produces_no_second_event():
    """The latch is what stops a still-breached holding alerting every day."""
    existing = [{"user_id": "u1", "item_type": "theme", "region": "",
                 "name": "Uranium & Nuclear", "stopped_on": dt.date(2026, 8, 3),
                 "drawdown": -0.19, "notified": True}]
    insert = MagicMock()
    out = _run(_prices([100.0, 130.0, 105.0]), _position(), _user(), existing, insert)
    assert out == {}
    insert.assert_not_called()


def test_breach_older_than_opt_in_is_recorded_but_not_notified():
    """The day-one guard: switching the feature on must not fire a burst of
    push notifications about drawdowns from weeks ago."""
    insert = MagicMock()
    out = _run(_prices([100.0, 130.0, 105.0]), _position(),
               _user(since="2027-01-01"), [], insert)
    assert out == {}
    assert insert.call_count == 1
    assert insert.call_args.kwargs["notified"] is False


def test_unbreached_position_writes_nothing():
    insert = MagicMock()
    out = _run(_prices([100.0, 130.0, 125.0]), _position(), _user(), [], insert)
    assert out == {}
    insert.assert_not_called()


def test_peak_window_starts_at_the_star_date():
    """A high before starring is not this holding's peak."""
    prices = _prices([200.0, 100.0, 110.0, 99.0])
    out = _run(prices, _position(created="2026-08-02"), _user(), [], MagicMock())
    assert out == {}          # 99/110-1 = -10%, inside the 12% threshold


def test_missing_table_is_non_fatal():
    """Merged before the migration is applied: the scan must survive."""
    with patch.object(alerts, "get_all_positions", return_value=_position()), \
         patch.object(alerts, "get_stop_loss_users", return_value=_user()), \
         patch.object(alerts, "get_position_stops",
                      side_effect=RuntimeError('relation "position_stops" does not exist')):
        conn = MagicMock()
        assert alerts.collect_stop_events(conn, _prices([100.0, 130.0, 105.0]), _CFG) == {}
        conn.rollback.assert_called()


def test_position_with_no_prices_is_skipped_not_crashed():
    out = _run({}, _position(), _user(), [], MagicMock())
    assert out == {}


def test_a_stop_never_removes_the_position():
    """A fired stop states that the threshold was breached; whether the user
    sold is their call. Auto-unstarring would destroy state on a rule they may
    disagree with, and would be indistinguishable from them having sold."""
    import inspect
    src = inspect.getsource(alerts.collect_stop_events)
    assert "delete" not in src.lower()
