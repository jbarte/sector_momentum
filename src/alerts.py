"""Threshold alerts — notify on top-N rank transitions after each scan."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

import pandas as pd

from src.state import get_scan_history, get_theme_scan_history

logger = logging.getLogger(__name__)

RANK_THRESHOLD = 3


def detect_top_n_events(
    history_df: pd.DataFrame,
    n: int = RANK_THRESHOLD,
) -> list[dict]:
    """Compare the two latest scans and return top-N entry/exit events.

    Works on any DataFrame with columns: scan_id, region, gics_sector, rank.
    Returns [] if fewer than 2 scans exist.
    """
    if history_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) < 2:
        return []

    prev_id, curr_id = scan_ids[-2], scan_ids[-1]
    prev = history_df[history_df["scan_id"] == prev_id]
    curr = history_df[history_df["scan_id"] == curr_id]

    def _top_n(df: pd.DataFrame) -> dict[tuple[str, str], int]:
        top = df[df["rank"] <= n]
        return {
            (row["region"], row["gics_sector"]): int(row["rank"])
            for _, row in top.iterrows()
        }

    prev_top = _top_n(prev)
    curr_top = _top_n(curr)

    events: list[dict] = []
    for key, rank in curr_top.items():
        if key not in prev_top:
            events.append({
                "cohort": key[0],
                "sector": key[1],
                "event": "entry",
                "rank": rank,
            })

    for key, rank in prev_top.items():
        if key not in curr_top:
            events.append({
                "cohort": key[0],
                "sector": key[1],
                "event": "exit",
                "rank": rank,
            })

    return events


def format_alert_body(events: list[dict]) -> str:
    """Format events into a grouped Markdown body."""
    cohort_order = []
    grouped: dict[str, list[dict]] = {}
    for ev in events:
        label = f"Sectors — {ev['cohort']}" if ev["cohort"] != "THEME" else "Themes"
        if label not in grouped:
            cohort_order.append(label)
            grouped[label] = []
        grouped[label].append(ev)

    lines: list[str] = []
    for label in cohort_order:
        lines.append(label)
        for ev in grouped[label]:
            if ev["event"] == "entry":
                lines.append(f"  ▲ {ev['sector']} entered top {RANK_THRESHOLD} (rank {ev['rank']})")
            else:
                lines.append(f"  ▼ {ev['sector']} exited top {RANK_THRESHOLD} (was rank {ev['rank']})")
        lines.append("")

    return "\n".join(lines).rstrip()


def post_ntfy(topic: str, title: str, body: str) -> None:
    """POST a notification to ntfy.sh."""
    url = f"https://ntfy.sh/{topic}"
    data = body.encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Title", title)
    req.add_header("Content-Type", "text/markdown")
    req.add_header("Tags", "chart_with_upwards_trend")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def send_alerts(conn, scan_date: str) -> None:
    """Detect top-N rank transitions and send a ntfy notification if any."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return

    sector_history = get_scan_history(conn, n_scans=2)
    theme_history = get_theme_scan_history(conn, n_scans=2)

    events = detect_top_n_events(sector_history)
    events.extend(detect_top_n_events(theme_history))

    if not events:
        logger.info("No top-%d rank transitions — skipping alert.", RANK_THRESHOLD)
        return

    title = f"Sector Momentum — {scan_date}"
    body = format_alert_body(events)
    post_ntfy(topic, title, body)
    logger.info("Alert sent: %d event(s) to ntfy topic '%s'.", len(events), topic)
