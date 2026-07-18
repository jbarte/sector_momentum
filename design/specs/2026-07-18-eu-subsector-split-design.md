# Split EU Composite Sectors into Standalone Sectors — Design

**Date:** 2026-07-18
**Status:** Approved
**Backlog item:** Split EU composite sectors into standalone sectors (research)

## Goal

Replace the two untradeable equal-weight EU composite sectors with their
underlying STOXX Europe 600 sub-sector ETFs as first-class sectors:

- **Financials** (EXV1.DE + EXH2.DE + EXH5.DE) → **Banks** (EXV1.DE),
  **Financial Services** (EXH2.DE), **Insurance** (EXH5.DE)
- **Materials** (EXV6.DE + EXV7.DE) → **Basic Resources** (EXV6.DE),
  **Chemicals** (EXV7.DE)

The EU universe goes from 11 GICS buckets to 14 sectors. The US universe is
unchanged (11 GICS sectors).

## Why (research findings, 3y daily data)

- Basic Resources vs Chemicals daily-return correlation is **0.50**; they
  disagree on the *sign* of 6m momentum on 37% of days. The blend hides real
  rotations (at research time: Chemicals +8.9% vs Basic Resources −2.1%
  relative 6m momentum vs STOXX 600).
- Financials components correlate ~0.68–0.70; median best-vs-worst 6m
  momentum spread is ~15pp, and the best component beats the composite by
  >5pp on 72% of days.
- The composites cannot be bought; acting on a composite rank already means
  picking a component blind. After the split the scanner names the strongest
  component directly.
- Counterarguments assessed and found weak: ranking is a single global
  z-scored cross-section (no US↔EU pairing anywhere), so the 14-vs-11
  asymmetry is harmless; mild leaderboard crowding by correlated financial
  rows is informative rather than misleading.

Analysis script: session scratchpad (`split_research.py`, yfinance 3y pull);
key numbers reproduced above.

## Changes

### 1. `config/universe.yaml`

`eu_sectors` becomes 14 single-ticker entries (scalar values, no lists):

```yaml
eu_sectors:
  Technology: EXV3.DE
  Banks: EXV1.DE
  Financial Services: EXH2.DE
  Insurance: EXH5.DE
  Energy: EXH1.DE
  Health Care: EXV4.DE
  Industrials: EXH4.DE
  Consumer Discretionary: EXH7.DE
  Consumer Staples: EXH3.DE
  Utilities: EXH9.DE
  Basic Resources: EXV6.DE
  Chemicals: EXV7.DE
  Real Estate: IPRP.L
  Communication Services: EXV2.DE
```

Comment block about composites is deleted.

### 2. Dead code removal — `src/pipeline.py`

With no multi-element entries left, `build_composite_series()` and the
multi-ticker branch in `build_signals_rows()` (the `else` arm that builds a
composite and joins tickers with `+`) are dead. Remove both, plus their
tests. The single-ticker path (`value if isinstance(value, list) else
[value]` normalization) simplifies to reading the scalar directly.

### 3. Parent-sector mapping — `config/sector_map.yaml` becomes live config

`stoxx_to_gics` in `config/sector_map.yaml` already maps the five new names
to their GICS parents but is consumed by nothing. It becomes real config
with a loader — a new small module `src/sector_map.py` exposing
`load_parent_map(path="config/sector_map.yaml") -> dict[str, str]` and
`parent_sector(name, parent_map) -> str` (identity fallback) — and two
consumers:

- **FinBERT news sentiment** (`scan.py` step 8d): sentiment is scored per
  GICS-11 name (`GDELT_SECTOR_THEMES`). When applying scores to sector keys,
  resolve the key's sector name through the parent map first —
  `Banks → Financials` — with identity fallback for unmapped names (all US
  sectors and unchanged EU sectors). Sub-sectors thus inherit their parent's
  news sentiment instead of silently getting neutral 0.0.
- **Swedish ticker matching** (`src/report.py`): `swedish_tickers.csv`
  classifies stocks by GICS-11 names. Resolve the ranked sector's name
  through the same parent map before matching, so a top-ranked Banks row
  still lists Financials-classified Swedish stocks.

The map stays name-keyed and additive; names not present map to themselves.

### 4. `config/sector_etfs.yaml` — EU drill-down re-keying

The five component ETFs are already listed under the composite headings in
the EU section. Re-key them under their own sector names (Banks, Financial
Services, Insurance, Basic Resources, Chemicals); delete the composite
comment block. US section unchanged.

### 5. History, DB, dashboard

- **No schema change.** `gics_sector` is free text everywhere (SQLite/
  Postgres tables, long-format signals, dashboard keys).
- Old `EU|Financials` / `EU|Materials` rows remain in the DB as history and
  drop off the leaderboard automatically (it renders the latest scan only).
- New keys start with flat trajectory (`→`, existing default) and no
  deltas; self-heals after two scans.
- No i18n work: sector names render in English on both EN and SV.

### 6. Verified non-impacts

- **Scoring/ranking:** single global cross-section over all rows
  (`zscore_cross_section` + `rank_sectors`); indifferent to row count and
  names.
- **Google Trends sector sentiment:** keyed by ETF tickers; EU `.DE`
  tickers are already dead on Trends (documented in BACKLOG Parked), so no
  change in behaviour.
- **Breadth:** computed for `US|…` keys only.
- **Rotations backtest:** all `config/rotations.yaml` entries are US.
- **Themes:** separate universe, untouched.

## Error handling

- A sub-sector ETF with no price data is skipped with a warning by the
  existing single-ticker path (unchanged behaviour).
- Parent-map lookups never raise: unmapped names resolve to themselves.
- If `sector_map.yaml` is missing or malformed, the loader raises at scan
  start (config error, same treatment as a broken `universe.yaml`).

## Testing

- Update any pipeline tests/fixtures exercising multi-element `eu_sectors`
  entries; remove `build_composite_series` tests.
- New tests for the parent-map loader: mapped name (`Banks → Financials`),
  identity fallback (`Technology → Technology`), and the FinBERT
  application path picking up a parent's score for a sub-sector key.
- Report test: Swedish ticker matching for a sub-sector name.
- Existing scoring/dashboard tests pass unchanged (nothing keys on sector
  count).

## Post-merge notes

- First post-merge scan produces the new 14-sector EU cross-section; ranks
  and deltas normalize by the second scan.
- Verify EXV1.DE, EXH2.DE, EXH5.DE, EXV6.DE, EXV7.DE are buyable on Avanza
  (Xetra-listed iShares UCITS; expected yes).

## Out of scope

- Full STOXX supersector universe (~19 EU sectors, replacing stand-in ETFs
  like EXH7.DE-as-Consumer-Discretionary) — separate decision, stays out of
  the backlog until wanted.
- Any change to the US universe, themes, or weights.
