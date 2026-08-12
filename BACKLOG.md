# Backlog

Loosely prioritized list of features and improvements not yet scheduled.

**How this file stays in sync (read before editing):**

- **One item = one `##` section** under Queued (or one bullet in a grouped
  sweep). Keep sections self-contained — union merge combines *additions*
  cleanly but silently doubles concurrent *edits* to the same paragraph.
- **Shipping an item: delete its Queued section entirely** and add one entry
  at the **top of Done** — in the same branch/PR as the code. Never
  strikethrough-in-place in Queued; half-struck sections are exactly how this
  file drifted before.
- **Partially shipping:** rewrite the Queued section so it describes *only
  what remains*, and record the shipped part in Done.
- **Done is append-at-top and never edited** — it's the permanent record.
- Run `/backlog-sync` to audit Queued against git history and the code when
  drift is suspected (e.g. after merge conflicts touching this file).

---

# Queued

## Alerts section should be a modal, opened from a footer link

The alerts/notifications UI (`_footer.html.j2`, `<section id="alert-prefs">`)
currently renders as a **permanently-present block appended after the footer**,
shown only when `auth` is configured and toggled by `alert-prefs.js` flipping
its `hidden` attribute. It sits below the site footer — after the disclaimer,
Methodology link and RSS link — which is both an odd reading order and an odd
place for a control that only signed-in readers can use.

**Do this:** make it a modal opened from a footer link, exactly mirroring the
Methodology treatment that already works:

- A `<button type="button" id="alerts-link" class="footer-link">` in the
  `<footer class="site-footer">` row, beside the existing `#methodology-link`.
- The section's markup moves into an overlay + dialog wrapper matching
  `_methodology.html.j2`'s shape (`.methodology-overlay` / `role="dialog"` /
  `aria-modal="true"` / `aria-labelledby`), with its own close button.
- Bind with the **existing shared helper**: `window.SMModal.bind(overlay,
  {closeBtn})` from `_modal.js.j2` — it already provides the focus trap,
  Escape, backdrop-close and focus restore. Do not hand-roll any of that; the
  2026-08-09 audit's P1 finding was specifically that the gate modal declared
  `aria-modal="true"` and implemented none of it, and the fix was to lift
  `_methodology.html.j2`'s working implementation into that shared helper.
- Consider `location.hash === "#alerts"` deep-linking, matching
  `#methodology`.

**Watch out for two things:**

1. **`alert-prefs.js` owns `#alert-prefs`'s `hidden` attribute** to mean "this
   reader has alerts available". A modal overlay also uses `hidden` to mean
   "closed". Those two meanings will collide if the section element becomes the
   overlay element — the wrapper needs to be a new element, with the existing
   `#alert-prefs` section nested inside it, so `alert-prefs.js` keeps working
   unchanged.
2. The whole block is inside `{% if auth %}`, so the footer link must be too —
   otherwise guests get a link that opens an empty dialog.

**Why it is worth doing:** it removes a stray always-rendered block from below
the footer, puts the control where its sibling controls already live, and
inherits a modal implementation that is already accessible rather than adding
a third bespoke one.

## Ongoing fund costs (TER) are not modelled anywhere

`costs.round_trip_bps` covers per-trade cost only. The backtest has no concept
of an **annual** drag, and two real ones exist:

- **The fund's TER.** Avanza's disclosure for L&G ROBO Global Robotics shows
  *Löpande avgifter 0.8%/yr* plus *Transaktionsavgifter 0.03%/yr*. Against a
  13.9% modelled CAGR that is not a rounding error.
- **The US-vs-UCITS TER gap.** The backtest runs on US-listed ETF price series,
  which are already net of the *US* fund's TER. The instrument actually bought
  is the UCITS equivalent with its own, usually higher, TER. So the modelled
  return silently assumes the cheaper wrapper.

Recorded UCITS TERs in `config/themes.yaml` average 0.46% (max 0.65%), so a
first cut is a flat annual haircut of roughly 0.5% applied to strategy returns —
but note it should apply to the **benchmark** too, or the comparison tilts the
wrong way (ACWI trackers are cheap, ~0.12–0.20%).

Worth checking while doing this: the fund in that Avanza screenshot (L&G ROBO)
is **not** the one `themes.yaml` lists for AI & Robotics — the config records
XAIX (Xtrackers AI & Big Data, 0.35%). If the position actually held differs
from the recorded UCITS equivalent, the recorded TERs are not the ones being
paid, and the `match` quality field is describing the wrong instrument.

## Re-pick the horizon presets at realistic cost

The cost assumption was fixed on 2026-08-09 (see Done), but the presets
themselves were **selected** by a sweep that defaulted to 0 bps, and free
trading systematically flatters whichever cadence trades most. Re-running the
sweep at 50 bps puts **all three current cells off the frontier**:

| preset | current cell | @50bps | a frontier cell that beats it |
|---|---|---|---|
| short | W/3/5 | 14.8% / 0.70 / 26.9 tr | **W/3/6 — 16.3% / 0.76 / 19.8 tr** |
| medium | M/5/4 | 15.0% / 0.83 / 19.5 tr | M/4/5 — 15.7% / 0.84 / 14.7 tr |
| long | 2M/4/6 | 13.7% / 0.72 / 7.2 tr | 2M/5/8 — 14.1% / 0.75 / 4.4 tr |

`W/3/6` beats the current `short` on return, Sharpe **and** churn at once —
one wider buffer.

**Deliberately not done as part of the cost fix.** This changes what the
dashboard tells you to hold, and it rests on one sweep over one market history.
This repo has twice in one month had a single-cell result reverse under a
subperiod check (SIL/OIH, and the 12m trend filter). Re-pick with the
multi-window discipline: confirm each candidate on 2008– *and* 2015– before
adopting, and check the band fraction `(top_n + buffer) / n_themes` rather than
the absolute buffer, since the universe size keeps moving.

## Composite structure — 4.2 effective signals of 8

Same measurement run. Worth recording so the question is not re-opened from
scratch, and **not** obviously worth acting on.

- Effective independent signals (PCA entropy on the correlation matrix):
  **4.21 of 8**; PC1 alone explains 51% of variance.
- LEVEL is the concentrated pillar: **2.42 effective of 4**, mean pairwise
  ρ = 0.59. `rs_ratio` ↔ `above_50dma` = 0.79.
- The pillars leak into each other: `return_3m` (Level) ↔ `ma50_slope`
  (Change) = **0.90**.
- `mean(z(above_50dma), z(rs_ratio))` alone reproduces the full ranking at
  **0.896 Spearman**, 84% identical top-5 holdings.

So the ranking is dominated by two trailing-strength signals. It is not that
the other six are useless — 16% of picks differ, which compounds — but the
"eight signals across two balanced pillars" model oversells what is happening.
Any future signal added to Level should be checked for correlation against
`above_50dma` and `rs_ratio` first; adding a ninth correlated signal buys
almost nothing.

## Per-user horizon for alerts (level 3)

The Short/Medium/Long selector drives the backtest curves and the leaderboard
badges, but **alerts stay on the configured default** (`horizons.default` in
`config/weights.yaml`). Making them per-user needs:

- a horizon column on the alert-prefs table (`dashboard/assets/alert-prefs.js`,
  `src/personal_alerts.py` already carry per-user rows)
- band-crossing evaluated per user in the scan rather than once globally —
  `src/alerts.py:detect_badge_events` currently computes one set of crossings
  from the default horizon

Deferred because it touches the path that reaches Jonas's inbox, and because
one shared default is defensible until more than one person uses the dashboard.

## Backtest artifact is stale, non-reproducible, and ignores `--start`

Three related defects in `backtests/`, all found while wiring the theme track
(2026-08-05). None is urgent; all undermine trusting the backtest tab.

**1. The committed artifact is 11 years narrower than a fresh run.**
`backtests/summary.json` was seeded once (`1c0dd08`, generated 2026-06-26 from a
`--start 2015-01-01` run) and never refreshed. `DEFAULT_START` is `2003-01-01`,
so a plain `python3 backtest.py` today produces a materially different picture:

| | committed | fresh run |
|---|---|---|
| US window | 2015-01-30 → 2026-06-25 | 2003-05-30 → 2026-08-04 |
| US CAGR / maxDD | 12.5% / −18.4% | 10.8% / **−45.2%** |
| EU CAGR / maxDD | 10.3% / −18.5% | 8.5% / **−42.2%** |

The live dashboard therefore shows a backtest that excludes the GFC and
understates drawdown by more than half. **Decide which window is intended** —
longer history with a real crash is more honest, but it is a product choice, so
it was deliberately not slipped into an unrelated PR.

**2. Two identical runs disagree.** — **FIXED 2026-08-12.** Root-caused: the
engine is deterministic (same prices in one process → identical metrics); the
drift came entirely from each run re-fetching prices. `_cache_is_fresh` judged
coverage by the cache's first row, so any instrument younger than `--start`
(49 of 53 cached tickers at `--start 2003-01-01`) was permanently "too short"
and refetched every run. Caches now record the window they were fetched over,
so a second run reuses pinned inputs. Verified: two consecutive backtests now
produce byte-identical metrics and equity curves (previously ~1e-7 apart). The
"tied holdings flip" half was already fixed by `_select`'s deterministic sort
(2026-08-08). See Done.

**3. `--start` does not trim cached data.** — **FIXED 2026-08-12.** The cache
branch of `fetch_prices` now trims to `start` on load; regression test
`test_start_trims_data_served_from_a_warm_cache` pins the cache path via
`stats_out` so it cannot pass by silently refetching. See Done.

**Sequencing:** the sector tracks disappear when the sector cohort is retired, so
fix (1) is best resolved at that point — regenerate the artifact once, with only
the THEME track in it. (2) and (3) are independent.

## Sector-era naming in the data layer (`region`, `gics_sector`)

Cosmetic only, and **not obviously worth doing** — recorded so the question
isn't re-opened from scratch. The user-visible naming was already fixed when the
leaderboard was ungrouped (column reads "Theme", copy says themes); what remains
is internal.

| identifier | refs | what it is |
|---|---:|---|
| `region` | ~265 | **DB column** on `signals`, `scores`, `sentiment_signals`, `positions`, and the `v_recent_scores` view |
| `gics_sector` | ~123 | **DB column** on the same tables |
| `sector_key` | ~27 | derived `"{region}\|{name}"` string |
| `sectors_expected` / `sectors_produced` | 16 | `scans` health columns |
| `sector_id`, `top_sector`, `sector_count` | ~9 | template/DOM ids and report fields |

**Recommendation: leave the two DB columns alone.** Renaming them means a
migration against a live database that also holds 41 scans of retired sector
history, plus the Supabase view, its RLS/grants, the backup/restore path, and
every reader — for zero functional gain. `region` in particular is load-bearing
*as a name that no longer matches its meaning*: it is the filter that keeps the
retired US/EU rows out of every read, so touching it is the riskiest cosmetic
change available.

If it is ever done, `region` → `cohort` and `gics_sector` → `name` are the honest
names, and the cheap subset (`sector_key`, `sector_id`, `top_sector`,
`sector_count`, the two `scans` health columns) can be renamed independently of
the schema at much lower risk.

## Design review findings (2026-08-09 audit)

From a dual-agent design review + mechanical browser measurement. Nielsen
score **23/40**; cognitive-load checklist fails 6 of 8. Full detail is in the
review thread; the actionable set:

**P0 — three of nine leaderboard columns carry no information.** `DATA` is a
verbatim duplicate of `COMPOSITE` in every row (sentiment weighs 0, so
`composite = data_score` by construction). `RANK Δ` is empty (see the weekend
item above). `SENTIMENT` affects nothing at the default weight. Meanwhile the
composite — the entire thesis — is rendered as bare 3-decimal monospace with no
bar, no zero reference, no scale.

**P0 — `.z-bar` encodes signed values as unsigned.** It fills from the *left*
with width ∝ |z| and uses near-invisible `#C4B89A` for negative, so −2.5 and
+2.5 render identically in the same direction, and the worst signals appear as
the faintest marks. Should be a centre-origin diverging bar.

**P0 — meaning-bearing text fails contrast at sub-10px.** 54 elements below
4.5:1. Column headers 2.95:1 at 10px. `.traj-badge` — the Trend column the
guide explicitly says to trust for exits — is **3.80:1 at 9.84px**.

**P0 — no focus indicators.** 43 of 43 visible interactive elements show no
ring; the stylesheet has only three `:focus-visible` rules, none covering tabs,
chips, rows or headers.

**P1 — the gate modal declares `aria-modal="true"` and implements none of it.**
No focus move, no trap, no Escape, no backdrop close; first Tab lands *behind*
the overlay. `_methodology.html.j2` implements all of it correctly and its
comment claims it mirrors the gate modal — it is the other way round. Lift the
working `open/close/onKey` block into a shared helper.

**P1 — mobile shows ~35% of the table.** At 375px the wrap is 269px against a
769px table with no sticky first column, so scrolling right loses rank and
theme name and `TREND` is unreachable blind. 32 touch targets under 44px; the
⚙ that changes ranking weights is 7×18px.

**P1 — badge/trend hierarchy is inverted against the copy.** `▲ Entry` is a
tinted pill beside the theme name; `Trend` is a 9.84px arrow in the last
column — but the guide says badges describe *position only* and Trend is the
health check. A theme can show a loud green Entry next to a red ↓.

**P2 — heading outline is broken** (`h2` → `h4`, no `h3`); no `<main>` and no
skip link, and `role="tablist"` on the only `<nav>` suppresses the navigation
landmark, leaving `<footer>` as the page's sole landmark.

**P2 — 13 icon-only `↗` links have no accessible name**; 474 elements render
under 12px.

## Deferred UI/code polish (small, grouped sweep)

Minor findings deliberately deferred during code review — none affect
correctness, all are small. Recorded so they aren't rediscovered from scratch.

- **Position-star tooltips are English-only.** `dashboard/assets/positions.js`
  sets `title`/`aria-label` on the ★/☆ toggle as literal English strings; the
  rest of the UI carries EN/SV pairs. Add SV strings (the glyph itself is
  language-neutral, so this is polish, not a blocker).
- **`GoTrueClient` multiple-instance console warning.** `auth.js` and
  `positions.js` each call `createClient`, so Supabase logs a "multiple
  GoTrueClient instances" warning. Harmless (both share the same persisted
  session via localStorage) but noisy in the console; could be resolved by
  exposing one shared client.
- **Themes-page setup badges are lagged for signed-in users.** The client-side
  live upgrade only rebuilds the sector table (`#leaderboard-table`), so on the
  themes page the Exit/Entry badges — and therefore the held+Exit ⚠ cue — reflect
  the baked (lagged) scan rather than the live one.
- **Dead guard branches in `scripts/walkforward_weights.py`.** The
  `bench_returns`/`base_bench` `None`-checks are unreachable, since Phase B is
  gated on the baseline scheme having succeeded. Reviewed and accepted as
  harmless; remove if that file is touched again.

## Rebrand Phase 2 — rename the repo (optional, arguably forever)

Phase 1 shipped 2026-08-09: the product is called **ETF Momentum** everywhere a
reader can see it. What remains is only the repo/URL rename, which is a
different risk class — it touches sign-in.

It buys a tidier URL and nothing else; nobody reads the repo path, everybody
reads the `<h1>` — which now already says the right thing.

- Pages URL moves to `jbarte.github.io/etf_momentum/`. GitHub redirects the old
  paths, but the **Supabase Auth Site URL and redirect allowlist must be updated
  first** or magic-link sign-in breaks for everyone, including you.
- **Companion repo** `sector_momentum-notes` renames alongside it, plus the
  ~10 spec paths quoting it in this file and in `CLAUDE.md`.
- **Local clone path.** `~/AI Projects/sector_momentum` is what keys this
  project's Claude memory and session history
  (`~/.claude/projects/-Users-jonasbarte-AI-Projects-sector-momentum/`).
  Renaming the folder orphans both. Rename the remote and leave the local
  directory alone, or move the memory directory deliberately.
- `sector_momentum_test` DB name in `tests/test_state_wipe_guard.py:70` and the
  CI workflow.

**Timing:** the only forcing function would be wanting the URL to match the
name. There is no deadline, and the Supabase step means a botched attempt locks
everyone out of sign-in — so do it deliberately or not at all.

**Out of scope:** the DB columns `region` / `gics_sector` — see *Sector-era
naming in the data layer* above, which recommends leaving them alone. A rebrand
is a rename of the *product*, not a schema migration; conflating the two is how
a cosmetic PR turns into a live-database risk.

---

# Parked

## Signed-in drill-down gap after a universe change

**Accepted 2026-07-26 — self-healing, no code change.** For roughly `LAG_DAYS`
(7) after any universe change, a signed-in user can see leaderboard rows that
cannot expand.

Why: the baked dashboard renders the newest scan at least 7 days old
(`dashboard/gating.py`, `LAG_DAYS = 7`), while signed-in users are upgraded
client-side to the *latest* scan. A sector that exists only in the latest scan
therefore has no baked `.breakdown-row` panel to attach to, so clicking it does
nothing (it degrades quietly — no error).

Observed after the 2026-07-18 EU sub-sector split (Banks / Financial Services /
Insurance / Basic Resources / Chemicals), which the then-current lagged scan
(132, pre-split) knew nothing about. It resolves on its own once the lagged
window crosses the change.

Not worth fixing by baking the latest panels — that would leak gated data into
the public HTML and defeat content gating. The real fix, if it ever becomes
annoying, is a gated client-side breakdown fetch (its own spec/plan).

## Symbol-based Trends sentiment — Phase 2 (US constituents)

**Parked 2026-06-26 after Phase 1 validation.** Adding constituent tickers
(more, lower-volume, more-ambiguous terms) makes ticker-collision
contamination worse, not better. Key findings kept for the record:

- Mechanism works for liquid US ETFs (full 13/13 coverage on `XLK/VGT` etc.);
  EU `.DE` tickers are dead on Trends (0/13).
- Ambiguous tickers dominate the cross-sectional z (`VOX` → Vox Media z +4.16,
  `LOGS` → the English word z +1.27). Blocklisting is whack-a-mole; the real
  fixes are entity mids (since shipped for sectors, 2026-07-04) or the FinBERT
  pivot (since shipped, 2026-07-17).
- If ever revived: needs top-N liquidity ranking (no market-cap source in
  `fetch_sp500_constituents()`), aggregation weighting, and the Trends
  day-cache (since shipped, 2026-07-07).

Phase 1 design + plan: `design/{specs,plans}/2026-06-26-symbol-trends-*`.

## Streamlit live drill-down

Optional interactive drill-down UI. Carried from early planning; the static
dashboard's drill-down tab covers most of the need.

## Price fetch is a single point of failure (yfinance-only)

Decided 2026-08-09, when stooq was removed (see Done): yfinance had already
been carrying 100% of live fetches for weeks, so removing the dead stooq path
changed nothing in practice — but it did remove the *fallback*. A yfinance
outage or breaking API change now fails every ticker on that run instead of
falling through to a second source.

Not acted on now — replacing it means picking a provider (Alpha Vantage,
Twelve Data, IEX, Polygon, etc.), most of which need an API key and carry
their own rate limits, so it is its own integration + test surface, not a
same-day fix. Reopen this if yfinance actually fails a scan, rather than
speculatively — the caching layer already absorbs most single-day hiccups.

## Weekend scans score Thursday's close, not Friday's

The `_cache_is_fresh` 1-day grace (`src/data/prices.py`) exists so a weekday
morning run does not refetch every ticker just because today's US close has not
happened yet. That same grace makes a Saturday or Sunday run accept a cache
written Friday morning — whose last bar is **Thursday** — so weekend scans miss
Friday's close entirely. Confirmed against the live cache on 2026-08-09: every
cohort ticker's last bar was 2026-08-06 while 2026-08-07 was a completed
session.

The cohort stays internally consistent (the 2026-08-09 alignment fix guarantees
that), so this is staleness, not skew — scans 155 and 156 simply re-scored
Thursday's data twice. A correct rule has to be market-hours aware ("has the US
close happened yet?"), which is why it was not slipped into the alignment PR.

## As-of alignment — remaining consumers and observability

Follow-ups deliberately left out of the 2026-08-09 alignment fix to keep it
source-only and tight:

- **Other cross-sectional consumers don't align.** `dashboard/correlation.py`,
  `dashboard/badges.py` and `dashboard/validation.py` all call `fetch_prices`
  and compare tickers against each other without calling `align_cohort_asof`.
  Same defect, lower stakes (none of them feed the composite).
- **The as-of date isn't persisted.** `align_cohort_asof` reports it via
  `stats_out` and the scan logs it, but nothing writes it to `scans`. A
  `prices_asof` column (plus `asof_spread_days`) would make "which date was
  this snapshot actually scored on?" answerable after the fact, and would show
  the weekend-staleness item above directly in the health panel.

---

# Done

- **`--start` now trims a warm price cache** — `fetch_prices` returned the whole
  cached parquet frame on a cache hit, so once `data/backtest_cache/` held long
  history a narrower `--start` was silently ignored and the run covered the full
  window while reporting the narrow one. `_cache_is_fresh` only ever rejected a
  cache that was too *short*; nothing handled one that was too *long*. Now trimmed
  on load. This mattered most for the queued preset re-pick, which depends on
  multi-window checks (2008– vs 2015–) that would otherwise both have run on the
  full window. Regression test pins the cache branch with `stats_out` — an earlier
  draft "passed" against the real cache only because it was stale enough to
  refetch from the network, where `start` is honoured anyway. Closes defect (3) of
  the "Backtest artifact" item; (1) staleness is also resolved (artifact
  regenerated 2026-08-10 at 100 bps, guarded by
  `test_backtest_artifact_was_generated_at_the_configured_cost`), leaving only
  (2) run-to-run non-reproducibility. *(2026-08-12)*
- **Backtest runs are reproducible again** — two identical `backtest.py` runs
  disagreed at ~1e-7 on every metric, so "did this change the backtest?" could
  not be answered by diff. Root cause was *not* the engine: with the same prices
  in one process it is exactly deterministic, and holdings already matched across
  959/221/110 periods. The drift came from each run re-fetching prices, because
  `_cache_is_fresh` judged coverage by the cache's **first row** — so any
  instrument that simply did not exist at `--start` was ruled "too short" forever.
  At `--start 2003-01-01` that was **49 of 53** cached tickers, refetched on every
  single run. Caches now record the window they were fetched over (a `.meta.json`
  sidecar, written atomically, best-effort) and coverage is judged against that;
  caches without the sidecar keep the old behaviour and self-heal on next fetch.
  Verified end-to-end: first run fetched 18/20, second run served **20/20 from
  cache** and produced byte-identical metrics and equity curves. Closes defect (2)
  of the "Backtest artifact" item, and removes a large amount of redundant network
  I/O as a side effect. *(2026-08-12)*

- **Seven pre-existing same-idiom badges/tokens now clear 4.5:1 WCAG AA contrast**
  (2026-08-12). Found during the dark theme's own contrast work: several
  badges set their text colour to the *same* colour their background is a
  `color-mix(... N%, transparent)` tint of, and five sites
  (`dashboard/templates/css/_tables.css.j2`) plus the dark theme's own
  `--ok`/`--warn` status tokens fell below 4.5:1 in light mode.

  | Site | Was | Now (light / dark) |
  |---|---|---|
  | `.traj-badge.traj-strong_up` | `--up` 15% (4.26:1 ✗) | 10% — 4.53:1 / 6.76:1 |
  | `.setup-badge.entry` | `--up` 12% (4.43:1 ✗) | 10% — 4.53:1 / 6.76:1 |
  | `.traj-badge.traj-down` | `--down` 8% (4.28:1 ✗) | 2% — 4.64:1 / 5.88:1 |
  | `.traj-badge.traj-strong_down` | `--down` 15% (3.90:1 ✗) | 4% — 4.53:1 / 5.72:1 |
  | `.setup-badge.exit` | `--down` 12% (4.07:1 ✗) | 4% — 4.53:1 / 5.72:1 |
  | `--warn` (`_health.css.j2`, vs `--canvas`) | `#B8620C` (3.72:1 ✗) | `#9F5209` — 4.83:1 |
  | `--ok` (`_health.css.j2`, vs `--canvas`) | `#2E8B45` (3.64:1 ✗) | `#267539` — 4.83:1 |

  The queued item assumed the fix would be "choosing among values already in
  use elsewhere" (the 8/10/12/15% steps `.rank-badge.top3`'s regression fix
  established) — true for the `--up` badges (10% clears both themes), **false
  for the `--down` badges**: `--down` (terra-500) is close enough to
  `--bg-raised` that even the smallest existing step, 8%, still failed at
  4.28:1. Computing the actual per-percentage-point sweep (not assuming the
  existing steps would cover it) found 4% is the ceiling that clears 4.5:1 in
  both themes for `--down`, and `.traj-down` needed 2% to keep some visible
  distinction from `.traj-strong_down`'s 4% now that both sit well below the
  old 8%/15% pair. `--ok`/`--warn` needed new hex values entirely (not a tint
  idiom) — picked by darkening each toward its `--canvas` background until
  clearing 4.5:1, dark-mode values untouched (already 8.6:1+).

  7 new regression tests in `tests/test_color_theme.py`, following the
  existing `.rank-badge.top3`/`.traj-flat` contrast-test pattern (percentage
  read from source CSS, not copy-pasted). Sabotage-verified: reverting
  `.traj-strong_down` to 15% and `--warn` to its original hex each
  independently fail their new test with the expected ratio.

- **`build.py`'s per-asset copy step is now regression-tested** (2026-08-12).
  Static analysis, no DB/build needed: `tests/test_build_assets.py` greps
  every template for `<script src="assets/…">`, greps `build.py`'s copy
  block for every `docs_assets / "…"` destination, and asserts the first set
  is a subset of the second. Sabotage-verified against the exact bug it
  targets — removing `theme.js`'s copy block fails the test with a clear
  message; restoring it passes.

  This exists because that precise failure already happened: `theme.js` was
  referenced by both pages from the dark theme feature's first commit but
  never added to the copy block, so the whole feature 404'd silently for 3
  commits until a manual browser check caught it (`python3 dashboard/build.py`
  exits 0 either way — nothing about the build script's own success signals
  whether the *output* actually serves what the templates ask for).

- **Dark theme** (2026-08-11). System `prefers-color-scheme` default, plus a
  manual Auto/Light/Dark override persisted to `localStorage` — a scope
  expansion from the original spec, which was system-only until the human
  partner asked for a manual override mid-brainstorming. Warm dark palette
  (not a neutral/cool grey) to stay visually consistent with the existing warm
  beige light theme rather than reading as a bolted-on generic dark mode.
  Charts follow via client-side colour substitution over the baked Plotly
  JSON (`CHART_DARK` map + `SMTheme.recolor`), not a baked second payload.
  Flash-prevention is a synchronous inline script, first in `<head>`, that
  sets `data-theme` from `localStorage` before any stylesheet parses —
  verified by disagreement (OS light / stored dark and OS dark / stored
  light both render correctly from first paint on a hard reload).

  **P0 contrast fix**, measured with real WCAG relative-luminance ratios
  against each element's actual composited background, not carried-over
  spec numbers: column headers (`thead th`, light theme) **2.95:1 → 4.53:1**
  via `--fg4` (`--beige-500` → `--beige-550`). `.rank-badge.top3` regressed
  to a failing ratio partway through implementation (15%-tint idiom) and was
  fixed in-flight by dropping to an 8% tint. `.traj-badge.traj-flat` — the one
  P0 badge in this feature's scope — passed light (4.63:1) but was found
  failing dark (`--fg4` 10%-tint-of-itself against `--bg-raised`) during this
  task's own final verification pass: **4.33:1 → 4.85:1**, fixed by brightening
  dark-mode `--fg4` from `#9A9078` to `#A59A80` at the token layer (both the
  `[data-theme="dark"]` block and its `@media` Auto-fallback twin, which must
  stay value-identical per `test_dark_tokens_are_three_way_consistent`).

  **Raw-ramp cleanup beyond the spec's original hex audit**: promoted every
  hardcoded hex and raw-ramp `var(--beige-*/--green-*/--terra-*)` reference
  outside `_foundation.css.j2` to a semantic token, across `_tables.css.j2`,
  `_sentiment.css.j2`, `_chrome.css.j2`, `_guides.css.j2`, and `_charts.css.j2`
  — anything still reading a raw ramp directly is stuck in the light palette
  forever, since the ramps themselves are intentionally left undefined in the
  dark blocks (`test_raw_ramps_untouched_in_dark_blocks`). Almost all of these
  were value-for-value swaps with no visible change; two were deliberate,
  approved exceptions where the light theme's rendered colour changed on
  accessibility grounds: `.tab-note`'s caption colour (`#8C8370` →
  `var(--fg4)`) went **3.75:1 → 5.63:1** against its card background, and
  `select.sector-select:focus`'s border colour (`var(--green-400)` →
  `var(--brand-strong)`) went **2.46:1 → 5.16:1** against the select's own
  `--bg-raised` background — both were failing WCAG AA before this pass.

- **Shipping flagged unbuyable — scored, never held** (2026-08-10). One flag in
  `config/themes.yaml` (`unbuyable: true`) drives both halves, so the board and
  the published backtest describe one strategy. Shipping stays in the universe
  because it shapes the cross-sectional z-scores; dropping it measured worse on
  the default Medium preset.

  **On the board:** a quiet `⊘ Not buyable` marker on the row, the Enter prompt
  suppressed (Hold/Exit survive — the reader may hold the exposure another way,
  and only the *buy* prompt is false), and the drill-down says why instead of
  rendering an empty panel. Both render paths carry it: the baked page from the
  row flag, the signed-in rebuild from `window.UNBUYABLE`.

  **In the backtest:** `strategy.simulate(..., unbuyable=…)` removes it AFTER
  selection, so the slot goes unused rather than passing to rank N+1 —
  substituting measured worse in all three presets on both CAGR and Sharpe.
  Regenerated figures, and the preset stats in `weights.yaml` with them:

  | preset | periods holding it | CAGR | Sharpe |
  |---|---|---|---|
  | short | 57 / 958 (5.9%) | 13.9% → **14.3%** | 0.67 → 0.68 |
  | medium | 25 / 221 (11.3%) | 14.2% → **15.0%** | 0.81 → 0.83 |
  | long | 4 / 110 (3.6%) | 13.1% → **14.1%** | 0.70 → 0.73 |

  Smaller than the +1.1/+2.7/+1.1pp measured on 2021+ alone, as expected: BOAT
  has prices only from 2021-08, so 13 of the 18 backtest years are unaffected.
  Same direction in all three, which is the coherence check that matters.

  **Two things that nearly shipped wrong:**

  - `unbuyable_json` was first derived from `leaderboard_rows`. Those are
    **lagged** — gating capped this build at scan 150, a 13-theme universe,
    while the live scan has 18 — so it rendered `[]` and would have dropped the
    marker on the signed-in path, the only path that currently shows Shipping
    at all. Now sourced from config via `breakdown.unbuyable_names()`.
  - The artifact test read `holdings` as lists of sector keys; they are
    `{"date", "sectors"}` dicts, so it compared against the dict's keys and
    passed on every input, including artifacts that *did* book Shipping. It
    reported "0 periods held Shipping" both before and after the change, which
    briefly made a correct result look like a no-op. Now asserts the structure
    before asserting the content.

  **Code review found the flag was only half-applied.** A badge is not the only
  way to say "buy this": `detect_badge_events` still emitted an entry event for
  Shipping, and `build_personal_alerts` fans entry events to every enabled user
  — so the board hid the prompt while the push notification still delivered it,
  on the loudest channel there is. The badge scorecard also folded suppressed
  prompts into the Enter cohort, making its forward-return stat vouch for
  something the reader is never shown. Both now read the same flag.

  The predicate itself moved to `src/universe.py`, because the review caught the
  dashboard matching on a bare theme name while the backtest matched on the
  region-scoped `THEME|<name>` key. Latent only because the US/EU cohorts are
  retired — a same-named sector would have been unbuyable on the board and
  tradeable in the backtest. Four surfaces, one region-scoped predicate now.

  **Noticed, not fixed** — the lag means guests see 13 themes while the live
  board has 18, and the five newer themes (Shipping among them) have no
  drill-down panel for signed-in readers either, since auth.js reuses only the
  baked panels. That is the queued *Signed-in drill-down gap after a universe
  change* item; this confirms it is still live.

- **Badges are action-aware and signed-in only** (2026-08-10). Two queued items
  shipped together, because gating removes the guest case and makes
  "every badge render has holdings available" the only case rather than a
  special one.

  **Wording (Jonas's call, mid-build):** `▲ Enter / ● Hold / ▼ Exit`. Verbs, so
  they read as actions, but the strategy's own vocabulary rather than a
  broker's — `Buy/Hold/Sell` was rejected because the site is public and carries
  "Analysis tool, not investment advice". Swedish unified to `Gå in / Behåll /
  Gå ur`, which also fixed an existing split where the badge said *Ursteg* and
  its own filter chip said *Utsteg*.

  **The rule** (`Rescore.badgeForRank`): not held + buy band → Enter; held and
  anywhere above the exit rank → Hold; held + past the exit rank → Exit;
  otherwise nothing. `Hold` deliberately spans the buy band *and* the silent
  middle, so a holding drifting rank 4 → 6 keeps one label — scoping it to the
  buy band would reintroduce exactly the flicker the band rule exists to remove.
  The two rows that motivated the change: `Entry` on something already owned
  (read as a buy signal when the honest action was nothing) and `Exit` on
  something never owned.

  **Gating** keys off the same `auth_ctx["auth"]` flag as the content lag, so a
  local build with no auth keeps its badges. It had to happen where `setup` is
  computed, not at render: `setup` also reaches the reader via the row's
  `data-setup` attribute and via `data.json`'s theme rows. All three verified
  empty on the built page.

  Four things found while doing it, all fixed here:

  - **The sentiment-rescore path was still on the pre-band heuristic** —
    trajectory + change score, the rule `_compute_setup` replaced. Toggling the
    sentiment weight silently re-badged the whole board by a rule no other path
    had used since. All four paths now delegate to one pass.
  - **Rescoring never updated `data-rank`**, so the next badge pass and the
    Top 5 filter read pre-rescore ranks.
  - **Horizon switching left badges in English** for a Swedish reader; the pass
    now re-applies the language, and rewrites `data-en` because the badge span
    is reused across kinds and `applyLang()` restores English from that cache.
  - **`position-warn` (the ⚠ on a holding gone to Exit) was derived separately**
    in `positions.js` by looking for a rendered `.setup-badge.exit` — i.e. from
    whatever the previous pass had left behind. It is the same fact as the Exit
    badge now, so the badge pass owns both.

  A missing `badges_gated` context key renders as **gated**, not ungated: the
  obvious `{{ 'true' if badges_gated else 'false' }}` emits `false` for an
  undefined name, so dropping the key from build.py would have published the
  badge to guests silently. Test covers it.

  **Not touched, deliberately:** broadcast alerts (`src/alerts.py`) stay
  band-only and alert on crossings; personal alerts already joined holdings.
  The Backtest tab's badge scorecard scores the *band* because it has no
  holdings history. All three had their label renamed to `▲ Enter` only, so the
  product speaks one vocabulary.

  **Code review caught four things, all fixed in-branch:**

  - **`hidden` did not hide the filter chips.** `.filter-group { display: flex }`
    is an author rule and beats the UA stylesheet's `[hidden] { display: none }`
    at any specificity, so guests kept the Enter/Hold/Exit chips and clicking one
    emptied the board — the exact failure the code claimed to prevent. Eight
    other toggled elements in this CSS already carry an explicit
    `X[hidden] { display: none }`; `.filter-group` needed one too.
  - **Applying the action-aware rule without holdings deletes every Exit badge**
    (nothing is held, so nothing can be exited). That hit ungated builds, where
    the pass ran over Exit badges the server had just baked, *and* signed-in
    users whose holdings read failed — `loadHoldings()` fails open to an empty
    Set, which is indistinguishable from "owns nothing". Now three-way:
    `positions.js:holdingsState()` returns ready / loading / unknown, and
    `Rescore.badgeFor()` maps them to action-aware / nothing / plain band. A
    possibly-irrelevant Exit beats a hidden real one.
  - **A mid-fetch pass flashed "Enter" on held themes** — `loading` now badges
    nothing rather than guessing.
  - **The Swedish unification missed five more strings** (`alerts_intro`, the
    drill-down guide, the footer, and both alert modules), which would have
    reintroduced the Utsteg/Ursteg split this change set out to remove.

- **Rank-settings gear is a real, labelled control** (2026-08-10). Measured on
  the live page it was a **7x18px** bare `⚙` glyph at **3.67:1** contrast whose
  only accessible name was an `aria-label` — for the control that silently
  re-ranks which themes show Entry. It failed target size, labelling and
  contrast simultaneously.

  Now `⚙ Ranking`: **75x32** on desktop, **79x44** on touch pointers, **7.2:1**
  contrast, with visible text instead of an `aria-label` (an aria-label
  overrides the visible name for AT, and a control this consequential should
  say what it does on its face) and a `:focus-visible` ring. Matches the
  sibling `ⓘ How to read this tab` trigger. The glyph sits in its own non-i18n
  span so it survives a language switch; `rank_settings` added to the Swedish
  dictionary.

  Fixed while verifying, and not part of the original complaint: the popover is
  ~347px wide and anchored to the trigger, so on a 375px viewport **neither
  anchor fit** — `right: 0` put its left edge at **-179px**, and `left: 0` would
  have run off the other side. Below 600px it now anchors to the whole utility
  row and spans it, wrapping its two controls: 271x74, fully inside the
  viewport, both inputs reachable. Pre-existing, but making the trigger
  prominent without this would have sent people to a panel they could not use.

  Still open, deliberately: the popover does not close on outside click or
  Escape, and the weight `<input type=number>` re-ranks with no confirmation.
  Both are the panel's behaviour rather than its discoverability.
- **Horizon selector shows only the horizon** (2026-08-10). Each `<option>` read
  `Short — 3 held, ~49d, 22 trades/yr`, so the control's own label carried three
  facts and the dropdown was hard to scan. Options are now just
  `Short` / `Medium` / `Long`, with the selected horizon's cost rendered beside
  the control and updating live on switch.

  The numbers were worth keeping — they make the cost of the choice visible —
  but the three-way *comparison* they enabled already exists in the Backtest
  tab, which tabulates every preset and highlights the active row. The option
  label was duplicating it in the one place that cannot lay it out.

  Implementation note: numbers live in their own elements and the unit words
  carry `data-i18n`. `applyLang()` rewrites `[data-i18n]` textContent wholesale,
  so a single node holding both would have its figures blanked on every language
  switch. Verified EN→SV→EN and switching horizon while in Swedish.

  Fixed in passing: `horizon_label` and `horizon_note` carried `data-i18n` but
  had **no Swedish entry**, so they silently fell back to English. Added
  alongside the new unit strings.

- **Tab guides converted from inline `<details>` to modals** (2026-08-09).
  Each guide was a flex sibling of the filter bar inside `.utility-row`, so
  opening one inflated the row to ~700px: `align-items: center` stranded the ⚙
  mid-column, the eight filter chips collapsed into a floating block detached
  from the table they filter, and the leaderboard was pushed below the fold. At
  375px the summary was squeezed to 44×90px and rendered as a five-line vertical
  word-stack.

  All 8 guides (7 tabs + the sentiment page) are now one modal per page, with
  every guide body pre-rendered inside it and toggled by key. Bodies stay in the
  DOM rather than being cloned in on demand, so the i18n pass still finds every
  `[data-i18n-html]` node and can swap EN/SV on guides for tabs the reader has
  not opened.

  Measured before/after at 1280×800: the utility row now stays **36px whether
  the guide is open or closed** (was inflating to ~700px) and the filter chips
  do not move at all. At 375px the trigger is a single-line 143×44 button (was
  44×90 across five lines), and the panel fits the viewport with its own scroll.

  The modal machinery — focus trap, Escape, backdrop close, focus restore — was
  extracted from `_methodology.html.j2` into `_modal.js.j2` and both now share
  it, rather than carrying two copies. It is defined with `window.SMModal ||`
  so double-inclusion cannot swap the object identity under an already-bound
  modal. **The gate modal is still on its own incomplete handling** and remains
  queued; it touches auth, so it stays a separate change — but the helper it
  needs now exists.

  It also gets its **own header illustration** (`_guide_illo.html.j2`), not the
  rotation arcs the methodology and gate modals share. Those depict rotation;
  this depicts the ranked board and its bands — a descending composite profile
  with the buy band, the deliberately quiet hold band (drawn with explicit
  dashed edges, because a soft fill alone did not read as a *band*), and the
  exit tail. Zone boundaries match the shipped Medium preset (top_n 5,
  exit_rank 9), and the ▲/▼ marks are the leaderboard's own, so the picture
  speaks the product's vocabulary rather than inventing a second one. Bars rise
  from the baseline rather than arcs drawing, sharing the `--illo-soft` easing
  so the two read as one family. Full `prefers-reduced-motion` fallback.

  Verified in-browser: all 7 triggers open their own guide and only that one;
  Escape and backdrop close while a click inside the panel does not; focus
  moves to the close button and returns to the trigger; EN→SV→EN round-trips
  the content correctly; the sentiment page titles its modal "How is the
  sentiment score calculated?" rather than the generic heading; console clean.

- **Duplicate weekend/holiday scans no longer counted as observations**
  (2026-08-09). The cron runs seven days a week against a five-day market, so
  Saturday and Sunday scans replay Friday's close unchanged — correctly so,
  nothing moved. Two consumers read raw `scan_id`s and therefore counted
  replays as data:

  - **`Rank Δ` compared against `scan_ids[-2]`**, so it rendered `—` for every
    row on Saturday, Sunday **and Monday** (Monday's predecessor is Sunday,
    which still carries Friday's close). Verified against the live DB:
    153→154 (Thu→Fri) changed 12 of 20 ranks; 154→155 and 155→156 changed
    **zero**.
  - **The Trend slope averaged the last 5 `scan_id`s**, of which only **3 were
    distinct** on 2026-08-09 — diluting the slope toward flat by roughly 2.5×
    in the column the guide explicitly tells the reader to trust for exits.
    Semiconductors read `[14, 13, 13, 13, 13]` where the true sequence was
    `[14, 13, 13]`.

  New `dashboard/rows.distinct_scan_ids()` collapses *consecutive* duplicate
  scans, keeping each run's newest id so the rendered scan is always the latest.
  It fingerprints on **rank and composite together**, not composite alone —
  declaring two scans identical when they are not would skip a real
  observation, so the wider fingerprint is the safe direction. NaN maps to a
  sentinel, since `NaN != NaN` would otherwise make a scan differ from itself
  exactly when scores are missing.

  Keyed off the *data*, not the calendar, so market holidays — which produce the
  identical duplicate on a weekday — are handled by the same code without a
  holiday calendar. Past `MAX_DUPLICATE_RUN` (7) consecutive replays the delta
  falls back to showing no change: that many means the pipeline is stuck, not
  that the market was quiet, and showing a delta would present week-old data as
  a fresh move.

  Effect on the live build: 6 of 13 rows now show real movement where
  previously all 13 read `—`.

  13 new tests, **sabotage-verified** — the two that matter fail when the fix is
  reverted. They also pin the inverse: genuinely unchanged ranks must still read
  `—`, so skipping replays cannot manufacture movement.

  Chose the data-driven fix over skipping weekend cron runs: a cron rule would
  need a holiday calendar to be correct, and the daily run is also the heartbeat
  that proves the pipeline still works.

- **`acceleration` removed from scoring, replaced by `return_1m`** (2026-08-09).
  `acceleration = return_1m - return_3m`, and `return_3m` is simultaneously a
  positive Level input — so the composite carried the same return with one sign
  in Level and the opposite sign embedded in Change. Measured over 176
  month-ends it correlated **-0.31 with the composite it belonged to** and
  **-0.82 with return_3m**: the only scored signal pulling against its own score.

  Two candidate fixes, both tested across **3 presets x 2 windows at 100 bps**
  rather than in one cell:

  | variant | Sharpe deltas across the 6 cells | verdict |
  |---|---|---|
  | drop it entirely | -0.05, -0.09, -0.03, -0.05, -0.02, +0.05 | worse in 4 of 6 — rejected |
  | swap for `return_1m` | +0.06, +0.03, +0.06, +0.03, 0.00, -0.01 | improves or holds in 5 of 6 — adopted |

  The swap removes *only* the `- return_3m` term: `return_1m` already entered
  positively inside `acceleration`, so this is not a new bet on one-month
  momentum and carries no new short-term-reversal exposure.

  Regenerated figures at 100 bps (previous in brackets): short 13.9% CAGR /
  0.67 Sharpe / 22.1 trades-yr [12.2% / 0.61 / 26.9], medium 14.2% / 0.81 /
  17.6 [13.9% / 0.78 / 19.5], long 13.1% / 0.70 / 7.7 [13.2% / 0.70 / 7.2].
  Short gains 1.7pp of CAGR *and* trades five fewer times a year; long is
  roughly neutral.

  `acceleration` stays computed and stored, and now appears in the drill-down's
  "Not scored" line instead of the Change block. Methodology copy and
  ARCHITECTURE updated to match.

  Three new tests pin the pillar composition, which nothing had been guarding:
  `acceleration` must stay out of the scored lists, the two pillars must be
  disjoint and every scored name must exist in `SIGNAL_COLUMNS` (a typo there
  scores a column of zeros silently, since z-scoring fills missing with 0.0),
  and `weights.yaml`'s display-order keys must match the real lists.

  Also replaced a fragile test written the same day: it blocklisted the known
  gross CAGRs to catch a zero-cost regeneration, and produced a false positive
  within hours when this change legitimately moved Medium's net CAGR onto a
  blocklisted number. It now asserts `backtests/summary.json`'s recorded
  `cost_bps` matches the configured cost — the actual invariant.

- **README and ARCHITECTURE rewritten for the system that exists** (2026-08-09).
  Both still described the retired sector architecture: `README.md` opened *"a
  daily momentum scanner for US SPDR and STOXX Europe 600 sector ETFs, mapped to
  the 11 GICS sectors… a parallel thematic ETF track"*, four days after the
  sector cohort was retired and themes became the only cohort. `ARCHITECTURE.md`
  listed **seven modules in its index that do not exist** (`constituents.py`,
  `breadth.py`, `sector_map.py`, `rotations.py`, `sector_etfs.yaml`,
  `rotations.yaml`, `sector_map.yaml`) and still named stooq the primary price
  source. Root cause: the Phase 1 rebrand changed the README *title* and left
  the body.

  Both rewritten from the code rather than edited in place. ARCHITECTURE now
  carries the things that were undocumented and are easy to get wrong:
  the exclusive-`end` price contract and why it exists; `align_cohort_asof` and
  the per-ticker-freshness problem it solves; that the pillar lists are
  hardcoded in `scoring.py` while `weights.yaml`'s `level_signals:` keys only
  control dashboard column order; that `region` reads as legacy but is
  load-bearing as the filter keeping retired sector rows out of every read;
  that `positions` / `alert_prefs` / `v_recent_scores` are managed Supabase-side
  and not by this repo's DDL; the `SV` vs `SV_HTML` i18n rule; that alerts fire
  on band *crossings* not membership; and that the hysteresis band is stored in
  absolute ranks so it does not scale with universe size.

  Also recorded rather than quietly left: yfinance is now a single point of
  failure, `breadth_above_50dma` is a permanently-NaN leftover column, and the
  daily cron runs seven days a week against a five-day market.

  Verified mechanically, since listing non-existent modules was the original
  defect: every backticked file reference in both documents resolves to a real
  git-tracked file, and every constant quoted in the prose (18 themes, ACWI,
  `LAG_DAYS=7`, `MAX_ASOF_LAG_DAYS=4`, 50 bps, both pillar signal lists, the
  five rebalance cadences) was read back out of the code and matched.

- **Backtest costs are no longer assumed to be zero** (2026-08-09). `--cost-bps`
  defaulted to `0.0` in `backtest.py` **and** in `scripts/horizon_sweep.py` —
  the sweep that picks the presets. Free trading systematically flatters
  whichever cadence trades most, and measured on this universe the preset CAGR
  ranking **inverts at roughly 50 bps**:

  | cost | Short | Medium | Long |
  |---|---|---|---|
  | 0 bps (what picked the presets) | **17.4%** | 16.1% | 14.2% |
  | 50 bps | 14.8% | **15.0%** | 13.7% |
  | **100 bps (the rate actually paid)** | 12.2% | **13.9%** | 13.2% |

  Sharpe told the same story at every level including zero (Medium 0.88 vs
  Short 0.80), so Medium was always the right default — but the Backtest tab
  presented Short's 17.4% as the top line.

  New `costs.round_trip_bps` in `config/weights.yaml`, read via
  `src.horizons.round_trip_bps()` so the backtest, the sweep and the dashboard
  share one assumption. Both CLIs now default from it; `--cost-bps 0` still
  reproduces the old figures.

  Set to **100 bps**, from Avanza's own fee disclosure for L&G ROBO Global
  Robotics (the UCITS equivalent of BOTZ) on a 10 000 SEK position: *"Avgifter
  för köp och sälj — Avanzas avgifter: 100 SEK / 1%"*. That heading covers both
  legs, so it is already round-trip. **At that rate Short is the worst of the
  three presets, not the best** — the ranking the 0 bps sweep produced is
  exactly inverted.

  Deliberately excluded from the number: the fund's 0.8% TER and 0.03% internal
  dealing costs, both of which are annual drags rather than per-trade costs and
  would be billed per turnover if they lived in this field. Neither is modelled
  anywhere in the backtest — recorded as its own queued item.

  Recorded preset CAGRs regenerated net (0.122 / 0.139 / 0.132) and
  `backtests/summary.json` rebuilt. Tab copy now reads "net of 100 bps
  round-trip trading costs" instead of "no costs".

  Fixed in passing, both found while editing: `note_backtest_caveat` carried
  `<strong>` markup in the **`SV`** dictionary, which is applied via
  `textContent` — so switching to Swedish rendered literal `<strong>` tags on
  the page, and switching back left the English permanently unbolded. Moved it
  and `note_backtest` to `SV_HTML`/`data-i18n-html` and verified the bold
  survives an EN→SV→EN round-trip. The leaderboard guide still said "best of
  these **20**" after the 20→18 trim while the Swedish said 18; both now derive
  from the rendered row count. (`bt_themes_empty` also carries `<code>` but is
  referenced nowhere — dead key, left alone.)

  Not done here, queued instead: re-picking the presets themselves. At 50 bps
  all three current cells are off the frontier, and `W/3/6` beats the current
  `short` on return, Sharpe and churn simultaneously — but that changes what
  the dashboard tells you to hold, on one sweep over one history, and this repo
  has twice this month had a single-cell result reverse under a subperiod check.

- **stooq retired — the scanner is honestly yfinance-only now** (2026-08-09).
  `prices_stooq` had been 0 on all 20 instrumented scans; a probe confirmed why
  before touching anything, ruling out the "fix the endpoint" option the
  backlog had left open. `curl` (with or without a spoofed browser user-agent)
  gets an HTTP 200 whose body is a JavaScript proof-of-work anti-bot challenge,
  never CSV. Python `requests`' default user-agent gets a flat HTTP 404
  instead — a different rejection path, not a flakier version of the same one.
  Neither is a URL or header fix; solving a SHA-256 proof-of-work server-side
  to fetch a CSV was ruled out as circumventing anti-bot protection, not
  "fixing an endpoint" — so replacing stooq with a different fallback source
  was deliberately left as a separate, larger item (provider choice, most need
  an API key, own rate limits) rather than folded in here.

  Removed `_fetch_stooq`, `_stooq_symbol`, and the two-source loop in
  `_fetch_single` (`src/data/prices.py`); `fetch_prices` now reports
  `{cache, yfinance}` only. Left alone, deliberately: the `prices_stooq` DB
  column (`src/state.py`) and its historical values — a schema change against
  a live database for a column that just reads 0 going forward isn't worth the
  risk, matching the standing decision on `region`/`gics_sector` naming. The
  health panel (`_footer.html.j2`) and methodology copy no longer mention
  stooq, so they stop implying a redundancy that has not existed since at
  least 2026-07-21. Verified in the browser: health panel reads
  `yfinance N · cache N · failed N`; a live dry-run scan completed end-to-end
  (`Price sources: yfinance 0/20, cache 20/20`) with no stooq references left
  in the source tree outside the DB column and its tests. 580 passed
  (was 588; net of tests deleted for dead code, one added for the
  now-reachable "0 succeeded" warning path).

- **Rebrand Phase 1 — the product is now "ETF Momentum"** (2026-08-09). Display
  name only: `<h1>`, both page `<title>`s, the gate modal, the Atom feed title
  and subtitle, the alert email subject, and the `scan.py` / `stats.py` CLI
  banners, plus `README.md` and `ARCHITECTURE.md`. No URL moved and no Supabase
  config was touched, so sign-in was never at risk — that is Phase 2, still
  queued and still optional.

  The sweep turned out to be the larger half. Surviving sector-era copy was
  rewritten in **both languages**: the RRG, drill-down, momentum-shift and
  history tab guides, the nav segment ("Sectors" → "Themes"), the scan-history
  headers, the filter empty state, and the footer health label. The sentiment
  guide was the worst of it — it still described "the 11 GICS sectors", topic
  codes, and "Both US and EU rows for the same GICS sector share the same
  score", none of which has been true since the sector cohort was retired on
  2026-08-05; it now describes the per-theme keyword queries the code actually
  runs (`src/data/news_sentiment.py`). A Swedish mistranslation was fixed in
  passing: "vs peers" had been rendered *jämnåriga* (people of the same age)
  rather than *jämförbara*.

  Deliberately untouched: `sector_key`, `data-sector`, `sector_count`,
  `#sector-select` and the `region` / `gics_sector` DB columns — that is the
  separate data-layer item, which recommends leaving them alone.

- **Cohort as-of alignment — every ticker is now scored on the same last bar**
  (2026-08-09). Signals read `iloc[-1]` of whatever series they are handed
  (`src/signals/*.py`), and alignment only ever happened *within* a ticker
  (ticker vs benchmark, close vs volume) — never across the cohort. Since the
  composite z-scores each signal *across* the cohort, a theme ending on Tuesday
  could be ranked against a peer ending on Wednesday.

  **Quantified before fixing, and the premise turned out to be half wrong.**
  `prices_yfinance` is emphatically non-zero — 22–27 tickers on 13 of the 20
  instrumented scans — but `prices_stooq` is **0 on all 20**, and stooq now
  returns HTTP 404 for every symbol. So the cross-source skew (stooq inclusive
  `d2` vs yfinance exclusive `end`) has never actually fired: there is only one
  working source. What remains live is the per-ticker path — cache freshness is
  decided independently per ticker (`_cache_is_fresh`), so one refetched ticker
  beside 19 cache hits staggers the cohort, which is reachable every time the
  universe changes (it changed twice in the first week of August).

  Shipped: `align_cohort_asof()` in `src/data/prices.py`, called by `scan.py`
  between fetch and scoring. As-of = the newest date every kept ticker has a
  bar for; a ticker lagging the cohort's modal last date by more than 4 calendar
  days is dropped instead of dragging everyone back to its date (the existing
  80% coverage guard catches it if that becomes widespread).
  `tests/test_price_asof_alignment.py` pins the invariant end-to-end: whatever
  `fetch_prices` returns, the frames handed to the signal builder all end on
  the same date.

  The source mismatch was also settled, in the *safe* direction: `end` is now
  documented as EXCLUSIVE and `_fetch_stooq` converts (`d2 = end - 1`), rather
  than making yfinance inclusive. Code review caught why that matters — an
  inclusive `end` pulls in Yahoo's partial candle for an in-progress session on
  any run during market hours (manual `workflow_dispatch`, local dev), and
  `_cache_is_fresh` only checks the *date*, so that half-formed close would be
  cached and never refetched. Excluding the current session is the property
  worth keeping; the first draft of this fix threw it away.

  Left queued rather than folded in: the dead stooq source, the weekend cache
  staleness the grace window causes, and persisting the as-of date to `scans`.

- **Diversifier audit — the diversification thesis mostly failed; universe
  trimmed 20 → 18** — the open thread from the 2026-08-08 audit. The seven names
  added on 2026-08-05 were selected on correlation alone, which is blind to
  whether a name is simply worse. Checked properly this time: drop-one on the
  **backtest**, at matched window and matched band.

  **First attempt was invalid and is worth recording.** Drop-one at a single
  buffer flipped sign for 6 of 9 themes between buffer 4 and 5 — Gold Miners read
  −2.4% CAGR ("essential") at one and +1.4% ("dead weight") at the other. The
  measurement was dominated by the band interaction, not by the theme. Repeating
  it across five buffer settings and counting how often removal helps is what
  made it stable.

  **The finding.** Correlation did fall as designed (0.52 → 0.375) but bought
  almost no drawdown protection, because in the crashes that matter everything
  falls together. Of the seven:

  | name | verdict |
  |---|---|
  | Energy Producers (IXC) | **removed** — hurts at 5/5 buffers; drawdown 5.8pp better without |
  | Medical Devices (IHI) | **removed** — hurts at 5/5; drawdown 3.5pp better without |
  | Insurance, Healthcare Providers | kept — earn their place on **return**, not diversification |
  | Gold & Precious Metals Miners | kept — the only genuine risk diversifier, at ~1.4pp CAGR for ~1.1pp drawdown |
  | Shipping, Food & Beverage | kept — ambiguous; not trimmed further on thin evidence |

  Presets re-picked on the 18-theme universe (short W/3/5, medium M/5/4, long
  2M/4/6) rather than band-scaled mechanically. Medium lands at band 50% again.

  **Honest scorecard — this is not a clean win on every preset:**

  | preset | before (20) | after (18) |
  |---|---|---|
  | Short | 18.1%, 0.84, −39.3%, 34/yr | 17.4%, 0.80, **−44.3%**, 27/yr |
  | **Medium (default)** | 15.5%, 0.84, −38.3%, 21/yr | **16.1%, 0.88, −35.4%, 20/yr** |
  | Long | 15.0%, 0.78, −35.8%, 8/yr | 14.2%, 0.74, **−31.4%**, 7/yr |

  Medium improves on everything. Long trades less and drops less but earns less.
  Short is genuinely worse on drawdown. The default is what most matters, and the
  two removed names were harmful at every setting, so the change stands — but it
  is a trade, not a free win.

  `test_methodology_keeps_its_factual_anchors` caught the docs still claiming
  "20 themes" the moment the config changed, which is exactly what it was
  written for.

- **Methodology and tab guides rewritten for a novice reader** — the docs still
  described US/EU sectors, a monthly-only rotation, and the *old* trajectory-based
  Entry/Exit rule, all of which the 2.0 and horizon work replaced. Worse than
  stale: the badge section actively described behaviour the code no longer has.

  The methodology modal is now built to teach rather than specify — it opens with
  a one-sentence summary, defines "theme" and "ETF" before using them, explains
  momentum as a tendency rather than a law, explains z-scores in plain terms
  ("0 = average, +1 = better than about 5 of 6"), and devotes a section each to
  the horizon presets and the hold band, since those are the settings that decide
  what the dashboard tells the reader to do.

  Two things it now states that it previously did not: that Entry/Exit describe
  *position, not health* (a collapsing theme ranked first still reads Entry), and
  the three ways the backtest flatters the strategy — hindsight universe,
  settings fitted to the same history, and an assumed perfect investor.

  Rewritten: the methodology modal, the Leaderboard guide, and the Correlation
  guide (which still referenced "25 sector ETFs", the US/EU dividers and the EU
  financial sub-sectors). **Added: a Backtest guide**, which did not exist — the
  tab with the most numbers and the most caveats had no explanation at all.
  Swedish translations updated to match; the two languages were describing
  different strategies.

  Also fixed a readability regression the rewrite caused: `.methodology-body p`
  and `.tab-guide-body p` had zero margin, which was fine when sections were a
  single paragraph and produced a wall of text once they were not.

  `tests/test_methodology.py` now pins *topics and factual anchors* rather than
  exact headings, so prose can be rewritten without breaking it, and asserts the
  stated universe size matches `config/themes.yaml`. Assertions run on
  tag-stripped, whitespace-collapsed text so they survive re-wrapping.

- **Long preset retuned to 2M / top_n 4 / buffer 7** — the only preset the
  widened sweep grid showed a strictly better cell for. Better on every axis:

  | | before (2M/5/5) | after (2M/4/7) |
  |---|---|---|
  | CAGR | 14.2% | **15.0%** |
  | Sharpe | 0.76 | **0.78** |
  | max drawdown | −36.5% | **−35.8%** |
  | trades/year | 12.5 | **7.8** |
  | median hold | 181 d | 183 d |

  Short and Medium were deliberately left alone — their alternatives were inside
  the noise of a single history, and Medium's "better" cell was worse
  risk-adjusted (Sharpe 0.76 vs 0.84).

  **Side effect worth having: the presets now tag rows distinctly.** Medium and
  Long previously shared a band (both top_n=5, buffer=5), so switching between
  them changed the backtest curve but not a single Entry/Exit badge — flagged as
  a known wrinkle when presets shipped. Long holding 4 names with a wider band
  separates them: entry ≤3/≤5/≤4 and exit >8/>10/>11 across short/medium/long.
  `test_medium_and_long_share_a_band` was written to fail loudly if this ever
  changed; it did, and is replaced by `test_every_preset_has_a_distinct_band`.

- **Horizon sweep grid widened to bracket the shipped presets** — the sweep
  explored `BUFFERS = [0,1,2,3]` while the presets, retuned by the 2026-08-08
  audit, use **buffer 5**. Re-running it would have printed a return/churn
  frontier that excluded the configuration actually in use, and pointed at cells
  the audit had already superseded. Grid now runs 0-8, and the table carries a
  `band` column — `(top_n + buffer) / universe_size` — because that fraction, not
  the raw buffer, is the number that transfers when the universe changes size.

  With the full grid, none of the three shipped presets is on the frontier.
  **Deliberately not retuned again**, because the margins are inside the noise of
  a single market history and the presets were already retuned once this week:

  | preset | shipped | best alternative | note |
  |---|---|---|---|
  | Short | W/3/5 — 18.1%, Sharpe 0.84, 33.6/yr | W/3/6 — 18.2%, 0.84, 28.3/yr | real but small |
  | Medium | M/5/5 — 15.5%, 0.84, 21.3/yr | 2W/3/7 — 15.8%, 0.76, 17.7/yr | worse Sharpe — not a win |
  | Long | 2M/5/5 — 14.2%, 0.76, 12.5/yr | 2M/4/7 — 15.0%, 0.78, 7.8/yr | better on all three |

  Only Long has a clearly better cell. Chasing the frontier of the same single
  history is how the presets get overfit to 2008-2026; prefer the broad plateau,
  which is what buffer 5-6 at band ~50% already is.

- **Niche diversifiers (SIL, OIH) tested and rejected — low correlation is not
  useful diversification** — a negative result, recorded so it is not retried.

  The question was whether the universe could get its diversification from more
  *niche* themes rather than from the sector-ish ETFs added on 2026-08-05
  (Insurance, Healthcare Providers, Food & Beverage et al., which are industry
  slices wearing theme labels). Screening 20 niche thematic candidates found
  only two that are both genuinely niche and genuinely low-correlation: Silver
  Miners (SIL, rho 0.38) and Oil & Gas Services (OIH, 0.43). Most niche themes
  sit at 0.50-0.70 against the growth block, because they *are* the growth
  block — Cloud 0.64, FinTech 0.65, Autonomous & EV 0.70.

  Adding both was tested end to end, with the buffer bumped 5 -> 6 to hold the
  band at ~50% of the widened 22-name universe. **It made the strategy worse at
  every buffer level**, on the same track window:

  | universe | best cell | CAGR | Sharpe | maxDD |
  |---|---|---:|---:|---:|
  | 20 themes | buffer 5 | **15.5%** | **0.84** | **-38.3%** |
  | 22 themes | buffer 6 | 13.7% | 0.72 | -42.1% |

  Meanwhile the diversification gain was negligible: mean rho 0.376 -> 0.373,
  effective bets 9.4 -> 9.6.

  **The lesson: a low-correlation name is only a diversifier if it is not
  reliably worse.** SIL and OIH are volatile and, over this history, poor
  performers that momentum periodically selects and is punished for. They lower
  pairwise correlation while diluting return — the correlation screen used to
  build the universe on 2026-08-05 cannot see that, because it measures
  co-movement and says nothing about level.

  Any future universe change should be judged on the **backtest**, not on
  correlation alone; and universe and buffer must be varied **one at a time**,
  since the band is measured in absolute ranks and moves with universe size.

- **Theme redundancy audit — and the buffer bug it uncovered** — the audit's own
  premise turned out to be wrong, and the real finding was elsewhere.

  **What the audit disproved.** Clustering (correlation distance, average
  linkage) says the structure is not "four AI names" but **12 of 20 themes in
  one growth block** at within-rho 0.53. Drop-one analysis confirms the growth
  names are the ones adding correlation and the seven 2026-08-05 diversifiers
  are the ones removing it. Effective number of bets: **9.4 of 20**, one factor
  explaining 43% of variance.

  But the feared failure mode does not occur. Over 221 rebalances the strategy
  held **2.43 of 5 from the growth block on average against 3.00 expected at
  random** — less concentrated than chance, with all-five-from-growth in only
  3.2% of rebalances. A cluster cap was tested at 4/3/2 and does essentially
  nothing: **max drawdown is identical (-42.8%) at every cap**, which is the one
  thing it would be added for. Pruning the most concentrating themes also lost
  return. **Neither pruning nor capping is worth doing.**

  **What it actually found: the hysteresis band is measured in absolute ranks,
  so expanding the universe silently tightened it.** 13 themes at exit_rank 8 is
  62% of the universe; 20 themes at exit_rank 8 is 40%. The 2026-08-05 expansion
  therefore tripled churn without anyone intending it — on a common window
  (2010-08 onward, both universes alive) the 20-theme universe traded 39.5
  times/yr against the 13-theme universe's 13.8.

  Retuning `buffer` 3 → 5 (band ≈ 50% of universe) improves **every preset on
  every axis** — return, Sharpe, drawdown, churn and holding period:

  | preset | before | after |
  |---|---|---|
  | Short | 15.2%, 0.71, -45.4%, 66/yr, 21d | 18.1%, 0.84, -39.3%, 34/yr, 42d |
  | Medium | 13.7%, 0.77, -42.8%, 35/yr, 62d | 15.5%, 0.84, -38.3%, 21/yr, 91d |
  | Long | 13.3%, 0.71, -39.0%, 20/yr, 119d | 14.2%, 0.76, -36.5%, 12/yr, 181d |

  One buffer across all three presets rather than three separately fitted
  numbers, which is less overfit and states the actual rule: hold band ≈ half
  the universe. **The band does not scale itself — revisit it if the universe
  size changes again**, noted in `config/weights.yaml`.

  **A comparison trap worth recording.** The 13-theme universe first appeared to
  beat the 20 on every axis (17.4% vs 13.7% CAGR, -30.1% vs -42.8% drawdown).
  That was invalid: its track starts 2010-07-30 versus 2008-03-31, because most
  of the original 13 ETFs did not exist in 2008 — it simply missed the GFC. On a
  common window the drawdown ordering **reverses** (-25.0% for the 20 vs -30.1%
  for the 13). Always check track windows before comparing universes.

- **Horizon presets live — Short / Medium / Long** — phase 2, completing the
  rebalance-horizon work. `config/weights.yaml` gains a `horizons:` block read
  through the new `src/horizons.py`, so the backtest, the server-rendered badges
  and the client-side re-derivation share one definition instead of drifting.

  **The Entry/Exit badge is now a position band, not a momentum reading.** Entry
  at `rank <= top_n`, Exit at `rank > top_n + buffer`, silence in between —
  the same rule `strategy.simulate` validates, so the dashboard and the backtest
  finally describe one strategy. The old rule keyed off trajectory + change
  score and recomputed every scan, which is why badges churned daily no matter
  what cadence was validated. The Trend column keeps the trajectory reading.

  Switching preset re-derives badges **client-side** via the existing
  `rescore.js` path (top_n/buffer are pure rank comparisons against `data-rank`),
  persisted in `localStorage`. Cadence can't be re-simulated in the browser, so
  it selects one of three precomputed curves — `backtest.py` now replays all
  three presets into `backtests/summary.json`, keyed by preset.

  **Two real bugs found while building it.** `_compute_setup`'s callers in
  `src/alerts.py` and `dashboard/badges.py` build row dicts without a `rank`
  key, so the band rule would have returned None forever and **alerts would have
  silently stopped**. And `detect_badge_events` reported band *membership*, not
  transitions — under the band rule every held name reads "entry" on every scan,
  so it would have emailed the same positions daily, the exact churn this work
  exists to remove. Alerts now fire only on band crossings.

  Alerts stay on the default preset; per-user horizon would need a prefs column
  and per-user crossing evaluation (see the queued follow-up).

  Copy states plainly that these are precomputed operating points from one sweep
  over one market history, and every preset shows its trades/year beside its
  return so it can't be picked on CAGR alone.

- **Rebalance cadence + hysteresis buffer in the backtest engine** — phase 1 of
  the horizon work. `replay.rebalance_dates(index, freq)` generalises the
  month-end-only calendar to W/2W/M/2M/Q; `strategy.simulate(..., buffer=N)`
  replaces the hard `ranked[:top_n]` slice with a hysteresis band (hold while
  `rank <= top_n + buffer`, fill free slots from the best unheld). New
  `scripts/horizon_sweep.py` sweeps cadence x top_n x buffer, scoring **once per
  cadence** and reusing it across the grid (top_n/buffer don't affect scores, so
  per-cell scoring would be ~12x slower for identical numbers).

  Churn is now reported in human terms — `trades_per_year`, `median_holding_days`
  — alongside `avg_turnover`. Positions still open at the end are treated as
  censored and excluded from the median, which would otherwise be biased toward
  the sample length.

  **Found and fixed a real bug while sweeping:** `metrics.cagr`, `sharpe` and
  `annualized_vol` hardcode `periods_per_year=12`. That was correct while
  month-end was the only calendar, but the moment cadence became a parameter it
  annualised a quarterly track as monthly — the first sweep reported 32-40% CAGR
  for quarterly cells against 11-13% for monthly, and would have recommended
  quarterly on a threefold arithmetic error. New `metrics.periods_per_year()`
  measures cadence from the actual calendar; engine and sweep both pass it.

  Regression gate: old vs new code, same day, same cache — **identical holdings
  across all 221 rebalance dates**, worst metric delta 5e-6 (the known
  price-fetch noise floor). Defaults (`M`, `buffer=0`) are inert.

  **Headline result: the current live default is dominated.** `M, top_n=3,
  buffer=0` gives 11.5% CAGR at 46.5 trades/yr; `2M, top_n=5, buffer=3` gives
  13.3% at 19.7 trades/yr — better return *and* 58% fewer trades. Holds at 10 bps
  costs, where the low-churn cells drag 0.2-0.3pp against 0.8-1.2pp for weekly.

- **Leaderboard, backtest and validation ungrouped for a single cohort** — with
  themes the only cohort, every per-cohort grouping affordance was a control that
  could not do anything. Removed the leaderboard's Region column (9 columns now,
  sort indices renumbered) and its cohort header rows, the cohort filter-chip
  group, the Region columns on the validation and sentiment tables, and the
  Track column on the backtest metrics table. The validation "All" aggregate is
  now skipped at one cohort, where it repeated that cohort's own rows verbatim
  under a second label. `grouped_rows` is gone from the build context; the
  template renders the flat `leaderboard_rows`.

  **Fixed a real bug found while doing it: the backtest equity curve had not
  rendered at all since the sector cohort was retired.** `renderBacktest()`
  iterated a hardcoded `['US', 'EU']` looking up `backtest-chart-US`/`-EU`
  container ids, while `activeCohort` was `THEME` — so it matched nothing and
  silently drew no chart. The PR-C check confirmed `BACKTEST_DATA` had the THEME
  key but never confirmed a container existed to draw into. Now one
  `#backtest-chart` container renders the active cohort's track.

  Also removed the two legacy `[{region:"US"},{region:"EU"}]` fallback lists in
  `auth.js` and `scan-history.js`. Those were live hazards rather than dead
  code: both files read sources with no region filter (`v_recent_scores`, and
  historical scans), so if `window.COHORTS` were ever missing the fallback would
  have replayed retired sector rows onto the leaderboard for signed-in users.
  They now render nothing rather than guessing. `auth.js` also drops the region
  cell so its signed-in rows match the static build's column set.

  Rotation event-study plumbing deleted (`_build_rotation_figures`, the
  `rotation_json`/`has_rotations` context, `ROTATION_DATA`, the renderer, and
  `write_results(rotations=...)`) — `config/rotations.yaml` went with the sector
  cohort, so it had been permanently empty. Plus dead `.tag-region` /
  `.region-header-row` CSS and five orphaned i18n keys; `cohort_label` is kept
  because the cohort selector markup is deliberately retained for a future
  second cohort.

  `rescore.js` keeps its per-region ranking pools: that grouping is the
  cross-sectional invariant (ranks are computed *within* a cohort, never
  across), it derives its groups from the data rather than a hardcoded list, and
  it is not presentation.

- **Sector cohort retired — themes are the only cohort** — completes Sector
  Momentum 2.0. `scan.py` now runs the theme pipeline as its primary (and
  fatal-on-failure) track rather than bolting `_run_themes_track` on as a
  non-fatal extra; FinBERT sentiment moved to the theme GDELT path.
  `src/cohorts.py` yields one cohort, and `src.state.SECTOR_REGIONS` became
  `DEFAULT_REGIONS = ("THEME",)`.

  **The historical US/EU rows were deliberately kept** (41 scans, 451 US + 502 EU
  in `scores`). Retiring the cohort removed the *writer*, not the rows, which is
  why the readers keep an explicit region filter rather than selecting
  everything — an unfiltered read would resurrect dead sectors into the
  leaderboard, charts and movers. The five starred sector positions are left in
  place, inert.

  Deleted: `src/data/constituents.py`, `src/signals/breadth.py`,
  `src/backtest/rotations.py`, `src/sector_map.py`, `run_track`/`run_all`/
  `_track_instruments`, `replay.score_as_of`, `pipeline.build_signals_rows`,
  `report.build_swedish_overlay`, `news_sentiment.fetch_news_headlines`/
  `apply_polarity_to_keys`/`build_news_signal_rows`, `config/sector_etfs.yaml`,
  `config/rotations.yaml`, `config/sector_map.yaml`, `config/swedish_tickers.csv`,
  and the three `scripts/*_research.py` sector sweeps. `config/universe.yaml`
  keeps only `price_lookback_days`.

  The **sentiment page was ported** rather than deleted — theme FinBERT scores
  have been computed and stored since July but never displayed there.

  Deviation from the spec, deliberate: `breadth_above_50dma` stays in
  `SIGNAL_COLUMNS` as a permanently-NaN column instead of being removed. Dropping
  it would change `SIGNAL_COLUMNS`, `weights.yaml` and the comparability of
  stored history; themes have always carried it as NaN, so keeping it is the
  zero-change option. Only its sector-only *producer* was deleted.

  Verified: no `US|`/`EU|` key appears anywhere in `docs/`; leaderboard renders
  the theme cohort only; correlation heatmap is 20×20 (was 45×45); page weight
  1129 KB → 456 KB; historical sector rows confirmed still present in the DB.
  Spec: `sector_momentum-notes/specs/2026-08-05-retire-sector-cohort-design.md`
  (PR C of 3).

- **Theme backtest wired into the shared results contract** — `run_theme_track`
  had been computing a full theme track on every backtest run since it was built,
  writing it to `backtests_themes/summary.json` under a *different shape*
  (`{"track": …}` rather than `{"tracks": {region: …}}`), where nothing read it —
  and `backtests_themes/` was never even committed, so it never reached the
  deployed site. The track already returns byte-identical structure to
  `run_track` (region, benchmark, top_n, start, end, metrics, equity_curve,
  holdings), so the fix was to put it in the same dict: `tracks["THEME"] =
  run_theme_track(...)` at the call site in `backtest.py`. `_build_backtest_context`
  keys figures by region and the template already reads `BACKTEST_DATA[rg]`, so no
  dashboard change was needed. Deleted `write_theme_results`. Merging at the call
  site rather than inside `run_all` keeps the sector-universe function ignorant of
  themes, so after the sector retirement it collapses to `{"THEME": …}` unchanged.
  Per-track `top_n` (3 for themes vs 5 for sectors) survives because each track
  carries its own. Spec:
  `sector_momentum-notes/specs/2026-08-05-retire-sector-cohort-design.md` (PR B
  of 3). *The committed `backtests/` artifact was deliberately not regenerated —
  see the queued item above.*

- **Theme universe expanded to 20 — correlation diversifiers** — the 13-theme
  cohort averaged 0.52 pairwise correlation, three times the 0.17 of the 25
  sectors it is due to replace, with no defensive member and four expressions of
  the same AI trade at 0.72–0.88. Since momentum rotation is relative, "top 3 of
  13" was selecting between correlated versions of one factor. Screened 28 liquid
  US-listed thematic/industry ETFs by their effect on cohort mean correlation and
  added the seven best: Insurance (IAK), Healthcare Providers (IHF), Energy
  Producers (IXC), Gold & Precious Metals Miners (GDX), Shipping (BOAT), Food &
  Beverage (PBJ), Medical Devices (IHI). Cohort mean falls 0.519 → 0.375.
  Config-only — `src/cohorts.py` already derives the cohort from YAML. Dropped
  GLD (commodity, redundant with GDX) and FCG (0.91 with energy) from the greedy
  output; used IXC rather than XLE because XLE is already the US Energy sector
  instrument and a cross-cohort duplicate ticker makes the correlation heatmap
  fail silently. New `tests/test_themes_config.py` guards that and four other
  config invariants against the shipped file. Spec:
  `sector_momentum-notes/specs/2026-08-05-retire-sector-cohort-design.md` (PR A
  of 3). *Note: the 7 new themes only appear on the dashboard after the next
  scan scores them, and every theme's z-scored composite steps discontinuously
  at that scan because the cohort grew from 13 to 20.*

- **Cohort unification PR 5 — one page for every cohort** — `themes.html.j2` is
  deleted and the main page renders all three cohorts (US 11, EU 14, THEME 13).
  Themes inherit the twelve features that were sector-only: filter chips (plus a
  new cohort filter), sortable columns, the sentiment column (theme sentiment has
  been computed and stored since July but never displayed), scan-history
  browsing, the digest banner, per-scan reports, the validation panel,
  correlation and badge scorecard. The correlation heatmap now spans all three
  cohorts in one combined 38×38 matrix (unlike RRG, correlation has no
  per-cohort benchmark to conflict), guarded against a ticker configured in two
  cohorts — none exist today, but nothing previously enforced that. The other
  chart tabs (RRG, Drill-down, Movers, History, Backtest) render one cohort at a
  time via a selector, because `rs_ratio` is measured against each cohort's own
  benchmark and combining them would put ACWI-relative and RSP-relative values
  on one axis. Also fixed two latent bugs where a hardcoded `["US","EU"]` region
  pair with a silent fallback would have misfiled THEME rows into the US group:
  `index.html.j2`'s `sortTable()`/`sortVisibleByRank()`, and
  `scan-history.js`'s past-scan browser. Sector rendering (leaderboard rows,
  per-scan reports, the Atom feed) verified byte-identical to pre-merge output.
  Completes the cohort unification (PRs 1–5). Spec:
  `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-01-cohort-unification-design.md`.
  *(2026-08-04)*

- **Cohort unification PR 3 — legacy theme schema retired** — `save_theme_scan`
  no longer dual-writes; the `theme_scores`/`theme_signals`/
  `theme_sentiment_signals` tables are gone from `_DDL_STATEMENTS`,
  `_SCAN_CHILD_TABLES` and `src/backup.py`, and `scripts/theme_tables_drop.sql`
  removes them from the database (post-merge step). The shared
  `scores`/`signals`/`sentiment_signals` tables are now the single source of
  truth for both cohorts. Backups predating the drop stay restorable because
  `read_backup` only iterates the (now four-table) `_COLUMNS` map, so legacy
  theme_*.csv members in an old archive are ignored before `load_tables` ever
  sees them; `load_tables`'s `_table_exists` skip is defence-in-depth, not the
  mechanism doing the work. Spec:
  `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-01-cohort-unification-design.md`.
  *(2026-08-03)*

- **Cohort unification PR 4 — cohort-generic seams** — added `src/cohorts.py`,
  turning `config/universe.yaml` (and optionally `config/themes.yaml`) into a
  list of `Cohort` records (region, label, benchmark, instrument map). The
  hardcoded `("US", "EU")` loops in `dashboard/validation.py`,
  `dashboard/badges.py`, `dashboard/correlation.py` and `src/report.py` now
  iterate that list, and the correlation heatmap's single hardcoded US/EU
  divider generalised to one line per cohort boundary. Pure refactor — every
  consumer still receives sector cohorts only, verified by a byte-identical
  dashboard build. `src/data/news_sentiment.py` deliberately stays sector-only:
  themes have an independent GDELT/FinBERT path, and routing them through both
  would double-write theme sentiment. Spec:
  `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-01-cohort-unification-design.md`.
  *(2026-08-03)*

- **Cohort unification PR 2 — readers switched to the shared tables** — the
  theme readers in `src/state.py` now read `scores`/`signals` filtered to
  `region='THEME'` instead of the legacy `theme_*` tables, with output shape
  unchanged (`get_theme_signals_for_latest_scan` aliases `gics_sector AS theme`,
  which `dashboard/rows.py` filters on). Verified by diffing each reader's
  output against a pre-change production baseline and by a byte-identical
  dashboard build. Also deleted `get_theme_scores_for_latest_scan`, which had
  zero callers. The `theme_*` tables are still written — the dual-write and the
  tables themselves go in PR 3. Spec:
  `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-01-cohort-unification-design.md`.
  *(2026-08-02)*

- **CI Postgres test database** — `.github/workflows/test.yml` now runs a
  `postgres:17` service container (matching production's 17.6), so the 14
  DB-backed tests gated on `TEST_DATABASE_URL` actually execute instead of
  skipping. They had **never run anywhere** — not locally, not in CI — which
  left the migration-idempotency, same-day-replacement and region-scoping
  guards decorative. Non-obvious detail, documented in the workflow: the safety
  guard (`_same_database`) fails safe on an *empty* `DATABASE_URL`, so setting
  only `TEST_DATABASE_URL` silently skips everything; both vars point at the
  throwaway container at different database names. Running them immediately
  found two stale tests that saved several scans with `datetime.now()` and were
  silently collapsed into one by same-day replacement. *(2026-08-02)*

- **Cohort unification PR 1 — shared-table groundwork** — sector readers are now
  region-scoped (`SECTOR_REGIONS`, `regions=` parameter on six readers in
  `src/state.py`), and `save_theme_scan` dual-writes theme rows into
  `scores`/`signals`/`sentiment_signals` with `region='THEME'` alongside the
  legacy `theme_*` tables. Adds `scripts/theme_cohort_migration.sql` to backfill
  the 22 historical theme scans. No user-visible change; readers still use
  `theme_*` until PR 2. Groundwork for retiring the redundant theme schema and
  giving themes the twelve sector-only dashboard features. Spec:
  `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-01-cohort-unification-design.md`.
  *(2026-08-01)*

- **Validation panel provisional mode** — the "Do the rankings predict returns?"
  panel no longer colours hit-rate/mean/median cells green or red when the scan
  history is too short to support a conclusion. Gated on calendar span (not scan
  count) via a new `CONCLUSIVE_SPAN_DAYS = 365` threshold in
  `dashboard/validation.py`; below it, coloring is suppressed and a caveat states
  the actual span, the first-scan date, and that forward-return windows overlap
  too heavily to be independent (observed 2026-08-01: 38 scans spanning only 37
  days, yielding t-statistics that looked significant but weren't). The sibling
  holding-period panel is unaffected. *(2026-08-01)*

- **Personalized alerts (position tracking phase 2)** — each signed-in user gets
  their own ntfy topic (`public.alert_prefs`, RLS owner-scoped, unique topic) and
  receives Exit signals only for sectors/themes they hold, plus Entry signals
  across the whole universe. New pure `src/personal_alerts.py` composes per-user
  payloads (region and item_type discriminate, so a US holding never matches the
  same-named EU sector); `src/alerts.py` fans them out with the existing
  `post_ntfy` — fail-open and isolated per user, so one bad topic can't block the
  rest, and topics are never logged. The shared `NTFY_TOPIC` broadcast is
  unchanged and still carries CI failure alerts; personal alerts fire even when it
  is unset. Topic is generated client-side (`crypto.getRandomValues`) and managed
  from an Alerts panel in the footer (enable, copy, pause, regenerate) with an
  explicit "anyone with this topic can read your alerts" warning. New
  `scripts/alert_prefs_migration.sql` (manual post-merge run; the scan degrades to
  broadcast-only until it is applied). *(2026-07-31)*

- **Leaderboard filtering** — chip bar above the Sectors leaderboard filtering by
  setup (Entry/Exit), trend (Rising/Flat/Falling), and thresholds (Composite > 0,
  Top 5, Positive change). OR within the setup and trend facets, independent ANDs
  for the thresholds, AND across groups. Pure client-side visibility toggling over
  five new row `data-*` attributes; composes with the existing column sort, hides
  empty region headers, collapses filtered-out breakdown rows, and shows a
  "Showing X of N" count. Works signed in too — `renderLatestRows` emits the same
  attributes, with setup/trend taken from the client-side meta `rescore.js`
  already computes; filters are re-applied after the live upgrade. Hidden only in
  the past-scan view, whose rebuilt rows lack the attributes (restored, with
  prior filter state re-applied, on "Back to latest"). No persistence (resets on
  reload). *(2026-07-31)*

- **Risk-adjusted momentum (signal research)** — added three info-only signals
  (`rar_3m`, `rar_6m` = return / annualized realized vol over the matching window;
  `calmar_6m` = return_6m / |max_dd_1y|) plus `compute_realized_vol`, and optional
  `level_signals`/`change_signals` overrides threaded through
  `score_all`/`score_as_of`/`run_track` so signal sets can be A/B'd in the backtest
  without touching globals. New `scripts/riskadj_research.py` compares baseline
  against additive and substitutive variants per region, with per-era consistency
  and a correlation redundancy check.
  **Outcome: PARK** — no variant beat baseline in either region (US 10.94% CAGR /
  0.82 Sharpe vs best variant 10.88 / 0.82 with a deeper drawdown; EU 8.51 / 0.62
  vs best variant 8.43 / 0.61), and the scattered wins were confined to a single
  era. Mechanism is clear: each risk-adjusted signal is ~0.96 correlated with the
  raw return it is built from, so cross-sectionally it barely reorders sectors —
  which is also why the *additive* variant was the worst in both regions. Live
  scoring and ranking unchanged (new signals are not in `_LEVEL_SIGNALS`/
  `_CHANGE_SIGNALS`; `config/weights.yaml` untouched). Full findings in the notes
  repo (`research/2026-07-26-risk-adjusted-momentum.md`). *(2026-07-26)*

- **Walk-forward weight validation (research)** — validated the assumed 0.50/0.50
  level/change split. New pure `src/backtest/walkforward.py` (trailing-window scheme
  selection + stitching, with no-look-ahead guard tests confirmed to fail against
  both a wholly-forward window and a one-month boundary shift before being
  confirmed against the correct trailing-window slice) and
  `scripts/walkforward_weights.py`, which grades 11 fixed splits plus the regime
  spike's V3 per region, then builds an out-of-sample walk-forward track by
  re-selecting on trailing Sharpe (36/60/120-month windows, 12-month cadence).
  **Outcome: PARK — walk-forward selection never beats fixed 0.50/0.50 in both
  regions (US Sharpe 0.82 incumbent vs 0.71/0.80/0.83 at 36/60/120m; EU 0.62
  incumbent vs 0.57/0.54/0.61, worse at every window), and the grid is a broad
  plateau once level weight ≥~0.3-0.4 with 50/50 already inside it.** Live
  scoring untouched (`config/weights.yaml` still 50/50). Full findings in the notes
  repo (`research/2026-07-23-walk-forward-weight-validation.md`). *(2026-07-26)*

- **Sentiment chart empty-state** — when the rendered scan has no news
  sentiment for any sector (e.g. a lagged scan predating the FinBERT pivot, or
  a GDELT outage), the Data ⇄ Sentiment scatter used to render as a confusing
  flat line of hollow points at 0. It now shows an explicit "No news sentiment
  for this snapshot" panel (EN/SV) instead. New `sentiment_available` flag on
  the sentiment page context (`dashboard/sentiment.py`, true when the latest
  scan has ≥1 real sentiment value); template branches on it. No pipeline
  change — sentiment itself works; this is display-only.
- **Position tracking (phase 1)** — signed-in users toggle a per-row star to
  flag sectors/themes they hold (boolean; presence of a `public.positions` row).
  Held rows highlighted; held + Exit-flagged rows get a ⚠ warn cue. Browser-side
  under RLS (`positions_owner`), fail-open. New `scripts/positions_migration.sql`
  (manual post-merge run). Also scoped `auth.js`'s live upgrade to
  `#leaderboard-table` (was clobbering the themes table on the themes page).
- **Signed-in row expansion fix** — the client-side live leaderboard upgrade
  (`auth.js` `renderLatestRows`) wiped the tbody, which discarded the static
  drill-down panels and rebuilt rows without `data-sector-id`, so signed-in
  users could no longer expand rows. Now the rebuild snapshots the
  `.breakdown-row` panels, sets `data-sector-id` on each rebuilt row
  (`region-gics` with spaces → underscores), and re-appends each panel under its
  row. Drill-down content still reflects the build's scan (a minor known
  staleness vs the live composite).
- **docs/data.json export** — the build now emits `docs/data.json` alongside the
  HTML: latest public scan with per-sector and per-theme raw scores, rank,
  rank-delta, trajectory, and setup badge, plus `schema_version`/`generated_at`/
  `scan_id`/`scan_date`/`lagged` metadata. Mirrors the baked (lagged-for-guests)
  view; fail-open so a JSON error never breaks the build. `dashboard/data_export.py`
  builds the payload; no scan/DDL/template changes. *(2026-07-23)*
- **Max-drawdown (1y) display** — trailing 1-year max drawdown added as an
  info-only signal (`max_dd_1y`, `compute_max_drawdown`), computed for every
  sector and theme in the pipeline and shown on the breakdown panel's "Not scored"
  line. Not part of scoring; no DDL or client-render changes. (Risk-adjusted
  momentum remains queued as a separate research spike.) *(2026-07-23)*
- **Regime-conditional weighting (research)** — added a backtest research harness
  (`scripts/regime_research.py`) comparing the fixed 50/50 level/change split against
  regime-conditional schemes (SPY vs 200-DMA), via additive weight overrides in
  `score_all`/`score_as_of` and a `weights_fn` hook on `run_track` (live scan
  unchanged, `config/weights.yaml` still 50/50). **Outcome: parked** — no scheme
  meaningfully improved risk-adjusted return in both US and EU; the "favour change
  in risk-off" hypothesis slightly hurt the US, and the only non-losing scheme (V3,
  favour level in uptrends) was neutral in the US and within-noise better in the EU
  on ~30 regime switches. Full findings in the notes repo
  (`research/2026-07-22-regime-conditional-weighting.md`); a walk-forward retest of
  V3 is the natural follow-up (separate queued item). *(2026-07-22)*
- **Methodology documentation** — a footer "Methodology" link on every page opens
  an accessible modal (`_methodology.html.j2`) explaining the scanner end-to-end:
  universe, data sources, Level/Change signals, per-region z-scoring + 50/50
  composite, sentiment (info-only), trajectory/setup badges, backtest, and research
  basis. English only, static, no new data/DB. *(2026-07-22)*
- **scan.py run() refactor** — extracted FinBERT sentiment, persist, themes
  track, dashboard build, and alerts into module-level helpers; `run()` is now a
  readable orchestrator with a clean 1..17 step sequence (was a ~355-line
  function with drifting step numbers). Behavior-preserving — scan smoke,
  workflow, and health tests pass unchanged. Completes the ops hardening sweep
  (backup restore drill shipped the same day). *(2026-07-22)*
- **Backup restore drill** — new gated integration test
  (`tests/test_backup_drill.py`) round-trips a seeded fixture through
  `dump_tables` → `write_backup` → zip → `read_backup` → `load_tables(force=True)`
  → re-dump and asserts row-for-row equality across all seven tables. Exercises
  the real-DB restore paths (FK-safe deletes, NULL handling, sequence reset)
  that mocked unit tests never touched. Gated on `TEST_DATABASE_URL`; skips
  safely without a disposable DB. Part of the ops hardening sweep (scan.py
  cleanup shipped the same day). *(2026-07-22)*
- **Price-cache adjustment consistency — retired (premise moot)** — the queued
  item assumed the parquet cache *appends* fresh rows onto old ones, letting
  auto_adjust re-adjustments accumulate inconsistently around distributions.
  Verified this is false: `fetch_prices` (`src/data/prices.py`) has no
  append/concat path — a cache miss re-fetches the entire `start..end` window
  and **overwrites** the whole parquet file, so every served series is a single
  atomic, self-consistent auto_adjust snapshot. The weekend-aware freshness
  change (2026-07-22) makes refetches ~daily, so a served snapshot's adjustment
  epoch is at most ~1 trading day stale and self-heals on the next refetch. No
  code change needed. *(2026-07-22)*
- **Weekend-aware price-cache freshness** — replaced the flat 4-day tolerance
  in `_cache_is_fresh` with a weekday walk-back heuristic (`_expected_latest_close`)
  + 1-day grace. Prices now refresh daily on normal trading days; Friday's close
  bridges to Monday. No new dependencies. *(2026-07-22)*
- **FinBERT sentiment for themes** — keyword-based GDELT queries per theme
  scored with FinBERT, z-scored within theme cohort, stored in
  `theme_sentiment_signals` + `theme_scores.sentiment_score`.
  `themes.yaml` restructured to `{ticker, gdelt_keywords}` per theme.
  Informational only (`blend_sentiment=False`). *(2026-07-22)*
- **Content gating hardened beyond leaderboard** — all baked data (SCAN_HISTORY
  blob, scan index, scan reports, charts, RRG, feed) now capped at the lagged
  scan boundary when auth is configured. Previously only the leaderboard table
  was lagged; guests could reach latest scores via the History tab, figures, or
  feed. Restructured `dashboard/build.py` to compute auth + lag before building
  any downstream artefact. *(2026-07-21)*
- **Content gating (lagged data for guests)** — landing modal (sign in / continue
  as guest) + persistent lag-notice banner on the Sectors page. The baked
  leaderboard now renders the newest scan ≥7 days old when auth is configured
  (`dashboard/gating.py`, `apply_leaderboard_lag`); authed users upgrade the
  leaderboard to the latest scan client-side via the RLS-protected
  `v_latest_scores` view (`scripts/content_gating_migration.sql`, run post-merge).
  Other tabs keep full history. Falls back to latest-everywhere when auth is
  disabled. *(2026-07-21)*
- **Rolling correlation heatmap** — new Correlation tab on the sectors page
  showing a 25×25 Plotly heatmap of 60-trading-day rolling return correlations
  across all sector ETFs. Rows/columns ordered by region then rank, top-5 per
  region in bold. Build-time computation from cached prices, info-only.
  *(2026-07-20.)*
- **Data-health panel** — collapsible footer on the sectors page showing
  per-source price fetch stats, sector coverage, FinBERT/GDELT stats, and
  scan duration with green/amber/red badges. Health metadata persisted as
  nullable columns on the `scans` table. *(2026-07-20.)*
- **Public-repo privacy audit** — decided to keep `sector_momentum` public
  (required for free-tier GitHub Pages hosting; going private would take the
  live dashboard down) and instead split `design/specs/` and `design/plans/`
  (37 + 32 files) into a new private companion repo, `jbarte/sector_momentum-notes`.
  `design/` is removed from this repo (git history still has it — no rewrite,
  same trade-off as `docs/`). `CLAUDE.md` and `ARCHITECTURE.md` updated to point
  future spec/plan output and existing doc references at the new repo. Added a
  hard constraint to the Position tracking item: positions must live behind RLS,
  fetched client-side, never in the static build. *(2026-07-20)*

- **Deploy Pages via artifact** — `scan.yml` and `build-docs.yml` now deploy
  `docs/` directly via `actions/upload-pages-artifact` + `actions/deploy-pages`
  instead of committing it. `docs/` is gitignored; the Pages source was
  flipped from `legacy` (branch `main:/docs`) to `workflow` and a live deploy
  verified (leaderboard, themes, sentiment, a report, `feed.xml`, `.nojekyll`
  all serving correctly). Eliminates the recurring `docs/` merge-conflict
  class and the ~1&nbsp;MB/day git-history bloat. Existing `docs/` blobs
  remain in history (untracking, not a rewrite). *(2026-07-20)*

- **Signal correlation audit — drop `above_200dma`** — one-time correlation
  matrix confirmed `above_50dma`/`above_200dma` collinearity. Removed
  `above_200dma` from `_LEVEL_SIGNALS` (5→4 level signals). Demoted to
  info-only in the dashboard breakdown panel. Risk-adjusted momentum remains
  queued separately. *(2026-07-20)*

- **CI price cache** — added `actions/cache@v4` step to `scan.yml` persisting
  `data/cache/` across runs. Uses `run_id` key with `restore-keys` fallback so
  every run reuses the most recent cache; per-file freshness logic handles
  staleness. Cuts ~545 live ticker fetches to near-zero on warm runs, reducing
  429 risk and runtime. *(2026-07-20)*

- **Restore stooq price source** — replaced broken `pandas-datareader` stooq
  driver with direct CSV endpoint fetch (`requests.get`). Removed
  `pandas-datareader` dependency. Added per-source success/failure stats
  logging with WARNING when a source goes 0-for-N. *(2026-07-20)*

- **Per-region cohort scoring** — live scan now scores US (11 sectors) and EU
  (14 sectors) as independent z-score cohorts, matching the backtest. Leaderboard
  shows two region-grouped tables. Client-side rescore, scan-history, and
  scan-digest are region-aware. Backfill script recomputes historical ranks.
  *(2026-07-20)*

- **Ops quick wins: failure alerting, job timeout, lock script, SQL warnings**
  — `scan.yml` now pings the existing ntfy topic (`if: failure()`, high
  priority, run URL) so silent scan failures like Jul 18–19 can't recur, and
  caps the job at `timeout-minutes: 180`. `scripts/lock.sh` encodes the exact
  `uv pip compile` invocations (Linux platform + `--upgrade`) whose omission
  caused that outage. `src/state.py` reads moved from `pd.read_sql_query` on a
  raw psycopg2 connection to a cursor-based `_read_sql` helper — dashboard
  builds no longer emit a UserWarning per query. Restore drill + scan.py
  step cleanup remain queued. *(2026-07-20)*

- **Retired Google Trends sentiment** — removed the Trends pipeline entirely
  (fetch, day-cache, derived signals, comparative attention, rising queries)
  after it was 429-blocked from CI since ~2026-07-14 and FinBERT (2026-07-17)
  took over `sentiment_score`. Deleted `src/data/trends_symbols.py`,
  `src/data/trends_cache.py`, 11 Trends test files, `config/trends_*.yaml`,
  `scripts/resolve_trends_entities.py`, the `trends:` sections of
  `config/themes.yaml`, and the pytrends dependency (~2,900 lines). Themes lose
  sentiment (were Trends-only); `theme_sentiment_signals` goes dormant.
  Historical `sentiment_signals`/`theme_sentiment_signals` rows and DDL kept;
  `sentiment_signals` keeps receiving the FinBERT news_* rows. Same PR hardened
  the GDELT fetch (inter-query pause 5s→20s, retries 3→4, final-attempt
  give-up now logged) to lift FinBERT sector coverage. Sentiment page is now
  FinBERT-only (no cohort toggle, no Trends columns). *(2026-07-19)*

- **Split EU composite sectors into standalone sectors** — the two untradeable
  equal-weight EU composites replaced by their STOXX sub-sector ETFs as
  first-class sectors: Financials → Banks (EXV1.DE) + Financial Services
  (EXH2.DE) + Insurance (EXH5.DE); Materials → Basic Resources (EXV6.DE) +
  Chemicals (EXV7.DE). EU universe 11 → 14 sectors; composite-building code
  removed from the pipeline. `config/sector_map.yaml` `stoxx_to_gics` became
  live config (`src/sector_map.py`): FinBERT news sentiment and Swedish-ticker
  matching resolve sub-sectors to their GICS parent (identity fallback).
  Research basis (3y daily): Basic Resources↔Chemicals correlation 0.50 with
  37% 6m-momentum sign disagreement; Financials components ~0.70 with ~15pp
  median best-vs-worst momentum spread — the blends were averaging away the
  signal the scanner exists to find. *(2026-07-18)*

- **User authentication (login foundation)** — invite-only magic-link sign-in
  on the static dashboard via Supabase Auth + supabase-js v2 (UMD bundle
  vendored at build time like Plotly, gitignored). Sign in/out control in the
  command-bar meta-cluster (EN+SV); session persisted in localStorage;
  `dashboard/assets/auth.js` + `window.SUPABASE_CONFIG` baked by `build.py`
  only when `SUPABASE_PUBLISHABLE_KEY` is set — fail-open, without the key
  the dashboard is unchanged. Allowlist is server-side: Supabase sign-ups
  disabled + `shouldCreateUser: false`; invitees added via the Supabase
  dashboard. RLS enabled (no policies) on all 7 pipeline tables
  (`scripts/enable_rls.sql`) — anon/authenticated blocked, postgres-role
  pipeline unaffected. Foundation for Position tracking (queued). *(2026-07-18)*

- **FinBERT news sentiment** — signed (positive/negative) news polarity per
  GICS sector using ProsusAI/finbert over GDELT DOC 2.0 API headlines
  (English, 24h window, 11 sector queries via GDELT theme codes). Replaces
  the directionless Google Trends slope as `sentiment_score` in the composite
  scoring path, making the dashboard's blend toggle meaningful. Google Trends
  derived signals stay info-only. Four new info columns on the sentiment page:
  Polarity, Articles, Pos%, Neg%. Non-fatal step 8d in scan.py with
  `--no-finbert` CLI flag; Trends z-score is the fallback if FinBERT fails.
  `src/data/news_sentiment.py` handles GDELT fetch, FinBERT inference, and
  cross-sectional z-scoring. Sectors only — themes stay Trends-only. EN+SV
  i18n. No DDL changes. *(2026-07-17)*

- **Forward-return validation & holding-period stats** — two info-only panels
  in the Backtest tab. For every scan where a sector ranks top-5, computes
  5-day and 1-month excess return vs the region benchmark (RSP / EXSA.DE) and
  aggregates hit rate, mean, and median by region. Separately, extracts
  contiguous top-5 rank streaks and reports median/mean/min/max duration.
  `dashboard/validation.py` handles all computation at build time from
  `all_scores_df` + cached prices; `_validation.html.j2` renders both tables.
  EN+SV i18n. No schema changes, no scoring impact. *(2026-07-17)*

- **Threshold alerts (daily scan notifications)** — post-scan step (Step 15 in
  `scan.py`) computes Entry/Exit setup badges for the latest scan (using rank
  trajectories over the last 5 scans) and sends a ntfy.sh push notification
  when any sector or theme gets an Entry or Exit badge. Covers both US/EU
  sectors and themes. "No events, no noise" — nothing sent if no badges fire.
  `src/alerts.py` handles event detection (reuses `_compute_rank_trajectories`
  and `_compute_setup` from `dashboard/rows.py`), formatting, and delivery
  (ntfy JSON API, stdlib `urllib`, no new dependency). Fail-open: missing
  `NTFY_TOPIC` env var silently skips; `--no-alerts` CLI flag to suppress.
  CI wired via `scan.yml` secret. *(2026-07-17)*

- **Macro regime context bar** — a thin info strip below the dashboard header showing
  SPY vs 200-DMA (above/below + distance %) and VIX band (Calm/Elevated/Stressed).
  Fetched at dashboard build time via the existing price cache, non-fatal if unavailable.
  `dashboard/macro.py` computes the indicators; `_macro_bar.html.j2` renders the strip
  on all three pages. Info-only, no scoring impact. *(2026-07-15)*

- **Entry/Exit badge scorecard** — historical hit-rate table for all 7 badge
  types (Entry, Exit, 5 trajectory states) plus a no-badge baseline. For each
  badge that appeared on a past scan, computes the 5-trading-day forward ETF
  return and aggregates count, hit rate, mean, and median. Displayed in the
  Backtest tab below the equity curves. Computed at `build.py` time from
  `get_scan_history(n_scans=None)` + cached prices; no new DB tables.
  `dashboard/badges.py` holds the logic. EN+SV i18n. Info-only — no scoring
  impact. *(2026-07-16)*
- **Dashboard UX redesign** — compact command bar (scan info + page nav +
  disclaimer + guide + lang toggle), card-shell surfaces on all three pages
  (sectors, themes, sentiment), utility-row pattern for tab actions, footer
  with version/feed/GitHub links. Deleted macro bar (absorbed into command
  bar), guide modal, and several legacy layout patterns. Full CSS variable
  foundation. *(2026-07-16)*
- **Macro regime context bar** — risk-on/risk-off context bar: SPY vs 200-DMA
  (above/below + distance) and VIX band (calm/elevated/stressed) from cached
  daily prices. Displayed in the command bar's macro context section.
  `dashboard/macro.py` builds the context; info-only, no scoring impact.
  *(2026-07-15)*
- **RSS/Atom feed of scan results** — `build.py` now emits `docs/feed.xml`, an Atom
  feed with one entry per scan (last 30). Each entry lists the top-5 sectors per region
  and biggest rank movers. All three HTML pages link to the feed via
  `<link rel="alternate">`. New module `dashboard/feed.py` builds entries from
  `all_scores_df`; template `dashboard/templates/feed.xml.j2` renders the Atom XML.
  No schema changes, no JS. *(2026-07-15)*

- **Theme backtest tab** — the Themes page now has a Backtest tab matching the
  sector page. `score_themes_as_of` (`src/backtest/replay.py`) replays the theme
  scoring pipeline as-of any historical date; `run_theme_track`
  (`src/backtest/engine.py`) runs a monthly top-N rotation strategy against ACWI
  (default top-3, configurable via `--theme-top-n`). `backtest.py` now fetches
  theme tickers alongside sectors and writes theme results to
  `backtests_themes/summary.json` (separate from the sector backtest).
  `_build_theme_backtest_context` (`dashboard/figures.py`) loads the results and
  builds a Plotly equity curve; `themes.html.j2` renders the Backtest tab with
  metrics table + chart, EN+SV i18n, and Guide section. No rotations event-study
  for themes (no curated historical events yet). *(2026-07-15)*
- **Theme sentiment (Google Trends for themes)** — the thematic ETF cohort now
  gets a Trends sentiment dimension, mirroring the sector path. A `trends:`
  section in `config/themes.yaml` maps each theme to a real search phrase
  (uranium, defense stocks, robotics…) with an optional `trends_entities:`
  mid override; `build_theme_symbol_map`/`load_theme_entities`
  (`src/data/trends_symbols.py`) key them as `THEME|<name>` and reuse
  `fetch_symbol_trends`/`score_symbol_sentiment`/`derived_signals`/
  `fetch_comparative_interest`/`fetch_rising_queries` verbatim (pulled
  worldwide — `THEME: [""]` in `config/trends_geo.yaml`). `scan.py`'s themes
  block fetches sentiment (isolated non-fatal try so price scores still persist),
  passes it to `score_all` to fill the existing `theme_scores.sentiment_score`
  (stored, never blended), and saves derived/attention/rising rows to a new
  `theme_sentiment_signals` table via `save_theme_scan(..., sentiment_signals_df=)`.
  Surfaced on the shared `docs/sentiment.html` behind a client-side
  **Sectors ⇄ Themes** toggle (localStorage-persisted, lazy-plotted scatters,
  EN+SV). `_rows_from_df` now normalizes NaN→NULL for text columns; scatter
  builder gained a THEME series colour. Full parity with the sector sentiment
  view. *(2026-07-15)*
- **Sentiment honesty fixes** — coverage guard (`_aggregate` omits dead
  sector keys, `score_symbol_sentiment` z-scores live subset only with
  `_MIN_LIVE_SECTORS=8` threshold, NaN for dead/under-threshold); pinned
  `_MOMENTUM_WINDOW=13` shared constant so headline sentiment == z(momentum)
  as documented; honest health log (counts live series before z-scoring, not
  post-z non-zeros); bumped default fetch `sleep_s` 20→25; removed dead
  `pillars` block from `config/weights.yaml` (closes the "Sentiment →
  composite blend decision" item — toggle-only is the permanent design until
  FinBERT); `score_all` reads pillars lazily via `.get()` defaults.
  Spec: `design/specs/2026-07-13-sentiment-honesty-design.md`. *(2026-07-13)*
- ~~Maintenance sweep~~ — deleted dead `src/data/stocktwits.py` +
  `tests/test_stocktwits.py` (superseded by symbol-based Trends sentiment);
  `_cache_is_fresh` (`src/data/prices.py`) now tolerates a 4-day gap so the
  day after a market holiday no longer triggers a spurious live re-fetch, and
  also checks that cached data covers a newly-requested longer `start` range
  (re-fetches instead of silently truncating); deduped three repeated
  patterns in `src/state.py` — a shared latest-scan query helper, a shared
  recent-scan-filter builder, and a shared DataFrame-to-rows insert helper —
  all pure refactors, same output/behavior, verified by the existing test
  suite. *(2026-07-12)*
- ~~Theme timestamp parse crash~~ — `_build_drilldown_data`/`_build_history_figure`
  (`dashboard/figures.py`) crashed on `run_at` values that mixed ISO8601
  timestamps with and without a `+00:00` timezone suffix (`pd.to_datetime`
  infers a fixed format from early rows, then chokes on later rows). Fixed by
  passing `format="ISO8601", utc=True`. Was blocking local
  `python3 dashboard/build.py` runs on the theme drilldown path. *(2026-07-12)*
- ~~"What changed today" digest~~ — a summary strip above the sector
  leaderboard shows new top-5 entries and the biggest rank movers (up to 3
  gains, 3 drops) vs the previous scan. Entirely client-side
  (`dashboard/assets/scan-digest.js`), reusing the `SCAN_HISTORY` blob already
  shipped by renderable-scan-history — no pipeline or schema changes. Updates
  live when browsing historical scans via the existing scan-history viewer.
  Trajectory flips deferred (would need porting the server-side trailing-slope
  algorithm to JS). *(2026-07-12)*
- ~~Backlog rewrite + drift guardrails~~ — rewrote this file (deleted the
  fully-shipped code-review-findings section and stale queued text for shipped
  work), added the lifecycle rules above, created a real
  `.claude/commands/backlog-sync.md` (CLAUDE.md referenced a command that
  didn't exist), and un-ignored `.claude/commands/` so shared commands are
  versioned. Dropped record: **Swedish overlay polish** was dropped 2026-06-26
  (hand-maintained 30-stock list with static caps, vestige of the original
  thesis). *(2026-07-12)*
- ~~Renderable scan history~~ — clicking any scan row in the History tab rebuilds
  the Leaderboard with that scan's scores via an embedded `SCAN_HISTORY` JSON blob
  and client-side JS table rebuild. Sectors page only; charts stay multi-scan as-is.
  Keyboard accessible (tabindex + Enter/Space), i18n (EN+SV), "Back to latest"
  restore. *(2026-07-12)*
- ~~Sentiment enrichment — seasonal baseline + rising queries~~ — extended Trends fetch
  from 3 months to 12 months (`today 12-m`, 52 weeks); existing derived signals still
  operate on the trailing 13 weeks. New `seasonal_ratio` signal = mean(last 13 wk) /
  mean(first 39 wk), surfaced as a "Seasonal" column (EN+SV) on the sentiment page.
  New `fetch_rising_queries()` calls `related_queries()` per sector per geo (cached,
  fail-open), top 5 results stored in a `text_value TEXT` column on `sentiment_signals`,
  displayed as expandable panels with delegated click/keyboard toggle. Both info-only —
  neither affects the composite score. *(2026-07-12)*
- ~~P4 split build.py~~ — split `dashboard/build.py` (1,459 lines) into
  `figures.py`, `rows.py`, `breakdown.py`, `sentiment.py`, `reports.py`
  (~300 lines remain in build.py as orchestrator + re-exports). Extracted
  `_base_layout()` helper eliminating ~80 lines of duplicated Plotly layout
  boilerplate per figure. Deleted unused `_CHART_STYLE` constant. Deduped
  sector/theme leaderboard row builders via shared `_build_rows_common()`.
  Extracted `_header.html.j2` (shared across all 3 pages) and `_tabs.js.j2`
  (shared between index and themes). Created `dashboard/__init__.py`. All
  existing imports preserved via re-exports in build.py. *(2026-07-12)*
- ~~P4 dead config + minor sweep~~ — clarified `config/weights.yaml` (comments
  documenting signal-list keys are dashboard display order only, scoring hardcodes
  the lists; removed unused `emerging_min_consecutive`; noted `blend_sentiment=False`
  means pillar weights are not applied). Replaced `datetime.utcnow()` with
  `datetime.now(timezone.utc)` in scan.py, backtest.py, src/backup.py, and tests.
  Removed dead imports in scan.py (math, numpy). Moved mid-file imports to top in
  trends_symbols.py and state.py. Filtered `backup_*.zip` in restore latest
  selection. GitHub Actions already pinned (first-party at major version tags,
  third-party SHA-pinned); test.yml already had `fix/**` trigger. *(2026-07-11)*
- ~~Review P4: test coverage gaps~~ — added 22 unit tests for `src/data/prices.py`
  (cache freshness, stooq→yfinance fallback, all-NaN/empty/corrupted edge cases,
  `_normalize_columns` with MultiIndex, `fetch_prices` cache-vs-live integration),
  8 tests for `src/data/macro.py` (stub contract), 13 pipeline value-range assertions
  + missing-benchmark/sector handling, and 7 render-based dashboard tests (full
  leaderboard render with breakdown panels, figure builder JSON validation,
  trajectory computation, `_safe_float`/`_format_raw_value` edge cases, multi-call
  render-context coverage). `_render_context_keys` now finds all three `_render()`
  calls (index/sentiment/themes) instead of just the first. *(2026-07-11)*
- ~~README + ARCHITECTURE docs~~ — rewrote `README.md` (purpose, disclaimer, live dashboard link, env keys, dev commands, pointers) and fully synced `ARCHITECTURE.md` to current reality (Supabase/Postgres, daily cron, Google Trends sentiment, actual module structure and data flow). *(2026-07-11)*
- ~~rs_momentum fast=1→5~~ — `compute_rrg` default changed from `fast=1` (one-day noise) to `fast=5`; configurable via `config/weights.yaml` `signal_params.rs_momentum_fast`; threaded through `latest_rrg` → `compute_signals_for_sector` → `build_signals_rows` / `build_theme_signals_rows` → `scan.py`. Expect rank shifts from the smoother momentum signal. *(2026-07-11)*
- ~~Backtest realism~~ — four fixes: (1) `--cost-bps` CLI flag debits one-way transaction costs proportional to turnover on each rebalance; (2) benchmark NaN months dropped instead of silently treated as 0%; (3) `close_at` rejects prices older than 5 trading days (returns NaN); (4) Sharpe column labelled "Sharpe (rf=0)" in EN+SV. *(2026-07-11)*
- ~~Dependency lockfile & pytrends pin~~ — split `requirements.txt` (runtime, `>=` floors) from `requirements-dev.txt` (adds pytest); `uv pip compile` generates exact-pinned `.lock` files that CI installs from (`requirements.lock` for build-docs/scan, `requirements-dev.lock` for tests); `pytrends` pinned to `==4.9.2` in the input file. Daily cron no longer installs newest versions on every run. *(2026-07-11)*
- ~~Review P1: z-score NaN handling~~ — `zscore_cross_section` now standardizes on
  non-NaN values and fills missing z-scores with 0.0 (neutral in z-space) instead
  of filling raw values with 0.0 first, which made any sector with a failed
  ~100-centred signal (rs_ratio/rs_momentum) a fake outlier that distorted the
  whole cross-section. Regression test added. *(2026-07-11)*
- ~~Review P1: backup/restore table coverage~~ — backups now include
  `sentiment_signals`, `theme_scores`, `theme_signals` (previously silently
  dropped, and `restore.py --force` failed on an FK violation deleting `scans`
  with live child rows). Deletes/loads now run in FK-safe order; old backups
  lacking the new tables restore gracefully (empty DFs). Schema-coverage test
  asserts `_COLUMNS` covers every table in the DDL. *(2026-07-11)*
- ~~Review P2: CI hardening~~ — `scan.yml` and `build-docs.yml` now share a
  `commit-to-main` concurrency group and rebase before pushing (fixes the
  lost-commit race); daily scan gated on a green `pytest`; `test.yml` also
  triggers on `fix/**`/`chore/**`; `claude-code-action` pinned to SHA. *(2026-07-11)*
- ~~Review P2: dependency fragility~~ — covered by the lockfile + pytrends pin
  entry above; pytrends already degrades gracefully, maintained replacement
  (trendspy) deferred to if/when pytrends breaks. *(2026-07-11)*
- ~~Scan robustness: coverage guard, idempotent saves, connection cleanup~~ — scan.py aborts (exit 1) if <80% of configured sectors produce signal rows; `save_scan` replaces same-UTC-day scans so CI retries don't duplicate; DB connection wrapped in try/finally; report + dashboard steps non-fatal. *(2026-07-11)*
- ~~Dashboard quick wins: movers clip, rank guard, rescore init, dead code, report skip, plotly-basic~~ — removed fixed 520px height from movers containers (both templates); added `row.rank is number` guard in index.html.j2; `applyRanking()` only runs on init when sentiment toggle is enabled; deleted dead per-signal drilldown figure loop (751-796); `_generate_scan_reports` skips reports whose file already exists; switched to plotly-basic bundle (~3.6MB → ~1MB). *(2026-07-11)*
- ~~i18n gaps + CSS vars~~ — added SV `guide_tab_themes` (full themes Guide page), `guide_body_rrg_themes`, `guide_body_drilldown_themes`, `si_download`, `leaderboard_empty`, `scans_empty`; generalized "topp-5" in `note_backtest`; fixed `--font-sans` → `--font-body`, `--brand` → `--brand-strong`, `--text-muted` → `--fg4`. *(2026-07-12)*
- ~~Accessibility~~ — tabs: `aria-selected`/`aria-controls`, `role="tabpanel"`, arrow-key nav; leaderboard rows: `tabindex="0"` + Enter/Space delegated handler; sortable `<th>`: `tabindex="0"` + keyboard trigger; `.sig-tip`: focusable + tooltip on focus; guide modal: `aria-modal="true"`, focus trap, focus restore on close. Both index.html.j2 and themes.html.j2. *(2026-07-12)*
- ~~XSS hardening~~ — `js_json` Jinja filter escapes `</` in script-block JSON; inline `onclick="toggleBreakdown()"` replaced with `data-sector-id` + delegated click listener (both pages); ETF URL scheme validation rejects non-http(s) URLs. *(2026-07-12)*
- ~~Themes — full tab parity with sectors~~ — the Themes page now has the same
  tab structure as Sectors: Leaderboard, RRG, Drill-down, Movers, History, and
  Guide. Added `get_theme_rrg_history()` in `state.py`; all other build functions
  (`_build_rrg_figure`, `_build_drilldown_data`, `_build_movers_figure`,
  `_build_history_figure`) reused as-is with `theme_history_df`. Backtest tab
  deferred (requires a separate theme backtest runner). *(2026-07-10)*
- ~~Remove region-split / composite view toggle~~ — removed the Region-split vs
  Composite `<select>` toggle, `data-view` row attributes, `mergeComposite` in
  `rescore.js`, `_build_composite_rows`/`_build_composite_history` in
  `build.py`, composite CSS rules, 3 i18n keys, and `test_dashboard_composite.py`.
  US and EU sectors stay separate — no merged "Global" rows. *(2026-07-10)*
- ~~Themes — EU-available ETF alternatives~~ — added a `ucits:` section to
  `config/themes.yaml` with one UCITS-listed equivalent per theme (10 total:
  Global X, VanEck, iShares, First Trust). Each entry has ticker, name, ISIN,
  TER, issuer, match quality (exact/close/partial), and justETF URL.
  `_build_instruments_html` now renders a "UCITS Alternative" table in theme
  breakdown panels with a colour-coded Match column. Scoring stays on US ETFs;
  UCITS shown as reference instruments only. *(2026-07-10)*
- ~~Comparative (cross-sector) interest~~ — `fetch_comparative_interest`
  (`src/data/trends_symbols.py`) pulls each region's sectors through
  anchor-chained Trends batches (`_rescale_chain`) so interest is scored
  head-to-head against all other sectors in the same region, not
  independently-scaled per-sector series. Wired into `scan.py`, persisted as
  `attention_level` rows in `sentiment_signals`, and surfaced as an "Attention"
  column (EN+SV) on `docs/sentiment.html`. Toggle-only/info-only — no composite
  or ranking impact, sectors page unchanged. *(2026-07-09)*
- ~~Thematic ETF momentum — Phase 2 (leaderboard deltas + trajectory)~~ — the Themes
  leaderboard now shows rank-Δ (vs the previous scan) and a trajectory badge (rank
  slope over the last 5 scans), matching the sector board. Computed at dashboard-build
  time from a new `get_theme_scan_history` loader (aliased region="THEME" so
  `_compute_rank_trajectories` and the delta-merge reuse verbatim) — no schema or
  `scan.py` change. Phase 3 (RRG scatter, composite-history chart, Trends sentiment
  for themes) remains queued above. *(2026-07-08)*
- ~~Thematic ETF momentum — Phase 1 (universe + score + leaderboard)~~ — a thematic
  ETF universe (`config/themes.yaml`, one ETF per theme) is scored by the existing
  momentum pillars vs a single global benchmark (ACWI, SPY fallback) in its own
  z-score cohort (`build_theme_signals_rows` + `score_all`), persisted to new
  `theme_scores`/`theme_signals` tables under the daily `scan_id`, and shown as a
  read-only **Themes** leaderboard (third header segment, reusing the breakdown panel).
  Breadth is N/A for themes; the themes pass is fully non-fatal. Phases 2 (deltas /
  trajectory / RRG / history) and 3 (Trends sentiment for themes) remain queued above.
  *(2026-07-07)*
- ~~Sentiment — durable Trends day-cache~~ — successfully-fetched Trends batches are
  cached per UTC day in Supabase Storage (bucket `trends-cache`, one
  `trends_cache_<date>.json` object) so a re-triggered CI run or same-day re-run
  reuses them and re-fetches only the batches that 429'd/failed. Cache is per
  `(geo, batch)` (`src/data/trends_cache.py`), read/written in `_fetch_geo`, and
  loaded/saved around the fetch in `scan.py`. Fully **fail-open** — any Storage error
  logs a warning and the scan runs live/uncached; `--no-cache` bypasses it. Cuts the
  429 exposure from the ~4× region-aware call volume. *(2026-07-07)*
- ~~Sentiment — region-aware Trends pulls~~ — `fetch_symbol_trends` now queries US
  sectors in `geo="US"` and EU sectors averaged across `DE`/`FR`/`GB`, normalized
  against a stable ubiquitous anchor (`YouTube`, configurable in
  `config/trends_geo.yaml`) instead of the worldwide `SPY` pull. Symbols are
  partitioned by region (`_symbols_by_region`), fetched per geo (`_fetch_geo`), and
  multi-geo regions averaged per symbol (`_average_geo_series`); `_aggregate`/scoring
  and the entity-mid path are unchanged (ticker-keyed). Toggle-only. Costs ~4× the
  Trends API calls (day-cache remains a separate backlog item), and the anchor change
  breaks comparability with pre-change stored sentiment. *(2026-07-07)*
- ~~Sentiment — Trends entity-mid resolution~~ — `fetch_symbol_trends` now queries a
  ticker's Google Knowledge Graph **entity mid** instead of the ambiguous raw string
  where one is curated in `config/trends_entities.yaml`, killing collision false-positives
  (the `VOX`→Vox Media / `LOGS`→the-word class). Per-ticker term substitution +
  column re-keying keep `_aggregate`/scoring unchanged (ticker-keyed); tickers without a
  curated mid fall back to strings, so the change is strictly additive. A dev-only
  `scripts/resolve_trends_entities.py` proposes candidates for human review; the scan
  path never calls `suggestions()`. Toggle-only. The committed config ships empty —
  real mids are added after running the script and eyeballing each entity. *(2026-07-04)*
- ~~Sentiment enrichment — derived Trends signals~~ — the sentiment page now surfaces
  four complementary read-outs alongside the headline slope, all computed from the same
  ~13-week interest series in `derived_signals()` (`src/data/trends_symbols.py`):
  **acceleration** (recent-half vs earlier-half slope), **range position** (percentile in
  the window min–max), **spike** (z of the latest point vs trailing weeks), and
  **volatility** (std of week-over-week changes). Stored per sector-key in a new additive
  `sentiment_signals` table (no schema migration; old scans simply lack rows), rendered as
  an info-only table on `docs/sentiment.html` (EN+SV). Still **toggle-only** — only
  `momentum`/slope feeds the composite via the existing toggle; the new signals never touch
  the ranking. Region-aware pulls, Trends topics/entity-mids, seasonal baseline, and rising
  queries remain queued above. *(2026-07-02)*
- ~~Sentiment moved to its own page~~ — sentiment is no longer a dashboard tab; it now
  lives on `docs/sentiment.html`, linked from the main nav ("Sentiment ↗"), decoupled
  from the Leaderboard/RRG/History/etc. tab bar. The leaderboard's "include sentiment in
  ranking" toggle and Sentiment column are unchanged — this only moves the read-only
  scatter/explainer view. Shared CSS and the EN⇄SV language toggle were extracted into
  `dashboard/templates/_style.html.j2` / `_i18n.html.j2` so both pages stay in sync.
  Enrichment ideas (region-aware Trends, more derived signals) remain queued above.
  *(2026-07-02)*
- ~~EU sector composites (Phase 1: Financials, Materials)~~ — EU Financials (Banks +
  Financial Services + Insurance) and Materials (Basic Resources + Chemicals) are now
  equal-weight composites of their STOXX supersector ETFs instead of a single sub-sector,
  making them truer GICS proxies. `eu_sectors` values are lists; `build_composite_series`
  blends a rebased-mean Close + summed Volume; single-component sectors and all US sectors
  unchanged. Phase 2 (Consumer Discretionary/Staples/Comm + Media/P&HG crosswalk) pending. *(2026-06-29)*

- ~~EU-available instruments reference~~ — the per-sector "Instruments" panel now lists one
  EU-available UCITS ETF per sector (US → iShares S&P 500 sector UCITS ETFs, Real Estate →
  iShares US Property Yield). For EU, the reference is the **same instrument the scanner
  uses** (`universe.yaml eu_sectors`) for all 11 sectors — reference == signal source; the
  three previously-Amundi sectors (Energy/Industrials/Consumer Discretionary) were realigned
  to the scanned iShares STOXX 600 funds (`EXH1/EXH4/EXH7.DE`). US can't match (scanned
  `XLV`/`XLK` aren't EU-available). Reference-only (`config/sector_etfs.yaml`); scanned
  instruments/benchmarks unchanged. *(2026-06-29)*
- ~~Stop publishing internal design docs~~ — moved `docs/superpowers/` (specs + plans) to
  repo-root `design/` so they're versioned but no longer served on the public Pages site
  (`docs/` is the published web root; static `.nojekyll` serving has no per-folder exclude).
  CLAUDE.md now points spec/plan output at `design/{specs,plans}`. *(2026-06-29)*
- ~~Published History tab stale (GitHub Pages frozen)~~ — the daily scan committed
  current `docs/`, but Pages' legacy **Jekyll** build hard-failed on Liquid brace syntax
  in `docs/superpowers/` plan snippets, freezing the published site at the last good
  deploy (scan 113 / 06-26) while `docs/` and the DB kept advancing to scan 116. Fix:
  `build.py` now emits `docs/.nojekyll` so Pages serves the static artifact as-is. *(2026-06-29)*
- ~~DB backup → Supabase Storage (pre-run)~~ — replaced the git-committed `backups/` CSV dump with a pre-run zip uploaded to a private `db-backups` Supabase Storage bucket (`src/storage_backup.py` + `backup_to_storage`/`restore_from_storage`); `scan.py` backs up before writing; `scan.yml` no longer commits `backups/`; `restore.py` pulls latest from Storage (`--list`/`--local`). One new secret `SUPABASE_SERVICE_KEY`. *(2026-06-29)*
- ~~Backtest against past rotations (Phase 2 — rotation event-study)~~ — curated rotations in `config/rotations.yaml` → `src/backtest/rotations.py` recovers each sector's point-in-time rank-over-time vs the ETF's indexed price (reusing `score_as_of`); persisted in `backtests/summary.json` and rendered as dual-axis small-multiples in the Backtest tab. Visual-only. *(2026-06-27)*
- ~~Symbol-based Google Trends sentiment (Phase 1 — ETF symbols)~~ — Trends now queries the
  sector ETF symbols (primary + linked, both regions) instead of generic theme words;
  anchor-normalized (SPY) and aggregated to a region-aware sentiment z per region|sector via
  `src/data/trends_symbols.py`. Toggle-only (composite unchanged). Phase 2 (US constituents)
  pending; live coverage of EU `.DE` tickers to be validated. *(2026-06-26)*
- ~~Language support: Swedish (UI chrome)~~ — client-side EN⇄SV toggle (English default,
  persisted in `localStorage`) translating the dashboard chrome: tab names, table headers,
  controls, tab-notes, disclaimer. `data-i18n`-tagged elements + a Swedish dictionary in
  the template; help prose, Plotly chart labels, and GICS sector names stay English.
  Template-only. *(2026-06-26)*
- ~~Backtest against past rotations (Phase 1 — edge)~~ — US/EU monthly top-5 rotation
  backtest vs RSP/EXSA.DE; `backtest.py` CLI + committed `backtests/` artifact + dashboard
  Backtest tab (equity curves + metrics). Point-in-time (no look-ahead), price-pillars-only,
  each region scored within its own cohort. Phase 2 (rotation event-study) still pending.
  *(2026-06-26)*
- ~~Sentiment methodology explanation~~ — collapsible "How is the sentiment score
  calculated?" guide in the Data ⇄ Sentiment tab (reuses the `tab-guide` pattern):
  states it's Google Trends search-attention only, the 13-week slope→z-score method,
  and that it doesn't affect the ranking unless the toggle is on. Template-only, no
  pipeline change. *(2026-06-26)*
- ~~Fetch history & per-scan export~~ — dashboard History tab now lists every scan
  (scan index with active-scan marker) with a per-scan report link; `write_report`
  refactored into `build_report_markdown`, per-scan reports generated to
  `docs/reports/report_<scan_id>.md`, and `get_scan_history(n_scans=None)` loads all
  scans. *(2026-06-25, PR #27)*
- ~~Data persistence & sync strategy~~ — migrated from a git-committed SQLite blob to
  Supabase (Postgres) so the DB stays in sync across local dev and CI. *(2026-06-22)*
- ~~Data inventory & coverage statistics~~ — `stats.py` CLI script: scan count + date
  range, cadence gaps, per-region/per-sector coverage, signal NULL rates, table row
  counts. *(2026-06-24)*
- ~~Constituent breadth (Phase 3.1)~~ — true breadth for US sectors: % of each
  sector's S&P 500 constituents (Wikipedia GICS list, fetched with a browser UA)
  above their own 50-DMA, info-only; EU shows "—"; retired the single-ETF proxy.
  *(2026-06-24)*
- ~~Sentiment toggle~~ — dashboard toggle + weight field blends Google Trends sentiment
  into the leaderboard ranking client-side (`rescore.js`); canonical composite stays
  pure-data (`score_all(..., blend_sentiment=False)`). Thin Trends wired into the scan;
  rich Trends tab still pending. *(2026-06-24)*
- ~~Sector view toggle~~ — leaderboard toggle between region-split (22 rows) and
  composite (11 GICS rows, simple mean of US+EU) views; client-side recompute in
  `rescore.js` (`mergeComposite`), composite rows + dual-region breakdown rendered in
  `build.py`, persisted in `localStorage`, default region-split. *(2026-06-25)*
- ~~Test suite could wipe production~~ — hardened the `test_state_smoke.py` wipe guard
  to be identity-based (resolves Supabase project ref, not raw URL string) so a
  prod-equivalent `TEST_DATABASE_URL` can't slip through, plus an `_assert_disposable`
  backstop that refuses to DELETE the live DB. *(2026-06-25)*
- ~~Back up the database on every scan~~ — `src/backup.py` writes a full CSV dump
  (`scans`/`scores`/`signals` + `manifest.json`) to repo-committed `backups/` after each
  scan (non-fatal, `--no-backup`); `restore.py` loads it back (refuses non-empty DB
  unless `--force`); CI commits `backups/`. Git history = the rolling backup set.
  *(2026-06-25)*
- ~~Claude Code `/scan` command~~ — `.claude/commands/scan.md`: runs `scan.py` then
  rebuilds the dashboard, with a concise completion summary. Local-only (`.claude/` is
  gitignored). *(2026-06-25)*
