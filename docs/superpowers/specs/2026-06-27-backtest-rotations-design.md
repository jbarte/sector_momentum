# Backtest Phase 2 — rotation event-study — design

**Date:** 2026-06-27
**Status:** Approved (design)
**Backlog item:** "Backtest against past rotations — Phase 2 (rotation event-study)"
**Builds on:** Phase 1 (edge) — `docs/superpowers/specs/2026-06-26-backtest-design.md`

## Purpose

The "early-flag" half of the backtest: for a curated set of historical sector rotations,
show whether the scanner's **rank for that sector climbed before the price ran**. Pure
visualization — no scoring or composite changes.

## Approach

Visual-only small-multiples. Per curated rotation, overlay two series across the rotation
window:
- the sector's **scanner rank** (inverted axis, so "up = better rank"), recomputed
  point-in-time, and
- the sector ETF's **price**, normalized to 100 at the window start.

You eyeball whether rank rose ahead of the price move. No quantified "lead" metric (would
require a fuzzy definition of when a price move "starts" — deferred / YAGNI).

## Reuse & constraints

- Entirely reuses the Phase-1 point-in-time engine: `replay.score_as_of(universe, prices,
  as_of, region)` (returns a scored frame indexed by `region|sector` with `composite` and
  `rank`) and `replay.month_end_dates(index)`.
- **Price-pillars only** (breadth + sentiment excluded), same as Phase 1.
- **No composite change**; this is read-only analysis.
- **US-focused:** US price history runs to ~2003; EU is shorter, so seeded rotations are US.
  A rotation naming a region/sector with insufficient history is skipped gracefully.

## Components

- **`config/rotations.yaml`** (new, editable) — a list of rotations, each:
  `name`, `region`, `gics_sector`, `start` (YYYY-MM-DD), `end` (YYYY-MM-DD). Seeded with a
  few US rotations (e.g. Energy 2021-01→2022-06; Technology/AI 2022-10→2023-12;
  Utilities/defensive 2022-01→2022-09). Sector names must match `config/universe.yaml`.
- **`src/backtest/rotations.py`**
  - `load_rotations(path="config/rotations.yaml") -> list[dict]`
  - `event_study(universe, prices, rotations) -> list[dict]` — for each rotation, over the
    month-end dates within `[start, end]`: call `score_as_of` for the rotation's region,
    extract that `region|sector` row's `rank` and `composite`; pull the sector ETF's close
    and normalize to 100 at the first window date. Returns per-rotation:
    `{name, region, sector, ticker, dates: [...], rank: [...], composite: [...],
    price_indexed: [...]}`. A rotation whose sector/ticker/data is missing or has < 2 valid
    month-ends is skipped (logged), not fatal.
- **`src/backtest/results.py`** — `write_results(tracks, rotations=None, out_dir=…,
  generated_at=…, top_n=…)` adds a `"rotations"` key to `summary.json` (defaults to `[]`;
  backward-compatible with existing callers/readers).
- **`backtest.py`** — after `run_all`, compute the event-study (unless `--no-rotations`) via
  `load_rotations` + `event_study`, and pass it to `write_results`.
- **Dashboard** (`dashboard/build.py` + template) — `_build_rotation_figures(summary) ->
  list[dict]` returns per-rotation `{title, fig_json}` dual-axis Plotly charts (rank
  inverted on the left y-axis; normalized price on the right y-axis). Rendered as
  small-multiples under the equity curves in the existing **Backtest** tab. Graceful
  absence: no rotations → a "no rotations yet" note, build never fails.

## Data flow

```
backtest.py
  └─ (Phase 1) run_all → tracks
  └─ rotations = load_rotations("config/rotations.yaml")
  └─ event = rotations.event_study(universe, prices, rotations)   # reuses score_as_of
  └─ results.write_results(tracks, rotations=event)               # summary.json["rotations"]

dashboard/build.py
  └─ summary = load_summary("backtests")
  └─ _build_rotation_figures(summary) → small-multiples in the Backtest tab
```

## Error handling / edge cases

- Missing `config/rotations.yaml` → `load_rotations` returns `[]` (feature inert).
- Rotation with unknown sector, missing ETF price, or < 2 month-ends in window → skipped
  with a log line; other rotations proceed.
- `score_as_of` returning `None` for a date (no rows) → that date omitted from the series.
- Dashboard with no `rotations` in summary → placeholder, no failure (mirrors Phase 1).

## Testing

- `load_rotations` — parses the YAML to the expected list of dicts; missing file → `[]`.
- `event_study` (synthetic prices) — produces the rank series for the named sector and a
  `price_indexed` series starting at 100; skips a rotation with a bogus sector/ticker.
- `results` round-trip — `summary.json` includes `rotations`; old summaries without the key
  still load.
- `_build_rotation_figures` — returns valid Plotly JSON per rotation (dual-axis: two
  traces), and `[]` when the summary has no rotations.

## Out of scope

- Quantified lead/lag metric (visual-only by decision).
- Non-curated/auto-detected rotations; EU-specific rotations (data-limited).
- Any change to the canonical composite or the Phase-1 edge backtest.
