"""
Report generator.

Produces a Markdown report from scored/ranked sector data.
Outputs to reports/report_<YYYY-MM-DD>.md
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd


def build_ranked_table(scores_with_deltas: pd.DataFrame) -> str:
    """
    Build a markdown table of all sectors ranked by composite score.

    Input DataFrame columns (from scoring + compute_deltas):
        region, gics_sector, composite, level_score, change_score,
        data_score, rank, delta_composite, delta_rank, emerging_flag

    Output: markdown table string with columns:
        Rank | Sector | Region | Composite | Level | Change | ΔRank | ΔComposite | ⭐
    Where ⭐ column shows "🌱" for emerging_flag=True, empty otherwise.
    Rows sorted by rank (ascending).
    Numeric values formatted to 2 decimal places.
    ΔRank as integer (e.g. +2, -1, 0).
    """
    df = scores_with_deltas.sort_values("rank", ascending=True).reset_index(drop=True)

    header = "| Rank | Sector | Region | Composite | Level | Change | ΔRank | ΔComposite | ⭐ |"
    separator = "|------|--------|--------|-----------|-------|--------|-------|------------|---|"

    rows = [header, separator]
    for _, row in df.iterrows():
        rank = int(row["rank"])
        sector = row["gics_sector"]
        region = row["region"]
        composite = f"{row['composite']:.2f}"
        level = f"{row['level_score']:.2f}"
        change = f"{row['change_score']:.2f}"
        delta_rank = int(row["delta_rank"])
        delta_rank_str = f"{delta_rank:+d}"
        delta_composite = f"{row['delta_composite']:.2f}"
        star = "🌱" if row.get("emerging_flag", False) else ""
        rows.append(
            f"| {rank} | {sector} | {region} | {composite} | {level} | {change} | {delta_rank_str} | {delta_composite} | {star} |"
        )

    return "\n".join(rows)


def build_movers(scores_with_deltas: pd.DataFrame, top_n: int = 5) -> str:
    """
    Build a markdown section highlighting the biggest movers.

    Returns a markdown string with two sub-sections:
    ## 🚀 Top Climbers (by ΔRank)
    - List of top_n sectors with largest positive delta_rank

    ## ⚠️ Biggest Fallers (by ΔRank)
    - List of top_n sectors with largest negative delta_rank

    Each entry format: "**{sector}** ({region}): rank {rank} (Δ{delta_rank:+d})"
    """
    df = scores_with_deltas.copy()

    # Top climbers: largest positive delta_rank (ascending rank = better)
    # A positive delta_rank means the rank number went up (fell in standings),
    # but in the context of "climbers" we want sectors whose rank improved (rank number decreased).
    # The spec says "largest positive delta_rank" so we follow the spec literally.
    climbers = df.sort_values("delta_rank", ascending=False).head(top_n)
    fallers = df.sort_values("delta_rank", ascending=True).head(top_n)

    def format_entry(row: pd.Series) -> str:
        sector = row["gics_sector"]
        region = row["region"]
        rank = int(row["rank"])
        delta_rank = int(row["delta_rank"])
        return f"- **{sector}** ({region}): rank {rank} (Δ{delta_rank:+d})"

    climber_lines = [format_entry(row) for _, row in climbers.iterrows()]
    faller_lines = [format_entry(row) for _, row in fallers.iterrows()]

    parts = [
        "## 🚀 Top Climbers (by ΔRank)",
        "\n".join(climber_lines) if climber_lines else "- None",
        "",
        "## ⚠️ Biggest Fallers (by ΔRank)",
        "\n".join(faller_lines) if faller_lines else "- None",
    ]

    return "\n".join(parts)


def build_swedish_overlay(
    scores_with_deltas: pd.DataFrame,
    swedish_tickers_path: str = "config/swedish_tickers.csv",
    top_n: int = 5,
) -> str:
    """
    For the top_n sectors by composite score, list matching Swedish tickers.

    Returns a markdown string:
    ## 🇸🇪 Swedish Expression

    **{sector} ({region}) — rank {rank}**
    | Ticker | Name | Market Cap (BSEK) |
    ...
    (rows for matching tickers sorted by market_cap_bn_sek descending)

    If no tickers map to a top sector, skip that sector.
    If swedish_tickers.csv doesn't exist, return a note string.
    """
    tickers_path = Path(swedish_tickers_path)
    if not tickers_path.exists():
        return "## 🇸🇪 Swedish Expression\n\n*Swedish tickers file not found.*"

    tickers_df = pd.read_csv(tickers_path)

    top_sectors = (
        scores_with_deltas
        .sort_values("composite", ascending=False)
        .head(top_n)
        .reset_index(drop=True)
    )

    lines = ["## 🇸🇪 Swedish Expression"]

    for _, sector_row in top_sectors.iterrows():
        sector = sector_row["gics_sector"]
        region = sector_row["region"]
        rank = int(sector_row["rank"])

        matching = tickers_df[tickers_df["gics_sector"] == sector].copy()
        if matching.empty:
            continue

        matching = matching.sort_values("market_cap_bn_sek", ascending=False)

        lines.append("")
        lines.append(f"**{sector} ({region}) — rank {rank}**")
        lines.append("| Ticker | Name | Market Cap (BSEK) |")
        lines.append("|--------|------|-------------------|")
        for _, t in matching.iterrows():
            lines.append(f"| {t['ticker']} | {t['name']} | {t['market_cap_bn_sek']} |")

    if len(lines) == 1:
        lines.append("\n*No Swedish tickers matched the top sectors.*")

    return "\n".join(lines)


def write_report(
    scan_date: str,        # "YYYY-MM-DD"
    ranked_table: str,
    movers: str,
    swedish: str,
    output_dir: str = "reports",
) -> str:
    """
    Compose and write the full report to reports/report_<scan_date>.md.

    Report structure:
    # Sector Momentum Report — {scan_date}
    > Analytical tooling, not investment advice.

    ## Rankings
    {ranked_table}

    ## Movers
    {movers}

    {swedish}

    ---
    *Generated by Sector Momentum Scanner. Last scan: {scan_date}.*

    Returns the path of the written file.
    """
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    filename = f"report_{scan_date}.md"
    filepath = os.path.join(output_dir, filename)

    content = f"""# Sector Momentum Report — {scan_date}
> Analytical tooling, not investment advice.

## Rankings
{ranked_table}

## Movers
{movers}

{swedish}

---
*Generated by Sector Momentum Scanner. Last scan: {scan_date}.*
"""

    with open(filepath, "w", encoding="utf-8") as fh:
        fh.write(content)

    return filepath
