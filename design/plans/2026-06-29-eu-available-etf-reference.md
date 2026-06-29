# EU-available single-instrument ETF reference Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-sector "Instruments" reference in `config/sector_etfs.yaml` so every sector lists exactly one EU-available UCITS ETF (US → iShares S&P 500 sector UCITS ETFs; EU → the existing primary), without touching what the scanner measures.

**Architecture:** Pure config edit. `config/sector_etfs.yaml` is read by `dashboard/build.py` (`_build_instruments_html`, which iterates the per-sector list) and by `build_symbol_map` in `scan.py` (Trends query terms). `config/universe.yaml` (scanned tickers + benchmarks) is untouched, so all momentum signals are unchanged.

**Tech Stack:** YAML, Python 3 (PyYAML), pytest, `dashboard/build.py`.

**Spec:** `design/specs/2026-06-29-eu-available-etf-reference-design.md`

## Global Constraints

- **Config-only.** No production code or test changes. Edit `config/sector_etfs.yaml` only (plus `config/trends_blocklist.yaml` if Task 2 finds a collision, and `BACKLOG.md`).
- **`universe.yaml` is untouched** — scanned instruments (`XLK`/`XLV`…, `EXV3.DE`…) and benchmarks (`RSP`, `EXSA.DE`) stay exactly as-is.
- **One instrument per sector per region.**
- **Entry schema (unchanged):** `ticker`, `name`, `isin`, `ter` (string like `"0.15%"`), `issuer`, `url`.
- **ISIN is the source of truth.** `url` is the justetf profile by ISIN: `https://www.justetf.com/en/etf-profile.html?isin=<ISIN>`.
- **Do NOT commit `docs/`** — it is CI-owned. Build locally only to verify.
- **Branch:** `feature/eu-available-etf-reference` (already created). Conventional commits; subject < 72 chars; end messages with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: Rewrite `config/sector_etfs.yaml` — one instrument per sector, both regions

**Files:**
- Modify: `config/sector_etfs.yaml`

**Interfaces:**
- Consumes: nothing.
- Produces: a `sector_etfs` mapping `{US: {<sector>: [one entry]}, EU: {<sector>: [one entry]}}` where each sector key exactly matches `config/universe.yaml` sector names.

#### US section — replace with iShares S&P 500 sector UCITS ETFs (one per sector)

All ten sector funds: `issuer: iShares`, `ter: "0.15%"`. Real Estate uses the iShares US Property Yield substitute (`ter: "0.40%"`). `url` = `https://www.justetf.com/en/etf-profile.html?isin=<ISIN>`.

| Sector key (universe.yaml) | name | isin |
|---|---|---|
| Technology | iShares S&P 500 Information Technology Sector UCITS ETF | IE00B3WJKG14 |
| Financials | iShares S&P 500 Financials Sector UCITS ETF | IE00B4JNQZ49 |
| Energy | iShares S&P 500 Energy Sector UCITS ETF | IE00B42NKQ00 |
| Health Care | iShares S&P 500 Health Care Sector UCITS ETF | IE00B43HR379 |
| Industrials | iShares S&P 500 Industrials Sector UCITS ETF | IE00B4LN9N13 |
| Consumer Discretionary | iShares S&P 500 Consumer Discretionary Sector UCITS ETF | IE00B4MCHD36 |
| Consumer Staples | iShares S&P 500 Consumer Staples Sector UCITS ETF | IE00B40B8R38 |
| Utilities | iShares S&P 500 Utilities Sector UCITS ETF | IE00B4KBBD01 |
| Materials | iShares S&P 500 Materials Sector UCITS ETF | IE00B4MKCJ84 |
| Communication Services | iShares S&P 500 Communication Sector UCITS ETF | IE00BDDRF478 |
| Real Estate | iShares US Property Yield UCITS ETF | IE00B1FZSF77 |

**Ticker field:** use the **Xetra (`.DE`) listing symbol** shown on each fund's justetf profile (URL above), to match the file's existing convention (`EXV3.DE`). Confirmed tickers: Real Estate → `IQQ7.DE`. For the ten sector funds, read the "Listing / Exchange" section of the justetf profile and use the Xetra symbol (suffix `.DE`). If a fund has no Xetra listing, use the LSE symbol with suffix `.L`. ISIN is authoritative regardless of the ticker chosen.

#### EU section — reduce to the existing primary (drop the second alternate)

Keep exactly these existing entries (already in the file with full `ticker/name/isin/ter/issuer/url`); delete the second entry under each sector that has two:

| Sector key | keep (ticker / isin) | drop |
|---|---|---|
| Technology | EXV3.DE / DE000A0H08Q4 | LTUG (Amundi) |
| Financials | EXV1.DE / DE000A0F5UJ7 | LBNK |
| Energy | LOGS / LU1834988278 | *(only entry — keep)* |
| Health Care | EXV4.DE / DE000A0Q4R36 | LHTC |
| Industrials | LIGS / LU1834987890 | *(only entry — keep)* |
| Consumer Discretionary | LTVL / LU1834988781 | *(only entry — keep)* |
| Consumer Staples | EXH3.DE / DE000A0H08H3 | LFOD |
| Utilities | EXH9.DE / DE000A0Q4R02 | LUTI |
| Materials | EXV6.DE / DE000A0F5UK5 | LBRE |
| Real Estate | IPRP.L / IE00B0M63284 | *(only entry — keep)* |
| Communication Services | EXV2.DE / DE000A0H08R2 | LTCM |

Preserve the file's header comments; update the US header comment to note the new source (iShares S&P 500 sector UCITS ETFs, EU-domiciled, Ireland) and that Real Estate uses iShares US Property Yield (no S&P 500 Real Estate UCITS exists).

- [ ] **Step 1: Capture the ten Xetra tickers**

For each S&P 500 sector ISIN above, fetch `https://www.justetf.com/en/etf-profile.html?isin=<ISIN>` and record the Xetra (`.DE`) listing ticker. Record `IQQ7.DE` for Real Estate (already confirmed).

- [ ] **Step 2: Rewrite the `US:` block**

Replace the entire `US:` section so each of the 11 sectors has a single entry using the name/isin/ter/issuer/url/ticker above. Sector keys must match `config/universe.yaml` exactly (e.g. `Communication Services`, `Health Care`).

- [ ] **Step 3: Trim the `EU:` block to one entry per sector**

For each EU sector, keep the entry in the "keep" column and delete the alternate.

- [ ] **Step 4: Verify the YAML parses and is shaped correctly**

Run:
```bash
python3 - <<'PY'
import yaml
d = yaml.safe_load(open("config/sector_etfs.yaml"))
for region in ("US", "EU"):
    secs = d[region]
    assert len(secs) == 11, (region, len(secs))
    for s, lst in secs.items():
        assert len(lst) == 1, (region, s, len(lst))
        e = lst[0]
        for k in ("ticker", "name", "isin", "ter", "issuer", "url"):
            assert e.get(k), (region, s, k)
        assert e["isin"] in e["url"], (region, s, "url/isin mismatch")
print("OK: 11 US + 11 EU sectors, one well-formed entry each")
PY
```
Expected: `OK: 11 US + 11 EU sectors, one well-formed entry each`

- [ ] **Step 5: Confirm sector keys match universe.yaml**

Run:
```bash
python3 - <<'PY'
import yaml
u = yaml.safe_load(open("config/universe.yaml"))
e = yaml.safe_load(open("config/sector_etfs.yaml"))
for region, key in (("US", "us_sectors"), ("EU", "eu_sectors")):
    assert set(u[key]) == set(e[region]), (region, set(u[key]) ^ set(e[region]))
print("OK: sector keys align with universe.yaml")
PY
```
Expected: `OK: sector keys align with universe.yaml`

- [ ] **Step 6: Commit**

```bash
git add config/sector_etfs.yaml
git commit -m "feat: EU-available single ETF per sector in instruments reference" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Blocklist check, verification, backlog

**Files:**
- Possibly modify: `config/trends_blocklist.yaml`
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: the new tickers from Task 1.
- Produces: confirmation that the full suite is green and the dashboard renders the new single-instrument panels.

- [ ] **Step 1: Check new tickers for ambiguous-word collisions**

The new US tickers (e.g. `IQQ7.DE`, the `.DE`/`.L` S&P 500 sector symbols) feed `build_symbol_map`. They are non-dictionary strings, so no collision is expected, but confirm none is a common English word like the documented `VOX`/`LOGS` cases. If any is, add it to `config/trends_blocklist.yaml`. Otherwise no change.

Note: the scanned primaries (`XLV`/`XLK`…) come from `universe.yaml` and are unchanged, so each sector's Trends query keeps a liquid term regardless.

- [ ] **Step 2: Run the full test suite**

Run: `python3 -m pytest -q`
Expected: PASS (same count as before this change; tests use fake `sector_etfs` fixtures, so none is coupled to the real file). 6 skips are pre-existing.

- [ ] **Step 3: Build the dashboard locally and verify the panels**

Run: `python3 dashboard/build.py`
Then verify the new instruments appear and the old Vanguard alternates are gone:
```bash
grep -c "IE00B43HR379" docs/index.html   # iShares S&P 500 Health Care (US) ISIN — expect >=1
grep -c "VHT\|VGT\|VFH\|VNQ" docs/index.html  # dropped US Vanguard alternates — expect 0
```
Note: `XLK`/`XLV` still appear in `docs/index.html` (the breakdown footer shows the *scanned* ETF, e.g. `ETF: XLK`) — that is expected and correct, since `universe.yaml` is unchanged. Only the **Instruments** panel content changes.

Do **not** `git add docs/` (CI-owned). Discard the local rebuild afterwards: `git checkout -- docs/ 2>/dev/null; git clean -fdq docs/`.

- [ ] **Step 4: Move the backlog item to Done**

Add to the top of `## Done` in `BACKLOG.md`:
```markdown
- ~~EU-available instruments reference~~ — the per-sector "Instruments" panel now lists one
  EU-available UCITS ETF per sector (US → iShares S&P 500 sector UCITS ETFs, Real Estate →
  iShares US Property Yield; EU → existing iShares/Amundi primary). Reference-only
  (`config/sector_etfs.yaml`); scanned instruments/benchmarks unchanged. *(2026-06-29)*
```

- [ ] **Step 5: Commit**

```bash
git add BACKLOG.md config/trends_blocklist.yaml
git commit -m "docs: backlog — EU-available instruments reference shipped" \
  -m "Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- US → iShares S&P 500 sector UCITS (one per sector) → Task 1 US table. ✓
- Real Estate substitute (iShares US Property Yield) → Task 1, IE00B1FZSF77. ✓
- EU → one per sector, iShares-preferred, Amundi where only option → Task 1 EU table. ✓
- `universe.yaml` untouched → Global Constraints + no task edits it. ✓
- Sentiment side-effect handled (blocklist check) → Task 2 Step 1. ✓
- Config-only, no test changes, verify via build → Global Constraints + Task 2. ✓

**Placeholder scan:** No "TBD"/"handle appropriately". Ticker capture is a deterministic per-fund step with exact source URLs and one confirmed value (`IQQ7.DE`). ✓

**Type consistency:** Entry schema (`ticker/name/isin/ter/issuer/url`) is identical across both tasks and matches the existing file and `_build_instruments_html`. Sector keys verified against `universe.yaml` in Task 1 Step 5. ✓
