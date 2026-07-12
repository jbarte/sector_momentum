# Sentiment Enrichment: Seasonal Baseline + Rising Queries

**Date:** 2026-07-12
**Status:** Approved
**Backlog item:** Sentiment page — enrichment (get more out of Google Trends)

## Summary

Two additions to the Google Trends sentiment pipeline:

1. **Seasonal baseline** — extend the Trends fetch from 3 months (13 weeks) to
   12 months (52 weeks). Existing derived signals still operate on the trailing
   13 weeks. A new `seasonal_ratio` signal compares recent interest to its
   trailing baseline, surfacing whether a sector's attention is above or below
   its own historical norm.

2. **Rising queries** — call `pytrends.related_queries()` once per sector
   (primary representative term) to surface emerging search terms. Displayed as
   an expandable panel per row on the sentiment page.

Both are info-only / toggle-only — neither affects the momentum composite score.

## Approach

**Single extended fetch** (Approach A). Change the existing `fetch_symbol_trends`
timeframe from `today 3-m` to `today 12-m`. One API call set, same call count as
before. Derived signals slice the trailing 13 weeks. Seasonal ratio uses the full
52-week series.

Rising queries are a separate fetch pass (one `related_queries()` call per sector
per geo), cached in the day-cache, fail-open.

## Data Fetch Changes

### Extended timeframe

- `fetch_symbol_trends`: default `timeframe` → `"today 12-m"`, `window` → `52`
- `_fetch_geo`: returns 52-point series
- `_aggregate`: passes through the full series
- `scan.py`: `window=52, timeframe="today 12-m"`

### `derived_signals` changes

Receives 52-week series. Existing signals operate on `series[-13:]`:

| Signal | Input |
|--------|-------|
| momentum | `series[-13:]` |
| acceleration | `series[-13:]` |
| range_position | `series[-13:]` |
| spike | `series[-13:]` |
| volatility | `series[-13:]` |
| **seasonal_ratio** (new) | full series |

**`seasonal_ratio`**: `mean(series[-13:]) / mean(series[:-13])`. Values >1.0 mean
current interest exceeds its trailing baseline; <1.0 means below. Returns `NaN` if
the trailing portion (first 39 weeks) averages zero.

`DERIVED_SIGNAL_NAMES` updated to include `"seasonal_ratio"`.

### Cache impact

Day-cache keys include batch terms but not timeframe. The timeframe change means
the first scan post-deploy fetches live data (can't reuse a same-day 3-month
cache entry). No structural cache change needed — subsequent runs cache normally.

## Rising Queries Fetch

### New function: `fetch_rising_queries()`

Location: `src/data/trends_symbols.py`

**Signature:**
```python
def fetch_rising_queries(
    symbol_map: dict[str, list[str]],
    client=None,
    timeframe: str = "today 12-m",
    sleep_s: float = 20.0,
    max_retries: int = 3,
    entities: dict[str, str] | None = None,
    region_geos: dict[str, list[str]] | None = None,
    cache: dict | None = None,
) -> dict[str, list[dict]]:
```

**Returns:** `{"US|Technology": [{"query": "nvidia stock", "growth": "2400%"}, ...], ...}`

**Logic:**
1. For each region, pick one representative term per sector (first symbol's
   entity mid or raw ticker — same logic as `fetch_comparative_interest`).
2. For each term × geo: `build_payload([term], timeframe, geo)` then
   `related_queries()`. Extract the `"rising"` DataFrame (columns: `query`,
   `value`).
3. Take top 5 per sector. Deduplicate across geos (union, keep highest growth).
4. `value` is an int (% growth) or the string `"Breakout"` (>5000% growth).
   Store as `{"query": str, "growth": str}`.

**API cost:** ~22 sectors × 1-3 geos = ~22-55 calls. Each is a lightweight
metadata call (no time-series). Cached under `"rising_{geo}"` in the day-cache.

**Error handling:** Entirely fail-open. Any failure → log warning, return empty
dict for that sector. No impact on other signals.

## Schema Change

### `sentiment_signals` table

Add `text_value TEXT` column:

```sql
ALTER TABLE sentiment_signals ADD COLUMN IF NOT EXISTS text_value TEXT;
```

Added to the DDL in `src/state.py` and as an idempotent `ALTER TABLE` in
`init_db()` for existing databases.

- Numeric signals: `value = <float>`, `text_value = NULL`
- Rising queries: `value = NULL`, `signal_name = "rising_queries"`,
  `text_value = <JSON string>`

### `save_scan` update

INSERT statement includes `text_value`. The `sentiment_signals_df` DataFrame
gains an optional `text_value` column (default `None`/`NaN`).

### `src/backup.py`

`_COLUMNS["sentiment_signals"]` updated to include `text_value`.

## scan.py Wiring

### Step 8 changes

```python
_trends_by_key = fetch_symbol_trends(
    _symbol_map, anchor=_anchor, entities=_entities,
    region_geos=_region_geos, cache=_cache,
    timeframe="today 12-m", window=52,
)
```

`derived_signals()` call unchanged — it handles the longer series internally.

### New Step 8c: Rising queries

After comparative interest (Step 8b):

```python
from src.data.trends_symbols import fetch_rising_queries
_rising = fetch_rising_queries(
    _symbol_map, entities=_entities, region_geos=_region_geos,
    cache=_cache,
)
# Append as text_value rows to sentiment_signals_df
```

Each sector with rising queries gets one row: `signal_name="rising_queries"`,
`value=None`, `text_value=json.dumps(queries_list)`.

## Dashboard Changes

### Sentiment signals table (`sentiment.html`)

- New **Seasonal** column showing `seasonal_ratio` formatted as `1.32x`.
  Values >1.0 get a subtle green tint, <1.0 red.
- i18n: EN "Seasonal" / SV "Säsong"

### Rising queries expandable panel

- Each sector row gets a click-to-expand control (same delegated-listener
  pattern as the leaderboard breakdown from the P4 a11y work).
- Expanded panel: small table with up to 5 rising queries + growth %.
  "Breakout" entries display as "Breakout" (not a number).
- If no rising queries exist for a sector, the expand control is hidden.
- Pre-rendered at build time (HTML baked into the template context), not
  client-side JS.
- i18n: EN "Rising Queries" / SV "Stigande sökningar",
  EN "Growth" / SV "Tillväxt"

### `dashboard/sentiment.py` changes

- `_build_sentiment_signal_rows`: add `seasonal_ratio` field (formatted as
  `f"{v:.2f}x"`), parse `rising_queries` from `text_value` JSON into a list
  for the template.

### Guide modal

Add a short paragraph to the sentiment methodology guide explaining:
- Seasonal ratio: compares recent 13 weeks of search interest to the prior
  39-week baseline. Above 1.0 = interest exceeding historical norm.
- Rising queries: emerging search terms surfaced by Google Trends for each
  sector's representative ETF/entity.

## Testing

| Test | What |
|------|------|
| `test_seasonal_ratio` | Normal case, all-zero trailing, short series, exact boundary |
| `test_derived_signals_52w` | 52-point input returns all 6 signals including seasonal_ratio |
| `test_derived_signals_13w_compat` | 13-point input still works (seasonal_ratio = NaN) |
| `test_fetch_rising_queries` | Mocked pytrends client, cache hit/miss, empty results, fail-open |
| `test_sentiment_row_seasonal` | `_build_sentiment_signal_rows` includes seasonal column |
| `test_sentiment_row_rising` | Rising queries JSON parsed and formatted correctly |
| `test_schema_text_value` | `sentiment_signals` DDL includes `text_value` column |

## Out of scope

- Blending seasonal_ratio into the composite score (stays info-only)
- True calendar-YoY comparison (would need a second 12-month pull shifted by 1 year)
- Rising queries for themes (themes universe — deferred, same mechanism applies later)
- Comparative interest changes (already shipped, unchanged)
