# Symbol-based Google Trends sentiment — design

**Date:** 2026-06-26
**Status:** Draft design (decisions are defaults pending Jonas's confirmation)
**Backlog item:** "Symbol-based Google Trends sentiment (tickers, not theme words)" (PR #34)

> Planning started under a tight token budget. Decisions below are **defensible
> defaults chosen by the agent**, each flagged **(confirm)**. Anything marked (confirm)
> is safe to change before implementation without reworking the architecture.

## Problem

Today the Trends sentiment uses **one generic theme word per sector** (`oil`, `bank`,
`food`…) — see `config/sentiment_keywords.yaml`, only `keywords[s][0]` is used. Those are
consumer-intent searches, not financial attention, so the signal is ~noise. We want to
query the **actual instrument symbols** we track instead, which is the finance-intent form
of Trends shown to track investor attention (Da/Engelberg/Gao 2011).

## Goal

Replace the keyword source with a **per-sector aggregate of search interest in the
sector's instrument symbols** — the sector ETFs we benchmark/link, plus (where feasible)
the underlying US constituent stock symbols — while keeping the downstream scorer and the
"toggle-only, out of the canonical composite" behavior unchanged.

## Symbol universe per sector

For each `region|sector`, the query symbols are sourced from configs we already maintain
(no new hand-maintained symbol list — DRY):

1. **Primary sector ETF** — `config/universe.yaml` (`us_sectors` / `eu_sectors`), e.g.
   `US|Technology → XLK`, `EU|Technology → EXV3.DE`.
2. **Linked/related sector ETFs** — `config/sector_etfs.yaml` (per region per sector list
   of `{ticker,…}`), e.g. US Technology also `VGT`.
3. **(Phase 2) Underlying US constituents** — `fetch_sp500_constituents()`
   (`src/data/constituents.py`) returns `{sector: [yf_ticker,…]}`; take the **top-N most
   liquid** per sector. EU has **no constituent list**, so EU stays ETF-only.

**Excluded:** the broad-market benchmark (`RSP`/`SPY`/`EXSA.DE`) — it's not sector-specific
and would add the same term to every sector. **(confirm)**

## Decisions (defaults — confirm)

- **Phasing.** **Phase 1 = ETF symbols only** (primary + linked, both regions). **Phase 2 =
  add top-N US constituents.** This de-risks the volume problem and makes Phase 1
  shippable on its own. **(confirm)** — top-N default **N = 10** liquid names per US sector.
- **Cross-batch normalization (the core technical problem).** Trends scales 0–100 *within
  each ≤5-term payload*, so series from different batches aren't comparable. Use a **fixed
  anchor term in every batch** (a high, stable reference — default `"SPY"`) and divide each
  symbol's series by the anchor's series to put all batches on one scale. **(confirm anchor)**
- **Per-symbol → per-sector aggregation.** For each symbol: anchor-normalize the 13-week
  series, then **keep the normalized series** (not yet a slope). Aggregate to the sector as
  the **mean across that sector's live symbols**, producing one 13-week series per sector.
  This feeds the *existing* `_search_momentum` (slope) + cross-sector z-score unchanged.
  Equal weight in Phase 1; market-cap weighting deferred. **(confirm)**
- **Dead-term handling.** Drop any symbol whose series is all-zero / below a small volume
  floor before aggregating (most obscure tickers, esp. EU `.DE` symbols, won't register).
  If a sector has **no** live symbols → neutral `0.0` (existing fallback). **(confirm floor)**
- **Ambiguous tickers.** Maintain a small blocklist of symbols that collide with common
  words (`ALL, KEY, IT, ON, A, SO, DD, …`) and skip them. Trends *Topics* (entity mids) are
  the better fix but are deferred (overlaps the regional/Topics backlog item). **(confirm)**
- **Geo.** Keep worldwide (`geo=""`) for Phase 1 to bound scope; regional geo is tracked in
  the separate "Google Trends tab" item. **(confirm)**
- **Composite.** Unchanged — sentiment stays `blend_sentiment=False`, toggle-only.

## Honest risk to validate first

US sectors should get real signal (`XLK`, `VGT`, mega-cap constituents are searched). **EU
ETF tickers (`EXV3.DE`, `EXH1.DE`…) are obscure and will likely return mostly zeros** → EU
sentiment may stay near-neutral. **Acceptance gate:** before trusting the signal, confirm
the aggregated per-sector series are not predominantly zero (a quick coverage report). If
EU is dead, that's an accepted limitation for Phase 1, surfaced in the dashboard copy.

## Architecture / components

- **`src/data/trends_symbols.py`** (new) — the symbol layer:
  - `build_symbol_map(universe, sector_etfs, constituents=None, blocklist=…) -> {sector_key: [symbol,…]}`
  - `fetch_symbol_trends(symbol_map, anchor="SPY", cache_dir="data/cache") -> {sector_key: pd.Series}`
    — batched anchor-normalized fetch + dead-term drop + per-sector mean aggregation. Mirrors
    `trends.py` resilience (retry/backoff, partial-success, one cache/day).
- **`src/signals/sentiment.py`** — unchanged: consumes the per-sector series via the existing
  `_search_momentum` (slope) + `_cross_zscore`.
- **`scan.py`** — swap the `fetch_trends(...)` call for the symbol-based fetch feeding
  `_compute_sentiment_for_scan`. Keyword config retired (or kept as fallback). **(confirm)**
- **Config** — reuse `universe.yaml` + `sector_etfs.yaml` + constituents; add a tiny
  `config/trends_blocklist.yaml` (ambiguous tickers) + anchor constant.

## Data flow

```
build_symbol_map(universe, sector_etfs[, constituents])     # {US|Tech: [XLK,VGT,...]}
  └─ fetch_symbol_trends(anchor="SPY")
        for each ≤4-symbol batch + anchor:
          interest_over_time (today 3-m, geo="") → 0–100
          normalize each series by the anchor series
        drop dead/ambiguous symbols
        mean live symbols per sector → {sector_key: 13-wk series}
  └─ compute_sentiment_score(trends_data=…, reddit=None, finnhub=None)  # existing slope+z
        → sentiment_score per sector_key (toggle-only)
```

## Testing

- `build_symbol_map`: correct symbols per sector from the configs; blocklist applied;
  constituents included only in Phase 2 path.
- `fetch_symbol_trends` (mocked pytrends): anchor normalization math; dead-term drop;
  per-sector mean; partial-batch failure → neutral; empty sector → 0.0.
- Aggregated series shape feeds `_search_momentum` correctly (existing tests stay green).
- A **coverage check** util: % of sectors/symbols with non-zero series (the acceptance gate).

## Out of scope (later / other items)

- Trends Topics (entity disambiguation), regional geo → the "Google Trends tab" backlog item.
- Market-cap weighting; a dedicated sentiment dashboard tab; feeding sentiment into the
  canonical composite.

## Open questions for Jonas (the (confirm) flags)

1. Phase 1 ETF-only first, then constituents? (default yes) — and N=10 constituents/sector?
2. Anchor term `SPY` ok, or prefer another stable high-volume reference?
3. Equal-weight aggregation ok for now (vs market-cap weighting)?
4. Retire the old `sentiment_keywords.yaml` keyword path, or keep it as a fallback?
5. Accept EU likely-sparse for Phase 1 (ETF tickers obscure), with an honest dashboard note?
