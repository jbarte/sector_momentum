# Comparative cross-sector interest

**Date:** 2026-07-09
**Status:** Design approved

## Problem

The current Trends pipeline fetches each sector's symbols in independent
batches. Google normalises 0-100 *within* each payload, so the raw values
are on different scales. The anchor-normalization (dividing by YouTube)
makes them roughly comparable, but it's an indirect proxy — it can't
answer "Technology gets 3x the search attention of Energy" directly.

## Solution

A **dedicated comparative fetch pass** that puts one representative term
per sector into overlapping 5-term payloads, chained via a bridge term so
all 11 sectors end up on a single common scale. The result is stored as a
new derived signal `attention_level` per `region|sector`.

## Anchor-chaining algorithm

With S sectors and a 5-term payload limit (no separate anchor — the
bridge sector IS the normalization reference):

1. Sort sectors by stable order (alphabetical GICS name).
2. Build overlapping batches:
   - Batch 0: `[S0, S1, S2, S3, S4]` — raw values from Google (S0 is
     the implicit reference within this batch).
   - Batch 1: `[S4, S5, S6, S7, S8]` — S4 is the bridge (appears in
     both batches 0 and 1).
   - Batch 2: `[S8, S9, S10]` — S8 is the bridge.
3. Rescale: multiply all of batch 1's values by
   `(S4_batch0 / S4_batch1)` to bring them onto batch 0's scale. Batch 2
   chains through batch 1 similarly.
4. If the bridge term is zero in either batch, that chain link is broken
   and downstream sectors get `NaN`.

**Representative term per sector:** for each `region|sector` key in the
symbol map, take `symbols[0]` (the primary ETF ticker). If that ticker
has a curated entity mid in `config/trends_entities.yaml`, use the mid;
otherwise use the raw ticker string. One term per sector, 11 per region.

**Per-region:** US sectors compared in `geo=US`, EU sectors averaged
across `DE/FR/GB` (same multi-geo pattern as the existing pipeline). The
two regions are on independent scales — no cross-region comparison.

**API cost:** ~3 batches per geo × ~2 effective geos (US + 3 EU averaged)
= ~9 extra API calls. With 20s inter-batch sleep, adds ~3 minutes. All
batches participate in the existing day-cache (same `batch_key()` keying).

## Storage

Stored as `signal_name = "attention_level"` rows in the existing
`sentiment_signals` table. No schema change. One row per `region|sector`
per scan.

The comparative fetch is a separate pass in `scan.py`, after the existing
`fetch_symbol_trends` call. Its output rows are appended to the
`sentiment_signals_df` before `save_scan`.

## New code

- **`fetch_comparative_interest()`** in `src/data/trends_symbols.py`:
  accepts a symbol map, picks one representative term per sector, builds
  chained batches, fetches via `_fetch_geo` (or a thin wrapper), rescales,
  returns `dict[str, float]` mapping `region|sector` to raw attention
  level.
- Helper **`_build_chained_batches()`**: given a list of terms, produces
  overlapping batch lists with bridge terms.
- Helper **`_rescale_chain()`**: given per-batch raw dicts + bridge
  mapping, rescales all batches onto a common scale.

## Dashboard

**Sentiment page (`sentiment.html`):** the derived signals table gains a
new column **"Attention"** showing the raw `attention_level` value.

**Guide text update:** the "Derived signals" explainer gets a bullet:
*"Attention — relative search interest compared head-to-head against all
other sectors in the same region. Higher = more attention."*

**i18n:** `sent_col_attention` → SV `"Uppmärksamhet"`.

**No composite/ranking impact.** Info-only, like acceleration/spike/
volatility. Only momentum feeds the sentiment toggle.

**No change to the sectors page.** Attention data lives on the sentiment
page only.

## Testing

- Unit test for `_build_chained_batches`: correct overlap, bridge terms,
  edge cases (fewer than 5 sectors).
- Unit test for `_rescale_chain`: known inputs → expected common-scale
  output; zero-bridge → NaN handling.
- Unit test for `fetch_comparative_interest` with a mock pytrends client.
- Integration: verify `attention_level` rows appear in
  `sentiment_signals` after a scan.
- Dashboard: verify the new column renders on the sentiment page.
