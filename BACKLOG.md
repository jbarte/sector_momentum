# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

---

## Data inventory & coverage statistics

**What:** A way to inspect what data we actually have in the database and summary
statistics about coverage — how much data, over what date range, and where gaps exist.

**Why:** As scans accumulate in Supabase, it's hard to know at a glance how complete
the dataset is — how many scans exist, which sectors/regions are covered, the earliest
and latest scan dates, and whether any runs are missing signals or scores.

**Possible scope:**
- Count of scans, and first/last `run_at` dates (overall and per region)
- Cadence check — average gap between scans, and any missing-day gaps vs the
  every-2-days schedule
- Per-sector / per-region coverage: how many scans each sector appears in
- Signal completeness: which `signal_name`s are present, and the count of
  NULL `raw_value` / `z_value` rows (data-quality view)
- Row counts per table (`scans`, `signals`, `scores`)

**Possible delivery:**
- A CLI command (e.g. `python scan.py --stats` or a small `stats.py` script) that
  prints the summary, querying Supabase via the existing `src/state.py` helpers
- Optionally surface the same numbers as a small panel/tab on the dashboard

**Notes:** Read-only — no schema changes needed. Builds directly on the
Supabase-backed `src/state.py` data layer.
