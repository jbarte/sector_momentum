"""Per-user alert composition — Exit for held items, Entry for everything.

Pure functions: no DB, no network. The scan supplies the events (from
src.alerts.detect_badge_events), all positions, and all enabled prefs; this
module decides who is told what and renders each message body.
"""
from __future__ import annotations


def _position_key(item_type: str, region: str, name: str) -> str:
    return f"{item_type}|{region}|{name}"


def _event_position_key(event: dict) -> str:
    """Map an alert event onto the positions-table key space.

    Events carry cohort = "US"/"EU" for sectors and "THEME" for themes;
    positions carry item_type ("sector"/"theme") + region ("" for themes).
    """
    cohort = event.get("cohort", "")
    if cohort == "THEME":
        return _position_key("theme", "", event["sector"])
    return _position_key("sector", cohort, event["sector"])


def _format_event(event: dict, held: bool) -> str:
    label = "▲ Entry" if event.get("event") == "entry" else "▼ Exit"
    region = "" if event.get("cohort") == "THEME" else f" ({event.get('cohort', '')})"
    rank = f" (rank {event['rank']})" if event.get("rank") is not None else ""
    star = " ★" if held else ""
    return f"  {label}: {event['sector']}{region}{rank}{star}"


def format_personal_body(held_exits: list[dict], entries: list[dict]) -> str:
    """Group by held-vs-discovery.

    Deliberately different from src.alerts.format_alert_body, which groups by
    cohort — here the useful split is "act on this" vs "consider this", so the
    region is rendered inline instead of as a group header.
    """
    lines: list[str] = []
    if held_exits:
        lines.append("Your holdings")
        for ev in held_exits:
            lines.append(_format_event(ev, held=True))
        lines.append("")
    if entries:
        lines.append("New signals")
        for ev in entries:
            lines.append(_format_event(ev, held=False))
        lines.append("")
    return "\n".join(lines).rstrip()


def build_personal_alerts(
    events: list[dict],
    positions: list[dict],
    prefs: list[dict],
    scan_date: str,
) -> list[dict]:
    """Return one payload per user to notify: {user_id, topic, title, body}.

    Exit events are included only for users holding that item; Entry events go
    to every enabled user. A user with neither gets no payload at all, which
    preserves the existing "no events, no noise" rule.
    """
    held_by_user: dict[str, set[str]] = {}
    for p in positions:
        uid = p.get("user_id")
        if not uid:
            continue
        held_by_user.setdefault(uid, set()).add(
            _position_key(p.get("item_type", ""), p.get("region", "") or "", p.get("name", ""))
        )

    entries = [e for e in events if e.get("event") == "entry"]
    exits = [e for e in events if e.get("event") == "exit"]

    out: list[dict] = []
    for pref in prefs:
        # get_alert_prefs already filters to enabled rows; re-checked here because
        # this function is pure and tested independently of that query.
        if not pref.get("enabled", True):
            continue
        topic = pref.get("ntfy_topic")
        uid = pref.get("user_id")
        if not topic or not uid:
            continue

        held = held_by_user.get(uid, set())
        held_exits = [e for e in exits if _event_position_key(e) in held]
        if not held_exits and not entries:
            continue

        out.append({
            "user_id": uid,
            "topic": topic,
            "title": f"Sector Momentum — {scan_date}",
            "body": format_personal_body(held_exits, entries),
        })
    return out
