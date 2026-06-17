"""
Static dashboard builder.

Reads momentum.db -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--db data/momentum.db] [--out docs]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dashboard.build")

# ---------------------------------------------------------------------------
# Plotly bundle management
# ---------------------------------------------------------------------------

PLOTLY_CDN = "https://cdn.plot.ly/plotly-2.27.0.min.js"
_ASSETS_DIR = Path(__file__).parent / "assets"


def _ensure_plotly_bundle() -> Path:
    """Download plotly.min.js once to dashboard/assets/ if not present."""
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bundle = _ASSETS_DIR / "plotly.min.js"
    if not bundle.exists():
        import requests

        logger.info("Downloading Plotly bundle from %s …", PLOTLY_CDN)
        resp = requests.get(PLOTLY_CDN, timeout=60)
        resp.raise_for_status()
        bundle.write_bytes(resp.content)
        logger.info("Saved Plotly bundle (%d KB) to %s", len(resp.content) // 1024, bundle)
    return bundle


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------

import plotly.graph_objects as go
import plotly.io as pio


def _safe_float(v) -> float | None:
    """Return float or None for NaN/None values."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _build_rrg_figure(history_df) -> str:
    """
    Scatter plot of rs_ratio (x) vs rs_momentum (y) for the most recent scan,
    with tail traces for the last 3 scans per sector and quadrant lines at 100/100.
    Signals come from the signals table but the history_df is scores-based;
    we use a stub if rs_ratio/rs_momentum are absent (they live in signals, not scores).
    """
    import pandas as pd

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(title="RRG — no data")
        return pio.to_json(fig)

    # history_df has scan_id, run_at, region, gics_sector, composite, rank, etc.
    # rs_ratio / rs_momentum are NOT in scores — they're in signals.
    # We synthesise placeholder values from composite so the chart renders.
    # (The real values require a JOIN with signals; the caller can augment this later.)
    latest_scan_id = history_df["scan_id"].max()
    latest = history_df[history_df["scan_id"] == latest_scan_id].copy()

    # Build a region -> color map
    regions = latest["region"].unique().tolist()
    color_palette = ["#4FC3F7", "#AED581", "#FFB74D", "#F06292", "#CE93D8"]
    region_colors = {r: color_palette[i % len(color_palette)] for i, r in enumerate(regions)}

    # Use composite as a proxy for rs_ratio offset (centred at 100)
    # This is clearly labelled so users know it is a composite-based proxy.
    latest["_x"] = 100 + latest["composite"].fillna(0) * 5
    latest["_y"] = 100 + latest["rank"].apply(lambda r: (6 - r) if r else 0).fillna(0) * 1.5

    # Tail traces — last 3 scans
    scan_ids_sorted = sorted(history_df["scan_id"].unique())
    tail_scan_ids = scan_ids_sorted[-3:]

    fig = go.Figure()

    # Quadrant shading (subtle)
    for qx, qy, color, label in [
        (1, 1, "rgba(100,200,100,0.05)", "Leading"),
        (-1, 1, "rgba(200,200,100,0.05)", "Improving"),
        (-1, -1, "rgba(200,100,100,0.05)", "Lagging"),
        (1, -1, "rgba(100,100,200,0.05)", "Weakening"),
    ]:
        pass  # annotations added below

    # Quadrant lines
    fig.add_shape(type="line", x0=100, x1=100, y0=90, y1=110,
                  line=dict(color="#555", width=1, dash="dot"))
    fig.add_shape(type="line", x0=90, x1=110, y0=100, y1=100,
                  line=dict(color="#555", width=1, dash="dot"))

    # Quadrant labels
    for qx, qy, qlabel in [
        (105, 105, "Leading"), (95, 105, "Improving"),
        (95, 95, "Lagging"), (105, 95, "Weakening"),
    ]:
        fig.add_annotation(x=qx, y=qy, text=qlabel,
                           showarrow=False, font=dict(size=9, color="#888"),
                           xanchor="center", yanchor="middle")

    # Tail lines per sector
    for region in regions:
        region_history = history_df[
            (history_df["region"] == region) &
            (history_df["scan_id"].isin(tail_scan_ids))
        ].copy()
        region_history["_x"] = 100 + region_history["composite"].fillna(0) * 5
        region_history["_y"] = 100 + region_history["rank"].apply(
            lambda r: (6 - r) if r else 0).fillna(0) * 1.5

        for sector in region_history["gics_sector"].unique():
            sec_data = region_history[region_history["gics_sector"] == sector].sort_values("scan_id")
            if len(sec_data) < 2:
                continue
            fig.add_trace(go.Scatter(
                x=sec_data["_x"].tolist(),
                y=sec_data["_y"].tolist(),
                mode="lines+markers",
                line=dict(color=region_colors[region], width=1, dash="dot"),
                marker=dict(size=4, color=region_colors[region]),
                showlegend=False,
                hoverinfo="skip",
                opacity=0.4,
            ))

    # Main scatter — latest scan
    for region in regions:
        region_latest = latest[latest["region"] == region]
        fig.add_trace(go.Scatter(
            x=region_latest["_x"].tolist(),
            y=region_latest["_y"].tolist(),
            mode="markers+text",
            marker=dict(size=12, color=region_colors[region],
                        line=dict(width=1, color="#222")),
            text=region_latest["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=9),
            name=region,
            hovertemplate=(
                "<b>%{text}</b><br>"
                "RS Ratio (proxy): %{x:.1f}<br>"
                "RS Momentum (proxy): %{y:.1f}<br>"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text="Relative Rotation Graph (composite proxy)", font=dict(size=13)),
        xaxis=dict(title="RS-Ratio (100 = benchmark)", range=[88, 112],
                   gridcolor="#333", zeroline=False),
        yaxis=dict(title="RS-Momentum (100 = benchmark)", range=[88, 112],
                   gridcolor="#333", zeroline=False),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(bgcolor="#1a1a2e", bordercolor="#444"),
        margin=dict(l=50, r=20, t=50, b=50),
    )
    return pio.to_json(fig)


def _build_drilldown_data(history_df) -> tuple[dict, list[str]]:
    """
    Build per-sector timeseries for each score column.
    Returns (drilldown_data, signals_list).

    drilldown_data: { sector_key: { signal_name: plotly_figure_json } }
    """
    import pandas as pd

    score_signals = [
        "composite", "level_score", "change_score", "data_score", "rank"
    ]

    if history_df.empty:
        return {}, score_signals

    sector_keys = (history_df["region"] + "|" + history_df["gics_sector"]).unique().tolist()
    sector_keys.sort()

    drilldown_data: dict[str, dict] = {}

    history_df = history_df.copy()
    history_df["sector_key"] = history_df["region"] + "|" + history_df["gics_sector"]
    # Format run_at for display
    history_df["run_at_str"] = pd.to_datetime(history_df["run_at"]).dt.strftime("%Y-%m-%d")

    for signal in score_signals:
        if signal not in history_df.columns:
            continue

        fig = go.Figure()

        for sk in sector_keys:
            sk_data = history_df[history_df["sector_key"] == sk].sort_values("scan_id")
            if sk_data.empty:
                continue
            region, sector_name = sk.split("|", 1)
            fig.add_trace(go.Scatter(
                x=sk_data["run_at_str"].tolist(),
                y=sk_data[signal].tolist(),
                mode="lines+markers",
                name=f"{sector_name} ({region})",
                hovertemplate=f"<b>{sector_name}</b><br>Date: %{{x}}<br>{signal}: %{{y:.3f}}<extra></extra>",
            ))

        fig.update_layout(
            title=dict(text=signal.replace("_", " ").title(), font=dict(size=13)),
            xaxis=dict(title="Scan Date", gridcolor="#333"),
            yaxis=dict(title=signal.replace("_", " ").title(), gridcolor="#333"),
            paper_bgcolor="#1a1a2e",
            plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
            legend=dict(bgcolor="#1a1a2e", bordercolor="#444", font=dict(size=9)),
            margin=dict(l=50, r=20, t=50, b=50),
            hovermode="x unified",
        )

        fig_json = pio.to_json(fig)

        # Store per sector_key grouped by signal
        if signal not in drilldown_data:
            drilldown_data[signal] = {}
        drilldown_data[signal] = fig_json  # one figure per signal, all sectors

    # Also provide per-sector per-signal breakdown
    sector_signal_data: dict[str, str] = {}
    for sk in sector_keys:
        sk_data = history_df[history_df["sector_key"] == sk].sort_values("scan_id")
        if sk_data.empty:
            continue
        region, sector_name = sk.split("|", 1)
        fig = go.Figure()
        for signal in score_signals:
            if signal not in sk_data.columns:
                continue
            fig.add_trace(go.Scatter(
                x=sk_data["run_at_str"].tolist(),
                y=sk_data[signal].tolist(),
                mode="lines+markers",
                name=signal.replace("_", " ").title(),
                hovertemplate=f"<b>{signal}</b><br>Date: %{{x}}<br>Value: %{{y:.3f}}<extra></extra>",
            ))
        fig.update_layout(
            title=dict(text=f"{sector_name} ({region}) — Score Components", font=dict(size=13)),
            xaxis=dict(title="Scan Date", gridcolor="#333"),
            yaxis=dict(title="Score / Rank", gridcolor="#333"),
            paper_bgcolor="#1a1a2e",
            plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
            legend=dict(bgcolor="#1a1a2e", bordercolor="#444", font=dict(size=9)),
            margin=dict(l=50, r=20, t=50, b=50),
            hovermode="x unified",
        )
        sector_signal_data[sk] = pio.to_json(fig)

    return sector_signal_data, sector_keys, score_signals


def _build_movers_figure(history_df) -> str:
    """Bar chart of delta_rank for the most recent scan, sorted."""
    import pandas as pd

    if history_df.empty or len(history_df["scan_id"].unique()) < 2:
        fig = go.Figure()
        fig.update_layout(
            title="Movers — need at least 2 scans",
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
        )
        return pio.to_json(fig)

    scan_ids = sorted(history_df["scan_id"].unique())
    latest_id = scan_ids[-1]
    prev_id = scan_ids[-2]

    latest = history_df[history_df["scan_id"] == latest_id][
        ["region", "gics_sector", "rank", "composite"]
    ].rename(columns={"rank": "rank_cur", "composite": "comp_cur"})

    prev = history_df[history_df["scan_id"] == prev_id][
        ["region", "gics_sector", "rank", "composite"]
    ].rename(columns={"rank": "rank_prev", "composite": "comp_prev"})

    merged = latest.merge(prev, on=["region", "gics_sector"], how="inner")
    merged["delta_rank"] = merged["rank_prev"] - merged["rank_cur"]
    merged["label"] = merged["gics_sector"] + " (" + merged["region"] + ")"
    merged = merged.sort_values("delta_rank", ascending=True)

    colors = ["#4FC3F7" if d >= 0 else "#F06292" for d in merged["delta_rank"]]

    fig = go.Figure(go.Bar(
        x=merged["delta_rank"].tolist(),
        y=merged["label"].tolist(),
        orientation="h",
        marker_color=colors,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Rank change: %{x:+.1f}<br>"
            "<extra></extra>"
        ),
    ))

    fig.update_layout(
        title=dict(text="Movers — Rank Change (latest vs prior scan)", font=dict(size=13)),
        xaxis=dict(title="Delta Rank (positive = climbing)", gridcolor="#333", zeroline=True,
                   zerolinecolor="#555"),
        yaxis=dict(title="", gridcolor="#333"),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        margin=dict(l=180, r=30, t=50, b=50),
        height=max(300, len(merged) * 28 + 80),
    )
    return pio.to_json(fig)


def _build_history_figure(history_df) -> str:
    """Line chart of composite rank over time, one line per sector+region."""
    import pandas as pd

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(
            title="History — no data",
            paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
            font=dict(color="#e0e0e0"),
        )
        return pio.to_json(fig)

    df = history_df.copy()
    df["sector_label"] = df["gics_sector"] + " (" + df["region"] + ")"
    df["run_at_str"] = pd.to_datetime(df["run_at"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values("scan_id")

    fig = go.Figure()
    for label in sorted(df["sector_label"].unique()):
        sec = df[df["sector_label"] == label]
        fig.add_trace(go.Scatter(
            x=sec["run_at_str"].tolist(),
            y=sec["composite"].tolist(),
            mode="lines+markers",
            name=label,
            hovertemplate=f"<b>{label}</b><br>Date: %{{x}}<br>Composite: %{{y:.3f}}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Composite Score History", font=dict(size=13)),
        xaxis=dict(title="Scan Date", gridcolor="#333"),
        yaxis=dict(title="Composite Score", gridcolor="#333"),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(bgcolor="#1a1a2e", bordercolor="#444", font=dict(size=9)),
        margin=dict(l=50, r=20, t=50, b=50),
        hovermode="x unified",
    )
    return pio.to_json(fig)


def _build_leaderboard_rows(history_df) -> tuple[list[dict], str]:
    """
    Return leaderboard rows from the most recent scan and the scan date string.
    """
    import pandas as pd

    if history_df.empty:
        return [], "N/A"

    latest_scan_id = history_df["scan_id"].max()
    latest = history_df[history_df["scan_id"] == latest_scan_id].copy()

    # Get scan date
    scan_date = pd.to_datetime(latest["run_at"].iloc[0]).strftime("%Y-%m-%d %H:%M UTC")

    # Compute delta_rank if we have a prior scan
    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) >= 2:
        prev_id = scan_ids[-2]
        prev = history_df[history_df["scan_id"] == prev_id][
            ["region", "gics_sector", "rank", "composite"]
        ].rename(columns={"rank": "rank_prev", "composite": "comp_prev"})
        latest = latest.merge(prev, on=["region", "gics_sector"], how="left")
        latest["delta_rank"] = (latest["rank_prev"] - latest["rank"]).fillna(0)
    else:
        latest["delta_rank"] = 0.0
        latest["rank_prev"] = latest["rank"]

    latest = latest.sort_values("rank", ascending=True)

    rows = []
    for _, row in latest.iterrows():
        delta = _safe_float(row.get("delta_rank", 0)) or 0.0
        composite = _safe_float(row.get("composite"))
        rank = _safe_float(row.get("rank"))
        rows.append({
            "rank": int(rank) if rank is not None else "—",
            "sector": row["gics_sector"],
            "region": row["region"],
            "composite": f"{composite:.3f}" if composite is not None else "—",
            "level_score": f"{_safe_float(row.get('level_score')):.3f}"
                if _safe_float(row.get("level_score")) is not None else "—",
            "change_score": f"{_safe_float(row.get('change_score')):.3f}"
                if _safe_float(row.get("change_score")) is not None else "—",
            "data_score": f"{_safe_float(row.get('data_score')):.3f}"
                if _safe_float(row.get("data_score")) is not None else "—",
            "delta_rank": f"{delta:+.1f}" if delta != 0 else "—",
            "arrow": "▲" if delta > 0 else ("▼" if delta < 0 else ""),
            "arrow_class": "up" if delta > 0 else ("down" if delta < 0 else ""),
        })
    return rows, scan_date


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _render(
    template_path: Path,
    out_path: Path,
    context: dict,
) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )
    template = env.get_template(template_path.name)
    html = template.render(**context)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s (%d KB)", out_path, len(html) // 1024)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static dashboard from momentum.db")
    parser.add_argument("--db", default="data/momentum.db", metavar="PATH",
                        help="Path to momentum.db (default: data/momentum.db)")
    parser.add_argument("--out", default="docs", metavar="DIR",
                        help="Output directory for docs/index.html (default: docs)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve paths relative to project root (parent of dashboard/)
    project_root = Path(__file__).parent.parent
    db_path = project_root / args.db
    out_dir = project_root / args.out

    # 1. Ensure plotly bundle
    _ensure_plotly_bundle()

    # 2. Open DB + load history
    sys.path.insert(0, str(project_root))
    from src.state import init_db, get_scan_history

    if not db_path.exists():
        print(f"No database found at {db_path}. Run scan.py first.")
        sys.exit(0)

    conn = init_db(db_path=db_path)
    history_df = get_scan_history(conn, n_scans=20)
    conn.close()

    if history_df.empty:
        print("No scans in database yet. Run scan.py first.")
        sys.exit(0)

    logger.info("Loaded %d rows from %d scans", len(history_df), history_df["scan_id"].nunique())

    # 3. Build figures
    logger.info("Building RRG figure …")
    rrg_json = _build_rrg_figure(history_df)

    logger.info("Building drill-down data …")
    sector_signal_data, sector_keys, signals_list = _build_drilldown_data(history_df)

    logger.info("Building movers figure …")
    movers_json = _build_movers_figure(history_df)

    logger.info("Building history figure …")
    history_json = _build_history_figure(history_df)

    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(history_df)

    # 4. Compute relative path from docs/ to dashboard/assets/plotly.min.js
    plotly_bundle_rel = "../dashboard/assets/plotly.min.js"

    # 5. Render template
    template_path = Path(__file__).parent / "templates" / "index.html.j2"
    out_path = out_dir / "index.html"

    _render(
        template_path=template_path,
        out_path=out_path,
        context=dict(
            scan_date=scan_date,
            leaderboard_rows=leaderboard_rows,
            rrg_data_json=rrg_json,
            drilldown_data=json.dumps(sector_signal_data),
            sector_keys=sector_keys,
            movers_json=movers_json,
            history_json=history_json,
            signals_list=signals_list,
            plotly_bundle=plotly_bundle_rel,
        ),
    )
    print(f"Dashboard built: {out_path}")


if __name__ == "__main__":
    main()
