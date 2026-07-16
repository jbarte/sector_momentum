# Badge Scorecard — Design Spec

## Goal

Measure whether the Entry/Exit setup badges and trajectory badges predict
forward returns. Surface the results as an info-only table inside the
existing Backtest tab on the sectors page.

## Badge definitions (existing logic)

Trajectory badges are derived from the OLS slope of a sector's rank over
the last 5 scans (`_compute_rank_trajectories` in `dashboard/rows.py`):

| State | Slope threshold | Label |
|-------|----------------|-------|
| strong_up | slope ≤ −1.5 | ↑↑ Rising fast |
| up | −1.5 < slope ≤ −0.3 | ↑ Rising |
| flat | −0.3 < slope < 0.3 | → Flat |
| down | 0.3 ≤ slope < 1.5 | ↓ Falling |
| strong_down | slope ≥ 1.5 | ↓↓ Falling fast |

Setup badges layer on top (`_compute_setup` in `dashboard/rows.py`):

- **Entry:** composite > 0 AND trajectory in (up, strong_up) AND change > 0
- **Exit:** trajectory in (down, strong_down) AND change < 0

A sector can have a trajectory badge without a setup badge but not vice
versa.

## Data pipeline

### Computation (`dashboard/badges.py`)

One public function:

```python
def build_badge_scorecard(
    history_df: pd.DataFrame,
    universe: dict,
    price_cache_dir: str = "data/cache",
) -> list[dict]:
```

Steps:

1. **Replay badges for every historical scan.** For each scan S (starting
   from the 6th, since trajectory needs 5 prior scans), compute
   `_compute_rank_trajectories` over the 5 most recent scans up to and
   including S. Then apply `_compute_setup` using S's composite and
   change_score to get each sector's trajectory state and setup badge.

2. **Compute 5-trading-day forward returns.** For each (scan, sector) pair
   with a badge observation, look up the sector's ETF ticker from the
   universe config, load the cached price DataFrame, and compute
   `close_at(df, scan_date + 5 trading days) / close_at(df, scan_date) − 1`.
   Use `src/backtest/strategy.close_at` (reuse, not copy). Trading-day
   offset: use `pd.bdate_range` or step forward through the price index
   to find the 5th trading day. If either price is NaN/missing, the
   observation is dropped.

3. **Aggregate by badge type.** Each (scan, sector) observation has
   exactly one trajectory state (one of the 5) and optionally one
   setup badge (Entry or Exit). The 8 table rows are two independent
   groupings:
   - **Trajectory rows** (↑↑, ↑, →, ↓, ↓↓): every observation appears
     in exactly one of these.
   - **Setup rows** (Entry, Exit, No-badge): Entry and Exit are subsets
     of the trajectory rows (Entry ⊂ ↑∪↑↑, Exit ⊂ ↓∪↓↓); "No badge"
     covers every observation that is neither Entry nor Exit.
   A sector can appear in both a trajectory row and a setup row (e.g.
   ↑↑ Rising fast AND ▲ Entry). Compute per bucket:
   - `count`: number of observations
   - `hit_rate`: for bullish badges (Entry, ↑↑, ↑) = % with return > 0;
     for bearish badges (Exit, ↓↓, ↓) = % with return < 0; for Flat and
     No-badge = % with return > 0 (neutral baseline)
   - `mean_return`: arithmetic mean of forward returns
   - `median_return`: median of forward returns

4. **Minimum-observation guard.** Buckets with fewer than 3 observations
   show `None` for hit_rate/mean/median (template renders "—").

5. **Return value:** a list of dicts ordered: Entry, ↑↑, ↑, →, ↓, ↓↓,
   Exit, No-badge. Each dict:
   ```python
   {
       "badge": "▲ Entry",       # display label
       "badge_key": "entry",     # i18n key suffix
       "count": 12,
       "hit_rate": 0.67,         # or None if count < 3
       "mean_return": 0.008,     # or None
       "median_return": 0.006,   # or None
   }
   ```

### Wiring (`dashboard/build.py`)

- Import `build_badge_scorecard` from `dashboard/badges.py`.
- Call it after loading `all_scores_df` (which is `get_scan_history(conn,
  n_scans=None)`), passing the universe dict and cache dir.
- Add `badge_scorecard` to the sectors page render context.

### Price data

ETF prices come from the existing `data/cache/` parquet files (populated
by `scan.py` on each run). `build_badge_scorecard` calls
`fetch_prices(tickers, start, end)` from `src/data/prices.py` to load
them — same as the backtest. The `start` date is the earliest scan date
minus 10 days (buffer); `end` is the latest scan date plus 10 trading
days (forward window).

## Display

### Location

Inside the Backtest tab on the sectors page (`index.html.j2`), below the
existing equity curve chart and metrics table. Separated by a heading.

### Table layout

Heading: **Badge scorecard**
Subtext: "5-day forward return after each badge appeared."

| Badge | Count | Hit rate | Mean | Median |
|-------|-------|----------|------|--------|
| ▲ Entry | 12 | 67% | +0.8% | +0.6% |
| ↑↑ Rising fast | 18 | 61% | +0.5% | +0.4% |
| ↑ Rising | 25 | 56% | +0.3% | +0.2% |
| → Flat | 40 | 50% | +0.1% | 0.0% |
| ↓ Falling | 22 | 55% | −0.2% | −0.1% |
| ↓↓ Falling fast | 15 | 67% | −0.6% | −0.5% |
| ▼ Exit | 8 | 75% | −0.9% | −0.7% |
| No badge | 80 | 48% | 0.0% | 0.0% |

(Values are illustrative.)

- Hit rate and returns use the `signal-hi` / `signal-lo` CSS classes for
  colour coding: green when the badge "worked" (bullish badge with
  positive mean, bearish badge with negative mean), red otherwise.
- Rows with count < 3 show "—" for hit rate, mean, and median.
- A note below the table: "Based on N scans, DATE_START – DATE_END.
  Minimum 3 observations per badge."

### i18n (EN + SV)

New keys in `_i18n.html.j2`:

| Key | EN | SV |
|-----|----|----|
| `badge_scorecard_title` | Badge scorecard | Badgepoäng |
| `badge_scorecard_desc` | 5-day forward return after each badge appeared. | 5-dagars framåtavkastning efter varje badge. |
| `badge_scorecard_note` | Based on {n} scans, {start} – {end}. Minimum 3 observations per badge. | Baserat på {n} skanningar, {start} – {end}. Minst 3 observationer per badge. |
| `badge_sc_count` | Count | Antal |
| `badge_sc_hit_rate` | Hit rate | Träffgrad |
| `badge_sc_mean` | Mean | Medel |
| `badge_sc_median` | Median | Median |
| `badge_rising_fast` | ↑↑ Rising fast | ↑↑ Stiger snabbt |
| `badge_rising` | ↑ Rising | ↑ Stiger |
| `badge_flat` | → Flat | → Flat |
| `badge_falling` | ↓ Falling | ↓ Faller |
| `badge_falling_fast` | ↓↓ Falling fast | ↓↓ Faller snabbt |
| `badge_no_badge` | No badge | Ingen badge |

Existing keys `badge_entry` ("▲ Entry" / "▲ Insteg") and `badge_exit`
("▼ Exit" / "▼ Ursteg") are reused.

## Scope

- **Sectors only** (not themes).
- **Both US and EU regions pooled** in the stats.
- **5 trading-day horizon**, hardcoded.
- **Computed at `build.py` time**, no new DB tables or schema changes.
- **Info-only** — no scoring impact.

## Non-goals

- Per-sector or per-region badge breakdown (too few observations per cell).
- Multiple horizons or horizon selector UI.
- Statistical significance tests (misleading with small N).
- Chart/visualization (a table is the right format for 8 rows).

## Testing

- Unit test for `build_badge_scorecard`: synthetic `history_df` with known
  ranks/composites/changes, mock prices with known forward returns →
  assert correct badge classification and stat computation.
- Edge cases: fewer than 6 scans (returns empty list), a badge with
  fewer than 3 observations (returns None stats), missing price data
  (observation dropped).
- Template render test: verify the scorecard table appears in the
  Backtest tab output.
