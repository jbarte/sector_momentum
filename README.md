# ETF Momentum

A daily momentum scanner for a curated universe of **thematic and niche ETFs**
(AI & robotics, semiconductors, uranium, space, gold miners, shipping,
insurance, …). It ranks every theme by how strongly it is outperforming a
single global benchmark (ACWI), persists a snapshot to Supabase/Postgres, and
publishes a static dashboard to GitHub Pages.

The universe lives in [`config/themes.yaml`](config/themes.yaml) — 18 themes at
the time of writing, one US-listed ETF each. For every theme the config also
records the closest **UCITS** equivalent, since the scoring uses the US listing
(longer history, deeper liquidity) while a European investor buys the UCITS one.

> **Disclaimer:** a personal research and hobby project — analytical tooling for
> measuring momentum, **not investment advice**.

## Live dashboard

<https://jbarte.github.io/sector_momentum/>

## How it works, in one paragraph

Everything is **relative and cross-sectional**. Each theme gets eight
price-based signals, each signal is z-scored *across the other themes on the
same day*, and those roll up into a **Level** score (how strong is it now) and a
**Change** score (which way is it heading), weighted 50/50 into a composite. A
composite of +1.0 means "roughly better than 5 of 6 themes today" — it says
nothing about whether the group as a whole is going up. Ranking is the composite
sorted best to worst.

A **horizon** preset turns that ranking into Enter / Hold / Exit badges using a
hysteresis band: a holding is kept while its rank stays inside `top_n + buffer`,
so a one-rank wobble is not a trade. Most themes, most days, sit silently inside
the band — that is the intended state.

There are **two presets, both reviewing monthly**, differing only in how far a
holding may fall before it is sold: `Medium` (hold 4, sell past rank 9) and
`Long` (hold 5, sell past rank 13). A third, faster preset existed until
2026-08-14 and was removed — sweeping across two independent windows found the
rebalance *cadence* contributes little while the width of that band does nearly
all the work. The leaderboard draws both band edges as horizontal cut lines, so
switching preset visibly moves them. They appear only while the table is sorted
by rank — under any other sort the band is not contiguous, so a single line
could not separate in-band from out.

The badges themselves are a **signed-in feature** and combine the band with the
holdings you have marked: `Enter` only for something you do not own, `Hold` for
anything you own that is still inside the band, `Exit` once it leaves.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full pipeline, or the
**Methodology** link in the dashboard footer for the reader-facing version.

## Required environment variables

Copy `.env.example` to `.env` and fill in:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Supabase Postgres connection string (direct, port 5432) |
| `SUPABASE_SERVICE_KEY` | Service-role key for the private `db-backups` and `trends-cache` Storage buckets. Optional for local dev — both degrade gracefully without it |

## Dev commands

```bash
# Rebuild the dashboard from the existing DB (no network, no scan)
python3 dashboard/build.py

# Run the full scan (requires .env)
python3 scan.py

# Replay the strategy over history and refresh backtests/
python3 backtest.py

# Run tests
pytest
```

`scan.py` options: `--dry-run`, `--no-dashboard`, `--no-backup`, `--no-alerts`,
`--no-finbert`, `--no-cache`.

`backtest.py` defaults its transaction cost from `costs.round_trip_bps` in
`config/weights.yaml`. Pass `--cost-bps 0` only to reproduce historical
cost-free figures — sweeping at zero systematically favours whichever cadence
trades most.

**`--start` bounds the evaluation window, not the price fetch.** History always
comes from `replay.FETCH_START`, so the trailing-window signals are warm on the
first evaluated date; `above_200dma` needs 200 bars and is NaN before that.
Truncating the fetch instead is a trap that has already produced one wrong
analysis — it inverted the horizon preset ranking. A window that starts inside
the warm-up is rejected, and a windowed run refuses to overwrite the committed
`backtests/` artifact, so pass `--out` when exploring.

`scripts/horizon_sweep.py` is the tool that *chooses* the presets: it sweeps
cadence x top_n x buffer and prints the return-versus-churn frontier. It shares
the fetch/evaluation split and the cost assumption with `backtest.py`.

## Project structure

```
scan.py                  # entrypoint: the daily pipeline
backtest.py              # strategy replay -> backtests/
restore.py               # restore a DB backup from Supabase Storage

config/
  themes.yaml            # THE scoring universe: themes, tickers, UCITS equivalents
  weights.yaml           # pillar split, signal params, horizon presets, trading cost
  universe.yaml          # scan-wide settings (price lookback)

scripts/
  horizon_sweep.py       # cadence x top_n x buffer sweep -> the preset frontier
  signal_correlation.py  # signal redundancy audit
  *.sql                  # one-off Postgres migrations

src/
  data/prices.py         # yfinance fetch, parquet cache, cohort as-of alignment
  data/news_sentiment.py # GDELT headlines -> FinBERT polarity (info-only)
  data/macro.py          # FRED macro context (not wired into scoring)
  signals/               # momentum, relative strength (RRG), technical
  pipeline.py            # signal-row builders (pure functions over price dicts)
  scoring.py             # cross-sectional z-scores -> level/change/composite/rank
  horizons.py            # horizon presets + trading cost, read by everything
  cohorts.py             # cohort definition (one: THEME)
  state.py               # Postgres DDL, read/write, deltas
  backtest/              # replay, strategy, metrics, results, fetch/eval window
  alerts.py              # band-crossing detection
  personal_alerts.py     # per-user alert routing

dashboard/
  build.py               # Jinja2 + Plotly -> docs/
  templates/             # HTML/JS/CSS templates, EN/SV i18n
  assets/                # client-side JS (auth, positions, rescore, …)
```

## Notes for contributors

- `docs/` is a **build output and is not tracked in git.** Build it locally to
  check a change; CI rebuilds it from the database on every run.
- Design docs and plans live in the **private** companion repo
  `jbarte/sector_momentum-notes`, not here — this repo is public for free-tier
  Pages hosting.
- See [CLAUDE.md](CLAUDE.md) for git workflow, commit style and backlog rules,
  and [BACKLOG.md](BACKLOG.md) for queued and completed work.
