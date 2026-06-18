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
        try:
            resp = requests.get(PLOTLY_CDN, timeout=30)
            resp.raise_for_status()
            bundle.write_bytes(resp.content)
            logger.info("Downloaded plotly bundle (%d KB)", len(resp.content) // 1024)
        except Exception as exc:
            logger.error(
                "Failed to download Plotly bundle from %s: %s\n"
                "Fix: manually download plotly.min.js from https://cdn.plot.ly/ "
                "and place it at dashboard/assets/plotly.min.js",
                PLOTLY_CDN, exc
            )
            sys.exit(1)
    return bundle


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------

import plotly.graph_objects as go
import plotly.io as pio


# ---------------------------------------------------------------------------
# Signal metadata for leaderboard breakdown
# ---------------------------------------------------------------------------

_SIGNAL_META: dict[str, dict] = {
    "rs_ratio":            {"label": "Relative Strength",  "group": "level"},
    "return_3m":           {"label": "3M Return",           "group": "level"},
    "return_6m":           {"label": "6M Return",           "group": "level"},
    "above_50dma":         {"label": "Dist. from 50-DMA",   "group": "level"},
    "above_200dma":        {"label": "Dist. from 200-DMA",  "group": "level"},
    "rs_momentum":         {"label": "RS Momentum",         "group": "change"},
    "acceleration":        {"label": "Momentum Accel.",     "group": "change"},
    "ma50_slope":          {"label": "50-DMA Slope",        "group": "change"},
    "obv_slope":           {"label": "OBV Trend",           "group": "change"},
    "return_1m":           {"label": "1M Return",           "group": "info"},
    "breadth_above_50dma": {"label": "Breadth >50-DMA",     "group": "info"},
}


def _safe_float(v) -> float | None:
    """Return float or None for NaN/None values."""
    if v is None:
        return None
    try:
        f = float(v)
        return None if math.isnan(f) else f
    except (TypeError, ValueError):
        return None


def _format_raw_value(name: str, value) -> str:
    """Format a signal's raw value for human display."""
    if value is None:
        return "—"
    v = float(value)
    if name in ("rs_ratio", "rs_momentum"):
        return f"{v:.1f}"
    if name == "breadth_above_50dma":
        return f"{v * 100:.0f}%"
    if name in ("ma50_slope", "obv_slope"):
        return f"{v:+.3f}"
    # return_*, above_*dma, acceleration — stored as decimal fraction
    return f"{v * 100:+.1f}%"


def _build_breakdown_html(
    sector_key: str,
    score_row: dict,
    sector_signals: list[dict],
    universe: dict,
    weights: dict,
) -> str:
    """Pre-render the breakdown panel for one sector row."""
    import html as _html

    region, sector_name = sector_key.split("|", 1)

    # Ticker + benchmark from universe
    if region == "US":
        ticker = universe.get("us_sectors", {}).get(sector_name, "—")
        benchmark = universe.get("us_benchmark", "RSP")
    else:
        ticker = universe.get("eu_sectors", {}).get(sector_name, "—")
        benchmark = universe.get("eu_benchmark", "EXSA.DE")

    # Weights
    data_weight  = weights.get("pillars", {}).get("data", 1.0)
    sent_weight  = weights.get("pillars", {}).get("sentiment", 0.0)
    level_weight = weights.get("data_pillar", {}).get("level", 0.5)
    chg_weight   = weights.get("data_pillar", {}).get("change", 0.5)

    def fv(v):
        f = _safe_float(v)
        return f"{f:.3f}" if f is not None else "—"

    composite     = fv(score_row.get("composite"))
    data_score    = fv(score_row.get("data_score"))
    level_score   = fv(score_row.get("level_score"))
    change_score  = fv(score_row.get("change_score"))
    sent_score    = fv(score_row.get("sentiment_score"))

    # Score-tree HTML
    tree = (
        f'<div class="score-tree">'
        f'<div class="st-row st-top">'
        f'<span class="st-label">Composite</span>'
        f'<span class="st-val">{composite}</span>'
        f'</div>'
        f'<div class="st-row">'
        f'<span class="st-conn">├─</span>'
        f'<span class="st-label">Data Score</span>'
        f'<span class="st-wt">({data_weight*100:.0f}%)</span>'
        f'<span class="st-val">{data_score}</span>'
        f'</div>'
        f'<div class="st-row st-sub">'
        f'<span class="st-conn">│ ├─</span>'
        f'<span class="st-label">Level</span>'
        f'<span class="st-wt">({level_weight*100:.0f}%)</span>'
        f'<span class="st-val">{level_score}</span>'
        f'<span class="st-meta">5 signals</span>'
        f'</div>'
        f'<div class="st-row st-sub">'
        f'<span class="st-conn">│ └─</span>'
        f'<span class="st-label">Change</span>'
        f'<span class="st-wt">({chg_weight*100:.0f}%)</span>'
        f'<span class="st-val">{change_score}</span>'
        f'<span class="st-meta">4 signals</span>'
        f'</div>'
        f'<div class="st-row">'
        f'<span class="st-conn">└─</span>'
        f'<span class="st-label">Sentiment</span>'
        f'<span class="st-wt">({sent_weight*100:.0f}%)</span>'
        f'<span class="st-val">{sent_score}</span>'
        f'</div>'
        f'</div>'
        f'<div class="bd-footer">'
        f'ETF: {_html.escape(str(ticker))} &middot; '
        f'Benchmark: {_html.escape(str(benchmark))}'
        f'</div>'
    )

    # Signal lookup
    sig_by_name = {s["signal_name"]: s for s in sector_signals}

    def sig_row(name: str) -> str:
        meta = _SIGNAL_META.get(name)
        if not meta:
            return ""
        sig  = sig_by_name.get(name, {})
        raw  = _format_raw_value(name, sig.get("raw_value"))
        z_v  = _safe_float(sig.get("z_value"))

        if z_v is not None:
            bar_w = min(abs(z_v) / 3.0, 1.0) * 60
            if z_v >= 0.5:
                color, chip = "#4FC3F7", '<span class="sig-chip bull">▲</span>'
            elif z_v <= -0.5:
                color, chip = "#F06292", '<span class="sig-chip bear">▼</span>'
            else:
                color, chip = "#666", '<span class="sig-chip neut">—</span>'
            bar = (
                f'<span class="z-bar-wrap">'
                f'<span class="z-bar" style="width:{bar_w:.0f}px;background:{color}"></span>'
                f'</span>'
            )
            z_str = f"{z_v:+.2f}"
        else:
            bar  = '<span class="z-bar-wrap"></span>'
            chip = '<span class="sig-chip neut">—</span>'
            z_str = "—"

        return (
            f'<tr>'
            f'<td class="sig-label">{_html.escape(meta["label"])}</td>'
            f'<td class="sig-raw">{_html.escape(raw)}</td>'
            f'<td class="sig-bar">{bar}</td>'
            f'<td class="sig-z">{_html.escape(z_str)}</td>'
            f'<td>{chip}</td>'
            f'</tr>'
        )

    level_order  = list(weights.get("level_signals",  {}).keys())
    change_order = list(weights.get("change_signals", {}).keys())
    level_rows  = "".join(sig_row(n) for n in level_order)
    change_rows = "".join(sig_row(n) for n in change_order)

    # Info-only signals (not scored)
    info_parts = []
    for n in ("return_1m", "breadth_above_50dma"):
        sig = sig_by_name.get(n, {})
        if sig.get("raw_value") is not None:
            lbl = _SIGNAL_META[n]["label"]
            val = _format_raw_value(n, sig["raw_value"])
            info_parts.append(f"{_html.escape(lbl)}: {_html.escape(val)}")
    info_html = (
        f'<div class="sig-info"><span class="info-lbl">Not scored:</span> '
        + " &middot; ".join(info_parts)
        + "</div>"
    ) if info_parts else ""

    signals = (
        f'<div class="sig-section">'
        f'<div class="sig-title">Level Signals</div>'
        f'<table class="sig-table"><tbody>{level_rows}</tbody></table>'
        f'</div>'
        f'<div class="sig-section">'
        f'<div class="sig-title">Change Signals</div>'
        f'<table class="sig-table"><tbody>{change_rows}</tbody></table>'
        f'</div>'
        f'{info_html}'
    )

    return (
        f'<div class="breakdown-inner">'
        f'<div class="breakdown-grid">'
        f'<div class="bd-left">{tree}</div>'
        f'<div class="bd-right">{signals}</div>'
        f'</div>'
        f'</div>'
    )


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
        xaxis=dict(title="RS-Ratio (composite proxy)", range=[88, 112],
                   gridcolor="#333", zeroline=False),
        yaxis=dict(title="RS-Momentum (composite proxy)", range=[88, 112],
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
        return {}, [], score_signals

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


def _build_sentiment_scatter_figure(history_df) -> str:
    """Data ⇄ Sentiment scatter: x=data_score, y=sentiment_score, latest scan only."""
    import pandas as pd

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Data ⇄ Sentiment — no data",
                          paper_bgcolor="#1a1a2e", plot_bgcolor="#16213e",
                          font=dict(color="#e0e0e0"))
        return pio.to_json(fig)

    latest_id = history_df["scan_id"].max()
    df = history_df[history_df["scan_id"] == latest_id].copy()

    # Separate sectors with/without sentiment scores
    has_sentiment = df["sentiment_score"].notna() & (df["sentiment_score"] != 0.0)
    solid = df[has_sentiment]
    faded = df[~has_sentiment]

    region_colors = {"US": "#4FC3F7", "EU": "#AED581"}

    fig = go.Figure()

    # Quadrant dividers at 0/0
    for axis, xy in [("line", dict(x0=0, x1=0, y0=-3, y1=3)),
                     ("line", dict(x0=-3, x1=3, y0=0, y1=0))]:
        fig.add_shape(type=axis, **xy, line=dict(color="#555", width=1, dash="dot"))

    # Quadrant labels
    for x, y, label in [
        (1.5,  1.5, "Agreement<br>(bullish)"),
        (-1.5, 1.5, "Sentiment<br>ahead"),
        (-1.5, -1.5, "Agreement<br>(bearish)"),
        (1.5, -1.5, "Data ahead<br>← early signal"),
    ]:
        color = "#AED581" if "early" in label else "#888"
        fig.add_annotation(x=x, y=y, text=label, showarrow=False,
                           font=dict(size=9, color=color),
                           xanchor="center", yanchor="middle")

    for region, color in region_colors.items():
        grp = solid[solid["region"] == region]
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["data_score"].tolist(),
            y=grp["sentiment_score"].tolist(),
            mode="markers+text",
            marker=dict(size=12, color=color, line=dict(width=1, color="#222")),
            text=grp["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=9),
            name=region,
            hovertemplate=(
                "<b>%{text} (" + region + ")</b><br>"
                "Data: %{x:.3f}<br>Sentiment: %{y:.3f}<extra></extra>"
            ),
        ))

    # Faded points (no sentiment data)
    if not faded.empty:
        fig.add_trace(go.Scatter(
            x=faded["data_score"].tolist(),
            y=[0.0] * len(faded),
            mode="markers+text",
            marker=dict(size=8, color="#555", symbol="circle-open"),
            text=faded["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=8, color="#666"),
            name="no sentiment data",
            hovertemplate="<b>%{text}</b><br>Sentiment: N/A<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Data ⇄ Sentiment", font=dict(size=13)),
        xaxis=dict(title="Data Score", gridcolor="#333", zeroline=False),
        yaxis=dict(title="Sentiment Score", gridcolor="#333", zeroline=False),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#16213e",
        font=dict(color="#e0e0e0"),
        legend=dict(bgcolor="#1a1a2e", bordercolor="#444"),
        margin=dict(l=50, r=20, t=50, b=50),
        height=520,
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
            "sentiment_score": f"{_safe_float(row.get('sentiment_score')):.3f}"
                if _safe_float(row.get("sentiment_score")) is not None else "—",
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
    from src.state import init_db, get_scan_history, get_signals_for_latest_scan

    if not db_path.exists():
        print(f"No database found at {db_path}. Run scan.py first.")
        sys.exit(0)

    conn = init_db(db_path=db_path)
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)   # must come before conn.close()
    conn.close()

    if history_df.empty:
        print("No scans in database yet. Run scan.py first.")
        sys.exit(0)

    logger.info("Loaded %d rows from %d scans", len(history_df), history_df["scan_id"].nunique())

    # Load config for breakdown panel
    import yaml as _yaml
    with open(project_root / "config/universe.yaml") as _fh:
        _universe = _yaml.safe_load(_fh)
    with open(project_root / "config/weights.yaml") as _fh:
        _weights = _yaml.safe_load(_fh)

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

    logger.info("Building Data⇄Sentiment scatter …")
    sentiment_scatter_json = _build_sentiment_scatter_figure(history_df)

    # Enrich rows with breakdown HTML (keyed by sector_id for JS toggle)
    latest_scan_id = history_df["scan_id"].max()
    latest_scores  = history_df[history_df["scan_id"] == latest_scan_id]
    for row in leaderboard_rows:
        key = f"{row['region']}|{row['sector']}"
        row["key"]       = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        mask = (
            (latest_scores["region"]      == row["region"]) &
            (latest_scores["gics_sector"] == row["sector"])
        )
        score_slice = latest_scores[mask]
        score_row_dict = {} if score_slice.empty else score_slice.iloc[0].to_dict()
        if not signals_df.empty:
            sig_mask = (
                (signals_df["region"]      == row["region"]) &
                (signals_df["gics_sector"] == row["sector"])
            )
            row_signals = signals_df[sig_mask].to_dict("records")
        else:
            row_signals = []
        row["breakdown_html"] = _build_breakdown_html(
            key, score_row_dict, row_signals, _universe, _weights
        )

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
            sentiment_scatter_json=sentiment_scatter_json,
            signals_list=signals_list,
            plotly_bundle=plotly_bundle_rel,
        ),
    )
    print(f"Dashboard built: {out_path}")


if __name__ == "__main__":
    main()
