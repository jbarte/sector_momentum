"""Rolling correlation heatmap for the Sectors dashboard."""
from __future__ import annotations

import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from dashboard.figures import _base_layout
from src.data.prices import fetch_prices

logger = logging.getLogger("dashboard.build")

_CORR_WINDOW = 60
_CALENDAR_BUFFER = 120


def _compute_correlation_matrix(
    prices: dict[str, pd.DataFrame],
    tickers: list[str],
    window: int = _CORR_WINDOW,
) -> pd.DataFrame | None:
    """Compute trailing pairwise correlation of daily log returns.

    Returns a ticker×ticker DataFrame, or None if insufficient data.
    """
    closes: dict[str, pd.Series] = {}
    for t in tickers:
        df = prices.get(t)
        if df is not None and not df.empty and "Close" in df.columns:
            closes[t] = df["Close"]

    if not closes:
        return None

    price_df = pd.DataFrame(closes)
    returns = np.log(price_df / price_df.shift(1)).dropna(how="all")

    if len(returns) < window:
        return None

    corr = returns.tail(window).corr()

    # Reindex to include all requested tickers (missing ones become NaN rows/cols)
    corr = corr.reindex(index=tickers, columns=tickers)
    return corr


def _order_labels(
    universe: dict,
    ranks: dict[str, int],
) -> tuple[list[str], list[str]]:
    """Return (labels, tickers) ordered US-first, then EU, by rank within region.

    Top-5 per region get <b>bold</b> labels for Plotly axis rendering.
    """
    ordered: list[tuple[str, str]] = []  # (label, ticker)

    for region_key, region_tag in [("us_sectors", "US"), ("eu_sectors", "EU")]:
        sectors = universe.get(region_key, {})
        items = []
        for name, ticker in sectors.items():
            rank = ranks.get(f"{region_tag}|{name}", 999)
            items.append((rank, name, ticker))
        items.sort(key=lambda x: x[0])

        # Bolding "top 5" only conveys information when the region actually
        # has more than 5 members — with 5 or fewer, every sector would be
        # bolded, which is meaningless emphasis.
        highlight = len(items) > 5

        for rank, name, ticker in items:
            label = f"{name} ({region_tag})"
            if highlight and rank <= 5:
                label = f"<b>{label}</b>"
            ordered.append((label, ticker))

    labels = [o[0] for o in ordered]
    tickers = [o[1] for o in ordered]
    return labels, tickers


def _build_heatmap_figure(
    corr: pd.DataFrame,
    labels: list[str],
    tickers: list[str],
    n_us: int,
) -> str:
    """Build a Plotly heatmap figure and return its JSON string."""
    # Reorder correlation matrix to match labels/tickers order
    z = corr.reindex(index=tickers, columns=tickers).values

    fig = go.Figure(data=go.Heatmap(
        z=z,
        x=labels,
        y=labels,
        colorscale="RdBu_r",
        zmin=-1,
        zmax=1,
        hovertemplate="%{x} vs %{y}: %{z:.2f}<extra></extra>",
        colorbar=dict(
            title=dict(text="Corr", side="right"),
            thickness=12,
            len=0.75,
        ),
    ))

    # Region separator lines
    sep = n_us - 0.5
    shapes = [
        dict(type="line", x0=sep, x1=sep, y0=-0.5, y1=len(labels) - 0.5,
             line=dict(color="#3E392B", width=1.5)),
        dict(type="line", x0=-0.5, x1=len(labels) - 0.5, y0=sep, y1=sep,
             line=dict(color="#3E392B", width=1.5)),
    ]

    layout = _base_layout(
        title=dict(text="60-day return correlation", font=dict(size=13)),
        xaxis=dict(
            tickangle=45,
            side="bottom",
            tickfont=dict(size=9),
        ),
        yaxis=dict(
            autorange="reversed",
            scaleanchor="x",
            tickfont=dict(size=9),
        ),
        margin=dict(l=120, r=40, t=50, b=130),
        hovermode="closest",
        shapes=shapes,
    )
    fig.update_layout(**layout)
    return pio.to_json(fig)


def build_correlation_context(shared: dict) -> dict:
    """Return context dict for the Correlation tab on the sectors page."""
    none_ctx = {
        "correlation_fig_json": None,
        "correlation_n_days": None,
        "correlation_date": None,
    }
    try:
        universe = shared["universe"]
        history_df = shared["history_df"]
        cache_dir = str(shared["project_root"] / "data" / "cache")

        # Build rank lookup from latest scan
        ranks: dict[str, int] = {}
        if not history_df.empty:
            latest_id = history_df["scan_id"].max()
            latest = history_df[history_df["scan_id"] == latest_id]
            for _, row in latest.iterrows():
                key = f"{row['region']}|{row['gics_sector']}"
                ranks[key] = int(row["rank"])

        labels, tickers = _order_labels(universe, ranks)
        n_us = len(universe.get("us_sectors", {}))

        # Fetch prices
        start = (date.today() - timedelta(days=_CALENDAR_BUFFER)).isoformat()
        end = date.today().isoformat()
        prices = fetch_prices(tickers, start=start, end=end, cache_dir=cache_dir)

        # Compute correlation
        corr = _compute_correlation_matrix(prices, tickers, window=_CORR_WINDOW)
        if corr is None:
            logger.warning("Correlation heatmap: insufficient price data")
            return none_ctx

        # Find the end date of the data used
        all_dates = set()
        for t in tickers:
            df = prices.get(t)
            if df is not None and not df.empty:
                all_dates.update(df.index)
        corr_date = max(all_dates).strftime("%Y-%m-%d") if all_dates else None

        fig_json = _build_heatmap_figure(corr, labels, tickers, n_us)
        logger.info("Correlation heatmap built: %d×%d, window=%d", len(tickers), len(tickers), _CORR_WINDOW)

        return {
            "correlation_fig_json": fig_json,
            "correlation_n_days": _CORR_WINDOW,
            "correlation_date": corr_date,
        }
    except Exception as exc:
        logger.warning("Correlation heatmap failed: %s", exc)
        return none_ctx
