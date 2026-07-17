"""Tests for src.alerts — threshold alert event detection and formatting."""
from __future__ import annotations

from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

from src.alerts import (
    RANK_THRESHOLD,
    detect_top_n_events,
    format_alert_body,
    send_alerts,
)


def _history(rows: list[tuple]) -> pd.DataFrame:
    """Build a minimal history DataFrame from (scan_id, region, sector, rank)."""
    return pd.DataFrame(rows, columns=["scan_id", "region", "gics_sector", "rank"])


class TestDetectTopNEvents:
    def test_entry_event(self):
        df = _history([
            (1, "US", "Energy", 4),
            (1, "US", "Tech", 1),
            (2, "US", "Energy", 2),
            (2, "US", "Tech", 1),
        ])
        events = detect_top_n_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "entry"
        assert events[0]["sector"] == "Energy"
        assert events[0]["rank"] == 2

    def test_exit_event(self):
        df = _history([
            (1, "US", "Energy", 2),
            (1, "US", "Tech", 1),
            (2, "US", "Energy", 5),
            (2, "US", "Tech", 1),
        ])
        events = detect_top_n_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "exit"
        assert events[0]["sector"] == "Energy"
        assert events[0]["rank"] == 2

    def test_no_change(self):
        df = _history([
            (1, "US", "Energy", 1),
            (1, "US", "Tech", 2),
            (2, "US", "Energy", 2),
            (2, "US", "Tech", 1),
        ])
        events = detect_top_n_events(df)
        assert events == []

    def test_empty_dataframe(self):
        df = pd.DataFrame(columns=["scan_id", "region", "gics_sector", "rank"])
        assert detect_top_n_events(df) == []

    def test_single_scan(self):
        df = _history([(1, "US", "Energy", 1)])
        assert detect_top_n_events(df) == []

    def test_new_sector_entering_top_n(self):
        df = _history([
            (1, "US", "Energy", 1),
            (2, "US", "Energy", 1),
            (2, "US", "Tech", 3),
        ])
        events = detect_top_n_events(df)
        assert len(events) == 1
        assert events[0]["event"] == "entry"
        assert events[0]["sector"] == "Tech"

    def test_themes_cohort(self):
        df = _history([
            (1, "THEME", "Uranium", 5),
            (2, "THEME", "Uranium", 1),
        ])
        events = detect_top_n_events(df)
        assert len(events) == 1
        assert events[0]["cohort"] == "THEME"
        assert events[0]["event"] == "entry"

    def test_multiple_events(self):
        df = _history([
            (1, "US", "Energy", 1),
            (1, "US", "Tech", 4),
            (2, "US", "Energy", 6),
            (2, "US", "Tech", 2),
        ])
        events = detect_top_n_events(df)
        assert len(events) == 2
        event_types = {(e["sector"], e["event"]) for e in events}
        assert ("Tech", "entry") in event_types
        assert ("Energy", "exit") in event_types


class TestFormatAlertBody:
    def test_grouped_output(self):
        events = [
            {"cohort": "US", "sector": "Energy", "event": "entry", "rank": 2},
            {"cohort": "EU", "sector": "Tech", "event": "exit", "rank": 1},
            {"cohort": "THEME", "sector": "Uranium", "event": "entry", "rank": 1},
        ]
        body = format_alert_body(events)
        assert "Sectors — US" in body
        assert "Sectors — EU" in body
        assert "Themes" in body
        assert "▲ Energy entered top 3" in body
        assert "▼ Tech exited top 3" in body
        assert "▲ Uranium entered top 3" in body

    def test_empty_events(self):
        assert format_alert_body([]) == ""


class TestSendAlerts:
    @patch("src.alerts.post_ntfy")
    @patch("src.alerts.get_theme_scan_history")
    @patch("src.alerts.get_scan_history")
    def test_sends_on_events(self, mock_sector, mock_theme, mock_post):
        mock_sector.return_value = _history([
            (1, "US", "Energy", 4),
            (2, "US", "Energy", 2),
        ])
        mock_theme.return_value = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "rank"]
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
    def test_no_notification_on_no_events(self, mock_sector, mock_theme, mock_post):
        mock_sector.return_value = _history([
            (1, "US", "Energy", 1),
            (2, "US", "Energy", 1),
        ])
        mock_theme.return_value = pd.DataFrame(
            columns=["scan_id", "region", "gics_sector", "rank"]
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
