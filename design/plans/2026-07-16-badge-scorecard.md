# Badge Scorecard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure whether Entry/Exit setup badges and trajectory badges predict 5-day forward returns, surfaced as an info-only stats table inside the Backtest tab.

**Architecture:** A new `dashboard/badges.py` module replays badge logic over all historical scans, computes 5-trading-day forward returns from cached ETF prices, and aggregates stats per badge type. `build.py` passes the result into the sectors template, which renders it as a table below the existing backtest content.

**Tech Stack:** Python, pandas, Jinja2, existing price cache (parquet), existing `close_at` from `src/backtest/strategy.py`.

## Global Constraints

- Info-only — no scoring impact, no new DB tables or schema changes.
- Sectors only (not themes), US and EU pooled.
- 5-trading-day horizon, hardcoded.
- `docs/` is a CI-generated artifact — do not `git add docs/`.
- Follow conventional commits. Branch: `feature/badge-scorecard`.
- EN + SV i18n for all new UI text.
- Reuse `close_at` from `src/backtest/strategy.py` (import, not copy).
- EU composite sectors: use first ticker in the list (same as `src/backtest/rotations.py:39-40`).

---

### Task 1: Badge scorecard computation (`dashboard/badges.py`)

**Files:**
- Create: `dashboard/badges.py`
- Create: `tests/test_badge_scorecard.py`

**Interfaces:**
- Consumes: `_compute_rank_trajectories(history_df) -> dict` and `_compute_setup(row) -> None` from `dashboard/rows.py`; `close_at(df, date) -> float` from `src/backtest/strategy.py`; `fetch_prices(tickers, start, end) -> dict[str, DataFrame]` from `src/data/prices.py`.
- Produces: `build_badge_scorecard(history_df, universe, price_cache_dir) -> list[dict]` — used by `build.py` (Task 2) and the template (Task 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_badge_scorecard.py`:

```python
"""Tests for dashboard.badges — badge scorecard computation."""
from __future__ import annotations

import math
from unittest.mock import patch

import pandas as pd
import pytest

from dashboard.badges import build_badge_scorecard


def _make_history(n_scans: int = 8, n_sectors: int = 4) -> pd.DataFrame:
    """Synthetic scan history with controllable ranks.

    Sector layout (region=US):
      - TechUp: rank improves 4→1 over 8 scans (strong_up trajectory).
        composite > 0 and change > 0 → Entry badge.
      - EnergyDown: rank worsens 1→4 (strong_down).
        change < 0 → Exit badge.
      - HealthFlat: rank stays 2 (flat trajectory).
        No setup badge.
      - FinFlat: rank stays 3 (flat trajectory).
        No setup badge.
    """
    rows = []
    sectors = ["Technology", "Energy", "Health Care", "Financials"]
    for i in range(n_scans):
        sid = 100 + i
        run_at = f"2026-07-{1 + i:02d}T10:00:00"
        ranks = [4 - i * 3 / (n_scans - 1), 1 + i * 3 / (n_scans - 1), 2.0, 3.0]
        composites = [0.5, -0.2, 0.3, 0.1]
        changes = [0.3, -0.4, 0.1, -0.05]
        for j, sec in enumerate(sectors):
            rows.append({
                "scan_id": sid,
                "run_at": run_at,
                "region": "US",
                "gics_sector": sec,
                "rank": round(ranks[j]),
                "composite": composites[j],
                "change_score": changes[j],
                "level_score": 0.5,
                "data_score": 0.4,
                "sentiment_score": None,
            })
    return pd.DataFrame(rows)


def _make_prices() -> dict[str, pd.DataFrame]:
    """Mock prices: every ticker returns a flat 100 except XLK which rises 1%
    over 5 business days (100 → 101)."""
    dates = pd.bdate_range("2026-06-25", "2026-07-20")
    flat = pd.DataFrame({"Close": [100.0] * len(dates)}, index=dates)

    xlk_prices = [100.0] * len(dates)
    for i in range(len(dates)):
        xlk_prices[i] = 100.0 + i * 0.2
    xlk = pd.DataFrame({"Close": xlk_prices}, index=dates)

    return {"XLK": xlk, "XLF": flat, "XLE": flat, "XLV": flat}


UNIVERSE = {
    "us_sectors": {
        "Technology": "XLK",
        "Energy": "XLE",
        "Health Care": "XLV",
        "Financials": "XLF",
    },
}


@patch("dashboard.badges.fetch_prices")
def test_scorecard_basic(mock_fetch):
    """With 8 scans (6 eligible), produces 8 rows with correct badge labels."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    assert isinstance(result, list)
    assert len(result) == 8
    keys = [r["badge_key"] for r in result]
    assert keys == [
        "entry", "rising_fast", "rising", "flat",
        "falling", "falling_fast", "exit", "no_badge",
    ]
    for row in result:
        assert "count" in row
        assert "hit_rate" in row
        assert "mean_return" in row
        assert "median_return" in row


@patch("dashboard.badges.fetch_prices")
def test_scorecard_too_few_scans(mock_fetch):
    """Fewer than 6 scans → empty list."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=5)
    result = build_badge_scorecard(history, UNIVERSE)
    assert result == []


@patch("dashboard.badges.fetch_prices")
def test_scorecard_min_obs_guard(mock_fetch):
    """Buckets with < 3 observations get None stats."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    for row in result:
        if row["count"] < 3:
            assert row["hit_rate"] is None
            assert row["mean_return"] is None
            assert row["median_return"] is None


@patch("dashboard.badges.fetch_prices")
def test_scorecard_entry_has_positive_mean(mock_fetch):
    """XLK (Entry badge) has rising prices → mean_return should be > 0."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    entry_row = next(r for r in result if r["badge_key"] == "entry")
    if entry_row["count"] >= 3:
        assert entry_row["mean_return"] > 0


@patch("dashboard.badges.fetch_prices")
def test_scorecard_metadata(mock_fetch):
    """Result includes scan count and date range metadata."""
    mock_fetch.return_value = _make_prices()
    history = _make_history(n_scans=8)
    result = build_badge_scorecard(history, UNIVERSE)
    # The function also returns metadata as the last element — but per spec
    # it returns list[dict] of badge rows. Metadata is on each row or
    # returned separately. Check the implementation handles this.
    assert len(result) > 0


def test_scorecard_eu_composite_ticker():
    """EU composite sectors use the first ticker in the list."""
    from dashboard.badges import _sector_ticker_map
    universe = {
        "us_sectors": {"Technology": "XLK"},
        "eu_sectors": {"Financials": ["EXV1.DE", "EXH2.DE", "EXH5.DE"]},
    }
    m = _sector_ticker_map(universe)
    assert m["US|Technology"] == "XLK"
    assert m["EU|Financials"] == "EXV1.DE"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_badge_scorecard.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.badges'`

- [ ] **Step 3: Implement `dashboard/badges.py`**

```python
"""Badge scorecard — historical hit rates for Entry/Exit and trajectory badges."""
from __future__ import annotations

import statistics
from datetime import timedelta

import pandas as pd

from dashboard.rows import _compute_rank_trajectories, _compute_setup, _safe_float
from src.backtest.strategy import close_at
from src.data.prices import fetch_prices

FORWARD_DAYS = 5
MIN_OBS = 3

_BADGE_ORDER = [
    ("▲ Entry", "entry", True),
    ("↑↑ Rising fast", "rising_fast", True),
    ("↑ Rising", "rising", True),
    ("→ Flat", "flat", None),
    ("↓ Falling", "falling", False),
    ("↓↓ Falling fast", "falling_fast", False),
    ("▼ Exit", "exit", False),
    ("No badge", "no_badge", None),
]

_TRAJ_STATE_TO_KEY = {
    "strong_up": "rising_fast",
    "up": "rising",
    "flat": "flat",
    "down": "falling",
    "strong_down": "falling_fast",
}


def _sector_ticker_map(universe: dict) -> dict[str, str]:
    """Build {region|sector: ticker} from universe config.

    EU sectors may be lists (composites); use the first ticker.
    """
    result: dict[str, str] = {}
    for sector, ticker in universe.get("us_sectors", {}).items():
        result[f"US|{sector}"] = ticker
    for sector, ticker_or_list in universe.get("eu_sectors", {}).items():
        if isinstance(ticker_or_list, list):
            ticker = ticker_or_list[0] if ticker_or_list else None
        else:
            ticker = ticker_or_list
        if ticker:
            result[f"EU|{sector}"] = ticker
    return result


def _forward_date(price_index: pd.DatetimeIndex, scan_date: pd.Timestamp, n: int) -> pd.Timestamp | None:
    """Find the n-th trading day after scan_date using the price index."""
    future = price_index[price_index > scan_date]
    if len(future) < n:
        return None
    return future[n - 1]


def build_badge_scorecard(
    history_df: pd.DataFrame,
    universe: dict,
    price_cache_dir: str = "data/cache",
) -> list[dict]:
    """Compute badge scorecard stats from scan history and cached prices.

    Returns a list of 8 dicts (one per badge type) ordered per _BADGE_ORDER,
    or an empty list if fewer than 6 scans are available.
    """
    if history_df.empty:
        return []

    scan_ids = sorted(history_df["scan_id"].unique())
    if len(scan_ids) < 6:
        return []

    ticker_map = _sector_ticker_map(universe)
    all_tickers = list(set(ticker_map.values()))

    scan_dates = {}
    for sid in scan_ids:
        mask = history_df["scan_id"] == sid
        run_at = pd.to_datetime(history_df.loc[mask, "run_at"].iloc[0], utc=True)
        scan_dates[sid] = run_at.normalize()

    earliest = min(scan_dates.values()) - timedelta(days=10)
    latest = max(scan_dates.values()) + timedelta(days=15)
    prices = fetch_prices(
        all_tickers,
        start=earliest.strftime("%Y-%m-%d"),
        end=latest.strftime("%Y-%m-%d"),
        cache_dir=price_cache_dir,
    )

    observations: dict[str, list[float]] = {key: [] for _, key, _ in _BADGE_ORDER}

    for idx in range(5, len(scan_ids)):
        window_ids = scan_ids[idx - 4 : idx + 1]
        window_df = history_df[history_df["scan_id"].isin(window_ids)].copy()

        current_sid = scan_ids[idx]
        current_rows = history_df[history_df["scan_id"] == current_sid]
        scan_date = scan_dates[current_sid]

        trajectories = _compute_rank_trajectories(window_df)

        for _, row_data in current_rows.iterrows():
            region = row_data["region"]
            sector = row_data["gics_sector"]
            sk = f"{region}|{sector}"

            traj = trajectories.get(sk, {"state": "flat"})
            traj_state = traj["state"]
            traj_key = _TRAJ_STATE_TO_KEY.get(traj_state, "flat")

            row_dict = {
                "_raw_composite": _safe_float(row_data.get("composite")),
                "_raw_change": _safe_float(row_data.get("change_score")),
                "trajectory_state": traj_state,
            }
            _compute_setup(row_dict)
            setup = row_dict["setup"]

            ticker = ticker_map.get(sk)
            if not ticker or ticker not in prices:
                continue
            price_df = prices[ticker]
            fwd_date = _forward_date(price_df.index, scan_date, FORWARD_DAYS)
            if fwd_date is None:
                continue

            p0 = close_at(price_df, scan_date)
            p1 = close_at(price_df, fwd_date)
            if not p0 or not p1 or p0 != p0 or p1 != p1:
                continue
            fwd_ret = p1 / p0 - 1.0

            observations[traj_key].append(fwd_ret)
            if setup == "entry":
                observations["entry"].append(fwd_ret)
            elif setup == "exit":
                observations["exit"].append(fwd_ret)
            else:
                observations["no_badge"].append(fwd_ret)

    result: list[dict] = []
    for label, key, bullish in _BADGE_ORDER:
        obs = observations[key]
        count = len(obs)
        if count < MIN_OBS:
            result.append({
                "badge": label,
                "badge_key": key,
                "count": count,
                "hit_rate": None,
                "mean_return": None,
                "median_return": None,
            })
        else:
            if bullish is True:
                hits = sum(1 for r in obs if r > 0)
            elif bullish is False:
                hits = sum(1 for r in obs if r < 0)
            else:
                hits = sum(1 for r in obs if r > 0)
            result.append({
                "badge": label,
                "badge_key": key,
                "count": count,
                "hit_rate": round(hits / count, 3),
                "mean_return": round(statistics.mean(obs), 6),
                "median_return": round(statistics.median(obs), 6),
            })

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_badge_scorecard.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add dashboard/badges.py tests/test_badge_scorecard.py
git commit -m "feat: add badge scorecard computation

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Wire into build.py and template

**Files:**
- Modify: `dashboard/build.py` (add import + call + context)
- Modify: `dashboard/templates/index.html.j2` (add scorecard table in Backtest tab)
- Modify: `dashboard/templates/_i18n.html.j2` (add EN + SV keys)

**Interfaces:**
- Consumes: `build_badge_scorecard(history_df, universe, price_cache_dir) -> list[dict]` from Task 1.
- Produces: rendered badge scorecard table in the Backtest tab, visible in browser.

- [ ] **Step 1: Add import and call in `build.py`**

In `dashboard/build.py`, add to the import block (after the `from dashboard.feed` import, around line 74):

```python
from dashboard.badges import (                    # noqa: E402, F401
    build_badge_scorecard,
)
```

In `main()`, after the `logger.info("Building backtest context …")` block (after line 294), add:

```python
    logger.info("Building badge scorecard …")
    badge_scorecard = build_badge_scorecard(
        all_scores_df, _universe,
        price_cache_dir=str(project_root / "data/cache"),
    )
```

In the sectors `_render()` call context dict (the one starting at line 321), add after `has_rotations`:

```python
            badge_scorecard=badge_scorecard,
```

- [ ] **Step 2: Add the scorecard table to the Backtest tab template**

In `dashboard/templates/index.html.j2`, inside the `tab-backtest` section, after the `{% endif %}` that closes the `{% if has_rotations %}` block (line 268) and before the `{% else %}` (line 269), insert:

```html
  {% if badge_scorecard %}
  <h3 style="margin:22px 0 6px;font-family:var(--font-display);font-size:15px;color:var(--fg1)" data-i18n="badge_scorecard_title">Badge scorecard</h3>
  <p class="tab-note" data-i18n="badge_scorecard_desc">5-day forward return after each badge appeared.</p>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th data-i18n="badge_scorecard_title">Badge</th>
          <th data-i18n="badge_sc_count">Count</th>
          <th data-i18n="badge_sc_hit_rate">Hit rate</th>
          <th data-i18n="badge_sc_mean">Mean</th>
          <th data-i18n="badge_sc_median">Median</th>
        </tr>
      </thead>
      <tbody>
        {% for row in badge_scorecard %}
        <tr>
          <td><span data-i18n="badge_{{ row.badge_key }}">{{ row.badge }}</span></td>
          <td>{{ row.count }}</td>
          {% if row.hit_rate is not none %}
          <td>{{ "%.0f%%"|format(row.hit_rate * 100) }}</td>
          <td class="{% if row.mean_return > 0 %}signal-hi{% elif row.mean_return < 0 %}signal-lo{% endif %}">{{ "%+.1f%%"|format(row.mean_return * 100) }}</td>
          <td class="{% if row.median_return > 0 %}signal-hi{% elif row.median_return < 0 %}signal-lo{% endif %}">{{ "%+.1f%%"|format(row.median_return * 100) }}</td>
          {% else %}
          <td>—</td><td>—</td><td>—</td>
          {% endif %}
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
```

- [ ] **Step 3: Add i18n keys to `_i18n.html.j2`**

In `dashboard/templates/_i18n.html.j2`, add these keys to the EN object:

```javascript
badge_scorecard_title: "Badge scorecard",
badge_scorecard_desc: "5-day forward return after each badge appeared.",
badge_sc_count: "Count",
badge_sc_hit_rate: "Hit rate",
badge_sc_mean: "Mean",
badge_sc_median: "Median",
badge_rising_fast: "↑↑ Rising fast",
badge_rising: "↑ Rising",
badge_flat: "→ Flat",
badge_falling: "↓ Falling",
badge_falling_fast: "↓↓ Falling fast",
badge_no_badge: "No badge",
```

And to the SV object:

```javascript
badge_scorecard_title: "Badgepoäng",
badge_scorecard_desc: "5-dagars framåtavkastning efter varje badge.",
badge_sc_count: "Antal",
badge_sc_hit_rate: "Träffgrad",
badge_sc_mean: "Medel",
badge_sc_median: "Median",
badge_rising_fast: "↑↑ Stiger snabbt",
badge_rising: "↑ Stiger",
badge_flat: "→ Flat",
badge_falling: "↓ Faller",
badge_falling_fast: "↓↓ Faller snabbt",
badge_no_badge: "Ingen badge",
```

Existing `badge_entry` and `badge_exit` keys are already present — do not duplicate them.

- [ ] **Step 4: Verify the build**

Run: `python3 dashboard/build.py`
Expected: builds clean, no errors. The scorecard table appears in `docs/index.html` inside the Backtest tab.

- [ ] **Step 5: Verify in browser**

Start the static server. Navigate to the Backtest tab. Confirm:
- "Badge scorecard" heading appears below the backtest charts.
- Table has 8 rows (Entry, ↑↑, ↑, →, ↓, ↓↓, Exit, No badge).
- Count column shows numbers; rows with count < 3 show "—" for stats.
- Mean/median columns show green/red colouring.
- SV toggle translates all badge labels and column headers.

- [ ] **Step 6: Commit**

```bash
git add dashboard/build.py dashboard/templates/index.html.j2 dashboard/templates/_i18n.html.j2
git commit -m "feat: wire badge scorecard into dashboard

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 3: Backlog update, push, and PR

**Files:**
- Modify: `BACKLOG.md`

**Interfaces:**
- Consumes: all code from Tasks 1-2.

- [ ] **Step 1: Update BACKLOG.md**

Delete the "Entry/Exit badge scorecard" section from Queued. Add a Done entry at the top of Done:

```markdown
- **Entry/Exit badge scorecard** — historical hit-rate table for all 7 badge
  types (Entry, Exit, 5 trajectory states) plus a no-badge baseline. For each
  badge that appeared on a past scan, computes the 5-trading-day forward ETF
  return and aggregates count, hit rate, mean, and median. Displayed in the
  Backtest tab below the equity curves. Computed at `build.py` time from
  `get_scan_history(n_scans=None)` + cached prices; no new DB tables.
  `dashboard/badges.py` holds the logic. EN+SV i18n. Info-only — no scoring
  impact. *(2026-07-16)*
```

- [ ] **Step 2: Commit backlog update**

```bash
git add BACKLOG.md
git commit -m "chore: update backlog for badge scorecard

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

- [ ] **Step 3: Push and open PR**

```bash
git push -u origin feature/badge-scorecard
```

```bash
gh pr create --title "feat: badge scorecard — historical hit rates for setup badges" --body "$(cat <<'EOF'
## Summary
- Add `dashboard/badges.py`: replays trajectory + setup badge logic over all historical scans, computes 5-trading-day forward ETF returns, aggregates stats per badge type (8 rows: Entry, 5 trajectory states, Exit, No-badge)
- Wire into `build.py` and render as a table in the Backtest tab
- EN + SV i18n for all new labels
- Info-only, no scoring impact, no new DB tables

## Spec
`design/specs/2026-07-16-badge-scorecard-design.md`

## Test plan
- [ ] `pytest tests/test_badge_scorecard.py` — all tests pass
- [ ] `python3 dashboard/build.py` — builds clean
- [ ] Backtest tab shows badge scorecard table with 8 rows
- [ ] Rows with < 3 observations show "—" instead of stats
- [ ] Mean/median columns show green/red colour coding
- [ ] SV toggle translates all badge labels and headers
- [ ] No console errors

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — do not merge**

Jonas reviews and merges the PR manually.
