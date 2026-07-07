# Region-aware Google Trends pulls — design

**Date:** 2026-07-07
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/trends-region-aware`

## Problem

`fetch_symbol_trends` (`src/data/trends_symbols.py`) currently queries every
symbol with `geo=""` (worldwide) and a single `SPY` anchor. Two consequences:

- **No region-specific attention.** US mega-brands dominate worldwide search, so
  EU sectors get little genuine local signal — the EU gap the backlog calls out.
- **`SPY` is a weak anchor.** It's a US ETF ticker with modest, event-driven search
  volume; it has effectively none outside the US, so it cannot anchor a European
  pull at all.

## Goal

Query each region's sectors in region-appropriate geographies and normalize every
geo against one stable, ubiquitous anchor:

- **US** sectors → `geo="US"`.
- **EU** sectors → averaged across `DE`, `FR`, `GB` (the three largest European
  markets; Google Trends has no single "Europe" geo).
- **Anchor** → a stable, ubiquitous, same-spelling-everywhere term (default
  `"YouTube"`), replacing `SPY`.

Everything downstream — per-ticker aggregation, entity-mid substitution, derived
signals, scoring — stays unchanged (ticker-keyed).

**Non-goals:** the Trends day-cache (separate backlog item), comparative
cross-sector payloads, seasonal baselines, rising queries. No change to which
tickers belong to which sector. Sentiment stays toggle-only.

## Key decisions

1. **EU = average DE+FR+GB.** Truer to a pan-European index than any single
   country, at the cost of ~3× the EU API calls.
2. **One ubiquitous anchor across all geos (default `"YouTube"`), configurable.**
   An anchor's job is to be a *flat, high-volume baseline* that stitches Trends'
   per-payload 0–100 scaling across batches. Finance anchors (SPY/DAX/CAC/FTSE)
   are the wrong instinct — they spike on the same market events that move sector
   attention, injecting correlated noise, and their volume is modest. `"YouTube"`
   has enormous, stable volume with identical spelling in US/DE/FR/GB (unlike
   `"news"`, which localizes to *Nachrichten*/*actualités*). Using the *same*
   anchor in every geo also makes each term "% of YouTube attention in that geo,"
   which renders US and EU relative attention genuinely cross-region comparable.
3. **Config-driven `region → geo(s)` map + anchor**, so the geo set and anchor are
   data, not code.

Rejected: dropping the anchor (with ~9 batches per geo, a sector's symbols land in
different independently-scaled payloads, so their averaged series and slope become
meaningless — the anchor is load-bearing for aggregation).

## Components

### 1. `config/trends_geo.yaml` (new)

```yaml
# Region-aware Google Trends configuration.
anchor: YouTube            # stable, ubiquitous; same spelling in every geo
region_geos:
  US: [US]                 # US sectors → US search interest
  EU: [DE, FR, GB]         # EU sectors → averaged across the 3 largest markets
```

Loaded in `scan.py`. Missing file → in-code defaults with these same values.

### 2. `fetch_symbol_trends` restructure (`src/data/trends_symbols.py`)

Signature changes:

```python
def fetch_symbol_trends(
    symbol_map: dict[str, list[str]],
    anchor: str = "YouTube",                       # was "SPY"
    client=None,
    timeframe: str = "today 3-m",
    window: int = 13,
    batch_size: int = 4,
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,   # new; default below
) -> dict[str, pd.Series]:
```

Default `region_geos` (when `None`): `{"US": ["US"], "EU": ["DE", "FR", "GB"]}`.

New flow:
1. **Partition** `symbol_map` into `{region: [unique symbols]}` by splitting each
   `REGION|Sector` key on `|` (pure helper `_symbols_by_region`).
2. For each region, look up its geo list (`region_geos.get(region, [""])` — a region
   with no mapping falls back to worldwide, preserving today's behavior for any
   unexpected region key).
3. For each geo in the list: batch that region's symbols (`batch_size`), and for each
   batch do the existing sequence — `_resolve_query_terms` (entity-mid substitution),
   `build_payload([anchor] + query_terms, timeframe=timeframe, geo=<geo>)`,
   `interest_over_time()`, re-key columns to tickers (`_rekey_by_ticker`), normalize
   (`_normalize_by_anchor`). This yields a `{ticker: series}` map **for that geo**.
4. For a region with **one** geo (US): that map is the region's result. For a region
   with **multiple** geos (EU): average each ticker's per-geo series across the geos
   where it is live (non-zero) via pure helper `_average_geo_series`; a ticker dead in
   all geos stays zero.
5. Merge all regions' `{ticker: series}` into `norm_by_symbol`, then
   `return _aggregate(norm_by_symbol, symbol_map, window=window)` — unchanged.

The client-init failure path still returns `_aggregate({}, symbol_map, window)`
(all-neutral), as today.

### 3. Pure helpers (new, unit-tested)

```python
def _symbols_by_region(symbol_map: dict[str, list[str]]) -> dict[str, list[str]]:
    """Group unique symbols by the region prefix of each 'REGION|Sector' key."""

def _average_geo_series(
    per_geo: list[dict[str, list[float]]],
    window: int,
) -> dict[str, list[float]]:
    """Average each ticker's series across geos, per element.

    Input: one {ticker: series} map per geo. For each ticker, average across the
    geos where its series is live (has a non-zero value); a ticker live in no geo
    yields an all-zero series of length `window`.
    """
```

### 4. `scan.py` wiring

- Load `config/trends_geo.yaml` (missing → defaults); extract `anchor` and
  `region_geos`.
- Pass both to `fetch_symbol_trends`.
- Log the geos used per region, e.g. `"Trends geos: US→US, EU→DE,FR,GB (anchor=YouTube)"`.

### 5. Dashboard explainer (`sentiment.html.j2` + `_i18n.html.j2`)

Update the "Method" / "Metod" paragraph to note: US sectors use US search interest,
EU sectors are averaged over DE/FR/GB, and interest is normalized against a neutral
ubiquitous anchor. Add a one-line note that this changed the anchor, so stored
history predating the change is not directly comparable.

## Data flow

```
config/trends_geo.yaml ──> anchor, region_geos
symbol_map {REGION|Sector: [tickers]}
   │  _symbols_by_region
   ▼
{US: [...], EU: [...]}
   │  per region, per geo in region_geos[region]:
   │     batch → _resolve_query_terms → build_payload(geo=…) → interest_over_time
   │           → _rekey_by_ticker → _normalize_by_anchor   ⇒ {ticker: series}_geo
   │  EU: _average_geo_series over [DE, FR, GB]
   ▼
norm_by_symbol {ticker: series}   [UNCHANGED from here]
   └─> _aggregate ─> score_symbol_sentiment / derived_signals
```

## Error handling

- Missing `trends_geo.yaml` → in-code defaults (US→US, EU→DE/FR/GB, anchor YouTube).
- A region key with no geo mapping → `[""]` (worldwide) fallback, so an unexpected
  region still returns data rather than nothing.
- A dead anchor in some geo → existing `_normalize_by_anchor` "anchor dead"
  passthrough (raw values) applies, same as today.
- A batch that 429s / errors → existing retry/backoff, then those symbols neutral
  for that geo (already handled in the loop).

## Testing

Pure-function unit tests (no network):
- `_symbols_by_region` groups by prefix, de-dupes within a region.
- `_average_geo_series` averages live geos per element, drops dead geos from the
  mean, and returns all-zero for a ticker live in no geo.

Integration test with a `FakeClient` that records each `(kw_list, geo)` call:
- US symbols are queried with `geo="US"`; EU symbols with `geo` in `{DE, FR, GB}`.
- The anchor sent is `"YouTube"` (not `SPY`).
- An EU sector's resulting series equals the average of its per-geo series.

**Not additive** — existing fetch and entity-mid fake-client tests assert the old
`geo=""` / `SPY` behavior; they are **updated** to the new anchor/geo expectations
(the change is intentional). Full suite must stay green afterward.

## Honest caveats

- **~4× the API calls** (US ~9 batches + EU ~9×3 geos ≈ 36 batches vs ~9 today):
  materially longer runtime under the existing inter-batch sleep, and higher 429
  risk. The day-cache that mitigates this is a separate backlog item and is **not**
  built here. If 429s prove disruptive, that cache is the follow-up.
- **History break:** changing the anchor shifts all sentiment values; stored
  `sentiment_score` from before this change is not comparable to after. Cosmetic —
  sentiment is toggle-only and info-only — but noted on the page.

## Out of scope / follow-ups

- Trends day-cache (`trends_symbols_<date>.json`) to cut repeat API load.
- Comparative cross-sector payloads, seasonal baseline, rising queries.
- Tuning the EU geo set (add/remove countries) — trivial config edit later.
