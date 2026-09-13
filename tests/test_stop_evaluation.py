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


def _run(prices, positions, users, existing, insert=None, distance=None):
    with patch.object(alerts, "get_all_positions", return_value=positions), \
         patch.object(alerts, "get_stop_loss_users", return_value=users), \
         patch.object(alerts, "get_position_stops", return_value=existing), \
         patch.object(alerts, "insert_position_stop", insert or MagicMock()), \
         patch.object(alerts, "upsert_position_stop_distance", distance or MagicMock()):
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


def test_unbreached_position_writes_only_the_distance_reading():
    insert = MagicMock()
    distance = MagicMock()
    out = _run(_prices([100.0, 130.0, 125.0]), _position(), _user(), [],
              insert, distance)
    assert out == {}
    insert.assert_not_called()
    assert distance.call_count == 1
    kwargs = distance.call_args.kwargs
    assert kwargs["user_id"] == "u1"
    assert kwargs["name"] == "Uranium & Nuclear"
    assert round(kwargs["drawdown"], 4) == round(125.0 / 130.0 - 1.0, 4)


def test_already_latched_position_gets_no_distance_upsert():
    """Once breached, the chip owns this row -- a stale distance reading for
    it is never rendered client-side, so there is no point writing one."""
    existing = [{"user_id": "u1", "item_type": "theme", "region": "",
                "name": "Uranium & Nuclear", "stopped_on": dt.date(2026, 8, 3),
                "drawdown": -0.19, "notified": True}]
    distance = MagicMock()
    _run(_prices([100.0, 130.0, 105.0]), _position(), _user(), existing,
        MagicMock(), distance)
    distance.assert_not_called()


def test_new_breach_still_writes_a_distance_reading_before_latching():
    """The scan that crosses the line writes BOTH the final distance reading
    and the latch -- only the NEXT scan skips the distance table, once
    key-in-latched short-circuits before evaluate_stop ever runs."""
    insert = MagicMock()
    distance = MagicMock()
    _run(_prices([100.0, 130.0, 105.0]), _position(), _user(), [],
        insert, distance)
    assert insert.call_count == 1
    assert distance.call_count == 1
    assert distance.call_args.kwargs["drawdown"] < -0.12


def test_distance_write_failure_does_not_block_that_position_s_breach_alert():
    """Isolated the same way insert_position_stop's own try/except is: a
    failure writing the LIVE reading must not silently swallow the breach
    alert for a position that is ALSO breaching this same scan."""
    def _boom(**kwargs):
        raise RuntimeError("boom")
    out = _run(_prices([100.0, 130.0, 105.0]), _position(), _user(), [],
              MagicMock(), _boom)
    assert [e["name"] for e in out["u1"]] == ["Uranium & Nuclear"]


def test_missing_price_data_leaves_any_prior_distance_reading_untouched():
    """evaluate_stop returning None means 'no opinion', not 'safe' -- the
    error-handling spec is explicit that a transient price gap must not
    erase yesterday's still-roughly-accurate reading, which is exactly what
    NOT calling the upsert achieves (the previous row, if any, is simply
    left in place in the DB)."""
    distance = MagicMock()
    out = _run({}, _position(), _user(), [], MagicMock(), distance)
    assert out == {}
    distance.assert_not_called()


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


def test_one_bad_position_does_not_prevent_others_from_being_evaluated(monkeypatch):
    """A single position raising an unexpected error during evaluation (e.g. a
    tz mismatch, or any other malformed-data surprise) must not sink the whole
    batch: send_personal_alerts's per-user try/except is the pattern this
    mirrors. The function must still evaluate every other position and return
    normally rather than propagating -- collect_stop_events runs before
    send_personal_alerts, so an uncaught exception here would silently kill
    ALL personal alerts for the scan, stops included."""
    import src.stops as stops_mod
    real_evaluate = stops_mod.evaluate_stop

    idx = pd.date_range("2026-08-01", periods=3, freq="D")
    ura_prices = pd.DataFrame({"Close": [100.0, 130.0, 105.0]}, index=idx)
    cibr_prices = pd.DataFrame({"Close": [100.0, 130.0, 105.0]}, index=idx)
    prices = {"URA": ura_prices, "CIBR": cibr_prices}

    def _boom_for_ura(prices_df, entry_date, stop_frac):
        if prices_df is ura_prices:
            raise RuntimeError("boom")
        return real_evaluate(prices_df, entry_date, stop_frac)

    monkeypatch.setattr(stops_mod, "evaluate_stop", _boom_for_ura)

    positions = [
        {"user_id": "u1", "item_type": "theme", "region": "",
         "name": "Uranium & Nuclear", "created_at": pd.Timestamp("2026-08-01")},
        {"user_id": "u1", "item_type": "theme", "region": "",
         "name": "Cybersecurity", "created_at": pd.Timestamp("2026-08-01")},
    ]
    cfg = {"themes": {"Uranium & Nuclear": {"ticker": "URA"},
                      "Cybersecurity": {"ticker": "CIBR"}}}
    insert = MagicMock()
    with patch.object(alerts, "get_all_positions", return_value=positions), \
         patch.object(alerts, "get_stop_loss_users", return_value=_user()), \
         patch.object(alerts, "get_position_stops", return_value=[]), \
         patch.object(alerts, "insert_position_stop", insert):
        out = alerts.collect_stop_events(MagicMock(), prices, cfg)

    assert [e["name"] for e in out["u1"]] == ["Cybersecurity"]
    assert insert.call_count == 1


def test_a_stop_never_removes_the_position():
    """A fired stop states that the threshold was breached; whether the user
    sold is their call. Auto-unstarring would destroy state on a rule they may
    disagree with, and would be indistinguishable from them having sold."""
    import inspect
    src = inspect.getsource(alerts.collect_stop_events)
    assert "delete" not in src.lower()
