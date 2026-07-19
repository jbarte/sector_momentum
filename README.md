# Sector Momentum Scanner

A daily momentum scanner for **US SPDR** and **STOXX Europe 600** sector ETFs, mapped to the 11 GICS sectors. The pipeline computes data-pillar signals (relative strength, returns, moving-average structure, breadth, volume), scores and ranks every sector cross-sectionally, persists snapshots to Supabase/Postgres, and publishes a static dashboard to GitHub Pages. A parallel **thematic ETF** track (AI, defence, clean energy, etc.) runs the same scoring engine over a separate universe.

> **Disclaimer:** This is a personal research and hobby project -- analytical tooling for measuring momentum, **not investment advice**.

## Live dashboard

<https://jbarte.github.io/sector_momentum/>

## Required environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Supabase Postgres connection string (direct, port 5432) |
| `SUPABASE_SERVICE_KEY` | Service-role key for the `db-backups` Storage bucket (optional for local dev -- backup step degrades gracefully without it) |

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires .env)
python3 scan.py

# Run tests
pytest
```

`scan.py` options: `--dry-run`, `--no-dashboard`, `--no-backup`, `--no-alerts`, `--no-finbert`.

## Project structure (overview)

```
scan.py                  # entrypoint: full pipeline
config/                  # universe, weights, sector maps, themes
src/
  data/                  # price loaders, FinBERT news sentiment, constituents
  signals/               # momentum, relative strength, technical, breadth
  backtest/              # rotation backtest engine
  scoring.py             # cross-sectional z-scores, composite ranking
  pipeline.py            # signal-row builders
  state.py               # Supabase/Postgres read/write, deltas
dashboard/
  build.py               # Jinja2 + Plotly -> docs/ static site
  templates/             # HTML/JS templates
```

## Further reading

- [ARCHITECTURE.md](ARCHITECTURE.md) -- system design, data flow, module details
- [BACKLOG.md](BACKLOG.md) -- queued and completed work
- [CLAUDE.md](CLAUDE.md) -- contributor instructions (commit style, CI, generated artifacts)
