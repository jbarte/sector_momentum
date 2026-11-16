"""Breakdown panel and instrument HTML for sector/theme rows."""

from __future__ import annotations

from dashboard.rows import _safe_float, _format_raw_value
from src.universe import is_unbuyable, unbuyable_names  # noqa: F401


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
    "acceleration":        {"label": "Momentum Accel.",     "group": "info"},
    "ma50_slope":          {"label": "50-DMA Slope",        "group": "change"},
    "obv_slope":           {"label": "OBV Trend",           "group": "change"},
    "return_1m":           {"label": "1M Return",           "group": "change"},
    "breadth_above_50dma": {"label": "Breadth >50-DMA",     "group": "info"},
    "max_dd_1y":           {"label": "Max Drawdown (1y)",   "group": "info"},
}

_SIGNAL_DESCRIPTIONS: dict[str, str] = {
    "rs_ratio":            "Relative strength vs benchmark over 12 weeks, normalised to 100. Above 100 = sector outperforming; below 100 = underperforming.",
    "return_3m":           "Price return of the sector ETF over the last 3 months. Measures medium-term absolute momentum.",
    "return_6m":           "Price return over the last 6 months. Longer-window confirmation of trend direction.",
    "above_50dma":         "How far the ETF price sits above its 50-day moving average. Positive = price above MA (bullish structure).",
    "above_200dma":        "Distance from the 200-day moving average. Positive = sector is in a long-term uptrend.",
    "rs_momentum":         "Rate of change of relative strength — whether the sector is outperforming faster or slower than last week. Above 100 = accelerating.",
    "acceleration":        "1-month return minus 3-month return. Stored for reference but no longer scored: because it subtracts the 3-month return, which is itself a Level signal, it partly cancelled the composite it belonged to.",
    "ma50_slope":          "Slope of the 50-day moving average. Positive = MA rising (uptrend intact); negative = MA rolling over.",
    "obv_slope":           "Slope of On-Balance Volume. Rising OBV means volume is flowing into the sector, confirming price strength with buying pressure.",
    "return_1m":           "Price return over the last month — the fastest-moving strength measure, and the Change pillar's read on whether the most recent stretch is stronger than the trailing window.",
    "breadth_above_50dma": "Percentage of stocks in the sector trading above their own 50-DMA. High breadth = broad-based rally, not just a few large caps.",
    "max_dd_1y":           "Largest peak-to-trough decline of the sector ETF over the last year. Closer to 0% = a shallower worst-case loss (steadier); more negative = a deeper historical drawdown. Info-only — not part of the score.",
}


def _build_instruments_html(
    theme_key: str,
    sector_etfs: dict,
    themes_cfg: dict | None = None,
) -> str:
    """Render the Instruments table for a sector breakdown panel."""
    import html as _html

    region, sector_name = theme_key.split("|", 1)
    if region == "THEME" and themes_cfg:
        etf_list = themes_cfg.get("ucits", {}).get(sector_name, [])
    else:
        etf_list = sector_etfs.get(region, {}).get(sector_name, [])

    # An unbuyable theme has no UCITS row precisely because none exists. Saying
    # so is the point of the flag — an empty panel reads as missing data, and
    # the reader is left to wonder which fund to buy for a theme that may be
    # sitting at rank 1.
    if is_unbuyable(region, sector_name, themes_cfg):
        return (
            '<div class="bd-instruments">'
            '<div class="sig-title">UCITS Alternative</div>'
            '<p class="bd-unbuyable">'
            "None exists — this theme cannot be bought from an EU account. It is "
            "still scored, because it shapes how every other theme ranks, but it "
            "is never held: no entry is prompted here and the backtest excludes it."
            "</p></div>"
        )

    if not etf_list:
        return ""

    is_ucits = region == "THEME"
    cards = ""
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
            f'<a class="etf-link" href="{_html.escape(url)}" target="_blank" rel="noopener" '
            f'aria-label="Open {_html.escape(name)} (opens in a new tab)">Open &#8599;</a>'
            if url else ""
        )
        meta = []
        if ter != "":
            meta.append(f'<span class="etf-ter">TER {_html.escape(str(ter))}</span>')
        if isin:
            meta.append(f'<span class="etf-isin">{_html.escape(isin)}</span>')
        if is_ucits and match:
            meta.append(f'<span class="etf-match etf-match-{_html.escape(match)}">'
                        f'{_html.escape(match.capitalize())} match</span>')
        if link:
            meta.append(link)
        cards += (
            f'<li class="etf-card">'
            f'<div class="etf-head"><span class="etf-ticker">{_html.escape(ticker)}</span> '
            f'<span class="etf-name">{_html.escape(name)}</span></div>'
            f'<div class="etf-meta">{"".join(meta)}</div>'
            f'</li>'
        )

    title = "UCITS Alternative" if is_ucits else "Instruments"
    return (
        f'<div class="bd-instruments">'
        f'<div class="sig-title">{title}</div>'
        f'<ul class="etf-list">{cards}</ul>'
        f'</div>'
    )


def _z_bar(z_v: float) -> tuple[str, str]:
    """Return (bar_html, chip_html) for a signed z-score.

    Centre-origin: a z-score is signed, so the bar grows left for negative and
    right for positive. Filling from the left edge with width proportional to
    |z| rendered -2.5 and +2.5 identically, leaving colour as the only cue.
    """
    half = min(abs(z_v) / 3.0, 1.0) * 50          # % of the track, from centre
    side = "left:50%" if z_v >= 0 else "right:50%"
    if z_v >= 0.5:
        color, chip = "#8FA77A", '<span class="sig-chip bull">▲</span>'
    elif z_v <= -0.5:
        color, chip = "#BF6F50", '<span class="sig-chip bear">▼</span>'
    else:
        color, chip = "#C4B89A", '<span class="sig-chip neut">—</span>'
    bar = (
        f'<span class="z-bar-wrap">'
        f'<span class="z-bar" style="{side};width:{half:.1f}%;'
        f'background:{color}"></span>'
        f'</span>'
    )
    return bar, chip


def _build_breakdown_html(
    theme_key: str,
    score_row: dict,
    sector_signals: list[dict],
    universe: dict,
    weights: dict,
    sector_etfs: dict | None = None,
    themes_cfg: dict | None = None,
) -> str:
    """Pre-render the breakdown panel for one sector (or theme) row."""
    import html as _html

    region, sector_name = theme_key.split("|", 1)

    # Ticker + benchmark from the themes config.
    _tcfg = (themes_cfg or {}).get("themes", {}).get(sector_name)
    ticker = (_tcfg["ticker"] if isinstance(_tcfg, dict) else _tcfg) if _tcfg else "—"
    benchmark = (themes_cfg or {}).get("benchmark", "ACWI")

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

    # The composite as one line: Composite 1.060 = ½ Level 1.347 + ½ Change 0.772. The
    # weights are the real ones from the config (a drawn tree said the same in 7 lines).
    def share(w: float) -> str:
        return "½" if abs(w - 0.5) < 1e-9 else f"{w * 100:.0f}%"

    # Each term is one unit, so a narrow screen breaks between the terms, not inside one.
    parts = (
        f'<span class="bd-term">{share(level_weight)} Level <b class="bd-part">{level_score}</b></span> '
        f'+ <span class="bd-term">{share(chg_weight)} Change <b class="bd-part">{change_score}</b></span>'
    )
    if abs(data_weight - 1.0) > 1e-9:           # the data pillar is the whole score today
        parts = f'{data_weight * 100:.0f}% &times; ({parts})'
    summary = (
        f'<div class="bd-summary">'
        f'<p class="bd-formula"><span class="bd-formula-lead">Composite</span> '
        f'<b class="st-composite-val">{composite}</b> <span class="bd-eq">=</span> {parts}</p>'
        f'<p class="bd-sub">ETF {_html.escape(str(ticker))} &middot; '
        f'benchmark {_html.escape(str(benchmark))}</p>'
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
            bar, chip = _z_bar(z_v)
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
    for n in ("above_200dma", "acceleration", "breadth_above_50dma", "max_dd_1y"):
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

    def section(title: str, rows: str, n: int) -> str:
        return (
            f'<div class="sig-section">'
            f'<div class="sig-title">{title} &middot; {n} signal{"" if n == 1 else "s"}</div>'
            f'<table class="sig-table"><tbody>{rows}</tbody></table>'
            f'</div>'
        )

    signals = (
        section("Level", level_rows, level_rows.count("<tr>"))
        + section("Change", change_rows, change_rows.count("<tr>"))
    )

    instruments = _build_instruments_html(
        theme_key, sector_etfs or {}, themes_cfg=themes_cfg,
    )
    return (
        f'<div class="breakdown-inner">'
        f'{summary}'
        f'<div class="breakdown-grid">{signals}</div>'
        f'{info_html}'
        f'{instruments}'
        f'</div>'
    )
