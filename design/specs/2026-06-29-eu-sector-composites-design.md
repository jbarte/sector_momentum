# EU sector composites — Phase 1 (Financials & Materials) — design

**Date:** 2026-06-29
**Status:** Approved (design)
**Affects:** `config/universe.yaml`, `src/pipeline.py`, `scan.py`, `src/backtest/rotations.py`, `config/sector_etfs.yaml`

## Purpose

Two EU GICS sectors are poor single-ETF proxies: **Financials** is scanned as Banks only
(`EXV1.DE`), and **Materials** as Basic Resources only (`EXV6.DE`). GICS Financials =
Banks + Financial Services + Insurance; GICS Materials = Basic Resources + Chemicals
(ARCHITECTURE §2.2, `config/sector_map.yaml`). This builds those two sectors' momentum on
a **composite** of their STOXX supersector ETFs, so EU Financials/Materials become truer
GICS proxies and more comparable to their US counterparts (`XLF`, `XLB`).

`config/sector_map.yaml` already documents the full crosswalk but is **dead config** —
nothing reads it; the live pipeline uses one ETF per GICS sector from `universe.yaml`.

## Scope

- **In scope:** EU **Financials** and **Materials** become equal-weight composites. The
  other 9 EU sectors and **all 11 US sectors are unchanged** — only these two sectors'
  EU signals move.
- **Out of scope (Phase 2):** Consumer Discretionary / Consumer Staples / Communication
  Services composites, and the Media + Personal-&-Household-Goods crosswalk reassignments
  (more components + GICS ambiguities). Static/market-cap weighting (Phase 1 is
  equal-weight). Composite-aware backtest (see Backtest below).

## Components (sourced, iShares STOXX Europe 600, Xetra `.DE`, TER 0.46–0.47%)

| GICS sector | Components (ticker / ISIN) |
|---|---|
| Financials | `EXV1.DE` Banks (DE000A0F5UJ7) · `EXH2.DE` Financial Services (DE000A0H08G5) · `EXH5.DE` Insurance (DE000A0H08K7) |
| Materials | `EXV6.DE` Basic Resources (DE000A0F5UK5) · `EXV7.DE` Chemicals (DE000A0H08E0) |

The three new components (`EXH2.DE`, `EXH5.DE`, `EXV7.DE`) are the same fund
family/exchange as the EU tickers already scanned, so Yahoo price coverage is expected;
**validate Yahoo data for the three new tickers during implementation** (they will be
fetched and scanned).

## Approach: composite price series

For a composite sector, build one synthetic OHLC-lite series and feed it to the existing
`compute_signals_for_sector` unchanged (no signal-math changes):

- **Close:** rebase each component's `Close` to 100 at the first date of the common
  (intersection) index, then take the **equal-weight mean** across components → one Close
  series. Rebasing prevents a higher-priced ETF from dominating; relative signals
  (RS/RRG/returns/MA) are unaffected by the base.
- **Volume:** element-wise **sum** of component volumes on the common index (keeps OBV
  working as a direction proxy).
- **Alignment:** intersect component indices to common dates. Drop any component whose
  series is missing/empty; blend the survivors. If **all** components are missing →
  the sector is skipped/neutral exactly as a missing single ticker is today.

A single-component sector is the identity case (composite of one == that ETF), so the
9 unchanged EU sectors and the US sectors produce byte-identical signals.

## Config representation

`config/universe.yaml` `eu_sectors` values become **lists of component tickers**
(single-element for the 9 unchanged sectors, multi for the two composites). Equal-weight
⇒ no weights stored. `us_sectors` stays single-ticker (strings). Example:

```yaml
eu_sectors:
  Financials: [EXV1.DE, EXH2.DE, EXH5.DE]   # Banks + Financial Services + Insurance
  Materials:  [EXV6.DE, EXV7.DE]            # Basic Resources + Chemicals
  Technology: [EXV3.DE]                      # single-component (unchanged behaviour)
  # …the other 8 EU sectors as single-element lists…
```

`config/sector_map.yaml` stays as the audit crosswalk (not wired in this phase).

## Components / data flow

- **`src/pipeline.py`** — add `build_composite_series(tickers: list[str], prices: dict) ->
  pd.DataFrame | None` returning a frame with `Close` and `Volume` (rebased-mean Close,
  summed Volume; `None` if no component has data). `build_signals_rows` normalizes each
  `eu_sectors` value to a list, builds the composite (or single series), and passes it to
  `compute_signals_for_sector`. `compute_signals_for_sector` gains an optional
  `sector_df: pd.DataFrame | None = None` parameter: when provided, it uses that frame
  directly instead of looking up `prices[sector_ticker]` (the benchmark lookup via
  `prices[benchmark_ticker]` is unchanged). `build_signals_rows` builds the composite via
  `build_composite_series` and passes it as `sector_df`; single-component sectors pass the
  one component's frame the same way, so the code path is uniform. **The benchmark path
  (`EXSA.DE`) is unchanged.**
- **`scan.py`** — the fetch set (`list(us_sectors.values()) + list(eu_sectors.values()) +
  benchmarks`) must **flatten** the now-list-valued `eu_sectors` so all component tickers
  are fetched once.
- **`src/backtest/rotations.py`** — `eu_sectors[sector]` may now be a list; take the
  **first (primary) component** for the historical backtest. Documented limitation: the
  rotation backtest uses the primary component, not the composite, for Financials/Materials
  in Phase 1.
- **`config/sector_etfs.yaml`** — the two composite sectors list their **component ETFs**
  (reference == the basket the signal is built from); the other sectors keep their single
  entry. New entries reuse the existing schema (`ticker/name/isin/ter/issuer/url`).

## Error handling / edge cases

- Missing one component → drop it, blend the rest (logged).
- All components missing → sector skipped (existing neutral behaviour).
- A component present in `universe.yaml` but absent from fetched `prices` → treated as
  missing (resilient to a Yahoo gap on one sub-sector).
- Benchmark unchanged; US path unchanged.

## Testing

- `build_composite_series`: rebase + equal-weight mean math on two toy series; volume sum;
  one-missing-component → blends the rest; all-missing → `None`; single-component → equals
  the input series (identity).
- `build_signals_rows`: a list-valued EU sector produces one row with all signal columns;
  a single-element EU sector yields the same numbers as before (regression guard).
- Existing signal/scoring/pipeline tests stay green (US + 9 EU sectors unchanged).
- `rotations.py`: list-valued sector uses the primary component without error.

## Verification / acceptance

After implementation, run a real scan (or a local rebuild against the DB) and confirm
EU Financials and Materials produce non-null signals computed from the composite, and that
the other 20 sector-keys are numerically unchanged. Because this **changes EU
Financials/Materials momentum**, note the shift and sanity-check it is directionally
reasonable (composite vs the old Banks-only / Basic-Resources-only series).

## Out of scope (recap)

Phase 2 composites and crosswalk reassignments; weighting beyond equal-weight; making the
backtest composite-aware; touching US sectors or benchmarks.
