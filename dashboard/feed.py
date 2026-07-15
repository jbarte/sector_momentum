"""Atom feed builder — one entry per scan, last N scans."""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd


def build_feed_entries(
    all_scores_df: pd.DataFrame,
    n_entries: int = 30,
) -> list[dict]:
    """Build feed entry dicts from scan history, newest first.

    Each entry contains:
      - scan_id, scan_date (ISO), updated (ISO)
      - title: "Scan #<id> — <date>"
      - summary_html: top-5 per region + biggest movers
    """
    if all_scores_df.empty:
        return []

    scan_ids = sorted(all_scores_df["scan_id"].unique(), reverse=True)[:n_entries]
    prev_lookup = _build_prev_lookup(all_scores_df)

    entries = []
    for sid in scan_ids:
        g = all_scores_df[all_scores_df["scan_id"] == sid]
        run_at = pd.to_datetime(g["run_at"].iloc[0])
        scan_date = run_at.strftime("%Y-%m-%d")
        updated_iso = run_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        top5 = _top_n_by_region(g)
        movers = _biggest_movers(g, prev_lookup.get(sid))
        summary = _render_summary_html(top5, movers)

        entries.append({
            "scan_id": int(sid),
            "scan_date": scan_date,
            "updated": updated_iso,
            "title": f"Scan #{int(sid)} — {scan_date}",
            "summary_html": summary,
        })
    return entries


def _build_prev_lookup(df: pd.DataFrame) -> dict[int, pd.DataFrame]:
    """Map each scan_id to its predecessor's DataFrame."""
    scan_ids = sorted(df["scan_id"].unique())
    lookup: dict[int, pd.DataFrame] = {}
    for i, sid in enumerate(scan_ids):
        if i > 0:
            lookup[sid] = df[df["scan_id"] == scan_ids[i - 1]]
    return lookup


def _top_n_by_region(g: pd.DataFrame, n: int = 5) -> dict[str, list[str]]:
    """Top-n sectors per region by rank."""
    result: dict[str, list[str]] = {}
    for region in sorted(g["region"].unique()):
        rg = g[g["region"] == region].nsmallest(n, "rank")
        result[region] = [
            f"{row['gics_sector']} ({row['composite']:+.2f})"
            for _, row in rg.iterrows()
        ]
    return result


def _biggest_movers(
    current: pd.DataFrame,
    prior: pd.DataFrame | None,
    n: int = 3,
) -> dict[str, list[str]]:
    """Return {'climbers': [...], 'fallers': [...]} strings."""
    if prior is None or prior.empty:
        return {"climbers": [], "fallers": []}

    merged = current.merge(
        prior[["region", "gics_sector", "rank"]].rename(columns={"rank": "prev_rank"}),
        on=["region", "gics_sector"],
        how="left",
    )
    merged["delta_rank"] = merged["prev_rank"].fillna(merged["rank"]) - merged["rank"]

    climbers = merged.nlargest(n, "delta_rank")
    fallers = merged.nsmallest(n, "delta_rank")

    def fmt(row):
        dr = int(row["delta_rank"])
        return f"{row['gics_sector']} ({row['region']}) {dr:+d}"

    return {
        "climbers": [fmt(r) for _, r in climbers.iterrows() if r["delta_rank"] > 0],
        "fallers": [fmt(r) for _, r in fallers.iterrows() if r["delta_rank"] < 0],
    }


def _render_summary_html(
    top5: dict[str, list[str]],
    movers: dict[str, list[str]],
) -> str:
    parts: list[str] = []
    for region, sectors in top5.items():
        parts.append(f"<h4>Top {region}</h4>")
        parts.append("<ol>" + "".join(f"<li>{s}</li>" for s in sectors) + "</ol>")

    if movers.get("climbers"):
        parts.append("<h4>Climbers</h4>")
        parts.append("<ul>" + "".join(f"<li>{m}</li>" for m in movers["climbers"]) + "</ul>")
    if movers.get("fallers"):
        parts.append("<h4>Fallers</h4>")
        parts.append("<ul>" + "".join(f"<li>{m}</li>" for m in movers["fallers"]) + "</ul>")

    return "\n".join(parts)


def feed_updated_timestamp(entries: list[dict]) -> str:
    """ISO timestamp of the most recent entry, or now."""
    if entries:
        return entries[0]["updated"]
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
