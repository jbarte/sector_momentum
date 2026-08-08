"""Tests for src.alerts — Entry/Exit badge event detection and formatting."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.alerts import detect_badge_events, format_alert_body, send_alerts
from src.horizons import default_horizon

# Badges are a rank band now, so fixtures are built from the configured horizon
# rather than magic ranks. IN = inside the buy band, HOLD = inside the hold band
# but not the buy band (silent), OUT = past the hold band.
_H = default_horizon()
IN, HOLD, OUT = 1, _H.exit_rank, _H.exit_rank + 1
assert IN <= _H.top_n < HOLD <= _H.exit_rank < OUT, "fixture bands are degenerate"


def _crossing(region, sector, from_rank, to_rank, n=5):
    """History where `sector` sits at from_rank then moves to to_rank on the
    last scan — i.e. a band crossing if the two ranks are in different bands."""
    return [(i + 1, region, sector, 0.5, 0.1, from_rank if i < n - 1 else to_rank)
            for i in range(n)]


def _history(rows: list[tuple]) -> pd.DataFrame:
    """Build a history DataFrame from (scan_id, region, sector, composite, change_score, rank)."""
    return pd.DataFrame(
        rows,
        columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"],
    )


class TestDetectBadgeEvents:
    def test_entry_badge(self):
        """Entry: composite > 0, change > 0, trajectory up (slope <= -0.3)."""
        df = _history(_crossing("US", "Energy", OUT, IN))
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "entry"
        assert events[0]["sector"] == "Energy"

    def test_exit_badge(self):
        """Exit: trajectory down, change < 0."""
        df = _history(_crossing("US", "Energy", IN, OUT))
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "exit"

    def test_no_badge(self):
        """A position sitting still inside the buy band is not news.

        This is the property that stops the daily nagging: the band rule means
        every held name reads "entry" on every scan, so alerting on membership
        would email the same positions forever. Only crossings count."""
        df = _history(_crossing("US", "Energy", IN, IN))
        assert detect_badge_events(df) == []

    def test_empty_dataframe(self):
        df = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"]
        )
        assert detect_badge_events(df) == []

    def test_single_scan(self):
        df = _history([(1, "US", "Energy", 0.5, 0.3, 1)])
        assert detect_badge_events(df) == []

    def test_themes_cohort(self):
        """Themes use region='THEME'."""
        df = _history(_crossing("THEME", "Uranium", OUT, IN))
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["cohort"] == "THEME"

    def test_multiple_sectors(self):
        """One entry, one exit in the same scan."""
        df = _history(_crossing("US", "Energy", OUT, IN)
                      + _crossing("US", "Tech", IN, OUT))
        events = detect_badge_events(df)
        event_types = {(e["sector"], e["event"]) for e in events}
        assert ("Energy", "entry") in event_types
        assert ("Tech", "exit") in event_types


class TestFormatAlertBody:
    def test_grouped_output(self):
        events = [
            {"cohort": "US", "sector": "Energy", "event": "entry", "rank": 2},
            {"cohort": "EU", "sector": "Tech", "event": "exit", "rank": 5},
            {"cohort": "THEME", "sector": "Uranium", "event": "entry", "rank": 1},
        ]
        body = format_alert_body(events)
        assert "Sectors — US" in body
        assert "Sectors — EU" in body
        assert "Themes" in body
        assert "▲ Entry: Energy (rank 2)" in body
        assert "▼ Exit: Tech (rank 5)" in body
        assert "▲ Entry: Uranium (rank 1)" in body

    def test_empty_events(self):
        assert format_alert_body([]) == ""


class TestSendAlerts:
    @patch("src.alerts.post_ntfy")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_sends_on_events(self, mock_sector, mock_theme, mock_post):
        mock_sector.return_value = _history(_crossing("US", "Energy", OUT, IN))
        mock_theme.return_value = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"]
        )
        conn = MagicMock()
        with patch.dict("os.environ", {"NTFY_TOPIC": "test-topic"}):
            send_alerts(conn, "2026-07-17")
        mock_post.assert_called_once()
        args = mock_post.call_args
        assert "test-topic" == args[0][0]
        assert "2026-07-17" in args[0][1]

    @patch("src.alerts.post_ntfy")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_no_notification_on_no_badges(self, mock_sector, mock_theme, mock_post):
        mock_sector.return_value = _history(_crossing("US", "Energy", IN, IN))
        mock_theme.return_value = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"]
        )
        conn = MagicMock()
        with patch.dict("os.environ", {"NTFY_TOPIC": "test-topic"}):
            send_alerts(conn, "2026-07-17")
        mock_post.assert_not_called()

    @patch("src.alerts.get_alert_prefs")
    @patch("src.alerts.get_scan_history")
    def test_skips_without_topic_or_prefs(self, mock_sector, mock_prefs):
        """No broadcast topic AND no enabled prefs -> no work at all."""
        mock_prefs.return_value = []
        with patch.dict("os.environ", {}, clear=True):
            send_alerts(MagicMock(), "2026-07-17")
        mock_sector.assert_not_called()

    @patch("src.alerts.post_ntfy")
    @patch("src.alerts.get_alert_prefs")
    @patch("src.alerts.get_all_positions")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_personal_alerts_run_without_broadcast_topic(
        self, mock_sector, mock_theme, mock_positions, mock_prefs, mock_post
    ):
        """A user with prefs still gets alerted when NTFY_TOPIC is unset."""
        mock_sector.return_value = _history(_crossing("US", "Energy", OUT, IN))
        mock_theme.return_value = pd.DataFrame()
        mock_positions.return_value = []
        mock_prefs.return_value = [
            {"user_id": "u1", "ntfy_topic": "sm-abc", "enabled": True}
        ]
        with patch.dict("os.environ", {}, clear=True):
            send_alerts(MagicMock(), "2026-07-17")
        mock_post.assert_called_once()
        assert mock_post.call_args[0][0] == "sm-abc"

    @patch("src.alerts.post_ntfy")
    @patch("src.alerts.get_alert_prefs")
    @patch("src.alerts.get_all_positions")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_one_failing_user_does_not_block_others(
        self, mock_sector, mock_theme, mock_positions, mock_prefs, mock_post
    ):
        """Per-user isolation: a bad topic must not stop the remaining users."""
        mock_sector.return_value = _history(_crossing("US", "Energy", OUT, IN))
        mock_theme.return_value = pd.DataFrame()
        mock_positions.return_value = []
        mock_prefs.return_value = [
            {"user_id": "u1", "ntfy_topic": "sm-bad", "enabled": True},
            {"user_id": "u2", "ntfy_topic": "sm-good", "enabled": True},
        ]

        def _post(topic, title, body):
            if topic == "sm-bad":
                raise RuntimeError("ntfy 500")

        mock_post.side_effect = _post
        with patch.dict("os.environ", {}, clear=True):
            send_alerts(MagicMock(), "2026-07-17")   # must not raise
        topics = [c[0][0] for c in mock_post.call_args_list]
        assert "sm-bad" in topics and "sm-good" in topics

    @patch("src.alerts.get_alert_prefs")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_missing_alert_prefs_table_is_non_fatal(
        self, mock_sector, mock_theme, mock_prefs
    ):
        """Merging before the migration runs must not break the scan."""
        mock_prefs.side_effect = RuntimeError('relation "alert_prefs" does not exist')
        mock_sector.return_value = pd.DataFrame()
        mock_theme.return_value = pd.DataFrame()
        conn = MagicMock()
        # A mocked conn has no real transaction state, so a mock connection
        # would happily "succeed" even if the code left a real Postgres
        # connection poisoned (aborted transaction) after the failed SELECT.
        # Asserting rollback was called is the only thing standing in for
        # that real-transaction check. get_alert_prefs is now fetched exactly
        # once (in send_alerts's own guard), so there is only one rollback
        # path left to exercise — but a bare "called somewhere" check still
        # wouldn't prove it happens at the right time. What actually matters
        # is that the rollback happens *before* get_scan_history runs, since
        # that's the query that would fail next on a still-poisoned
        # transaction. A manager mock lets us assert call order across the
        # two separately-patched mocks.
        manager = MagicMock()
        manager.attach_mock(conn.rollback, "rollback")
        manager.attach_mock(mock_sector, "get_scan_history")
        with patch.dict("os.environ", {"NTFY_TOPIC": "ops"}, clear=True):
            send_alerts(conn, "2026-07-17")   # must not raise
        call_names = [c[0] for c in manager.mock_calls]
        assert "rollback" in call_names
        assert call_names.index("rollback") < call_names.index("get_scan_history")
