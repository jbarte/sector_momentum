"""Breakdown panel and instrument HTML for sector/theme rows."""

from __future__ import annotations

from dashboard.rows import _safe_float, _format_raw_value


# ---------------------------------------------------------------------------
# Signal metadata for leaderboard breakdown
# ---------------------------------------------------------------------------

_SIGNAL_META: dict[str, dict] = {
    "rs_ratio":            {"label": "Relative Strength",  "group": "level"},
    "return_3m":           {"label": "3M Return",           "group": "level"},
    "return_6m":           {"label": "6M Return",           "group": "level"},
    "above_50dma":         {"label": "Dist. from 50-DMA",   "group": "level"},
    "above_200dma":        {"label": "Dist. from 200-DMA",  "group": "info"},
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


def _build_instruments_html(
    sector_key: str,
    sector_etfs: dict,
    themes_cfg: dict | None = None,
) -> str:
    """Render the Instruments table for a sector breakdown panel."""
    import html as _html

    region, sector_name = sector_key.split("|", 1)
    if region == "THEME" and themes_cfg:
        etf_list = themes_cfg.get("ucits", {}).get(sector_name, [])
    else:
        etf_list = sector_etfs.get(region, {}).get(sector_name, [])
    if not etf_list:
        return ""

    is_ucits = region == "THEME"
    rows = ""
    for etf in etf_list:
        ticker  = etf.get("ticker", "")
        name    = etf.get("name", "")
        ter     = etf.get("ter", "")
        isin    = etf.get("isin", "")
        url     = etf.get("url", "")
        if url and not (url.startswith("https://") or url.startswith("http://")):
            url = ""  # Drop URLs without valid scheme
        match   = etf.get("match", "")
        link    = (
            f'<a href="{_html.escape(url)}" target="_blank" rel="noopener">↗</a>'
            if url else ""
        )
        match_cell = (
            f'<td class="etf-match etf-match-{_html.escape(match)}">'
            f'{_html.escape(match)}</td>'
        ) if is_ucits else ""
        rows += (
            f"<tr>"
            f'<td class="etf-ticker">{_html.escape(ticker)}</td>'
            f'<td class="etf-name">{_html.escape(name)}</td>'
            f'<td class="etf-ter">{_html.escape(str(ter))}</td>'
            f'<td class="etf-isin">{_html.escape(isin)}</td>'
            f'{match_cell}'
            f'<td class="etf-link">{link}</td>'
            f"</tr>"
        )

    title = "UCITS Alternative" if is_ucits else "Instruments"
    match_header = "<th>Match</th>" if is_ucits else ""
    return (
        f'<div class="bd-instruments">'
        f'<div class="sig-title">{title}</div>'
        f'<table class="etf-table">'
        f"<thead><tr>"
        f"<th>Ticker</th><th>Name</th><th>TER</th><th>ISIN</th>"
        f"{match_header}<th></th>"
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
    themes_cfg: dict | None = None,
) -> str:
    """Pre-render the breakdown panel for one sector (or theme) row."""
    import html as _html

    region, sector_name = sector_key.split("|", 1)

    # Ticker + benchmark from universe (or themes config for the THEME track)
    if region == "THEME":
        ticker = (themes_cfg or {}).get("themes", {}).get(sector_name, "—")
        benchmark = (themes_cfg or {}).get("benchmark", "ACWI")
    elif region == "US":
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
        f'<div class="score-tree" data-sector-key="{_html.escape(sector_key)}">'
        f'<div class="st-row st-top">'
        f'<span class="st-label">Composite</span>'
        f'<span class="st-val st-composite-val">{composite}</span>'
        f'</div>'
        f'<div class="st-row">'
        f'<span class="st-conn">├─</span>'
        f'<span class="st-label">Data Score</span>'
        f'<span class="st-wt st-data-wt">(100%)</span>'
        f'<span class="st-val st-data-val">{data_score}</span>'
        f'</div>'
        f'<div class="st-row st-sub">'
        f'<span class="st-conn">│ ├─</span>'
        f'<span class="st-label">Level</span>'
        f'<span class="st-wt">({level_weight*100:.0f}%)</span>'
        f'<span class="st-val">{level_score}</span>'
        f'<span class="st-meta">4 signals</span>'
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
        f'<span class="st-wt st-sent-wt">(0%)</span>'
        f'<span class="st-val st-sent-val">{fv(score_row.get("sentiment_score"))}</span>'
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
            f'<span class="sig-tip" tabindex="0" data-tip="{_html.escape(tip)}"'
            f' aria-label="{_html.escape(meta["label"])}: {_html.escape(tip)}">'
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
    for n in ("above_200dma", "return_1m", "breadth_above_50dma"):
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

    instruments = _build_instruments_html(
        sector_key, sector_etfs or {}, themes_cfg=themes_cfg,
    )
    return (
        f'<div class="breakdown-inner">'
        f'<div class="breakdown-grid">'
        f'<div class="bd-left">{tree}</div>'
        f'<div class="bd-right">{signals}</div>'
        f'</div>'
        f'{instruments}'
        f'</div>'
    )
