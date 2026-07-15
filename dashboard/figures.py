"""Plotly figure builders for dashboard charts."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from dashboard.rows import _safe_float


# ---------------------------------------------------------------------------
# Shared constants
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


# ---------------------------------------------------------------------------
# Base layout helper — eliminates ~80 lines of per-figure boilerplate
# ---------------------------------------------------------------------------

def _base_layout(**overrides) -> dict:
    """Return the common Plotly layout dict, merged with caller overrides.

    Nested dicts (xaxis, yaxis, title, font, legend, margin) are merged one
    level deep so callers can override individual keys without restating the
    whole sub-dict.
    """
    base = dict(
        paper_bgcolor="#F5F0E6",
        plot_bgcolor="#FAF7F0",
        font=dict(color="#3E392B", family="Inter, -apple-system, sans-serif"),
        legend=dict(bgcolor="#FAF7F0", bordercolor="#DFD5BE", font=dict(size=9)),
        margin=dict(l=50, r=20, t=50, b=50),
        hovermode="x unified",
    )
    # Shallow-merge nested dicts one level deep
    for key in ("xaxis", "yaxis", "yaxis2", "title", "font", "legend", "margin"):
        if key in overrides and key in base and isinstance(base[key], dict):
            merged = {**base[key], **overrides.pop(key)}
            base[key] = merged
        elif key in overrides:
            base[key] = overrides.pop(key)
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Figure builders
# ---------------------------------------------------------------------------

def _build_rrg_figure(rrg_df) -> str:
    """
    Relative Rotation Graph using real JdK-style RS-Ratio (x) and RS-Momentum (y).

    rrg_df: DataFrame with columns scan_id, run_at, region, gics_sector,
            rs_ratio, rs_momentum — from get_rrg_history().
    """
    if rrg_df is None or rrg_df.empty:
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text="RRG — no data", font=dict(size=13, color="#3E392B")),
        ))
        return pio.to_json(fig)

    import pandas as pd

    rrg_df = rrg_df.dropna(subset=["rs_ratio", "rs_momentum"]).copy()
    if rrg_df.empty:
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text="RRG — no RS signals in DB yet",
                       font=dict(size=13, color="#3E392B")),
        ))
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

    fig.update_layout(**_base_layout(
        title=dict(text="Relative Rotation Graph",
                   font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="RS-Ratio (>100 = outperforming benchmark)",
                   range=[x_min, x_max], gridcolor="#DFD5BE", zeroline=False),
        yaxis=dict(title="RS-Momentum (>100 = RS-Ratio rising)",
                   range=[y_min, y_max], gridcolor="#DFD5BE", zeroline=False),
    ))
    return pio.to_json(fig)


def _build_sentiment_scatter_figure(history_df) -> str:
    """Data <-> Sentiment scatter: x=data_score, y=sentiment_score, latest scan only."""
    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text="Data ⇄ Sentiment — no data",
                       font=dict(size=13, color="#3E392B")),
        ))
        return pio.to_json(fig)

    latest_id = history_df["scan_id"].max()
    df = history_df[history_df["scan_id"] == latest_id].copy()

    has_sentiment = df["sentiment_score"].notna() & (df["sentiment_score"] != 0.0)
    solid = df[has_sentiment]
    faded = df[~has_sentiment]

    # THEME is included so the shared builder also renders the theme cohort's
    # solid points (region="THEME"); sector history never contains THEME rows.
    region_colors = {"US": "#A55A3C", "EU": "#5A6F49", "THEME": "#7A5B8E"}

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

    fig.update_layout(**_base_layout(
        title=dict(text="Data ⇄ Sentiment", font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Data Score", gridcolor="#DFD5BE", zeroline=False),
        yaxis=dict(title="Sentiment Score", gridcolor="#DFD5BE", zeroline=False),
        height=520,
    ))
    return pio.to_json(fig)


def _build_drilldown_data(history_df) -> tuple[dict, list[str]]:
    """
    Build per-sector timeseries for each score column.
    Returns (sector_signal_data, sector_keys, score_signals).

    sector_signal_data: { sector_key: plotly_figure_json }
    """
    import pandas as pd

    score_signals = [
        "composite", "level_score", "change_score", "data_score", "rank"
    ]

    if history_df.empty:
        return {}, [], score_signals

    sector_keys = (history_df["region"] + "|" + history_df["gics_sector"]).unique().tolist()
    sector_keys.sort()

    history_df = history_df.copy()
    history_df["sector_key"] = history_df["region"] + "|" + history_df["gics_sector"]
    history_df["run_at_str"] = pd.to_datetime(history_df["run_at"], format="ISO8601", utc=True).dt.strftime("%Y-%m-%d")

    # Per-sector per-signal breakdown (used by the drilldown tab)
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
        fig.update_layout(**_base_layout(
            title=dict(text=f"{sector_name} ({region}) — score components",
                       font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Scan Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Score / Rank", gridcolor="#DFD5BE"),
        ))
        sector_signal_data[sk] = pio.to_json(fig)

    return sector_signal_data, sector_keys, score_signals


def _build_movers_figure(history_df) -> str:
    """Bar chart of delta_rank for the most recent scan, sorted."""
    import pandas as pd

    if history_df.empty or len(history_df["scan_id"].unique()) < 2:
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text="Movers — need at least 2 scans",
                       font=dict(size=13, color="#3E392B")),
        ))
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

    fig.update_layout(**_base_layout(
        title=dict(text="Movers — rank change (latest vs prior scan)",
                   font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Delta rank (positive = climbing)", gridcolor="#DFD5BE",
                   zeroline=True, zerolinecolor="#C4B89A"),
        yaxis=dict(title="", gridcolor="#DFD5BE"),
        margin=dict(l=180, r=30, t=50, b=50),
        height=max(300, len(merged) * 28 + 80),
    ))
    return pio.to_json(fig)


def _build_history_figure(history_df) -> str:
    """Line chart of composite rank over time, one line per sector+region."""
    import pandas as pd

    if history_df.empty:
        fig = go.Figure()
        fig.update_layout(**_base_layout(
            title=dict(text="History — no data",
                       font=dict(size=13, color="#3E392B")),
        ))
        return pio.to_json(fig)

    df = history_df.copy()
    df["sector_label"] = df["gics_sector"] + " (" + df["region"] + ")"
    df["run_at_str"] = pd.to_datetime(df["run_at"], format="ISO8601", utc=True).dt.strftime("%Y-%m-%d")
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

    fig.update_layout(**_base_layout(
        title=dict(text="Composite score history", font=dict(size=13, color="#3E392B")),
        xaxis=dict(title="Scan Date", gridcolor="#DFD5BE"),
        yaxis=dict(title="Composite score", gridcolor="#DFD5BE"),
    ))
    return pio.to_json(fig)


def _build_backtest_figures(summary) -> dict:
    """Per-track equity curves (strategy vs benchmark). Returns {region: fig_json}."""
    if not summary or not summary.get("tracks"):
        return {}
    figs: dict[str, str] = {}
    for region, track in summary["tracks"].items():
        if not track or not track.get("equity_curve"):
            continue
        dates = [p["date"] for p in track["equity_curve"]]
        strat = [p["strategy"] for p in track["equity_curve"]]
        bench = [p["benchmark"] for p in track["equity_curve"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=strat, mode="lines",
                                 name=f"Top {track['top_n']} strategy",
                                 line=dict(color=_WARM_PALETTE[0])))
        fig.add_trace(go.Scatter(x=dates, y=bench, mode="lines",
                                 name=f"Benchmark ({track['benchmark']})",
                                 line=dict(color=_WARM_PALETTE[3], dash="dash")))
        fig.update_layout(**_base_layout(
            title=dict(text=f"{region} — growth of 1.0", font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Equity (×)", gridcolor="#DFD5BE"),
        ))
        figs[region] = pio.to_json(fig)
    return figs


def _build_rotation_figures(summary) -> list:
    """Per-rotation dual-axis charts: scanner rank (inverted) vs indexed price."""
    if not summary or not summary.get("rotations"):
        return []
    out = []
    for rot in summary["rotations"]:
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=rot["dates"], y=rot["rank"], mode="lines+markers", name="Scanner rank",
            yaxis="y", line=dict(color=_WARM_PALETTE[0])))
        fig.add_trace(go.Scatter(
            x=rot["dates"], y=rot["price_indexed"], mode="lines", name="Price (indexed=100)",
            yaxis="y2", line=dict(color=_WARM_PALETTE[3], dash="dash")))
        fig.update_layout(**_base_layout(
            title=dict(text=f"{rot['name']} — {rot['sector']} ({rot['region']})",
                       font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Rank (1 = best)", autorange="reversed", gridcolor="#DFD5BE"),
            yaxis2=dict(title="Price (indexed)", overlaying="y", side="right", showgrid=False),
            margin=dict(l=50, r=50, t=50, b=50),
        ))
        out.append({"title": rot["name"], "fig_json": pio.to_json(fig)})
    return out


def _build_backtest_context(backtests_dir: str) -> dict:
    """Load summary.json and shape it for the template."""
    import json as _json
    from src.backtest.results import load_summary

    summary = load_summary(backtests_dir)
    figs = _build_backtest_figures(summary)
    rot_figs = _build_rotation_figures(summary)
    rows: list[dict] = []
    if summary:
        for region, track in summary["tracks"].items():
            if not track:
                continue
            m = track["metrics"]
            rows.append({
                "region": region, "start": track["start"], "end": track["end"],
                "benchmark": track["benchmark"], "top_n": track["top_n"],
                "cagr": f"{100 * m['cagr']:.1f}%",
                "benchmark_cagr": f"{100 * m['benchmark_cagr']:.1f}%",
                "sharpe": f"{m['sharpe']:.2f}",
                "max_drawdown": f"{100 * m['max_drawdown']:.1f}%",
                "hit_rate": f"{100 * m['hit_rate']:.0f}%",
                "avg_turnover": f"{100 * m['avg_turnover']:.0f}%",
            })
    return {
        "backtest_json": _json.dumps({k: _json.loads(v) for k, v in figs.items()}),
        "backtest_metrics": rows,
        "has_backtest": bool(figs),
        "rotation_json": _json.dumps([{"title": r["title"], "fig": _json.loads(r["fig_json"])}
                                      for r in rot_figs]),
        "has_rotations": bool(rot_figs),
    }


def _build_theme_backtest_context(backtests_dir: str) -> dict:
    """Load theme backtest summary and shape it for the template."""
    import json as _json
    from src.backtest.results import load_summary

    summary = load_summary(backtests_dir)
    track = summary.get("track") if summary else None
    fig_json = "null"
    rows: list[dict] = []

    if track and track.get("equity_curve"):
        dates = [p["date"] for p in track["equity_curve"]]
        strat = [p["strategy"] for p in track["equity_curve"]]
        bench = [p["benchmark"] for p in track["equity_curve"]]
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=dates, y=strat, mode="lines",
                                 name=f"Top {track['top_n']} strategy",
                                 line=dict(color=_WARM_PALETTE[0])))
        fig.add_trace(go.Scatter(x=dates, y=bench, mode="lines",
                                 name=f"Benchmark ({track['benchmark']})",
                                 line=dict(color=_WARM_PALETTE[3], dash="dash")))
        fig.update_layout(**_base_layout(
            title=dict(text="Themes — growth of 1.0", font=dict(size=13, color="#3E392B")),
            xaxis=dict(title="Date", gridcolor="#DFD5BE"),
            yaxis=dict(title="Equity (×)", gridcolor="#DFD5BE"),
        ))
        fig_json = pio.to_json(fig)

        m = track["metrics"]
        rows.append({
            "region": "THEME", "start": track["start"], "end": track["end"],
            "benchmark": track["benchmark"], "top_n": track["top_n"],
            "cagr": f"{100 * m['cagr']:.1f}%",
            "benchmark_cagr": f"{100 * m['benchmark_cagr']:.1f}%",
            "sharpe": f"{m['sharpe']:.2f}",
            "max_drawdown": f"{100 * m['max_drawdown']:.1f}%",
            "hit_rate": f"{100 * m['hit_rate']:.0f}%",
            "avg_turnover": f"{100 * m['avg_turnover']:.0f}%",
        })

    return {
        "theme_backtest_json": fig_json,
        "theme_backtest_metrics": rows,
        "has_theme_backtest": bool(fig_json),
    }


def _build_rescore_data(history_df) -> dict:
    """Per-scan x per-sector data_score and sentiment_score arrays for the
    client-side leaderboard rescoring."""
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
            sk = df[df["sector_key"] == key].groupby("scan_id")[col].first()
            vals = []
            for sid in scan_ids:
                fv = _safe_float(sk.get(sid))
                vals.append(fv if fv is not None else 0.0)
            result[key] = vals
        return result

    return {
        "scans": scans_meta,
        "sectors": sectors,
        "data": _series("data_score"),
        "sentiment": _series("sentiment_score"),
    }


def _build_scan_history_data(all_scores_df) -> dict:
    """Per-scan scores for every sector, for the client-side scan-history viewer."""
    if all_scores_df.empty:
        return {"scans": [], "scores": {}}

    df = all_scores_df.copy()

    scan_ids = sorted(df["scan_id"].unique(), reverse=True)
    scans = []
    for sid in scan_ids:
        g = df[df["scan_id"] == sid]
        run_at_raw = str(g["run_at"].iloc[0])
        try:
            disp = pd.to_datetime(run_at_raw).strftime("%Y-%m-%d %H:%M UTC")
        except (ValueError, TypeError):
            disp = run_at_raw
        top = g.loc[g["rank"].idxmin()]
        scans.append({
            "id": int(sid),
            "date": disp,
            "sectors": int(len(g)),
            "top": f"{top['gics_sector']} ({top['region']})",
        })

    scores = {}
    for sid in scan_ids:
        g = df[df["scan_id"] == sid]
        sid_scores = {}
        for _, row in g.iterrows():
            key = f"{row['region']}|{row['gics_sector']}"
            rk = _safe_float(row["rank"])
            sid_scores[key] = {
                "rank": int(rk) if rk is not None else 99,
                "composite": round(_safe_float(row["composite"]) or 0.0, 3),
                "level": round(_safe_float(row["level_score"]) or 0.0, 3),
                "change": round(_safe_float(row["change_score"]) or 0.0, 3),
                "data": round(_safe_float(row["data_score"]) or 0.0, 3),
                "sentiment": round(_safe_float(row["sentiment_score"]) or 0.0, 3),
            }
        scores[str(int(sid))] = sid_scores

    return {"scans": scans, "scores": scores}
