"""Threshold alerts — notify on Entry/Exit badge appearances after each scan."""
from __future__ import annotations

import json
import logging
import os
import urllib.request
import urllib.error

import pandas as pd

from dashboard.rows import _compute_rank_trajectories, _compute_setup, _safe_float
from src.universe import is_unbuyable
from src.state import (
    get_theme_scan_history, get_all_positions, get_alert_prefs,
    get_stop_loss_users, get_position_stops, insert_position_stop,
    upsert_position_stop_distance,
)
from src.personal_alerts import build_personal_alerts

logger = logging.getLogger(__name__)

TRAJECTORY_WINDOW = 5


def detect_badge_events(history_df: pd.DataFrame,
                        themes_cfg: dict | None = None) -> list[dict]:
    """Detect Entry/Exit setup badges in the latest scan.

    Computes rank trajectories over the last 5 scans and evaluates each
    sector/theme in the latest scan for an Entry or Exit badge.

    Works on any DataFrame with the standard scan history columns:
    scan_id, region, gics_sector, composite, change_score, rank.
    Returns [] if fewer than 2 scans exist (trajectory needs history).

    `themes_cfg` suppresses ENTRY events for themes with no route to
    purchase. A push notification is the most direct buy prompt the product
    has, so leaving it unfiltered while the leaderboard hides the badge would
    just move the defect to the loudest channel. Exit events are unaffected —
    they only ever go to users who marked the item as held.
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
            _compute_setup(row_dict, universe_size=len(latest))
            return row_dict["setup"]

        setup = _setup(_safe_float(row_data.get("rank")))
        was = _setup(_safe_float(prev_rank.get((region, sector))))

        # Alert on CROSSINGS, not on membership. The band rule means every
        # held name reads "entry" every single scan; emitting that would be a
        # daily email listing the same positions, which is the churn this
        # whole change exists to remove. A name that was already in the band
        # last scan is not news.
        if setup == "entry" and is_unbuyable(region, sector, themes_cfg):
            continue
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
                lines.append(f"  ▲ Enter: {ev['sector']}{rank_info}")
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


def send_personal_alerts(conn, scan_date: str, events: list[dict],
                         prefs: list[dict],
                         stops_by_user: dict[str, list[dict]] | None = None) -> None:
    """Fan out per-user alerts. Non-fatal, and isolated per user.

    `prefs` is fetched once by the caller (send_alerts) and passed in here —
    do not re-fetch it. Never logs a topic — it is the user's secret;
    failures are logged by user id.
    """
    if not prefs:
        return
    try:
        positions = get_all_positions(conn)
        payloads = build_personal_alerts(events, positions, prefs, scan_date,
                                         stops_by_user)
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


def _load_themes_cfg() -> dict:
    """config/themes.yaml, or {} if it cannot be read.

    Fail-open: without it, unbuyable themes stop being suppressed, which is the
    pre-2026-08-10 behaviour — worse than ideal but not worse than an alert
    pipeline that raises.
    """
    try:
        import yaml
        from pathlib import Path as _Path
        path = _Path(__file__).resolve().parent.parent / "config" / "themes.yaml"
        return yaml.safe_load(path.read_text()) or {}
    except Exception as exc:
        logger.warning("themes.yaml unreadable (%s) — unbuyable filter off", exc)
        return {}


def collect_stop_events(conn, prices: dict, themes_cfg: dict) -> dict[str, list[dict]]:
    """Evaluate trailing stops for opted-in users; return NEW events per user.

    Returns only breaches that are both new (no latch row yet) and fresh
    (`stopped_on >= stop_loss_since`). Every new breach gets a latch row
    regardless — an older one with `notified = False`, so the dashboard shows
    it while the phone stays quiet. See the day-one guard in
    scripts/stop_loss_pref_migration.sql.

    Fail-open in every direction: this runs inside the scan, and no stop is
    worth taking a scan down for. A missing position_stops table (code merged
    before the migration is applied) rolls back and returns nothing.

    Also upserts a LIVE reading (position_stop_distance) for every
    evaluated, not-yet-breached position -- see
    sector_momentum-notes/specs/2026-09-11-stop-distance-indicator-design.md.
    Skipped for an already-latched position (the `if key in latched`
    guard above runs before this), since the chip owns that row from here on.
    """
    from src.horizons import trailing_stop_frac
    from src.stops import evaluate_stop, ticker_for

    try:
        users = {u["user_id"]: u for u in get_stop_loss_users(conn)}
        if not users:
            return {}
        positions = get_all_positions(conn)
        latched = {(s["user_id"], s["item_type"], s.get("region") or "", s["name"])
                   for s in get_position_stops(conn)}
    except Exception as exc:
        logger.warning("Stop evaluation skipped: %s", exc)
        # A failed SELECT aborts the transaction; roll back so the connection
        # stays usable for the alert queries that follow.
        try:
            conn.rollback()
        except Exception:
            logger.debug("Rollback after stop-query failure also failed", exc_info=True)
        return {}

    stop_frac = trailing_stop_frac()
    out: dict[str, list[dict]] = {}

    for pos in positions:
        # Isolated per position, matching send_personal_alerts's per-user
        # try/except: one position with missing/malformed data (or any other
        # unexpected error, e.g. a tz mismatch evaluate_stop didn't already
        # guard against) must not take down evaluation for every other
        # position, nor the pre-existing Entry/Exit alerts that run after
        # this in send_alerts.
        try:
            uid = pos.get("user_id")
            user = users.get(uid)
            if user is None:
                continue
            region = pos.get("region") or ""
            key = (uid, pos.get("item_type"), region, pos.get("name"))
            if key in latched:
                continue

            ticker = ticker_for(pos.get("item_type", ""), pos.get("name", ""), themes_cfg)
            if not ticker:
                continue
            res = evaluate_stop(prices.get(ticker), pos.get("created_at"), stop_frac)
            if res is None:
                continue

            # Written regardless of breach -- this is the live reading the
            # stop-distance indicator reads. Its own try/except, isolated
            # from the breach-alert path below: a write failure here must
            # not swallow this position's own breach alert if it is ALSO
            # breaching this scan.
            try:
                upsert_position_stop_distance(
                    conn, user_id=uid, item_type=pos["item_type"], region=region,
                    name=pos["name"], as_of=res["stopped_on"], peak_price=res["peak"],
                    peak_on=res["peak_on"], latest_price=res["latest"],
                    drawdown=res["drawdown"])
            except Exception as exc:
                logger.warning("Stop-distance write failed for %s: %s", uid, exc)

            if not res["breached"]:
                continue

            since = user.get("stop_loss_since")
            fresh = since is None or res["stopped_on"] >= pd.Timestamp(since).date()
            try:
                insert_position_stop(
                    conn, user_id=uid, item_type=pos["item_type"], region=region,
                    name=pos["name"], stopped_on=res["stopped_on"],
                    peak_price=res["peak"], peak_on=res["peak_on"],
                    drawdown=res["drawdown"], notified=bool(fresh))
            except Exception as exc:
                logger.warning("Stop latch write failed for %s: %s", uid, exc)
                continue

            if fresh:
                out.setdefault(uid, []).append({
                    "item_type": pos["item_type"], "region": region,
                    "name": pos["name"], "drawdown": res["drawdown"],
                    "peak": res["peak"], "peak_on": res["peak_on"],
                })
        except Exception as exc:
            logger.warning(
                "Stop evaluation failed for position %s/%s (user %s): %s",
                pos.get("item_type"), pos.get("name"), pos.get("user_id"), exc)
            continue

    return out


def send_alerts(conn, scan_date: str, prices: dict | None = None) -> None:
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

    # ONE cohort, ONE query. Until 2026-09-07 this also called
    # `get_scan_history(conn, n_scans=TRAJECTORY_WINDOW)` into a variable
    # named `sector_history` and ran detection over both -- a fossil from
    # when US/EU sectors were a separate cohort. `get_theme_scan_history` IS
    # `get_scan_history(regions=("THEME",))`, and DEFAULT_REGIONS became
    # ("THEME",) when the sector cohorts were retired on 2026-08-05, so the
    # two calls had silently become identical and every event was detected
    # and appended TWICE. Every push notification went out double-length for
    # a month; the tests missed it because they mock the two functions
    # separately and gave the theme one an empty frame, which is a shape
    # production never has. See test_events_are_not_duplicated_across_cohorts.
    theme_history = get_theme_scan_history(conn, n_scans=TRAJECTORY_WINDOW)

    # Read here rather than threaded in from the caller: send_alerts is
    # invoked from scan.py and from CI, and a missing config must degrade to
    # "no suppression", never to a crash mid-scan.
    themes_cfg = _load_themes_cfg()

    events = detect_badge_events(theme_history, themes_cfg)

    if topic:
        if events:
            title = f"ETF Momentum — {scan_date}"
            body = format_alert_body(events)
            post_ntfy(topic, title, body)
            logger.info(
                "Alert sent: %d event(s) to ntfy topic '%s'.", len(events), topic
            )
        else:
            logger.info("No Entry/Exit badges — skipping alert.")

    # Stops are evaluated only when the caller supplied prices — the scan
    # always does. The optional parameter keeps every existing two-argument
    # caller (and every existing test) working unchanged.
    stops_by_user = collect_stop_events(conn, prices, themes_cfg) if prices else {}
    send_personal_alerts(conn, scan_date, events, prefs, stops_by_user)
