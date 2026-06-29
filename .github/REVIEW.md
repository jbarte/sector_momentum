# Code Review Instructions

Guidance for automated PR review of the Sector Momentum Scanner. Keep reviews
focused on real defects; this is a small project with a single maintainer who
merges manually.

## What "Important" means here

Reserve Important for things that break behaviour or correctness:
- Wrong scoring/ranking logic (composite, z-scores, deltas, trajectory)
- A signal computed or stored incorrectly (NaN/None handling, wrong column)
- Secrets exposure — `DATABASE_URL`, `SUPABASE_SERVICE_KEY`, or any API token
  logged, printed, written to a tracked file, or echoed in CI output
- Data-loss or wrong-write paths against Supabase (`scans`, `signals`, `scores`)
- Client-side dashboard JS that throws and kills interactivity (a single JS
  syntax error blanks the whole page — e.g. an empty `var X = ;` from a missing
  Jinja context variable)

Treat naming, formatting, and style preferences as Nit at most.

## Do not report

- `docs/index.html` and anything under `docs/assets/` — generated build output
  from `dashboard/build.py`, not hand-edited source. Review the template
  (`dashboard/templates/index.html.j2`) and `dashboard/build.py` instead.
- `design/` — design specs and implementation plans, not code.
- Plotly JSON blobs embedded in the dashboard.
- Pre-existing issues unrelated to the PR's diff.

## Always check

- **No secrets in tracked files.** `.env` is gitignored and holds `DATABASE_URL`
  and `SUPABASE_SERVICE_KEY`; they must never appear in committed code, logs, or
  workflow files.
- **Scan resilience.** External fetches (yfinance/stooq prices, Google Trends)
  must fail soft — a fetch error (e.g. Trends 429) should fall back to neutral
  values and let the scan complete, not abort the run.
- **Canonical composite stays pure-data.** The stored `composite`/`rank` are
  data-only; sentiment is stored but blended into rankings only client-side in
  the dashboard. Flag any change that folds sentiment into the server-side
  canonical composite unless the PR explicitly intends it.
- **Dashboard JS↔Python parity.** `dashboard/assets/rescore.js` mirrors the
  Python scoring/ranking semantics; changes to one side should keep
  `tests/test_rescore_parity.py` green.
- **Tests run via the project venv** (`.venv/bin/pytest`) and Node is required
  for the parity test.
