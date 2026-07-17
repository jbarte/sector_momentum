"""Tests for src.alerts — Entry/Exit badge event detection and formatting."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.alerts import detect_badge_events, format_alert_body, send_alerts


def _history(rows: list[tuple]) -> pd.DataFrame:
    """Build a history DataFrame from (scan_id, region, sector, composite, change_score, rank)."""
    return pd.DataFrame(
        rows,
        columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"],
    )


class TestDetectBadgeEvents:
    def test_entry_badge(self):
        """Entry: composite > 0, change > 0, trajectory up (slope <= -0.3)."""
        df = _history([
            (1, "US", "Energy", 0.5, 0.3, 5),
            (2, "US", "Energy", 0.6, 0.4, 4),
            (3, "US", "Energy", 0.7, 0.5, 3),
            (4, "US", "Energy", 0.8, 0.6, 2),
            (5, "US", "Energy", 0.9, 0.7, 1),
        ])
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "entry"
        assert events[0]["sector"] == "Energy"

    def test_exit_badge(self):
        """Exit: trajectory down, change < 0."""
        df = _history([
            (1, "US", "Energy", 0.5, 0.3, 1),
            (2, "US", "Energy", 0.4, 0.1, 3),
            (3, "US", "Energy", 0.3, -0.1, 5),
            (4, "US", "Energy", 0.2, -0.2, 7),
            (5, "US", "Energy", 0.1, -0.3, 9),
        ])
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "exit"

    def test_no_badge(self):
        """Flat trajectory, no setup badge."""
        df = _history([
            (1, "US", "Energy", 0.5, 0.1, 3),
            (2, "US", "Energy", 0.5, 0.1, 3),
            (3, "US", "Energy", 0.5, 0.1, 3),
            (4, "US", "Energy", 0.5, 0.1, 3),
            (5, "US", "Energy", 0.5, 0.1, 3),
        ])
        events = detect_badge_events(df)
        assert events == []

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
        df = _history([
            (1, "THEME", "Uranium", 0.5, 0.3, 5),
            (2, "THEME", "Uranium", 0.6, 0.4, 4),
            (3, "THEME", "Uranium", 0.7, 0.5, 3),
            (4, "THEME", "Uranium", 0.8, 0.6, 2),
            (5, "THEME", "Uranium", 0.9, 0.7, 1),
        ])
        events = detect_badge_events(df)
        assert len(events) == 1
        assert events[0]["cohort"] == "THEME"

    def test_multiple_sectors(self):
        """One entry, one exit in the same scan."""
        df = _history([
            # Energy: rising trajectory -> entry
            (1, "US", "Energy", 0.5, 0.3, 5),
            (2, "US", "Energy", 0.6, 0.4, 4),
            (3, "US", "Energy", 0.7, 0.5, 3),
            (4, "US", "Energy", 0.8, 0.6, 2),
            (5, "US", "Energy", 0.9, 0.7, 1),
            # Tech: falling trajectory -> exit
            (1, "US", "Tech", 0.5, 0.3, 1),
            (2, "US", "Tech", 0.4, 0.1, 3),
            (3, "US", "Tech", 0.3, -0.1, 5),
            (4, "US", "Tech", 0.2, -0.2, 7),
            (5, "US", "Tech", 0.1, -0.3, 9),
        ])
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
        mock_sector.return_value = _history([
            (1, "US", "Energy", 0.5, 0.3, 5),
            (2, "US", "Energy", 0.6, 0.4, 4),
            (3, "US", "Energy", 0.7, 0.5, 3),
            (4, "US", "Energy", 0.8, 0.6, 2),
            (5, "US", "Energy", 0.9, 0.7, 1),
        ])
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
        mock_sector.return_value = _history([
            (1, "US", "Energy", 0.5, 0.1, 3),
            (2, "US", "Energy", 0.5, 0.1, 3),
            (3, "US", "Energy", 0.5, 0.1, 3),
            (4, "US", "Energy", 0.5, 0.1, 3),
            (5, "US", "Energy", 0.5, 0.1, 3),
        ])
        mock_theme.return_value = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "composite", "change_score", "rank"]
        )
        conn = MagicMock()
        with patch.dict("os.environ", {"NTFY_TOPIC": "test-topic"}):
            send_alerts(conn, "2026-07-17")
        mock_post.assert_not_called()

    @patch("src.alerts.get_scan_history")
    def test_skips_without_topic(self, mock_sector):
        conn = MagicMock()
        with patch.dict("os.environ", {}, clear=True):
            send_alerts(conn, "2026-07-17")
        mock_sector.assert_not_called()
