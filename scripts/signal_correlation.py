#!/usr/bin/env python3
"""One-time signal correlation audit.

Loads the latest scan's raw signal values from the DB and prints
the pairwise Pearson correlation matrix for the 9 scored signals,
highlighting pairs with |r| > 0.7.

Usage:
    python scripts/signal_correlation.py
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import psycopg2

SCORED_SIGNALS = [
    "rs_ratio", "return_3m", "return_6m", "above_50dma", "above_200dma",
    "rs_momentum", "acceleration", "ma50_slope", "obv_slope",
]


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("DATABASE_URL not set", file=sys.stderr)
        return 1

    conn = psycopg2.connect(db_url)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT scan_id FROM scans ORDER BY run_at DESC LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            print("No scans found", file=sys.stderr)
            return 1
        scan_id = row[0]

        cur.execute(
            "SELECT sector_key, signal_name, raw_value "
            "FROM sector_signals WHERE scan_id = %s",
            (scan_id,),
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    df = pd.DataFrame(rows, columns=["sector_key", "signal_name", "raw_value"])
    pivot = df.pivot(index="sector_key", columns="signal_name", values="raw_value")

    present = [s for s in SCORED_SIGNALS if s in pivot.columns]
    if len(present) < 2:
        print(f"Only {len(present)} scored signals found in scan {scan_id}", file=sys.stderr)
        return 1

    matrix = pivot[present].astype(float).corr()

    print(f"\nSignal correlation matrix (scan {scan_id}, {len(pivot)} sectors)")
    print("=" * 80)
    pd.set_option("display.float_format", lambda x: f"{x:+.2f}")
    pd.set_option("display.max_columns", 20)
    pd.set_option("display.width", 120)
    print(matrix.to_string())

    print("\n\nHighly correlated pairs (|r| > 0.7):")
    print("-" * 50)
    found = False
    for i, a in enumerate(present):
        for b in present[i + 1:]:
            r = matrix.loc[a, b]
            if abs(r) > 0.7:
                found = True
                print(f"  {a:20s}  ↔  {b:20s}  r = {r:+.3f}")
    if not found:
        print("  (none)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
