"""Schema-coverage guards for src/state.py.

The same-day replace path in save_scan deletes child rows before deleting
the scans row. If a new table with a scan_id FK is added to the DDL but not
to _SCAN_CHILD_TABLES, the delete hits a ForeignKeyViolation in production
(this happened with theme_sentiment_signals on 2026-07-19).
"""

import re

from src.state import _DDL_STATEMENTS, _SCAN_CHILD_TABLES


def _tables_referencing_scans() -> set[str]:
    tables = set()
    for ddl in _DDL_STATEMENTS:
        if "REFERENCES scans" not in ddl:
            continue
        m = re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", ddl)
        assert m, f"Could not parse table name from DDL: {ddl[:80]}"
        tables.add(m.group(1))
    return tables


def test_scan_child_tables_covers_every_fk_table():
    """Every table with a scan_id FK must be in the replace-delete list."""
    assert _tables_referencing_scans() == set(_SCAN_CHILD_TABLES)


def test_scan_child_tables_has_no_stale_entries():
    """No delete-list entry may name a table missing from the DDL."""
    ddl_tables = {
        re.search(r"CREATE TABLE IF NOT EXISTS (\w+)", ddl).group(1)
        for ddl in _DDL_STATEMENTS
    }
    for child in _SCAN_CHILD_TABLES:
        assert child in ddl_tables, f"{child} not present in DDL"
