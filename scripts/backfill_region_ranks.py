#!/usr/bin/env python3
"""Recompute per-region scores, ranks, and z-values for all stored scans.

One-off script to backfill historical data after switching from global
to per-region cohort scoring. Re-runnable and idempotent.

Usage:
    python scripts/backfill_region_ranks.py
    python scripts/backfill_region_ranks.py --dry-run   # preview without writing
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.scoring import score_all, zscore_cross_section
from src.state import init_db, _read_sql

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("backfill")

SIGNAL_COLUMNS = [
    "rs_ratio", "rs_momentum", "return_1m", "return_3m", "return_6m",
    "acceleration", "above_50dma", "above_200dma", "ma50_slope",
    "obv_slope", "breadth_above_50dma",
]


def recompute_scan(signals_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Pure function: given raw signals, return (scores_df, z_df) with per-region ranks."""
    wide = signals_df.pivot_table(
        index=["region", "gics_sector"],
        columns="signal_name",
        values="raw_value",
        aggfunc="first",
    )
    wide.index = wide.index.map(lambda x: f"{x[0]}|{x[1]}")
    present_cols = [c for c in SIGNAL_COLUMNS if c in wide.columns]
    wide = wide[present_cols]

    scored_parts = []
    z_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide.index.str.startswith(f"{region_prefix}|")
        region_df = wide[mask]
        if region_df.empty:
            continue
        region_scored = score_all(
            region_df,
            weights_path="config/weights.yaml",
            sentiment_score=None,
            blend_sentiment=False,
        )
        scored_parts.append(region_scored)
        z_parts.append(zscore_cross_section(region_df))

    scores_df = pd.concat(scored_parts)
    z_df = pd.concat(z_parts)
    return scores_df, z_df


def main():
    parser = argparse.ArgumentParser(description="Backfill per-region ranks for all stored scans.")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing to DB.")
    args = parser.parse_args()

    conn = init_db()
    try:
        cur = conn.cursor()

        cur.execute("SELECT DISTINCT scan_id FROM scores ORDER BY scan_id")
        scan_ids = [row[0] for row in cur.fetchall()]
        logger.info("Found %d scans to backfill", len(scan_ids))

        for scan_id in scan_ids:
            signals_df = _read_sql(
                conn,
                "SELECT region, gics_sector, signal_name, raw_value FROM signals WHERE scan_id = %s",
                params=(scan_id,),
            )
            if signals_df.empty:
                logger.warning("Scan %d: no signals found, skipping", scan_id)
                continue

            try:
                scores_df, z_df = recompute_scan(signals_df)
            except Exception as exc:
                logger.warning("Scan %d: recomputation failed (%s), skipping", scan_id, exc)
                continue

            if args.dry_run:
                us_max = scores_df.loc[scores_df.index.str.startswith("US|"), "rank"].max() if scores_df.index.str.startswith("US|").any() else 0
                eu_max = scores_df.loc[scores_df.index.str.startswith("EU|"), "rank"].max() if scores_df.index.str.startswith("EU|").any() else 0
                logger.info("Scan %d: would update %d scores (US max rank=%s, EU max rank=%s)",
                            scan_id, len(scores_df), us_max, eu_max)
                continue

            for sector_key, row in scores_df.iterrows():
                parts = sector_key.split("|", 1)
                region, gics_sector = parts[0], parts[1]
                cur.execute(
                    "UPDATE scores SET level_score=%s, change_score=%s, data_score=%s, "
                    "composite=%s, rank=%s "
                    "WHERE scan_id=%s AND region=%s AND gics_sector=%s",
                    (
                        float(row["level_score"]), float(row["change_score"]),
                        float(row["data_score"]), float(row["composite"]),
                        float(row["rank"]),
                        scan_id, region, gics_sector,
                    ),
                )

            z_long = z_df.reset_index().melt(
                id_vars=["index"],
                var_name="signal_name",
                value_name="z_value",
            )
            z_long[["region", "gics_sector"]] = z_long["index"].str.split("|", n=1, expand=True)
            for _, zrow in z_long.iterrows():
                cur.execute(
                    "UPDATE signals SET z_value=%s "
                    "WHERE scan_id=%s AND region=%s AND gics_sector=%s AND signal_name=%s",
                    (
                        float(zrow["z_value"]),
                        scan_id, zrow["region"], zrow["gics_sector"], zrow["signal_name"],
                    ),
                )

            conn.commit()
            logger.info("Scan %d: updated %d scores + z-values", scan_id, len(scores_df))

        logger.info("Backfill complete.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
