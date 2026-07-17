# Forward-return validation & holding-period stats

## Problem

The dashboard ranks sectors daily, and the backtest runs a historical
rotation simulation — but there's no live, continuously-updated answer to
two practical questions:

1. **Do the rankings predict returns?** When a sector enters the top 5, does
   it outperform the benchmark over the next week/month?
2. **How long should you hold?** Once a sector enters the top 5, how many
   scans does it typically stay there before dropping out?

The backtest equity curve answers these indirectly, but it's a single
aggregate number over a fixed history. These features give per-observation
transparency.

## Goal

Add two info-only panels to the Backtest tab that continuously validate the
scanner's live output against realised forward returns and holding durations.
Computed at dashboard-build time from existing scan history + cached prices.
No schema changes, no new dependencies, no scoring impact.

## Success criteria

1. The forward-return panel shows hit rate and mean/median excess return
   for both 5-day and 1-month horizons, broken out by region (US, EU) plus
   an "All" summary row.
2. The holding-period panel shows run count, median/mean/min/max duration
   for top-5 streaks, broken out by region plus "All".
3. Scans too recent to have a complete forward window are excluded (not
   counted as failures).
4. Panels are hidden when fewer than 10 scans exist.
5. EN+SV i18n for all table headers and labels.
6. Info-only — no composite/ranking impact, no interactivity beyond the
   existing tab switching.

---

## Design

### Definitions

- **Top-5 observation**: A row in scan history where a sector has
  `rank <= 5` within its region (US or EU).
- **Top-5 run**: A contiguous streak of scans where a sector maintains
  `rank <= 5`. A run starts at the first qualifying scan and ends at the
  last qualifying scan before the sector drops to rank > 5 (or the scan
  series ends). A sector re-entering the top 5 after a gap starts a new
  run.
- **Forward return**: The percentage price change of the sector's ETF over
  a fixed horizon from the scan date.
  `close_at(prices, fwd_date) / close_at(prices, scan_date) - 1`
- **Excess return**: Sector forward return minus benchmark forward return
  over the same period.
- **Hit rate**: Fraction of observations where excess return > 0.
- **Horizons**: 5 trading days and 21 trading days (~1 month).
- **Benchmarks**: RSP (US), EXSA.DE (EU) — from `config/universe.yaml`.

### Shared plumbing — top-5 runs

One pass over `get_scan_history(conn, n_scans=None)` per region produces a
rank timeline per sector. Group by `(region, gics_sector)`, sort by
`scan_id`, and walk the sequence: a run begins when `rank <= 5` and ends
when `rank > 5` or the series ends. Each run records:

```python
{
    "region": str,
    "sector": str,
    "entry_scan_idx": int,   # index into scan_ids list
    "exit_scan_idx": int,    # last scan_idx with rank <= 5
    "duration": int,         # exit_scan_idx - entry_scan_idx + 1
}
```

This structure feeds both features.

### Forward-return computation

For **every** scan where a sector has rank <= 5 (not just run entries —
every observation gives a data point):

1. Look up the sector's ETF ticker via the universe config
   (`_sector_ticker_map` pattern from `badges.py`).
2. Look up the scan date from the history DataFrame's `run_at` column.
3. Compute `fwd_5d_date` = 5th trading day after scan date, and
   `fwd_21d_date` = 21st trading day after scan date, using the price
   DataFrame's index (same `_forward_date` approach as `badges.py`).
4. Compute sector return and benchmark return for each horizon.
5. If either price is unavailable (scan too recent, or price gap), mark
   as pending/skip.

Aggregate per region:

| Field | Definition |
|-------|-----------|
| `obs` | Count of observations with complete forward data |
| `pending` | Count of observations still within the horizon window |
| `hit_rate` | `sum(excess > 0) / obs` |
| `mean_excess` | `mean(excess)` |
| `median_excess` | `median(excess)` |

Compute separately for 5-day and 1-month, for US, EU, and All.

### Holding-period computation

From the top-5 runs list, aggregate per region:

| Field | Definition |
|-------|-----------|
| `runs` | Total number of completed runs |
| `median` | Median run duration in scans |
| `mean` | Mean run duration in scans |
| `min` | Shortest run |
| `max` | Longest run |

Ongoing runs (sector still in top-5 at the latest scan) are excluded from
the stats to avoid downward bias — they're censored observations. They can
be noted as "(N ongoing)" in the display.

### Module: `dashboard/validation.py`

Public API:

```python
def build_validation_context(shared: dict) -> dict
```

Takes the `shared` dict from `build.py` (needs `all_scores_df`,
`project_root`, `universe`). Returns:

```python
{
    "validation_fwd_returns": [
        # One dict per (region, horizon) combination + "All" rows
        {
            "region": "US",
            "horizon": "5d",
            "obs": 87,
            "pending": 3,
            "hit_rate": 0.58,
            "mean_excess": 0.0012,
            "median_excess": 0.0008,
        },
        ...
    ],
    "validation_holding": [
        # One dict per region + "All" row
        {
            "region": "US",
            "runs": 34,
            "ongoing": 2,
            "median": 8,
            "mean": 11.2,
            "min": 1,
            "max": 45,
        },
        ...
    ],
    "validation_min_scans_met": True,
}
```

Internal functions:

- `_top5_runs(history_df, region) -> list[dict]` — extracts contiguous
  top-5 streaks. Filters `history_df` to the given region, groups by
  sector, walks scan sequence.
- `_compute_forward_returns(history_df, prices, benchmark_prices, region, horizons) -> list[dict]` —
  for each top-5 observation, computes excess returns at each horizon.
  Uses `close_at` from `src/backtest/strategy.py` and `_forward_date`
  ported from `badges.py`.
- `_aggregate_fwd_returns(observations, region_label) -> list[dict]` —
  groups by horizon, computes hit rate / mean / median.
- `_holding_stats(runs, region_label) -> dict` — aggregates run durations.

### Price fetching

Same pattern as `badges.py`:

```python
from src.data.prices import fetch_prices

tickers = list(ticker_map.values()) + [us_benchmark, eu_benchmark]
date_range = earliest_scan_date - 10d .. latest_scan_date + 30d
prices = fetch_prices(tickers, start, end)
```

### Integration in `build.py`

One import + one `.update()` call in the sectors context assembly:

```python
from dashboard import validation
sectors_ctx.update(validation.build_validation_context(shared))
```

### Template: `_validation.html.j2`

Included in `index.html.j2` inside the Backtest tab panel div, after the
badge scorecard section. Structure:

```html
{% if validation_min_scans_met %}
<h3 data-i18n="val_fwd_title">Do the rankings predict returns?</h3>
<p class="tab-note" data-i18n="val_fwd_desc">
  Excess return of top-5 sectors vs region benchmark.
</p>
<div class="table-wrap">
  <table><!-- forward return rows --></table>
</div>

<h3 data-i18n="val_hold_title">How long do top-5 positions last?</h3>
<p class="tab-note" data-i18n="val_hold_desc">
  Duration of contiguous top-5 rank streaks.
</p>
<div class="table-wrap">
  <table><!-- holding stats rows --></table>
</div>
{% endif %}
```

Positive excess returns / hit rates > 50% get `class="signal-hi"`;
negative / < 50% get `class="signal-lo"`. These classes already exist in
the dashboard CSS.

### i18n keys

New SV translations (added to `i18n/_validation.js.j2` or an existing
backtest i18n file):

| Key | EN | SV |
|-----|----|----|
| `val_fwd_title` | Do the rankings predict returns? | Förutsäger rankningarna avkastning? |
| `val_fwd_desc` | Excess return of top-5 sectors vs region benchmark. | Meravkastning för topp-5-sektorer mot regionindex. |
| `val_hold_title` | How long do top-5 positions last? | Hur länge varar topp-5-positioner? |
| `val_hold_desc` | Duration of contiguous top-5 rank streaks. | Längd på sammanhängande topp-5-perioder. |
| `val_region` | Region | Region |
| `val_horizon` | Horizon | Horisont |
| `val_obs` | Obs | Obs |
| `val_hit_rate` | Hit rate | Träffkvot |
| `val_mean` | Mean excess | Medel |
| `val_median` | Median excess | Median |
| `val_runs` | Runs | Perioder |
| `val_ongoing` | ongoing | pågående |
| `val_min` | Min | Min |
| `val_max` | Max | Max |
| `val_all` | All | Alla |

### Minimum data guard

`MIN_SCANS = 10`. If `len(scan_ids) < MIN_SCANS`, return
`{"validation_min_scans_met": False}` and the template hides both panels.

---

## Scope

### In scope

- `dashboard/validation.py` with top-5 run extraction, forward-return
  computation, holding-period stats, and context builder
- `_validation.html.j2` template partial with both tables
- i18n (EN+SV) for all labels
- Integration in `build.py` (one `.update()` call)
- Include in `index.html.j2` Backtest tab
- Tests for run extraction, forward-return computation, holding stats,
  and the context builder

### Out of scope

- Themes (sectors only; themes can be added later)
- Charts or distribution visualizations (tables only)
- Configurable rank threshold (hardcoded to 5)
- Per-sector breakdown (aggregate by region only)
- DB schema changes or scan.py changes
- Configurable horizons (hardcoded 5d + 21d)

## Verification

1. `pytest` — new tests for `_top5_runs`, `_compute_forward_returns`,
   `_holding_stats`, `_aggregate_fwd_returns`, and the guard.
2. `python3 dashboard/build.py` — panels render in the Backtest tab.
3. Visual check: tables display with correct formatting, i18n toggles
   work, responsive on mobile.
