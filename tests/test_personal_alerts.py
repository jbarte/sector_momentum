"""Tests for src.personal_alerts — per-user Exit-for-held + Entry-for-all."""
from __future__ import annotations

from src.personal_alerts import build_personal_alerts, format_personal_body

DATE = "2026-07-31"


def _entry(cohort, name, rank=3):
    return {"cohort": cohort, "sector": name, "event": "entry", "rank": rank}


def _exit(cohort, name, rank=7):
    return {"cohort": cohort, "sector": name, "event": "exit", "rank": rank}


def _pos(user, item_type, region, name):
    return {"user_id": user, "item_type": item_type, "region": region, "name": name}


def _pref(user, topic="sm-abc", enabled=True):
    return {"user_id": user, "ntfy_topic": topic, "enabled": enabled}


class TestSelection:
    def test_exit_included_when_held(self):
        out = build_personal_alerts(
            [_exit("US", "Energy")],
            [_pos("u1", "sector", "US", "Energy")],
            [_pref("u1")], DATE)
        assert len(out) == 1
        assert "Energy" in out[0]["body"]
        assert "Your holdings" in out[0]["body"]

    def test_exit_excluded_when_not_held(self):
        # Only an unheld Exit and no Entries -> nothing to say -> no message.
        out = build_personal_alerts(
            [_exit("US", "Energy")],
            [_pos("u1", "sector", "US", "Utilities")],
            [_pref("u1")], DATE)
        assert out == []

    def test_entry_included_for_every_user_regardless_of_holdings(self):
        out = build_personal_alerts(
            [_entry("US", "Health Care")], [], [_pref("u1"), _pref("u2", "sm-def")], DATE)
        assert len(out) == 2
        for msg in out:
            assert "Health Care" in msg["body"]
            assert "New signals" in msg["body"]

    def test_no_message_when_nothing_applies(self):
        """No-noise rule: unheld Exit only, no Entries -> no payload at all."""
        out = build_personal_alerts(
            [_exit("EU", "Banks")], [], [_pref("u1")], DATE)
        assert out == []

    def test_no_events_at_all(self):
        assert build_personal_alerts([], [], [_pref("u1")], DATE) == []

    def test_disabled_user_skipped(self):
        out = build_personal_alerts(
            [_entry("US", "Energy")], [], [_pref("u1", enabled=False)], DATE)
        assert out == []

    def test_user_without_prefs_gets_nothing(self):
        out = build_personal_alerts(
            [_entry("US", "Energy")], [_pos("u9", "sector", "US", "Energy")], [], DATE)
        assert out == []

    def test_pref_without_topic_skipped(self):
        out = build_personal_alerts(
            [_entry("US", "Energy")], [], [{"user_id": "u1", "ntfy_topic": "", "enabled": True}], DATE)
        assert out == []


class TestMatching:
    def test_theme_event_matches_theme_position_with_empty_region(self):
        out = build_personal_alerts(
            [_exit("THEME", "AI")],
            [_pos("u1", "theme", "", "AI")],
            [_pref("u1")], DATE)
        assert len(out) == 1
        assert "AI" in out[0]["body"]

    def test_region_discriminates_same_named_sector(self):
        """Holding US Energy must NOT match an EU Energy exit."""
        out = build_personal_alerts(
            [_exit("EU", "Energy")],
            [_pos("u1", "sector", "US", "Energy")],
            [_pref("u1")], DATE)
        assert out == []

    def test_item_type_discriminates_same_name(self):
        """Holding the *theme* 'Energy' must not match a *sector* Energy exit."""
        out = build_personal_alerts(
            [_exit("US", "Energy")],
            [_pos("u1", "theme", "", "Energy")],
            [_pref("u1")], DATE)
        assert out == []


class TestPerUserIsolation:
    def test_two_users_get_different_bodies(self):
        events = [_exit("US", "Energy"), _exit("EU", "Banks"), _entry("US", "Health Care")]
        positions = [
            _pos("u1", "sector", "US", "Energy"),
            _pos("u2", "sector", "EU", "Banks"),
        ]
        out = build_personal_alerts(events, positions, [_pref("u1"), _pref("u2", "sm-def")], DATE)
        by_user = {m["user_id"]: m for m in out}
        assert "Energy" in by_user["u1"]["body"]
        assert "Banks" not in by_user["u1"]["body"]
        assert "Banks" in by_user["u2"]["body"]
        assert "Energy" not in by_user["u2"]["body"]
        # Entry reaches both
        assert "Health Care" in by_user["u1"]["body"]
        assert "Health Care" in by_user["u2"]["body"]
        # Topics are per-user
        assert by_user["u1"]["topic"] == "sm-abc"
        assert by_user["u2"]["topic"] == "sm-def"


class TestPayload:
    def test_title_uses_scan_date(self):
        out = build_personal_alerts([_entry("US", "Energy")], [], [_pref("u1")], DATE)
        assert out[0]["title"] == f"ETF Momentum — {DATE}"


class TestFormatting:
    def test_held_section_first_and_starred(self):
        body = format_personal_body([_exit("US", "Energy")], [_entry("US", "Health Care")])
        assert body.index("Your holdings") < body.index("New signals")
        held_line = [l for l in body.splitlines() if "Energy" in l][0]
        assert "★" in held_line
        assert "▼ Exit" in held_line

    def test_sections_omitted_when_empty(self):
        only_entries = format_personal_body([], [_entry("US", "Energy")])
        assert "Your holdings" not in only_entries
        assert "New signals" in only_entries

        only_held = format_personal_body([_exit("US", "Energy")], [])
        assert "Your holdings" in only_held
        assert "New signals" not in only_held

    def test_region_inline_for_sectors_and_absent_for_themes(self):
        body = format_personal_body([], [_entry("US", "Energy"), _entry("THEME", "AI")])
        energy = [l for l in body.splitlines() if "Energy" in l][0]
        ai = [l for l in body.splitlines() if "AI" in l][0]
        assert "(US)" in energy
        # Strip the rank parenthetical, then assert no region parenthetical remains.
        ai_no_rank = ai.replace("(rank 3)", "").strip()
        assert "(" not in ai_no_rank

    def test_rank_omitted_when_none(self):
        body = format_personal_body([], [{"cohort": "US", "sector": "Energy",
                                          "event": "entry", "rank": None}])
        assert "rank" not in body
