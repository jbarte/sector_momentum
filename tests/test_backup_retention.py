"""Retention policy for the `db-backups` bucket.

Growth is quadratic (every object is a full dump of a monotonically growing
DB — see BACKLOG.md, "Nothing prunes the backup bucket") even though nothing
warns as it fills, and an upload failure is deliberately swallowed as
non-fatal by scan.py. Measured 2026-08-30 against production: 8.5 MB, ~0.85%
of the 1 GB free tier, modeled to reach it around 2028-06 — no fire today, but
the policy needs to exist before it becomes one.

Policy (as specified in the backlog item): keep every daily backup for the
last RETENTION_DAILY_DAYS days, then one per calendar week for the next
RETENTION_WEEKLY_WEEKS weeks, then one per calendar month beyond that.
Everything else is pruned.

This module is pure — no network, no Storage client — so the policy itself is
exhaustively tested without touching the real bucket. `prune_storage_backups`
(src/backup.py) is the thin, mostly-untested wiring that calls this and then
`storage_backup.delete`.
"""
from datetime import datetime, timedelta, timezone

import pytest

from src.backup import (
    RETENTION_DAILY_DAYS,
    RETENTION_WEEKLY_WEEKS,
    parse_backup_timestamp,
    select_objects_to_prune,
)

_NOW = datetime(2026, 8, 30, 6, 0, 0, tzinfo=timezone.utc)


def _name(dt: datetime) -> str:
    return f"backup_{dt.strftime('%Y-%m-%dT%H-%M-%SZ')}.zip"


def _days_ago(n: int, hour: int = 6) -> datetime:
    return (_NOW - timedelta(days=n)).replace(hour=hour, minute=0, second=0, microsecond=0)


# --- parse_backup_timestamp --------------------------------------------

def test_parses_a_well_formed_backup_name():
    dt = parse_backup_timestamp("backup_2026-08-29T12-41-44Z.zip")
    assert dt == datetime(2026, 8, 29, 12, 41, 44, tzinfo=timezone.utc)


@pytest.mark.parametrize("name", [
    "not-a-backup.txt",
    "backup_garbage.zip",
    "backup_2026-08-29T12-41-44Z.txt",  # wrong extension
    "BACKUP_2026-08-29T12-41-44Z.zip",  # wrong case
    "",
])
def test_unparseable_names_return_none_rather_than_raise(name):
    assert parse_backup_timestamp(name) is None


# --- select_objects_to_prune: safety-critical properties ----------------

def test_an_empty_bucket_prunes_nothing():
    assert select_objects_to_prune([], now=_NOW) == []


def test_a_name_that_does_not_parse_is_never_selected_for_deletion():
    """Fail safe: an object this policy cannot date must never be a deletion
    candidate, however old the rest of the bucket looks."""
    names = ["not-a-backup.txt", "README.md", _name(_days_ago(200))]
    pruned = select_objects_to_prune(names, now=_NOW)
    assert "not-a-backup.txt" not in pruned
    assert "README.md" not in pruned


def test_everything_within_the_daily_window_is_kept():
    names = [_name(_days_ago(d)) for d in range(RETENTION_DAILY_DAYS)]
    assert select_objects_to_prune(names, now=_NOW) == []


def test_the_daily_window_boundary_is_inclusive():
    """An object exactly RETENTION_DAILY_DAYS old is still a daily keep, not
    the first day handed to the weekly tier — an off-by-one here silently
    starts thinning backups a day early forever."""
    edge = _name(_days_ago(RETENTION_DAILY_DAYS))
    assert edge not in select_objects_to_prune([edge], now=_NOW)


def test_multiple_same_day_backups_within_the_daily_window_are_all_kept():
    """Duplicate-scan days exist (see BACKLOG.md, "Three of seven weekly
    scans still persist duplicate scores/signals") -- the daily tier means
    every backup from a recent day, not a dedup to one per day."""
    day = _days_ago(3)
    a, b = _name(day.replace(hour=6)), _name(day.replace(hour=18))
    assert select_objects_to_prune([a, b], now=_NOW) == []


def test_weekly_tier_keeps_one_object_per_calendar_week():
    """Two backups in the same week, past the daily window: only the older
    of the pair should be pruned, one survives."""
    week_start = _days_ago(RETENTION_DAILY_DAYS + 5)
    a = _name(week_start)
    b = _name(week_start + timedelta(days=1))
    pruned = select_objects_to_prune([a, b], now=_NOW)
    assert len(pruned) == 1
    assert set(pruned) < {a, b}


def test_weekly_tier_keeps_the_newest_object_in_each_week():
    """Not just "one" — the newest, so the kept backup is as fresh as the
    bucket allows for that week."""
    week_start = _days_ago(RETENTION_DAILY_DAYS + 5)
    older, newer = _name(week_start), _name(week_start + timedelta(days=2))
    pruned = select_objects_to_prune([older, newer], now=_NOW)
    assert pruned == [older]


def test_distinct_weeks_in_the_weekly_tier_each_keep_their_own_backup():
    d1 = _name(_days_ago(RETENTION_DAILY_DAYS + 3))
    d2 = _name(_days_ago(RETENTION_DAILY_DAYS + 10))
    assert select_objects_to_prune([d1, d2], now=_NOW) == []


def test_monthly_tier_keeps_one_object_per_calendar_month():
    beyond_weekly = RETENTION_DAILY_DAYS + RETENTION_WEEKLY_WEEKS * 7 + 10
    month_pos = _days_ago(beyond_weekly)
    a = _name(month_pos)
    b = _name(month_pos + timedelta(days=3))
    pruned = select_objects_to_prune([a, b], now=_NOW)
    assert len(pruned) == 1
    assert set(pruned) < {a, b}


def test_monthly_tier_keeps_the_newest_object_in_each_month():
    beyond_weekly = RETENTION_DAILY_DAYS + RETENTION_WEEKLY_WEEKS * 7 + 10
    month_pos = _days_ago(beyond_weekly)
    older, newer = _name(month_pos), _name(month_pos + timedelta(days=5))
    pruned = select_objects_to_prune([older, newer], now=_NOW)
    assert pruned == [older]


def test_distinct_months_in_the_monthly_tier_each_keep_their_own_backup():
    beyond_weekly = RETENTION_DAILY_DAYS + RETENTION_WEEKLY_WEEKS * 7 + 5
    a = _name(_days_ago(beyond_weekly))
    b = _name(_days_ago(beyond_weekly + 40))  # a different month
    assert select_objects_to_prune([a, b], now=_NOW) == []


def test_a_realistic_daily_history_prunes_only_the_older_tiers():
    """One backup a day for a full year: the daily window survives entirely,
    the weekly/monthly tiers collapse to roughly one-per-bucket, and nothing
    outside the parseable set is ever touched."""
    names = [_name(_days_ago(d)) for d in range(365)]
    pruned = set(select_objects_to_prune(names, now=_NOW))
    kept = set(names) - pruned
    for d in range(RETENTION_DAILY_DAYS):
        assert _name(_days_ago(d)) in kept, f"day {d} in the daily window was pruned"
    # Sanity on scale: a year of daily backups should collapse to roughly
    # 14 daily + ~8 weekly + ~4 monthly, not "most of them kept".
    assert len(kept) < 40, f"retention barely reduced anything: kept {len(kept)}/365"


def test_result_never_includes_duplicates():
    names = [_name(_days_ago(d)) for d in range(100)]
    pruned = select_objects_to_prune(names, now=_NOW)
    assert len(pruned) == len(set(pruned))


def test_prune_list_is_a_subset_of_the_input():
    """Never invent a name to delete that wasn't in the input."""
    names = [_name(_days_ago(d)) for d in range(100)] + ["ignored.txt"]
    pruned = select_objects_to_prune(names, now=_NOW)
    assert set(pruned) <= set(names)


# --- prune_storage_backups: thin wiring, mocked -------------------------

def test_prune_storage_backups_lists_selects_and_deletes(monkeypatch):
    """A lone old object is exactly what the monthly tier is FOR (the sole
    checkpoint for its month) and must survive alone — so this needs a SECOND,
    older-still object in the same monthly bucket to have anything prunable
    at all, real policy semantics, not a made-up shortcut."""
    from src import backup as backup_mod

    listed = ["backup_2020-01-01T00-00-00Z.zip",  # older in its month -> pruned
             "backup_2020-01-15T00-00-00Z.zip",  # newest in its month -> kept
             "backup_2026-08-29T12-41-44Z.zip"]  # inside the daily window -> kept
    calls = {}

    monkeypatch.setattr(backup_mod.storage_backup, "list_objects",
                        lambda bucket=None: listed)
    monkeypatch.setattr(backup_mod.storage_backup, "delete",
                        lambda names, bucket=None: calls.update(names=names, bucket=bucket))

    result = backup_mod.prune_storage_backups(bucket="db-backups")

    assert result == ["backup_2020-01-01T00-00-00Z.zip"]
    assert calls["names"] == ["backup_2020-01-01T00-00-00Z.zip"]
    assert calls["bucket"] == "db-backups"


def test_prune_storage_backups_with_nothing_to_prune_still_calls_delete_safely(monkeypatch):
    """delete([]) is itself a documented no-op — confirms the wiring doesn't
    special-case an empty result before handing it off."""
    from src import backup as backup_mod

    monkeypatch.setattr(backup_mod.storage_backup, "list_objects",
                        lambda bucket=None: ["backup_2026-08-29T12-41-44Z.zip"])
    deleted_with = []
    monkeypatch.setattr(backup_mod.storage_backup, "delete",
                        lambda names, bucket=None: deleted_with.append(names))

    assert backup_mod.prune_storage_backups() == []
    assert deleted_with == [[]]
