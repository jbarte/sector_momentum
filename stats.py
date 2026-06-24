#!/usr/bin/env python3
"""
stats.py — Data inventory and coverage statistics for the Sector Momentum Scanner.

Connects to Supabase and prints a summary of what data exists in the DB:
scan counts, date range, cadence, per-sector coverage, signal completeness,
and table row counts.

Usage:
    python stats.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).parent))
from src.state import init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hr(char="─", width=60) -> str:
    return char * width


def _section(title: str) -> None:
    print(f"\n{_hr()}")
    print(f"  {title}")
    print(_hr())


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------

def _overall_stats(conn) -> None:
    _section("Scans — overview")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT COUNT(*) AS n_scans,
                   MIN(run_at) AS first_scan,
                   MAX(run_at) AS last_scan
            FROM scans
        """)
        row = cur.fetchone()
    n, first, last = row
    print(f"  Total scans   : {n}")
    print(f"  First scan    : {first}")
    print(f"  Last scan     : {last}")


def _cadence_stats(conn) -> None:
    _section("Cadence — gaps between scans")
    with conn.cursor() as cur:
        cur.execute("SELECT run_at FROM scans ORDER BY run_at ASC")
        rows = [r[0] for r in cur.fetchall()]

    if len(rows) < 2:
        print("  Not enough scans to compute cadence.")
        return

    from datetime import datetime, timezone

    def _parse(ts):
        if hasattr(ts, "timetuple"):
            return ts
        ts = str(ts)
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(ts, fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse timestamp: {ts!r}")

    parsed = [_parse(r) for r in rows]
    gaps = [(parsed[i + 1] - parsed[i]).total_seconds() / 3600 for i in range(len(parsed) - 1)]

    avg_gap = sum(gaps) / len(gaps)
    max_gap = max(gaps)
    min_gap = min(gaps)
    large_gaps = [(parsed[i], parsed[i + 1], gaps[i]) for i, g in enumerate(gaps) if g > 60]

    print(f"  Avg gap       : {avg_gap:.1f} h")
    print(f"  Min gap       : {min_gap:.1f} h")
    print(f"  Max gap       : {max_gap:.1f} h")
    if large_gaps:
        print(f"\n  Gaps > 60 h (possible missed runs):")
        for a, b, g in large_gaps:
            print(f"    {a}  →  {b}  ({g:.0f} h)")
    else:
        print("  No gaps > 60 h detected.")


def _per_region_stats(conn) -> None:
    _section("Coverage — per region")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT s.region,
                   COUNT(DISTINCT s.scan_id) AS n_scans,
                   MIN(sc.run_at)            AS first_scan,
                   MAX(sc.run_at)            AS last_scan
            FROM scores s
            JOIN scans sc ON sc.scan_id = s.scan_id
            GROUP BY s.region
            ORDER BY s.region
        """)
        rows = cur.fetchall()
    if not rows:
        print("  No data.")
        return
    for region, n_scans, first_scan, last_scan in rows:
        print(f"  {region:4s}  {n_scans:3d} scans  {first_scan}  →  {last_scan}")


def _per_sector_stats(conn) -> None:
    _section("Coverage — per sector (scans present in)")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT region,
                   gics_sector,
                   COUNT(DISTINCT scan_id) AS n_scans
            FROM scores
            GROUP BY region, gics_sector
            ORDER BY region, n_scans DESC, gics_sector
        """)
        rows = cur.fetchall()
    if not rows:
        print("  No data.")
        return
    current_region = None
    for region, gics_sector, n_scans in rows:
        if region != current_region:
            current_region = region
            print(f"\n  [{current_region}]")
        print(f"    {gics_sector:<35s}  {n_scans:3d} scans")


def _signal_completeness(conn) -> None:
    _section("Signal completeness — NULL rates per signal")
    with conn.cursor() as cur:
        cur.execute("""
            SELECT signal_name,
                   COUNT(*)                                            AS total,
                   SUM(CASE WHEN raw_value IS NULL THEN 1 ELSE 0 END) AS null_raw,
                   SUM(CASE WHEN z_value   IS NULL THEN 1 ELSE 0 END) AS null_z
            FROM signals
            GROUP BY signal_name
            ORDER BY signal_name
        """)
        rows = cur.fetchall()
    if not rows:
        print("  No data.")
        return
    print(f"  {'Signal':<28s}  {'Total':>6}  {'Null raw':>9}  {'Null z':>7}")
    print(f"  {'-'*28}  {'-'*6}  {'-'*9}  {'-'*7}")
    for signal_name, total, null_raw, null_z in rows:
        null_raw_pct = 100 * null_raw / total if total else 0
        null_z_pct   = 100 * null_z   / total if total else 0
        flag = "  ⚠" if null_raw_pct > 20 else ""
        print(f"  {signal_name:<28s}  {total:>6d}  "
              f"{null_raw:>5d} ({null_raw_pct:4.0f}%)  "
              f"{null_z:>4d} ({null_z_pct:3.0f}%)"
              f"{flag}")


def _table_row_counts(conn) -> None:
    _section("Table row counts")
    with conn.cursor() as cur:
        for tbl in ("scans", "signals", "scores"):
            cur.execute(f"SELECT COUNT(*) FROM {tbl}")
            n = cur.fetchone()[0]
            print(f"  {tbl:<10s}  {n:>8,d} rows")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print(_hr("═"))
    print("  Sector Momentum Scanner — Data Inventory")
    print(_hr("═"))

    conn = init_db()
    try:
        _overall_stats(conn)
        _cadence_stats(conn)
        _per_region_stats(conn)
        _per_sector_stats(conn)
        _signal_completeness(conn)
        _table_row_counts(conn)
    finally:
        conn.close()

    print(f"\n{_hr('═')}\n")


if __name__ == "__main__":
    main()
