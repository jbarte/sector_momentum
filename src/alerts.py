"""Threshold alerts — notify on Entry/Exit badge appearances after each scan."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

import pandas as pd

from dashboard.rows import _compute_rank_trajectories, _compute_setup, _safe_float
from src.state import get_scan_history, get_theme_scan_history

logger = logging.getLogger(__name__)

TRAJECTORY_WINDOW = 5


def detect_badge_events(history_df: pd.DataFrame) -> list[dict]:
    """Detect Entry/Exit setup badges in the latest scan.

    Computes rank trajectories over the last 5 scans and evaluates each
    sector/theme in the latest scan for an Entry or Exit badge.

    Works on any DataFrame with the standard scan history columns:
    scan_id, region, gics_sector, composite, change_score, rank.
    Returns [] if fewer than 2 scans exist (trajectory needs history).
    """
    if history_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) < 2:
        return []

    trajectories = _compute_rank_trajectories(history_df)

    latest_id = scan_ids[-1]
    latest = history_df[history_df["scan_id"] == latest_id]

    events: list[dict] = []
    for _, row_data in latest.iterrows():
        region = row_data["region"]
        sector = row_data["gics_sector"]
        sk = f"{region}|{sector}"

        traj = trajectories.get(sk, {"state": "flat"})

        row_dict = {
            "_raw_composite": _safe_float(row_data.get("composite")),
            "_raw_change": _safe_float(row_data.get("change_score")),
            "trajectory_state": traj["state"],
        }
        _compute_setup(row_dict)
        setup = row_dict["setup"]

        if setup in ("entry", "exit"):
            events.append({
                "cohort": region,
                "sector": sector,
                "event": setup,
                "rank": int(row_data["rank"]) if pd.notna(row_data["rank"]) else None,
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
            rank_info = f" (rank {ev['rank']})" if ev["rank"] is not None else ""
            if ev["event"] == "entry":
                lines.append(f"  ▲ Entry: {ev['sector']}{rank_info}")
            else:
                lines.append(f"  ▼ Exit: {ev['sector']}{rank_info}")
        lines.append("")

    return "\n".join(lines).rstrip()


def post_ntfy(topic: str, title: str, body: str) -> None:
    """POST a notification to ntfy.sh using the JSON API."""
    url = f"https://ntfy.sh/"
    payload = json.dumps({
        "topic": topic,
        "title": title,
        "message": body,
        "markdown": True,
        "tags": ["chart_with_upwards_trend"],
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        resp.read()


def send_alerts(conn, scan_date: str) -> None:
    """Detect Entry/Exit badge events and send a ntfy notification if any."""
    topic = os.environ.get("NTFY_TOPIC")
    if not topic:
        return

    sector_history = get_scan_history(conn, n_scans=TRAJECTORY_WINDOW)
    theme_history = get_theme_scan_history(conn, n_scans=TRAJECTORY_WINDOW)

    events = detect_badge_events(sector_history)
    events.extend(detect_badge_events(theme_history))

    if not events:
        logger.info("No Entry/Exit badges — skipping alert.")
        return

    title = f"Sector Momentum — {scan_date}"
    body = format_alert_body(events)
    post_ntfy(topic, title, body)
    logger.info("Alert sent: %d event(s) to ntfy topic '%s'.", len(events), topic)
