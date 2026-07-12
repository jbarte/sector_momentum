"""Scan reports and scan-index builders."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

logger = logging.getLogger("dashboard.build")


def build_scan_index(all_scores_df) -> list[dict]:
    """One row per scan (newest first) for the history list."""
    if all_scores_df.empty:
        return []
    out = []
    for sid in sorted(all_scores_df["scan_id"].unique(), reverse=True):
        g = all_scores_df[all_scores_df["scan_id"] == sid]
        run_at_raw = str(g["run_at"].iloc[0])
        try:
            disp = pd.to_datetime(run_at_raw).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            disp = run_at_raw
        top = g.loc[g["rank"].idxmin()]
        out.append({
            "scan_id": int(sid),
            "run_at_display": disp,
            "run_at_raw": run_at_raw,
            "sector_count": int(len(g)),
            "top_sector": top["gics_sector"],
            "top_region": top["region"],
        })
    return out


def _generate_scan_reports(all_scores_df, out_dir, swedish_tickers_path="config/swedish_tickers.csv") -> list[int]:
    """Write report_<scan_id>.md for every scan; returns scan_ids written. Non-fatal per scan."""
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from src.state import compute_deltas
    from src.report import (build_ranked_table, build_movers,
                            build_swedish_overlay, build_report_markdown)

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if all_scores_df.empty:
        return []
    scan_ids = sorted(all_scores_df["scan_id"].unique())
    written = []
    for i, sid in enumerate(scan_ids):
        report_path = out_dir / f"report_{int(sid)}.md"
        if report_path.exists():
            written.append(int(sid))
            continue
        try:
            current = all_scores_df[all_scores_df["scan_id"] == sid].copy()
            prior = (all_scores_df[all_scores_df["scan_id"] == scan_ids[i - 1]].copy()
                     if i > 0 else None)
            swd = compute_deltas(current, prior)
            scan_date = pd.to_datetime(current["run_at"].iloc[0]).strftime("%Y-%m-%d")
            md = build_report_markdown(
                scan_date,
                build_ranked_table(swd),
                build_movers(swd),
                build_swedish_overlay(swd, swedish_tickers_path),
            )
            report_path.write_text(md, encoding="utf-8")
            written.append(int(sid))
        except Exception as exc:
            logger.warning("Report generation failed for scan %s (%s) — skipping", sid, exc)
    return written
