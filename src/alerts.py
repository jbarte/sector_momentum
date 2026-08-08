"""Threshold alerts — notify on Entry/Exit badge appearances after each scan."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

import pandas as pd

from dashboard.rows import _compute_rank_trajectories, _compute_setup, _safe_float
from src.state import (
    get_scan_history, get_theme_scan_history, get_all_positions, get_alert_prefs,
)
from src.personal_alerts import build_personal_alerts

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

    latest_id, prev_id = scan_ids[-1], scan_ids[-2]
    latest = history_df[history_df["scan_id"] == latest_id]
    prev_rank = (history_df[history_df["scan_id"] == prev_id]
                 .set_index(["region", "gics_sector"])["rank"].to_dict())

    events: list[dict] = []
    for _, row_data in latest.iterrows():
        region = row_data["region"]
        sector = row_data["gics_sector"]
        sk = f"{region}|{sector}"

        traj = trajectories.get(sk, {"state": "flat"})

        def _setup(rank):
            row_dict = {
                "_raw_composite": _safe_float(row_data.get("composite")),
                "_raw_change": _safe_float(row_data.get("change_score")),
                "trajectory_state": traj["state"],
                # _compute_setup is a rank band now — without this it silently
                # returns None for every row and no alert is ever sent.
                "rank": rank,
            }
            _compute_setup(row_dict)
            return row_dict["setup"]

        setup = _setup(_safe_float(row_data.get("rank")))
        was = _setup(_safe_float(prev_rank.get((region, sector))))

        # Alert on CROSSINGS, not on membership. The band rule means every
        # held name reads "entry" every single scan; emitting that would be a
        # daily email listing the same positions, which is the churn this
        # whole change exists to remove. A name that was already in the band
        # last scan is not news.
        if setup in ("entry", "exit") and setup != was:
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


def send_personal_alerts(conn, scan_date: str, events: list[dict], prefs: list[dict]) -> None:
    """Fan out per-user alerts. Non-fatal, and isolated per user.

    `prefs` is fetched once by the caller (send_alerts) and passed in here —
    do not re-fetch it. Never logs a topic — it is the user's secret;
    failures are logged by user id.
    """
    if not prefs:
        return
    try:
        positions = get_all_positions(conn)
        payloads = build_personal_alerts(events, positions, prefs, scan_date)
    except Exception as exc:
        logger.warning("Personal alerts skipped: %s", exc)
        # A failed query aborts the transaction; roll back so the
        # connection stays usable for any later caller.
        try:
            conn.rollback()
        except Exception:
            logger.debug("Rollback after alert-prefs failure also failed", exc_info=True)
        return

    sent = 0
    for payload in payloads:
        try:
            post_ntfy(payload["topic"], payload["title"], payload["body"])
            sent += 1
        except Exception as exc:
            logger.warning(
                "Personal alert failed for user %s: %s", payload["user_id"], exc
            )
    if payloads:
        logger.info("Personal alerts sent: %d/%d.", sent, len(payloads))


def send_alerts(conn, scan_date: str) -> None:
    """Send the ops broadcast and per-user personalized alerts."""
    topic = os.environ.get("NTFY_TOPIC")

    # Personal alerts are independent of the broadcast topic, so the early
    # return only applies when there is nothing to deliver either way.
    try:
        prefs = get_alert_prefs(conn)
    except Exception as exc:
        # A failed SELECT aborts the transaction; roll back or every later
        # query on this connection fails too and would take the broadcast down.
        logger.warning("Alert prefs unavailable: %s", exc)
        try:
            conn.rollback()
        except Exception:
            logger.debug("Rollback after alert-prefs failure also failed", exc_info=True)
        prefs = []

    if not topic and not prefs:
        return

    sector_history = get_scan_history(conn, n_scans=TRAJECTORY_WINDOW)
    theme_history = get_theme_scan_history(conn, n_scans=TRAJECTORY_WINDOW)

    events = detect_badge_events(sector_history)
    events.extend(detect_badge_events(theme_history))

    if topic:
        if events:
            title = f"Sector Momentum — {scan_date}"
            body = format_alert_body(events)
            post_ntfy(topic, title, body)
            logger.info(
                "Alert sent: %d event(s) to ntfy topic '%s'.", len(events), topic
            )
        else:
            logger.info("No Entry/Exit badges — skipping alert.")

    send_personal_alerts(conn, scan_date, events, prefs)
