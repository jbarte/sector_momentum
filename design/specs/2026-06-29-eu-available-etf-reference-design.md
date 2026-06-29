# EU-available single-instrument ETF reference — design

**Date:** 2026-06-29
**Status:** Approved (design)
**Affects:** `config/sector_etfs.yaml` (the per-sector "Instruments" reference)

## Purpose

The dashboard's per-sector breakdown panel shows an "Instruments" table of investable
ETFs, sourced from `config/sector_etfs.yaml`. The **US** entries are all US-listed
(SPDR Select Sector + Vanguard, e.g. `XLV`/`VHT`) — **none are UCITS**, so an EU/Swedish
investor can't actually buy them. This reworks the reference so every sector lists **one
EU-available UCITS ETF**, for both regions.

This is a presentation/reference change only. It does **not** change what the scanner
measures.

## Scope

- **In scope:** `config/sector_etfs.yaml` content (US + EU sections), one instrument per
  sector per region.
- **Out of scope:** `config/universe.yaml` — the **scanned** instruments (`us_sectors`
  `XLK`/`XLV`…, `eu_sectors` `EXV3.DE`…) and the benchmarks (`RSP`, `EXSA.DE`) are
  **untouched**, so all momentum signals are byte-for-byte identical. Switching the
  scanned instrument to UCITS, currency-hedged share classes, and listing >1 instrument
  per sector were all considered and deferred.

## Why reference-only (not swapping the scanned instrument)

`XLV`/`XLK` have the longest history and deepest liquidity, so they're the cleanest proxy
for each S&P 500 GICS sector's momentum. The iShares S&P 500 sector UCITS ETFs track the
**same** S&P 500 sector indices, so swapping the scan would not improve the signal — it
would only add data risk (shorter Yahoo history, thinner volume → noisier OBV, USD-vs-EUR
listing quirks). The "what can I buy" need is a presentation concern, which
`sector_etfs.yaml` already exists to serve.

## US mapping (one per sector)

GICS sector → iShares S&P 500 sector UCITS ETF:

| GICS sector | Instrument |
|---|---|
| Technology | iShares S&P 500 Information Technology Sector UCITS ETF |
| Financials | iShares S&P 500 Financials Sector UCITS ETF |
| Energy | iShares S&P 500 Energy Sector UCITS ETF |
| Health Care | iShares S&P 500 Health Care Sector UCITS ETF |
| Industrials | iShares S&P 500 Industrials Sector UCITS ETF |
| Consumer Discretionary | iShares S&P 500 Consumer Discretionary Sector UCITS ETF |
| Consumer Staples | iShares S&P 500 Consumer Staples Sector UCITS ETF |
| Utilities | iShares S&P 500 Utilities Sector UCITS ETF |
| Materials | iShares S&P 500 Materials Sector UCITS ETF |
| Communication Services | iShares S&P 500 Communication Sector UCITS ETF |
| Real Estate | **iShares US Property Yield UCITS ETF** (substitute) |

All ten S&P 500 sector funds appear in the iShares V PLC prospectus (24 Apr 2026). There
is **no iShares S&P 500 Real Estate Sector UCITS ETF**, so Real Estate uses the
EU-available US-REIT fund **iShares US Property Yield UCITS ETF** as the closest
substitute — consistent with the EU section already using a property-yield fund
(`IPRP.L`) for Real Estate.

## EU section

Reduce to one instrument per sector by keeping the existing **primary** and dropping the
second alternate:

- Keep the iShares STOXX Europe 600 sector ETF where one is listed.
- Where no iShares listing exists in the current file (Energy, Industrials, Consumer
  Discretionary — currently Amundi-only), keep the Amundi fund as the single entry.
- Real Estate stays `IPRP.L` (iShares European Property Yield).

Provider preference when both exist: **iShares** (matches the US side and the user's
direction).

## Entry shape

Each entry keeps the existing schema — no template/build changes:

```yaml
- ticker: <representative listing ticker>
  name: <full fund name>
  isin: <ISIN>
  ter: "<x.xx%>"
  issuer: iShares        # or Amundi for the EU-only sectors
  url: <justetf or ishares.com profile>
```

ISIN is the stable identifier; `ticker` is a representative EU listing (Xetra `.DE` or LSE
`.L`, matching the convention already in the file). Values are sourced from
ishares.com / justetf and cross-checked against the prospectus fund list. **Sourcing the
exact ISIN/ticker/TER per fund is the main implementation effort.**

## Side effect: symbol-based Trends sentiment

`sector_etfs.yaml` is also read by `build_symbol_map` ([scan.py](../../scan.py)) to build the
Google Trends query set for the symbol-based sentiment signal. Impact is negligible and
acceptable:

- The **scanned primaries** (`XLV`, `XLK`…) come from `universe.yaml`, which is unchanged,
  so each sector's Trends query keeps its liquid, well-searched term.
- The swapped alternates become obscure UCITS tickers (e.g. `IUHC`) with ~zero search
  volume; the existing dead-term drop in `trends_symbols.py` removes them.
- **Check:** scan the new tickers for ambiguous common-word collisions (the `VOX`/`LOGS`
  problem); add any to `config/trends_blocklist.yaml`. The iShares S&P 500 sector tickers
  (`IUHC`, `IUIT`, `IUFS`…) are not common words, so this is expected to be a no-op.

## Error handling / edge cases

- **Real Estate gap (US):** no S&P 500 Real Estate UCITS → use iShares US Property Yield
  (documented above).
- **EU sectors with no iShares listing:** keep the Amundi fund rather than leaving the
  sector empty.
- **`build.py` rendering:** `_build_instruments_html` iterates the per-sector list, so a
  single-element list renders correctly (no code change).

## Testing

- Config-only change; no production code changes.
- `tests/test_scan_smoke.py` stubs `sector_etfs.yaml` and `tests/test_trends_symbols_map.py`
  uses a fake `sector_etfs` dict, so no test is coupled to the real file's tickers — the
  suite stays green unchanged.
- Verification: rebuild the dashboard locally (`python3 dashboard/build.py`) and confirm
  each sector's Instruments panel renders the single EU-available fund (US + EU). Do
  **not** commit `docs/` (CI-owned).

## Out of scope

- Changing the scanned instruments or benchmarks (`universe.yaml`).
- Currency-hedged share classes (list the standard/representative UCITS share class).
- More than one instrument per sector.
- Reconciling the pre-existing mismatch between `universe.yaml` EU tickers (`EXH1.DE`…) and
  the EU reference entries — left as-is.
