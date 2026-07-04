# Trends entity-mid resolution — design

**Date:** 2026-07-04
**Status:** Approved (design), pending implementation plan
**Branch:** `feature/trends-entity-mids`

## Problem

The symbol-based Google Trends sentiment (`src/data/trends_symbols.py`) queries
**raw ETF ticker strings** (`XLK`, `XLF`, `VOX`, `LOGS`, …) as search terms. Some
tickers collide with unrelated high-volume search entities, contaminating the
signal. The Phase 1 live validation (2026-06-26) measured this directly:

- `US|Communication Services` z **+4.16**, driven by **`VOX`** (Vox Media, not the
  ETF).
- `EU|Energy` z **+1.27**, driven by **`LOGS`** (the English word).

These false positives dominate the cross-sectional z-score. A growing blocklist is
whack-a-mole. The real fix is to query **Google Knowledge Graph entities** (topic
"mids", e.g. `/m/0h50v1r`) instead of ambiguous strings — Trends disambiguates by
entity, eliminating the collision.

## Goal

Where a ticker has a known, human-approved Knowledge Graph entity, query that
entity's mid instead of the raw string. Everything else — per-ticker
anchor-normalization, per-sector aggregation, scoring — stays identical.

**Non-goals:** region-aware geo pulls, comparative cross-sector payloads, seasonal
baselines, rising queries (all tracked separately). No change to which tickers map
to which sector. No change to the composite (sentiment stays toggle-only).

## Key decisions

1. **Resolve each ticker to its entity** — keep the current per-ticker structure and
   per-sector averaging; only the *query term* for a ticker changes (mid vs string).
   Not pivoting to one theme-entity per sector.
2. **Curated static config, read at scan time, with raw-string fallback.** A
   committed `config/trends_entities.yaml` maps ticker → approved mid. Any ticker
   **absent** from the file queries exactly as today. The change is strictly
   additive: worst case equals current behavior.
3. **A dev-only build script bootstraps the config; a human approves every entry.**
   The scan path never calls `suggestions()` — no runtime lookup, deterministic,
   offline-safe.

Runtime `suggestions()` auto-picking was rejected: it re-introduces the exact
wrong-entity bug (it is *how* you land on Vox Media), is nondeterministic, and adds
per-scan API calls to an already rate-limit-fragile path.

## Components

### 1. `config/trends_entities.yaml` (committed artifact)

```yaml
# ticker -> approved Google Knowledge Graph entity.
# `title` is for human verification only; the scan queries `mid`.
XLK:
  mid: /m/0h50v1r
  title: Technology Select Sector SPDR Fund
VOX:
  mid: /m/07657k
  title: Communication Services Select Sector SPDR Fund
```

- Only human-approved tickers appear.
- Loaded in `scan.py`; `FileNotFoundError` → `{}` (everything falls back to strings).

### 2. `scripts/resolve_trends_entities.py` (dev-only helper)

- Reuses `build_symbol_map(universe, sector_etfs, blocklist)` to enumerate the full
  ticker set.
- For each ticker, calls `pytrends.suggestions(ticker)` and prints the candidate
  entities (`title` / `type` / `mid`).
- Writes a **proposed** YAML (stdout or a scratch file) for the human to prune. It
  does **not** overwrite the committed config.
- Never imported by `scan.py`; lives under `scripts/`. Not run in CI.

### 3. `fetch_symbol_trends` change (only production-path edit)

Signature gains one optional param:

```python
def fetch_symbol_trends(symbol_map, anchor="SPY", client=None,
                        timeframe="today 3-m", window=13, batch_size=4,
                        sleep_s=20.0, max_retries=3,
                        entities: dict[str, str] | None = None) -> dict[str, pd.Series]:
```

- `entities` maps ticker → mid (default `{}`).
- **Term substitution:** for each ticker in a batch, the query term is
  `entities.get(ticker, ticker)` — the mid where approved, else the raw string. The
  anchor stays the raw `"SPY"` string.
- **Reverse mapping:** Google returns interest columns keyed by the *term* we sent
  (a mid or a string). A helper maps each returned column back to its ticker before
  storing into `norm_by_symbol`, so that dict stays **ticker-keyed** and
  `_normalize_by_anchor` / `_aggregate` are unchanged.
- Per-batch term↔ticker maps handle the substitution both ways. The config is
  human-curated, so distinct tickers are expected to map to distinct mids; the
  build script / review should avoid assigning one mid to two tickers, since a
  payload with a duplicated term collapses to a single Trends column.

Downstream (`_aggregate`, `score_symbol_sentiment`, and — on the other branch —
`derived_signals`) require **no changes**: they operate on ticker-keyed series.

### 4. `scan.py` wiring

- Load `config/trends_entities.yaml` (missing → `{}`).
- Pass `entities=…` to `fetch_symbol_trends`.
- Log how many tickers resolved to a mid vs fell back to strings.

### 5. Dashboard explainer

Update the sentiment-page "Source/Method" text (`sentiment.html.j2` + Swedish
`_i18n.html.j2`) to note that queries use disambiguated Knowledge Graph entities
where available, falling back to ticker strings otherwise. (The current explainer is
already partly stale — this is a small correction, not a redesign.)

## Data flow

```
config/trends_entities.yaml ─┐
universe + sector_etfs ──────┼─> build_symbol_map ─> symbol_map (ticker-keyed)
                             │
scan.py loads entities ──────┘
        │
        └─> fetch_symbol_trends(symbol_map, entities=…)
                 │  per batch: term = entities.get(ticker, ticker)
                 │  build_payload([anchor] + terms) ; interest_over_time()
                 │  map returned columns (term) back to ticker
                 └─> norm_by_symbol (ticker-keyed)  [UNCHANGED from here on]
                        └─> _aggregate ─> score_symbol_sentiment / derived_signals
```

## Error handling

- Missing config file → `{}` → all raw-string queries (current behavior).
- A ticker with a mid that Google returns empty/dead → dropped by the existing
  dead-term filter in `_aggregate`, same as a dead string today.
- `suggestions()` failures in the build script are a dev-time concern only; the
  script prints the error and skips that ticker. Never touches the scan path.

## Testing

Pure-function unit tests (no network):

- **Term substitution:** given an `entities` map, a batch of tickers yields the
  expected term list (mid where present, string where absent), anchor unchanged.
- **Reverse mapping:** returned columns keyed by term map back to the correct
  ticker; a ticker without a mid round-trips as itself.
- **Fallback:** empty/`None` `entities` reproduces today's term list exactly
  (regression guard — proves additivity).

The build script is network-only and stays untested (dev tool).

Full existing suite must stay green (178 passed / 6 skipped baseline; the 6 skips
are the psycopg2-less DB modules).

## Expectation to keep honest

Many tickers will **not** have a useful entity — the Yahoo-suffixed EU tickers
(`EXV3.DE`, `EXH1.DE`, …) and thinly-searched SPDR tickers. Those fall back to
strings. This feature mainly cleans up the handful of US names with real collisions
(the `VOX`/`LOGS`-class problem). That is the right, targeted win — not a blanket
fix for every ticker.

## Out of scope / follow-ups

- Region-aware geo pulls, comparative payloads, seasonal baseline, rising queries.
- Re-running the build script when the universe changes (manual, as needed).
