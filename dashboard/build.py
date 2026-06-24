"""
Static dashboard builder.

Reads Supabase/Postgres -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--out docs]
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

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

_WARM_PALETTE = [
    "#5A6F49",  # green-600
    "#A55A3C",  # terra-500
    "#738A5F",  # green-500
    "#BF6F50",  # terra-400
    "#455636",  # green-700
    "#83462E",  # terra-600
    "#6A8599",  # info blue-gray
    "#8F8568",  # beige-500
    "#2F3C25",  # green-800
    "#5E3121",  # terra-700
]

_SCORE_SIGNAL_COLORS: dict[str, str] = {
    "composite":    "#5A6F49",
    "level_score":  "#8FA77A",
    "change_score": "#A55A3C",
    "data_score":   "#6A8599",
    "rank":         "#8F8568",
}

_CHART_STYLE = dict(
    paper_bgcolor="#F5F0E6",
    plot_bgcolor="#FAF7F0",
    font_color="#3E392B",
    font_family="Inter, -apple-system, sans-serif",
    gridcolor="#DFD5BE",
    zerolinecolor="#C4B89A",
    legend_bgcolor="#FAF7F0",
    legend_bordercolor="#DFD5BE",
)

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

_SIGNAL_DESCRIPTIONS: dict[str, str] = {
    "rs_ratio":            "Relative strength vs benchmark over 12 weeks, normalised to 100. Above 100 = sector outperforming; below 100 = underperforming.",
    "return_3m":           "Price return of the sector ETF over the last 3 months. Measures medium-term absolute momentum.",
    "return_6m":           "Price return over the last 6 months. Longer-window confirmation of trend direction.",
    "above_50dma":         "How far the ETF price sits above its 50-day moving average. Positive = price above MA (bullish structure).",
    "above_200dma":        "Distance from the 200-day moving average. Positive = sector is in a long-term uptrend.",
    "rs_momentum":         "Rate of change of relative strength — whether the sector is outperforming faster or slower than last week. Above 100 = accelerating.",
    "acceleration":        "1-month return minus 3-month return. Positive = recent price action outpacing the medium-term trend (momentum re-accelerating).",
    "ma50_slope":          "Slope of the 50-day moving average. Positive = MA rising (uptrend intact); negative = MA rolling over.",
    "obv_slope":           "Slope of On-Balance Volume. Rising OBV means volume is flowing into the sector, confirming price strength with buying pressure.",
    "return_1m":           "1-month price return. Short-term reference; stored but not included in scoring.",
    "breadth_above_50dma": "Percentage of stocks in the sector trading above their own 50-DMA. High breadth = broad-based rally, not just a few large caps.",
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


def _compute_rank_trajectories(history_df) -> dict:
    """
    Compute rank slope over last 5 scans per sector.

    Returns dict keyed by "{region}|{gics_sector}" with:
        label: "↑↑" | "↑" | "→" | "↓" | "↓↓"
        state: "strong_up" | "up" | "flat" | "down" | "strong_down"
        slope: float (rank units per scan; negative = improving)
    """
    if history_df.empty:
        return {}

    df = history_df.copy()
    df["_sk"] = df["region"] + "|" + df["gics_sector"]

    scan_ids = sorted(df["scan_id"].unique())
    recent_ids = set(scan_ids[-5:])
    recent = df[df["scan_id"].isin(recent_ids)]

    result = {}
    for sk in df["_sk"].unique():
        ranks = (
            recent[recent["_sk"] == sk]
            .sort_values("scan_id")["rank"]
            .dropna()
            .tolist()
        )
        n = len(ranks)
        if n < 2:
            result[sk] = {"label": "→", "state": "flat", "slope": 0.0}
            continue

        # Pure-Python OLS slope (no numpy needed)
        x_mean = (n - 1) / 2.0
        y_mean = sum(ranks) / n
        num = sum((i - x_mean) * (y - y_mean) for i, y in enumerate(ranks))
        den = sum((i - x_mean) ** 2 for i in range(n))
        slope = round(num / den, 3) if den else 0.0

        if slope <= -1.5:
            state, label = "strong_up", "↑↑"
        elif slope <= -0.3:
            state, label = "up", "↑"
        elif slope < 0.3:
            state, label = "flat", "→"
        elif slope < 1.5:
            state, label = "down", "↓"
        else:
            state, label = "strong_down", "↓↓"

        result[sk] = {"label": label, "state": state, "slope": slope}

    return result


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


def _build_instruments_html(sector_key: str, sector_etfs: dict) -> str:
    """Render the Instruments table for a sector breakdown panel."""
    import html as _html

    region, sector_name = sector_key.split("|", 1)
    etf_list = sector_etfs.get(region, {}).get(sector_name, [])
    if not etf_list:
        return ""

    rows = ""
    for etf in etf_list:
        ticker  = etf.get("ticker", "")
        name    = etf.get("name", "")
        ter     = etf.get("ter", "")
        isin    = etf.get("isin", "")
        url     = etf.get("url", "")
        link    = (
            f'<a href="{_html.escape(url)}" target="_blank" rel="noopener">↗</a>'
            if url else ""
        )
        rows += (
            f"<tr>"
            f'<td class="etf-ticker">{_html.escape(ticker)}</td>'
            f'<td class="etf-name">{_html.escape(name)}</td>'
            f'<td class="etf-ter">{_html.escape(str(ter))}</td>'
            f'<td class="etf-isin">{_html.escape(isin)}</td>'
            f'<td class="etf-link">{link}</td>'
            f"</tr>"
        )

    return (
        f'<div class="bd-instruments">'
        f'<div class="sig-title">Instruments</div>'
        f'<table class="etf-table">'
        f"<thead><tr>"
        f"<th>Ticker</th><th>Name</th><th>TER</th><th>ISIN</th><th></th>"
        f"</tr></thead>"
        f"<tbody>{rows}</tbody>"
        f"</table>"
        f"</div>"
    )


def _build_breakdown_html(
    sector_key: str,
    score_row: dict,
    sector_signals: list[dict],
    universe: dict,
    weights: dict,
    sector_etfs: dict | None = None,
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
    level_weight = weights.get("data_pillar", {}).get("level", 0.5)
    chg_weight   = weights.get("data_pillar", {}).get("change", 0.5)

    def fv(v):
        f = _safe_float(v)
        return f"{f:.3f}" if f is not None else "—"

    composite     = fv(score_row.get("composite"))
    data_score    = fv(score_row.get("data_score"))
    level_score   = fv(score_row.get("level_score"))
    change_score  = fv(score_row.get("change_score"))

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
                color, chip = "#8FA77A", '<span class="sig-chip bull">▲</span>'
            elif z_v <= -0.5:
                color, chip = "#BF6F50", '<span class="sig-chip bear">▼</span>'
            else:
                color, chip = "#C4B89A", '<span class="sig-chip neut">—</span>'
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

        tip = _SIGNAL_DESCRIPTIONS.get(name, "")
        label_html = (
            f'<span class="sig-tip" data-tip="{_html.escape(tip)}">'
            f'{_html.escape(meta["label"])}'
            f'</span>'
        ) if tip else _html.escape(meta["label"])
        return (
            f'<tr>'
            f'<td class="sig-label">{label_html}</td>'
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

    instruments = _build_instruments_html(sector_key, sector_etfs or {})
    return (
        f'<div class="breakdown-inner">'
        f'<div class="breakdown-grid">'
        f'<div class="bd-left">{tree}</div>'
        f'<div class="bd-right">{signals}</div>'
        f'</div>'
        f'{instruments}'
        f'</div>'
    )


def _build_rrg_figure(rrg_df) -> str:
    """
    Relative Rotation Graph using real JdK-style RS-Ratio (x) and RS-Momentum (y).

    rrg_df: DataFrame with columns scan_id, run_at, region, gics_sector,
            rs_ratio, rs_momentum — from get_rrg_history().
    """
    if rrg_df is None or rrg_df.empty:
        fig = go.Figure()
        fig.update_layout(
            title="RRG — no data",
            paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B"),
        )
        return pio.to_json(fig)

    import pandas as pd

    rrg_df = rrg_df.dropna(subset=["rs_ratio", "rs_momentum"]).copy()
    if rrg_df.empty:
        fig = go.Figure()
        fig.update_layout(title="RRG — no RS signals in DB yet",
                          paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
                          font=dict(color="#3E392B"))
        return pio.to_json(fig)

    latest_scan_id = rrg_df["scan_id"].max()
    latest = rrg_df[rrg_df["scan_id"] == latest_scan_id].copy()

    regions = sorted(rrg_df["region"].unique().tolist())
    color_palette = ["#5A6F49", "#A55A3C", "#738A5F", "#BF6F50", "#8FA77A"]
    region_colors = {r: color_palette[i % len(color_palette)] for i, r in enumerate(regions)}

    # Dynamic axis range: centre on 100, extend to fit all points + padding
    all_x = rrg_df["rs_ratio"].values
    all_y = rrg_df["rs_momentum"].values
    pad = 1.5
    x_min = min(all_x.min(), 100) - pad
    x_max = max(all_x.max(), 100) + pad
    y_min = min(all_y.min(), 100) - pad
    y_max = max(all_y.max(), 100) + pad

    fig = go.Figure()

    # Quadrant lines at 100/100
    fig.add_shape(type="line", x0=100, x1=100, y0=y_min, y1=y_max,
                  line=dict(color="#C4B89A", width=1, dash="dot"))
    fig.add_shape(type="line", x0=x_min, x1=x_max, y0=100, y1=100,
                  line=dict(color="#C4B89A", width=1, dash="dot"))

    # Quadrant labels
    qx_r = x_min + (x_max - x_min) * 0.82
    qx_l = x_min + (x_max - x_min) * 0.18
    qy_t = y_min + (y_max - y_min) * 0.88
    qy_b = y_min + (y_max - y_min) * 0.12
    for qx, qy, qlabel in [
        (qx_r, qy_t, "Leading"),
        (qx_l, qy_t, "Improving"),
        (qx_l, qy_b, "Lagging"),
        (qx_r, qy_b, "Weakening"),
    ]:
        fig.add_annotation(x=qx, y=qy, text=qlabel, showarrow=False,
                           font=dict(size=9, color="#8F8568"),
                           xanchor="center", yanchor="middle")

    # Tail traces — all scans except latest
    tail_df = rrg_df[rrg_df["scan_id"] != latest_scan_id]
    for region in regions:
        r_tail = tail_df[tail_df["region"] == region]
        for sector in r_tail["gics_sector"].unique():
            sec = r_tail[r_tail["gics_sector"] == sector].sort_values("scan_id")
            # Include the latest point to connect tail to current position before length check
            cur = latest[(latest["region"] == region) & (latest["gics_sector"] == sector)]
            if not cur.empty:
                sec = pd.concat([sec, cur]).sort_values("scan_id")
            if len(sec) < 2:
                continue
            fig.add_trace(go.Scatter(
                x=sec["rs_ratio"].tolist(),
                y=sec["rs_momentum"].tolist(),
                mode="lines+markers",
                line=dict(color=region_colors[region], width=1, dash="dot"),
                marker=dict(size=3, color=region_colors[region]),
                showlegend=False,
                hoverinfo="skip",
                opacity=0.35,
            ))

    # Main scatter — latest scan, one trace per region (for legend)
    for region in regions:
        r_latest = latest[latest["region"] == region]
        if r_latest.empty:
            continue
        fig.add_trace(go.Scatter(
            x=r_latest["rs_ratio"].tolist(),
            y=r_latest["rs_momentum"].tolist(),
            mode="markers+text",
            marker=dict(size=12, color=region_colors[region],
                        line=dict(width=1, color="#1F1C15")),
            text=r_latest["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=9),
            name=region,
            hovertemplate=(
                "<b>%{text} (" + region + ")</b><br>"
                "RS-Ratio: %{x:.2f}<br>"
                "RS-Momentum: %{y:.2f}<br>"
                "<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=dict(text="Relative Rotation Graph",
                   font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="RS-Ratio (>100 = outperforming benchmark)",
                   range=[x_min, x_max], gridcolor="#DFD5BE", zeroline=False),
        yaxis=dict(title="RS-Momentum (>100 = RS-Ratio rising)",
                   range=[y_min, y_max], gridcolor="#DFD5BE", zeroline=False),
        paper_bgcolor="#F5F0E6",
        plot_bgcolor="#FAF7F0",
        font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
        legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE"),
        margin=dict(l=50, r=20, t=50, b=50),
    )
    return pio.to_json(fig)


def _build_sentiment_scatter_figure(history_df) -> str:
    """Data ⇄ Sentiment scatter: x=data_score, y=sentiment_score, latest scan only."""
    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(title="Data ⇄ Sentiment — no data",
                          paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
                          font=dict(color="#3E392B"))
        return pio.to_json(fig)

    latest_id = history_df["scan_id"].max()
    df = history_df[history_df["scan_id"] == latest_id].copy()

    has_sentiment = df["sentiment_score"].notna() & (df["sentiment_score"] != 0.0)
    solid = df[has_sentiment]
    faded = df[~has_sentiment]

    region_colors = {"US": "#A55A3C", "EU": "#5A6F49"}

    fig = go.Figure()

    for xy in [dict(x0=0, x1=0, y0=-3, y1=3), dict(x0=-3, x1=3, y0=0, y1=0)]:
        fig.add_shape(type="line", **xy, line=dict(color="#DFD5BE", width=1, dash="dot"))

    for x, y, label in [
        (1.5,  1.5, "Agreement<br>(bullish)"),
        (-1.5, 1.5, "Sentiment<br>ahead"),
        (-1.5, -1.5, "Agreement<br>(bearish)"),
        (1.5, -1.5, "Data ahead"),
    ]:
        fig.add_annotation(x=x, y=y, text=label, showarrow=False,
                           font=dict(size=9, color="#8C8370"),
                           xanchor="center", yanchor="middle")

    for region, color in region_colors.items():
        grp = solid[solid["region"] == region]
        if grp.empty:
            continue
        fig.add_trace(go.Scatter(
            x=grp["data_score"].tolist(),
            y=grp["sentiment_score"].tolist(),
            mode="markers+text",
            marker=dict(size=12, color=color, line=dict(width=1, color="#C8B89A")),
            text=grp["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=9, color="#3E392B"),
            name=region,
            hovertemplate=(
                "<b>%{text} (" + region + ")</b><br>"
                "Data: %{x:.3f}<br>Sentiment: %{y:.3f}<extra></extra>"
            ),
        ))

    if not faded.empty:
        fig.add_trace(go.Scatter(
            x=faded["data_score"].tolist(),
            y=[0.0] * len(faded),
            mode="markers+text",
            marker=dict(size=8, color="#C8B89A", symbol="circle-open"),
            text=faded["gics_sector"].tolist(),
            textposition="top center",
            textfont=dict(size=8, color="#8C8370"),
            name="no sentiment data",
            hovertemplate="<b>%{text}</b><br>Sentiment: N/A<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Data ⇄ Sentiment", font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Data Score", gridcolor="#DFD5BE", zeroline=False),
        yaxis=dict(title="Sentiment Score", gridcolor="#DFD5BE", zeroline=False),
        paper_bgcolor="#F5F0E6",
        plot_bgcolor="#FAF7F0",
        font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
        legend=dict(font=dict(size=9), bgcolor="#FAF7F0", bordercolor="#DFD5BE"),
        margin=dict(l=50, r=20, t=50, b=50),
        height=520,
    )
    return pio.to_json(fig)


def _build_rescore_data(history_df) -> dict:
    """Per-scan × per-sector data_score and sentiment_score arrays for the
    client-side leaderboard rescoring. Arrays are aligned to the ascending
    scan list; missing / NaN values become 0.0."""
    if history_df.empty:
        return {"scans": [], "sectors": [], "data": {}, "sentiment": {}}

    df = history_df.copy()
    df["sector_key"] = df["region"] + "|" + df["gics_sector"]

    scan_ids = sorted(df["scan_id"].unique().tolist())
    scans_meta = []
    for sid in scan_ids:
        run_at = df[df["scan_id"] == sid]["run_at"].iloc[0]
        scans_meta.append({"scan_id": int(sid), "run_at": str(run_at)})

    sectors = sorted(df["sector_key"].unique().tolist())

    def _series(col: str) -> dict:
        result = {}
        for key in sectors:
            sk = df[df["sector_key"] == key].set_index("scan_id")
            vals = []
            for sid in scan_ids:
                v = sk[col].get(sid) if sid in sk.index else None
                fv = _safe_float(v)
                vals.append(fv if fv is not None else 0.0)
            result[key] = vals
        return result

    return {
        "scans": scans_meta,
        "sectors": sectors,
        "data": _series("data_score"),
        "sentiment": _series("sentiment_score"),
    }


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

        for i, sk in enumerate(sector_keys):
            sk_data = history_df[history_df["sector_key"] == sk].sort_values("scan_id")
            if sk_data.empty:
                continue
            region, sector_name = sk.split("|", 1)
            fig.add_trace(go.Scatter(
                x=sk_data["run_at_str"].tolist(),
                y=sk_data[signal].tolist(),
                mode="lines+markers",
                name=f"{sector_name} ({region})",
                line=dict(color=_WARM_PALETTE[i % len(_WARM_PALETTE)]),
                hovertemplate=f"<b>{sector_name}</b><br>Date: %{{x}}<br>{signal}: %{{y:.3f}}<extra></extra>",
            ))

        fig.update_layout(
            title=dict(text=signal.replace("_", " ").title(),
                       font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Scan Date", gridcolor="#DFD5BE"),
            yaxis=dict(title=signal.replace("_", " ").title(), gridcolor="#DFD5BE"),
            paper_bgcolor="#F5F0E6",
            plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
            legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
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
                line=dict(color=_SCORE_SIGNAL_COLORS.get(signal, "#8F8568")),
                hovertemplate=f"<b>{signal}</b><br>Date: %{{x}}<br>Value: %{{y:.3f}}<extra></extra>",
            ))
        fig.update_layout(
            title=dict(text=f"{sector_name} ({region}) — score components",
                       font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Scan Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Score / Rank", gridcolor="#DFD5BE"),
            paper_bgcolor="#F5F0E6",
            plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
            legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
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
            paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
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

    colors = ["#8FA77A" if d >= 0 else "#BF6F50" for d in merged["delta_rank"]]

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
        title=dict(text="Movers — rank change (latest vs prior scan)",
                   font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Delta rank (positive = climbing)", gridcolor="#DFD5BE",
                   zeroline=True, zerolinecolor="#C4B89A"),
        yaxis=dict(title="", gridcolor="#DFD5BE"),
        paper_bgcolor="#F5F0E6",
        plot_bgcolor="#FAF7F0",
        font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
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
            paper_bgcolor="#F5F0E6", plot_bgcolor="#FAF7F0",
            font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
        )
        return pio.to_json(fig)

    df = history_df.copy()
    df["sector_label"] = df["gics_sector"] + " (" + df["region"] + ")"
    df["run_at_str"] = pd.to_datetime(df["run_at"]).dt.strftime("%Y-%m-%d")
    df = df.sort_values("scan_id")

    fig = go.Figure()
    for i, label in enumerate(sorted(df["sector_label"].unique())):
        sec = df[df["sector_label"] == label]
        fig.add_trace(go.Scatter(
            x=sec["run_at_str"].tolist(),
            y=sec["composite"].tolist(),
            mode="lines+markers",
            name=label,
            line=dict(color=_WARM_PALETTE[i % len(_WARM_PALETTE)]),
            hovertemplate=f"<b>{label}</b><br>Date: %{{x}}<br>Composite: %{{y:.3f}}<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text="Composite score history", font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Scan Date", gridcolor="#DFD5BE"),
        yaxis=dict(title="Composite score", gridcolor="#DFD5BE"),
        paper_bgcolor="#F5F0E6",
        plot_bgcolor="#FAF7F0",
        font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
        legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
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
        latest["delta_composite"] = (latest["composite"] - latest["comp_prev"]).fillna(0)
    else:
        latest["delta_rank"] = 0.0
        latest["rank_prev"] = latest["rank"]
        latest["delta_composite"] = 0.0

    latest = latest.sort_values("rank", ascending=True)

    rows = []
    for _, row in latest.iterrows():
        delta = _safe_float(row.get("delta_rank", 0)) or 0.0
        delta_comp = _safe_float(row.get("delta_composite", 0)) or 0.0
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
            "emerging": delta > 0 and delta_comp > 0,
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
    parser = argparse.ArgumentParser(description="Build static dashboard from Supabase")
    parser.add_argument("--out", default="docs", metavar="DIR",
                        help="Output directory for docs/index.html (default: docs)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve paths relative to project root (parent of dashboard/)
    project_root = Path(__file__).parent.parent
    out_dir = project_root / args.out

    # 1. Ensure plotly bundle
    _ensure_plotly_bundle()

    # 2. Open DB + load history
    sys.path.insert(0, str(project_root))
    from src.state import init_db, get_scan_history, get_signals_for_latest_scan, get_rrg_history

    conn = init_db()
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)
    rrg_df = get_rrg_history(conn, n_scans=6)
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
    _etfs_path = project_root / "config/sector_etfs.yaml"
    _sector_etfs = _yaml.safe_load(_etfs_path.read_text()) if _etfs_path.exists() else {}

    # 3. Build figures
    logger.info("Building RRG figure …")
    rrg_json = _build_rrg_figure(rrg_df)

    logger.info("Building drill-down data …")
    sector_signal_data, sector_keys, signals_list = _build_drilldown_data(history_df)

    logger.info("Building movers figure …")
    movers_json = _build_movers_figure(history_df)

    logger.info("Building history figure …")
    history_json = _build_history_figure(history_df)

    logger.info("Building sentiment scatter …")
    sentiment_scatter_json = _build_sentiment_scatter_figure(history_df)

    logger.info("Building rescore data …")
    rescore_data_json = json.dumps(_build_rescore_data(history_df))

    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(history_df)
    trajectories = _compute_rank_trajectories(history_df)

    # Enrich rows with breakdown HTML (keyed by sector_id for JS toggle)
    latest_scan_id = history_df["scan_id"].max()
    latest_scores  = history_df[history_df["scan_id"] == latest_scan_id]
    for row in leaderboard_rows:
        key = f"{row['region']}|{row['sector']}"
        row["key"]       = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        row["trajectory_label"] = traj["label"]
        row["trajectory_state"] = traj["state"]
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
            key, score_row_dict, row_signals, _universe, _weights, _sector_etfs
        )

    # 4. Copy plotly.min.js into docs/assets/ so GitHub Pages can serve it
    import shutil
    docs_assets = out_dir / "assets"
    docs_assets.mkdir(exist_ok=True)
    plotly_src = _ASSETS_DIR / "plotly.min.js"
    if plotly_src.exists():
        shutil.copy2(plotly_src, docs_assets / "plotly.min.js")
    rescore_src = _ASSETS_DIR / "rescore.js"
    if rescore_src.exists():
        shutil.copy2(rescore_src, docs_assets / "rescore.js")
    plotly_bundle_rel = "assets/plotly.min.js"

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
            rescore_data_json=rescore_data_json,
            signals_list=signals_list,
            plotly_bundle=plotly_bundle_rel,
        ),
    )
    print(f"Dashboard built: {out_path}")


if __name__ == "__main__":
    main()
