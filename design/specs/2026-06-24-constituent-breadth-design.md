# Constituent Breadth — Design

> Status: approved 2026-06-24. Phase 3, sub-project 1 of 5. Replaces the
> single-ETF breadth *proxy* with **true constituent breadth** for US sectors:
> the % of each sector's S&P 500 constituents trading above their own 50-DMA.
> Info-only (not scored). EU sectors have no free constituent source and will
> show "—".

## Goal

Make `breadth_above_50dma` mean what the dashboard already claims it means —
"percentage of stocks in the sector trading above their own 50-DMA" — for US
sectors, by computing it from actual S&P 500 constituents instead of the current
single-ETF-price proxy.

## Non-goals

- **Not scored.** Breadth stays an info-only signal; the composite, weights, and
  ranking are untouched. (Promotion to a scored signal is a possible later item.)
- **No EU constituent breadth.** No clean free STOXX-600-by-sector source; EU
  sectors report NaN ("—") rather than a misleading proxy.
- **No new market-cap weighting.** Equal-weight across constituents (textbook
  breadth). No cap-weighted or top-N variants.

## Context (current state)

- `breadth_above_50dma` is **info-only** — it is NOT in `weights.yaml`'s
  `level_signals`/`change_signals`, and `dashboard/build.py:_SIGNAL_META` tags it
  group `"info"`. It is displayed in the breakdown panel's "Not scored" line.
- It is currently produced by `compute_breadth_proxy(close)` in
  `src/signals/technical.py`, which returns a 1.0 / 0.0 / −1.0 *flag* from the
  ETF's own price vs its 50-DMA — NOT constituent breadth. `build.py`
  `_format_raw_value` renders it as `v*100` → today every sector shows a
  misleading "100% / 0% / −100%".
- The breakdown tooltip (`_SIGNAL_DESCRIPTIONS["breadth_above_50dma"]`) already
  says "Percentage of stocks in the sector trading above their own 50-DMA." So
  this work fixes a correctness gap, and needs **no template/tooltip change** for
  US display.
- US sectors map to SPDR Select Sector ETFs in `config/universe.yaml`
  (`us_sectors`); the 11 GICS sector keys are: Technology, Financials, Energy,
  Health Care, Industrials, Consumer Discretionary, Consumer Staples, Utilities,
  Materials, Real Estate, Communication Services.
- `fetch_prices(tickers, start, end) -> dict[ticker, DataFrame]` already exists
  (`src/data/prices.py`) and returns only the tickers it fetched successfully.

## Decisions (from brainstorming)

- **Source:** S&P 500 constituents via the Wikipedia "List of S&P 500 companies"
  GICS table. Free, no API key, GICS-aligned. US-only.
- **Coverage:** ALL constituents, equal-weight. breadth = % above own 50-DMA.
- **Scored:** info-only (display only; do not touch composite/weights).
- **Degradation:** every step is non-fatal — failure → breadth NaN, scan still
  completes.

## Architecture & data flow

```
scan.py main()  — after ETF prices fetched, before scoring
  constituents = fetch_sp500_constituents()          # src/data/constituents.py
      → {our_sector: [yf_ticker, ...]}  or None
  if constituents:
      all_tickers = unique(flatten(constituents.values()))
      cons_prices = fetch_prices(all_tickers, start, end)   # reuse existing fetcher
      breadth = compute_constituent_breadth(cons_prices, constituents)  # src/signals/breadth.py
      → {"US|<sector>": pct_above_50dma in [0,1], or NaN if under-covered}
  # inject into US sector rows
  for row in rows where row.region == "US":
      row["breadth_above_50dma"] = breadth.get("US|"+row.gics_sector, NaN)
  for row in rows where row.region == "EU":
      row["breadth_above_50dma"] = NaN     # no constituent data → "—"
```

The whole block is wrapped non-fatally: any exception logs a warning and leaves
all breadth values as NaN, and the scan still completes. The stored
`breadth_above_50dma` for US rows is true constituent breadth (or NaN when
under-covered/failed); EU rows are always NaN. The old `compute_breadth_proxy`
value is never used for the stored signal (see Open items re: removing it).

## Components

### `src/data/constituents.py`

```
fetch_sp500_constituents(cache_dir="data/cache", ttl_days=7) -> dict[str, list[str]] | None
```
- Cache file `data/cache/sp500_constituents.json`. If present and younger than
  `ttl_days`, load and return it (no scrape).
- Else scrape `https://en.wikipedia.org/wiki/List_of_S%26P_500_companies` via
  `pandas.read_html(...)[0]`; use columns `Symbol` and `GICS Sector`.
- Map GICS Sector → our sector key via `_GICS_TO_SECTOR` (only non-identity entry:
  `"Information Technology" -> "Technology"`; all others identity). A GICS Sector
  value not in the map is logged and skipped (don't crash).
- Normalize tickers: `.` → `-` (e.g. `BRK.B`→`BRK-B`, `BF.B`→`BF-B`).
- Return `{our_sector: [ticker, ...]}`. On any failure (network, parse, empty)
  log a warning and return `None`.

### `src/signals/breadth.py`

```
compute_constituent_breadth(
    prices: dict[str, pd.DataFrame],          # ticker -> OHLCV frame (has "Close")
    constituents: dict[str, list[str]],       # our_sector -> [ticker]
    min_coverage: float = 0.60,
) -> dict[str, float]
```
- For each sector, for each constituent ticker present in `prices` with ≥50 valid
  closes: `above = last_close > mean(last 50 closes)`.
- `valid` = constituents with ≥50 closes available; `fetched_ratio = valid /
  len(sector constituents)`.
- If `fetched_ratio < min_coverage` OR `valid == 0` → `NaN` for that sector.
- Else `breadth = count(above) / valid` (float 0..1).
- Key results as `"US|<sector>"`.

### `scan.py`
- Import `fetch_sp500_constituents`, `compute_constituent_breadth`.
- Insert the non-fatal block above after `prices = fetch_prices(...)` and before
  scoring. Use `start`/`end`/`fetch_prices` already in scope. ~500 extra tickers.
- Inject `breadth_above_50dma` into rows as specified (US = true/NaN, EU = NaN).
  This overrides whatever `_compute_signals_for_sector` set for that key.

### `config/universe.yaml`
- No change required. US sector keys already match GICS names (post-mapping).

## Display

- No template or `_SIGNAL_META`/`_SIGNAL_DESCRIPTIONS` change. US sectors now show
  a real percentage (e.g. `62%`); EU sectors show "—" (NaN via `_format_raw_value`
  → the existing `if value is None: return "—"` path; ensure NaN maps to None via
  `_safe_float`, which it does).

## Error handling / degradation

- `fetch_sp500_constituents()` returns `None` on failure → skip breadth; US + EU
  rows get NaN → "—". Scan completes.
- Partial price fetch → breadth computed over what fetched, subject to the 60%
  min-coverage guard (under-covered sector → NaN).
- A single constituent with bad/short data is simply excluded from its sector's
  denominator.

## Cost

- Adds ~500 tickers to each scan (~20× today's 24), ≈1–3 min wall-clock,
  network-dependent. Mitigated by the 7-day constituent-list cache and batched
  price fetch. Acceptable for the daily CI scan; flagged as a real scan-time
  increase.

## Testing

- **constituents.py:** fixture HTML table → correct `{sector: [tickers]}`;
  `"Information Technology"`→`"Technology"` mapping; `BRK.B`→`BRK-B` normalization;
  cache-hit path skips scrape (monkeypatch read_html, assert not called); scrape
  exception → `None`.
- **breadth.py:** constructed constituent price frames → exact breadth fraction;
  60% min-coverage guard → NaN; zero-valid / empty sector → NaN; ticker present in
  constituents but missing from prices is excluded from denominator.
- **scan.py:** constituent fetch returning `None` (monkeypatched) leaves breadth
  NaN and the scan still produces sector rows (non-fatal contract); US rows get
  the injected value, EU rows get NaN.

## Open items (resolve during implementation)

- Whether to delete `compute_breadth_proxy` from `technical.py` (now unused for
  the stored value) or leave it. Lean: remove it and its call in
  `_compute_signals_for_sector`, replacing with NaN default, to avoid dead/
  misleading code — but confirm nothing else references it.
- `lxml`/`html5lib` is required by `pandas.read_html`; add to `requirements.txt`
  if not already resolvable.

## Files touched

| File | Change |
|------|--------|
| `src/data/constituents.py` | new — scrape + cache S&P 500 GICS constituents |
| `src/signals/breadth.py` | new — equal-weight % above 50-DMA per sector |
| `scan.py` | non-fatal constituent fetch + breadth injection into rows |
| `src/signals/technical.py` | likely remove `compute_breadth_proxy` + its call |
| `requirements.txt` | add `lxml` (and/or `html5lib`) if needed for read_html |
| `tests/test_constituents.py` | new |
| `tests/test_breadth.py` | new |
| `tests/test_scan_smoke.py` | extend — non-fatal breadth contract |
