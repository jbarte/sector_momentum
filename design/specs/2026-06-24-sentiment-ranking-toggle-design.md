# Sentiment Ranking Toggle — Design

> Status: approved 2026-06-24. Scope: wire thin Google Trends sentiment into the
> pipeline and let the dashboard optionally blend it into the leaderboard ranking
> at a user-chosen weight. The rich, dedicated Trends *tab* is a separate later task.

## Goal

Give the dashboard an optional **toggle + weight field** that includes Google Trends
sentiment in the leaderboard ranking. Off by default (pure data ranking); when on,
the leaderboard re-ranks live in the browser at the chosen sentiment weight, and all
rank-derived indicators (ΔRank, trajectory, Emerging) recompute consistently.

## Non-goals

- The rich Google Trends engine (all keywords, region-aware geos, multiple derived
  signals, seasonal baseline) — that belongs to the later dedicated-tab task.
- Making the RRG / Movers / History / Data↔Sentiment Plotly tabs react to the toggle.
  Those remain server-built at the canonical weighting.
- Folding sentiment into the canonical/official daily composite. Sentiment is stored
  but kept out of the server composite; it influences ranking only in the dashboard.

## Architecture & data flow

```
scan.py (server, per scan)
  fetch_trends (thin: primary keyword, 13-week, geo="")
      → compute_sentiment_score (Trends-only; reddit/finnhub args = None)
      → score_all(wide_df, sentiment_score=…)
      → stores real sentiment_score (was NaN)   [canonical composite stays PURE DATA]

dashboard/build.py (build time)
  ships per-scan × per-sector {data_score, sentiment_score} for all history scans
  as compact JSON (RESCORE_DATA, ~5 KB gzipped)

index.html + dashboard/assets/rescore.js (browser)
  rescore(W):  composite = (1−W)·data + W·sentiment, per sector, per scan
               → rank each scan → ΔRank, trajectory, Emerging
  toggle OFF → W = 0  (pure data, default)
  toggle ON  → W = weightField/100  (default 0.30), persisted in localStorage
  re-renders leaderboard only (order, rank #, composite, ΔRank, trajectory,
             Emerging, breakdown score-tree)
```

**Single source of truth for the leaderboard = `rescore()`.** It runs on every page
load (W=0 by default), so there is no separate static-vs-dynamic render path.

## Server side (scan.py + scoring)

1. **Wire thin Trends into the scan.** In `scan.py main()`, after signals are built,
   load `config/sentiment_keywords.yaml`, call `fetch_trends(keywords)` and
   `compute_sentiment_score(reddit_data=None, trends_data=<trends>, finnhub_data=None,
   sector_keys=…, us_sectors=…, eu_sectors=…)`. Trends failure returns `None` →
   sentiment Series is all-0.0 (neutral); the scan still completes.

2. **Store real `sentiment_score`.** Pass the sentiment Series into
   `score_all(wide_df, sentiment_score=…)`. `score_all` already emits the
   `sentiment_score` column and `save_scan` already persists it — no `state.py` change.

3. **Canonical composite stays pure data.** The stored `composite`/`rank` must reflect
   **data only** (data_weight = 1.0, sentiment_weight = 0.0), so the dashboard's
   default (toggle-off, W=0) leaderboard matches the server-built History/Movers
   figures and the stored record. This is achieved by computing the canonical composite
   without sentiment (see Implementation note below). Sentiment is stored alongside but
   never baked into the canonical composite.

   *Consequence:* the composite-history chart shows a one-time magnitude step where
   pre-existing scans (stored at the old `0.70·data` scaling) meet new pure-data scans.
   Rankings are unaffected (monotonic rescale). Acceptable — only ~3 real scans exist.

### Implementation note — keeping canonical composite pure-data

`score_all` reads pillar weights from `config/weights.yaml` (currently data 0.70 /
sentiment 0.30) and, when given a `sentiment_score`, blends at those weights. To store
sentiment **and** keep a pure-data canonical composite, `score_all` must compute the
stored composite from data alone while still emitting the sentiment column.

Chosen approach: add a `blend_sentiment: bool = True` parameter to `score_all`.
- `scan.py` calls `score_all(wide_df, sentiment_score=sent, blend_sentiment=False)`:
  the `sentiment_score` column is populated from `sent`, but `composite` is computed
  as `compute_composite(data, sentiment_score=None)` → pure `data` (data_weight 1.0).
- Default `blend_sentiment=True` preserves existing behavior for any other caller/tests.

This avoids editing `config/weights.yaml` (the 0.30 stays meaningful as the dashboard's
default slider value) and keeps the pure-data decision explicit at the call site.

## Client side (UI + rescore)

### Shipped data

```js
var RESCORE_DATA = {
  scans:   [{scan_id, run_at}, …],            // ascending by scan_id
  sectors: ["US|Technology", …],
  data:      {"US|Technology": [s0, s1, …], …},   // data_score per scan
  sentiment: {"US|Technology": [s0, s1, …], …}    // sentiment_score per scan
};
```
Every per-sector array length equals `scans.length`. Missing values → `0.0`.

### `dashboard/assets/rescore.js` (pure, no DOM)

Exports (UMD/global `Rescore`) pure functions:
- `rankAverage(values)` — descending rank, average tie-break (mirrors
  `scipy.rankdata(-x, method="average")` used in `src/scoring.py:rank_sectors`).
- `olsSlope(values)` — least-squares slope over `0..n-1` (mirrors the pure-Python OLS
  in `dashboard/build.py:_compute_rank_trajectories`).
- `trajectoryLabel(slope)` — same thresholds as `_compute_rank_trajectories`:
  `≤ −1.5 → "↑↑"/strong_up`, `≤ −0.3 → "↑"/up`, `< 0.3 → "→"/flat`,
  `< 1.5 → "↓"/down`, else `"↓↓"/strong_down`.
- `rescore(data, W)` → per latest-scan, per sector:
  `{ rank, composite, delta_rank, delta_composite, emerging, trajectory_label,
     trajectory_state }`, where:
  - `composite_i = (1−W)·data_i + W·sentiment_i`
  - rank within each scan via `rankAverage`
  - `delta_rank = rank_prev − rank_latest` (positive = climbed; 0 if no prior scan)
  - `delta_composite = composite_latest − composite_prev`
  - `emerging = delta_rank > 0 && delta_composite > 0`
  - trajectory from `olsSlope` of the last 5 scans' ranks (`<2` points → flat `→`)

### Inline script (DOM wiring only)

- Reads `localStorage` (`sentimentEnabled`, `sentimentWeight`); first visit →
  `false`, `30`.
- Computes `W = enabled ? weight/100 : 0`, calls `Rescore.rescore(RESCORE_DATA, W)`.
- Re-renders the leaderboard: re-sort rows by `rank` ascending; update rank number,
  composite cell, ΔRank arrow + up/down class, trajectory badge label/state, Emerging
  badge visibility; rebuild the breakdown **score-tree** fragment to show
  `Composite → Data (1−W)% / Sentiment (W)%` with live values.
- The signal tables and Instruments in each breakdown stay server-rendered (static).

### Control

A bar above the leaderboard:
```
☐ Include sentiment in ranking     Weight: [ 30 ]%
```
- Unchecked → W=0, weight field disabled/greyed.
- Checked → W = field/100 (clamped 0–100), default 30.
- Any change → persist to `localStorage` → `rescore` → re-render.
- A small note on the RRG/Movers/History/Data↔Sentiment tabs: "Sentiment weighting
  affects the leaderboard ranking only."

## Error handling

- Trends fetch failure → `compute_sentiment_score` returns all-0.0 → scan completes,
  sentiment stored as 0.0; dashboard behaves as pure-data at any W.
- Sector with no Trends series → `sentiment = 0.0`; holds at `(1−W)·data`.
- `RESCORE_DATA` empty / single scan → deltas and trajectory default to neutral
  (`delta_rank = 0`, `→`/flat); leaderboard still renders.
- Malformed `localStorage` → fall back to defaults (false / 30).

## Testing

- **`tests/test_rescore_parity.py`** — drives `rescore.js` under Node (same pattern as
  `tests/test_dashboard_js.py`) against a Python reference using the actual
  `scipy.rankdata` and the same OLS as `_compute_rank_trajectories`. Fixtures: random
  data/sentiment matrices and explicit tie cases. Asserts within float tolerance:
  ranks at W∈{0, 0.30, 1.0}, `delta_rank`, `emerging`, and trajectory labels match.
- **Behavioral anchor:** at W=0, `rescore` ranking equals ranking by `data_score`
  alone (= server pure-data canonical order).
- **Build-time guards (extend `tests/test_dashboard_js.py`):** `RESCORE_DATA` renders
  as valid JSON; `data`/`sentiment` present for every sector; each array length equals
  `scans.length`. Existing "no empty `var X = ;`" guard still applies.
- **scoring unit test:** `score_all(..., blend_sentiment=False)` populates the
  `sentiment_score` column but leaves `composite == data_score` (pure data).

## Files touched

| File | Change |
|------|--------|
| `src/scoring.py` | add `blend_sentiment` param to `score_all` |
| `scan.py` | load keywords, call `fetch_trends` + `compute_sentiment_score`, pass into `score_all(..., blend_sentiment=False)` |
| `dashboard/build.py` | build + embed `RESCORE_DATA`; ship `rescore.js`; add sentiment line to breakdown score-tree markup |
| `dashboard/assets/rescore.js` | new pure rescore module |
| `dashboard/templates/index.html.j2` | toggle+weight control, `RESCORE_DATA` var, `rescore.js` include, DOM wiring, tab note |
| `tests/test_rescore_parity.py` | new Node-vs-Python parity test |
| `tests/test_dashboard_js.py` | extend with `RESCORE_DATA` shape guards |
