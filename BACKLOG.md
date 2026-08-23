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

## Restore the sentiment blend control — and make it work when signed in

The "Ranking" cogwheel (`⚙ Ranking`, a `<details>` holding "Include sentiment in
ranking" plus a weight %) blended sentiment into the composite client-side and
re-ranked the board. **Withdrawn 2026-08-14** while sentiment is alpha — see
Done. Restoring it is one flag, `SENTIMENT_RANKING_ENABLED` in
`dashboard/build.py`, but two things should be settled first.

**1. Validate the sentiment pipeline.** This is the blocker and the reason the
control went away. Until there is a reason to believe a positive polarity
reading means something, letting it move the ranking is worse than not showing
it. Nothing below matters until this is answered.

**Measured 2026-08-22 (scans 164–169, 6 clean days, 18 themes, 107 readings).
The answer so far is negative, and it changes what "restoring the control"
should even mean.**

`news_polarity` decomposes into a large fixed per-theme level plus daily noise:

| measurement | result | interpretation |
|---|---|---|
| raw lag-1 autocorrelation | **0.299** (0.734 for themes with ≥50 articles/day) | looks strongly persistent |
| lag-1 after removing each theme's own mean | **−0.245** (−0.083 for ≥50 articles) | the persistence was *not* dynamics |
| pure-noise benchmark for this panel shape (18×6, simulated 20k draws) | mean **−0.200**, 95% range −0.388 to −0.005 | observed values sit at the 33rd and 88th percentile — **inside the noise band** |
| per-theme mean polarity, 138–152 vs 164–169 | **r = 0.707**, ρ = 0.696 (n=17, p ≈ 0.002) | the level is stable across two unrelated news periods |
| polarity vs composite rank | ρ = −0.189, Pearson 0.120 | negligible |

The two periods compared in that fourth row are **non-overlapping and separated
by eleven days of dead pipeline** (2026-07-22→08-05 vs 08-17→08-22). A theme's
average tone survived a complete change of news at r = 0.71. Meanwhile its
day-to-day movement is statistically indistinguishable from random.

**So FinBERT is largely scoring the vocabulary of the topic, not the news.**
Cybersecurity reads −0.095 in the first period and −0.104 in the second while
sitting 4th on the board with a positive composite — security headlines are
made of "breach", "attack", "threat" whatever the sector is doing. Data Centers
(−0.02 → −0.09) and Blockchain (−0.08 → −0.02) behave the same way.

Blending *raw* polarity into the composite would therefore apply a near-permanent
per-theme handicap that has nothing to do with performance — worse than noise,
because it is a **consistent** bias rather than one that averages out.

Two further problems visible in the same data:

- **Coverage is wildly uneven.** Article counts run 2–630/day. Eight of 18
  themes average under 20/day (Shipping 6, Food & Beverage 10, Healthcare
  Providers 10). Their day-to-day sd (0.19–0.29) exceeds the entire
  cross-sectional spread of the well-covered themes (sd ≈ 0.10), so the
  thinnest themes contribute the loudest swings. This is the estimator
  asymmetry the GDELT item flags, now quantified.
- **Predictive power is still unmeasured**, and cannot be measured yet: six
  scan-days give no usable forward-return window. That test needs months, and
  it is the only one that can actually justify the feature.

**Revised recommendation.** Do not restore the control as a raw-polarity blend.
If it is restored at all, the candidate signal is the **deviation from each
theme's own trailing baseline** (a fixed-effects correction that removes the
topical constant), gated on a minimum article count to suppress the thin
themes. That is a different feature from the one that was withdrawn, and it
still requires the forward-return test above before it earns a place in the
ranking. Re-measure once ~3 months of clean daily scans exist.

**2. It never worked signed in, and that is fixable.** `makeLeaderboardReadOnly()`
([`auth.js:147`](dashboard/assets/auth.js)) hides the cogwheel and disables
column sorting for signed-in readers, because the signed-in path replaces the
baked rows with fresh ones from `v_recent_scores`, and the client-side rescore
reads `RESCORE_DATA` — a per-scan history baked at build time and keyed by
`data-sector-key`, which the rebuilt rows do not carry.

But that query already selects what the blend needs:

```
level_score, change_score, data_score, sentiment_score, composite, rank
```

The composite is `(1 − W)·data + W·sentiment`, so the slider's whole job can be
done from the two scores already on each rebuilt row — no baked history required.
It needs `Rescore.rescore()`'s arithmetic sourced from the row instead of from
`RESCORE_DATA`, then a re-rank. Signing in currently trades the slider and
sorting for badges and fresh data, and it need not.

**Sorting is a separate, smaller fix:** `sortTable` groups by
`data-sector-key`, which `renderLatestRows` does not emit. Adding that attribute
is likely all it needs.

**Two things to honour when it returns:**

- **The band cut lines follow the ranking**, so they must be redrawn after any
  re-rank. Already wired on the guest path — `applyRanking()` calls
  `applyBandBoundaries()` after `sortVisibleByRank()` — so the signed-in path
  needs the same call, not a new mechanism.
- **The stored composite must stay pure.** `scan.py` passes
  `blend_sentiment=False` and that should not change: the blend is a reader's
  view, not a change to what is persisted, backtested or alerted on.
  `tests/test_sentiment_alpha_gate.py` pins this.

**Also queued with it:** the leaderboard's Sentiment column is not merely
hidden anymore — the 2026-08-19 6-column restructure (Stage 1 of the
leaderboard redesign, see Done) removed it outright, and `_sentiment.css.j2`'s
old `display:none` block is gone with it. Restoring the column now means
re-adding a `<th>`, a cell in **all three** row-builders (`dashboard/rows.py`,
`renderLatestRows()` in `auth.js`, and the row loop in `scan-history.js`), an
i18n key, and renumbering every `sortTable()`/`data-col` index and `colspan`
that counts columns — meaningfully more work than the one CSS block this used
to be. Also drop the `alpha` badge from the Sentiment nav and page note.

## Sentiment page never upgrades for signed-in readers

Split out 2026-08-22 when the empty-state copy shipped (see Done). The copy
bullet of that item is done; this is what is left, and it is **declined for
now** rather than pending — recorded so the reasoning is not rebuilt from
scratch.

**Corrected in review — the mechanism is not what was first recorded here.**
`sentiment.html.j2` *does* load `auth.js`: it includes `_footer.html.j2`, which
pulls in supabase-client, auth, positions and alert-prefs under `{% if auth %}`.
Sign-in works on this page and it is not byte-identical for every reader. What
is inert is the *data* refresh: `upgradeLeaderboard()` returns early without a
`#leaderboard-table tbody`, and nothing else re-fetches `sentiment_signals`.
So the reader-visible behaviour is unchanged — the board jumps to today, this
page does not — but the fix is "add a fetch", not "add auth to the page".

`build.py` also caps `sentiment_signals_df` at the lagged scan deliberately
(reading the true latest scan leaked the current News table to guests while the
scatter above it stayed capped, so the two surfaces disagreed about "latest").
Same shape as the parked *Signed-in drill-down gap*.

**Why declined:** the 2026-08-22 validation found polarity is largely topical
bias rather than news — roughly 70% of the cross-sectional spread is a fixed
per-theme constant that survives a complete change of news. A surface showing a
signal that may not survive its own validation does not warrant a gated
client-side fetch plus a scatter/table re-render, with the RLS care any
signed-in read needs. The shipped copy fix closes the *confusion* at a fraction
of the cost by naming the lag and saying signing in will not move it.

**Reopen if** sentiment is ever promoted out of alpha, or if the forward-return
test the validation still owes comes back positive.

**The desktop-scan-date bullet shipped separately (2026-08-23)** — see Done.
Only the signed-in fetch remains, above.

## `init_db()`'s DDL has a first-run TOCTOU race, systemic, not worth fixing narrowly

Code review, 2026-08-23 (on the `CREATE INDEX IF NOT EXISTS` statements added
that day): `CREATE INDEX IF NOT EXISTS` is not atomic across concurrent
sessions in Postgres. Two overlapping `init_db()` calls, both racing to create
the same not-yet-existing index, can both pass the existence check and
collide — one succeeds, the other raises a duplicate-relation error that
aborts that call's `init_db()` transaction (rolling back its column adds too,
since everything runs inside one `with conn:` block).

**Not unique to the index statements.** The identical TOCTOU exists for every
`CREATE TABLE IF NOT EXISTS` and `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` in
this same function — 19 statements total, none hardened, some shipped over a
month ago. Guarding only the 3 newest ones would be inconsistent, not a real
fix.

**Narrower in practice than it sounds.** CI's four `init_db()` callers
(`scan.py`, `dashboard/build.py`, `restore.py`,
`scripts/backfill_region_ranks.py`) only collide across `scan.yml` and
`build-docs.yml`, and those two share the `pages-deploy` concurrency group
specifically to serialize them. The real exposure is a human running one of
those scripts locally against production at the exact moment CI is creating a
given object *for the first time* — a window that closes for good the moment
that object exists, typically within one scan of merging.

**Recommendation: leave as-is.** A real fix (retry-on-duplicate-object-error
around every `IF NOT EXISTS` DDL statement, or serializing `init_db()` with an
advisory lock) is systemic hardening, not a three-line addition, and the
window it closes is a same-day, self-healing one. Worth doing in one pass if
`init_db()` is ever revisited for another reason — not on its own.

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

## Rescore path flattens the Trend badge to a bare glyph

Found while re-measuring the 2026-08-09 audit's badge/trend P1 (see Done,
2026-08-22). **Dormant, not live** — recorded so it isn't rediscovered from
scratch.

`rescoreRows()` in `dashboard/templates/index.html.j2` does:

```js
if (traj) { traj.className = "traj-badge traj-" + r.trajectory_state; traj.textContent = r.trajectory_label; }
```

`textContent` replaces the badge's children, destroying the
`<span class="traj-glyph">` / `<span class="traj-word">` pair every other
builder emits. The badge survives as a bare `↑` with no word — which is
exactly the "9.84px arrow" the design audit originally complained about, and
now also strips the tooltip and the word from a badge sitting beside the
theme name.

It cannot fire today: the only caller is the sentiment blend control, which
was withdrawn 2026-08-14. It becomes live the moment that control is restored
— and *that* is a queued item too ("Restore the sentiment blend control"), so
whoever picks that up should fix this in the same branch rather than treat it
as separate work.

The fix is to write the two spans instead of `textContent`, matching
`auth.js`'s `trendInner`. Worth extracting a shared `trajBadgeHTML(state,
label, word)` at that point — there would then be four builders emitting the
same markup, which is what let this one drift unnoticed in the first place.

## GDELT source alternatives — Web NGrams and BigQuery

Recorded 2026-08-16 when the bulk GKG feed shipped (see Done). Both were
researched and deliberately left out of scope; neither is needed unless GKG
coverage proves insufficient. Written down so the question is not re-opened
from scratch.

**The cheap lever (widening `gdelt_keywords`) was tried 2026-08-17 — see
Done.** It fixed AgTech & Food Innovation outright (0 → 36 headlines on the
bulk pass alone) but only partially helped Shipping (1 → 3, still below
`MIN_ARTICLES = 5`). Shipping's remaining lever is the DOC API fallback,
which reads the same widened keyword list but was not re-measured live to
avoid re-triggering the stateful rate limiter the bulk feed exists to avoid.
If a scan shows Shipping still thin after the fallback runs, it may simply be
a structurally thin news day for dry-bulk freight rather than a keyword gap —
check `news_count` in the sentiment_signals table before reaching for
BigQuery.

- **Web NGrams 3.0** — GDELT's own recommendation for high-volume users, and
  the one they point at in the 429 body. Ruled out on a data-model mismatch,
  not on effort: it is word-level frequency data, not article headlines, so
  it cannot feed FinBERT headline scoring without redesigning what the
  sentiment signal *is*. Only worth revisiting alongside a decision to change
  the signal itself.
- **BigQuery** (`gdelt-bq.gdeltv2.gkg`) — likely the *best* data quality of
  the three: full-text matching in one SQL query, rather than our local
  match against title + GKG themes/orgs/names. Costs a GCP dependency, a new
  CI secret, and a free-tier quota (1 TB/month) to manage. This is the option
  to reach for if keyword-widening fails and coverage still matters.

## Deferred polish from the GDELT bulk-fetch reviews (2026-08-16)

Minor findings from the per-task and whole-branch reviews of the bulk-fetch
work, each triaged "fine to defer" with a reason. None affect correctness.
Recorded so they are not rediscovered from scratch.

**Three of six acted on 2026-08-17** — see Done: the partial-slice-failure
warning, the dead-code removal, and the http/https check (confirmed *not*
free — see Done for why).

What remains:

- **`gdelt_gkg.py`** — the "no slices could be read" warning asserts caller
  behaviour ("falling back to the API") that belongs to the orchestrator.
  Accurate for the only production caller; misleading only if the bulk
  function is called directly.
- **`gdelt_gkg.py`** — the whole 24h corpus (~50k records, each carrying the
  large themes/orgs/names columns) accumulates in memory before matching, and
  only `title` survives. Fine at `hours=24`; scales linearly, so a wider
  window would want per-slice matching inside the download loop.
- **Estimator asymmetry between the two paths.** Bulk themes are sampled by
  local matching against title + metadata with no cap; fallback themes get
  ≤250 titles from the DOC API's full-text match. `zscore_polarity`
  cross-sections across both, so fallback themes may sit nearer the tails for
  sampling reasons rather than sentiment reasons — and *which* themes those
  are varies daily. Not acted on: sentiment is alpha, excluded from the
  composite and the ranking, and `news_count` is persisted per theme so the
  asymmetry is observable in the data. The measured totals also make the
  practical impact small (1140 headlines across 18 themes; only 2 themes
  above 250). Revisit if sentiment is ever promoted out of alpha — this
  would matter then.

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

## Nothing prunes the backup bucket

Found in the 2026-08-23 sweep. `backup_to_storage` (`src/backup.py:192`) uploads
a full DB zip to `db-backups` before **every** scan, and nothing ever deletes
one — `src/storage_backup.py` has `upload`, `download` and `list_objects` but no
`delete` at all.

The free tier is 1 GB of Storage. Growth is worse than linear: every object is a
complete dump, so each daily zip is slightly bigger than the last. Nothing
warns as it fills, and the failure mode is a failed *upload*, which
`scan.py` deliberately swallows as non-fatal — so the first symptom would be
backups quietly stopping while scans keep succeeding.

**Not measured.** `SUPABASE_SERVICE_KEY` is CI-only, so the sweep could not list
the bucket; the ceiling is inferred from the code, not observed. Measure first —
`list_objects` already returns everything needed.

Fix: a retention sweep after a successful upload (keep last ~14 daily, then one
per week for ~8 weeks, then one per month), plus `delete` in the storage client.
Prune *after* the new upload succeeds, never before — the whole point is that a
backup exists at every instant.

## Three of seven weekly scans are now byte-identical

Found in the 2026-08-23 sweep, measured against production. **Not a correctness
bug** — waste and history noise.

`_cache_is_fresh` requires the cache to reach
`_expected_latest_close(date.today() - 1 day)`. That resolves to **Friday for
Saturday, Sunday *and* Monday** runs, so all three score identical prices:

| run day | `today - 1` | required bar |
|---|---|---|
| Sat | Fri | **Fri** |
| Sun | Sat | **Fri** |
| Mon | Sun | **Fri** |
| Tue | Mon | Mon |

This is *correct* — the 06:00 UTC cron fires before the US close, so a Monday
morning scan genuinely has no bar newer than Friday's. But each duplicate still
writes a full row set (18 scores + ~250 signals), uploads a full backup zip, and
burns Actions minutes.

Verified: scans 168 (Fri 08-21) and 169 (Sat 08-22) are identical on **54/54**
theme×signal raw values. Scan 169 ran ~14h before the weekend-staleness fix
merged, so it reproduced the old two-duplicate pattern; post-fix the count goes
to three, because Saturday now correctly picks up Friday's close where Monday
used to be the first scan to see it.

**Sentiment is the complication.** GDELT is fetched fresh every run, so
`sentiment_signals` rows genuinely differ on all three days. A blanket "skip the
scan" would lose that history. The honest options are (a) skip only the
price/scoring persist and still write sentiment, (b) skip the pre-run backup
when the as-of is unchanged — the cheapest single win — or (c) leave the rows
alone and just *surface* it, so three scans sharing one market date read as one.

Newly cheap to detect: `prices_asof` has been persisted on `scans` since
2026-08-23, so "same market date as the previous scan" is one column comparison
rather than a re-derivation.

## 24% of `index.html` is duplicated Plotly `template` JSON

Found in the 2026-08-23 sweep. Every serialized figure carries Plotly's full
default-styling `template` object — **26 copies**, describing defaults for
`choropleth`, `scatter3d`, `mesh3d`, `parcoords`, `scatterternary` and 20 other
trace types the project never draws. Only `scatter`, `bar` and `heatmap` are
real (`go.Scatter` ×8, `go.Bar` ×1, `go.Heatmap` ×1 across `dashboard/`).

| variable | serialized | of which `template` |
|---|---:|---:|
| `COHORT_CHARTS` | 240,125 | 152,283 (63%) — 187 KB of it is the 18 drill-down figures |
| `BACKTEST_DATA` | 45,887 | 13,242 (29%) |
| `CORRELATION_DATA` | 15,036 | 6,621 (44%) |

**Measured honestly, the wire saving is small.** Stripping every `template`
takes `index.html` from 796,775 → 609,206 bytes raw, but gzipped only
114,141 → 108,796 — **5.2 KB, under 5%**. gzip already dedupes near-identical
blobs well. Recorded with the number attached precisely so this is not
re-discovered and over-sold as a bandwidth win.

The real payoff is parse time and mobile memory: 188 KB less JSON for the
browser to parse before the first chart draws. Fix is setting the figure's
`layout.template` to `None` before serialization in `dashboard/figures.py`;
Plotly.js falls back to its built-in default, which is what the template
was restating. Verify the charts still look right in both themes — pairs
naturally with the Plotly major bump above, which needs the same eyeball.

## Holding-period panel is denominated in scans

`validation._holding_stats` measures top-5 run lengths in scan-index units and
`_validation.html.j2` labels them honestly — *"Duration of contiguous top-5 rank
streaks (in scans)"*. The label is not wrong; the **unit** is the problem.

A scan is not a time the reader can act on, and it is not even a constant
interval: seven scans a week against five market days, and — see *Three of seven
weekly scans are now byte-identical* above — three of those seven now share one
market date. So "median 12" is roughly 8.5 calendar days, roughly 6 market days,
and the ratio drifts whenever the cron or the cache rule changes. The decision
this number feeds is a monthly rebalance cadence, which it cannot be compared to.

Fix: dedupe runs by distinct market date and report market days. `prices_asof`
on `scans` (persisted since 2026-08-23) is exactly the column that makes this
possible without re-deriving dates from `run_at`.

## Health panel has no signal for themes missing for reasons other than a stale as-of drop

Code review, 2026-08-23, on the `asof_dropped_count` PR (see Done). Recorded,
**not obviously worth acting on yet** — a real gap, but the fix is a design
question, not a bug.

`sectors_produced < sectors_expected` (the coverage badge) can happen for at
least three independent reasons, and the health panel now names only one of
them:

1. the ticker fetch failed outright — tracked (`prices_failed`)
2. `align_cohort_asof` dropped it for lagging the cohort's as-of date —
   tracked as of 2026-08-23 (`asof_dropped_count`)
3. `compute_signals_for_sector` (`src/pipeline.py`) returns `None` for a
   ticker that IS present in `prices` — e.g. insufficient history for a
   signal calculator — **not tracked anywhere**

A scan where 2 themes vanish for reason 3 shows coverage red/amber with both
`prices_failed` and `asof_dropped_count` reading a healthy 0 — a reader has no
way to tell why, and the panel's own honest badges actively point away from
the real cause.

**The naive fix — a fourth single-purpose column for reason 3 — is exactly the
pattern this note exists to interrupt.** Each of the three causes so far
became its own dedicated `scans` column, badge, and footer line, requiring the
same 4-file edit (`scan.py`, `_HEALTH_COLUMNS`, `dashboard/health.py`,
`_footer.html.j2`). A fourth cause would make it four copies of that edit; a
fifth would make five. The design question worth answering before adding
reason 3 (or the next one after it) is whether to keep doing that, or
converge on one extensible mechanism — e.g. a small `dropped_themes` JSON/text
column recording `{ticker: reason}` pairs, with one badge and one footer line
reading from it regardless of how many reasons exist. That is a real design
decision (schema shape, how much detail to surface, whether reason 1 and 2
should migrate into it too) rather than a three-line addition, so it belongs
in front of brainstorming, not skipped straight to implementation.

## Feature: UCITS tracking-difference monitor

`config/themes.yaml` records the closest UCITS equivalent per theme — ticker,
ISIN, TER, issuer, and a `match` quality of `exact` / `close` / `partial` — but
nothing ever measures whether that equivalent actually *tracks* the US listing
being scored.

That gap is the difference between what the board ranks and what can be bought
on Avanza. A `partial` match can diverge by several points a year through
different index construction, currency hedging, or a thinner basket — enough to
consume whatever edge the ranking finds — and one theme (Shipping) has no
equivalent at all, which the board already flags as unbuyable.

The measurement is cheap because the pipeline already fetches prices: pull the
UCITS ticker alongside the US one, and report realized return difference over
3m/6m/1y per theme, grouped by `match` quality. Two things fall out of it:

- an empirical check on the `match` labels, which are currently hand-assigned
  judgement rather than measured
- an honest per-theme haircut to apply when reading the backtest, which replays
  US listings the reader cannot buy

Open question before building: many UCITS lines are thinly traded and
EUR/SEK-denominated, so the comparison needs a currency decision (compare in
each listing's own currency, or convert both to one) before the number means
anything. That is a real design choice, not a detail — worth settling first.

## Feature: rank-based cross-sectional standardization

`zscore_cross_section` standardizes each signal with `mean` and `std(ddof=1)`
across **18 themes**. Both estimators are noisy at n=18 and neither is robust: a
single extreme theme inflates the std and compresses every other theme toward
zero, so one outlier quietly flattens the spread the composite is built to read.
`|z|` is bounded at √17 ≈ 4.12 regardless, which is its own distortion — the tail
is clipped by sample size rather than by any deliberate choice.

Rank-based (normal-score) standardization — rank cross-sectionally, map ranks
through the inverse normal CDF — is invariant to outliers and produces the same
distribution shape every scan.

This is a **different axis** from *Composite structure — 4.2 effective signals
of 8* above. That item is about redundancy *between* signals; this is about the
estimator applied *to* each one. Neither answers the other.

Testable with the harness that already exists: `scripts/horizon_sweep.py` and
`backtest.py` both drive the scoring pipeline as-of historical dates, and
`score_all` already accepts alternative signal lists, so an alternative
standardizer is the same kind of A/B. Judge it on the two independent windows
the horizon sweep already uses, at the configured `round_trip_bps` — sweeping at
0 bps is how the pre-2026-08-09 presets got picked.

Do not ship on a single-window improvement.

## Feature: prove the backups restore, not just that they upload

`tests/test_backup_drill.py` exercises the dump/restore round trip against a
local fixture, and `test.yml` already stands up a throwaway `postgres:17`
service container. What nothing checks is whether the **actual newest object in
the `db-backups` bucket** can be restored.

That is the failure mode backups have. An upload that returns 200 proves the
bytes left the machine; it does not prove the zip has all its members, that the
CSV columns still match a schema that has gained four columns since
(`text_value`, `prices_asof`, `asof_spread_days`, …), or that
`restore_from_storage` still works after `_ARCHIVE_MEMBERS` drifted.

Fix: a monthly scheduled workflow that downloads the newest bucket object and
restores it into the service container, asserting row counts per table. It needs
`SUPABASE_SERVICE_KEY` (already a repo secret) and must **only ever** point
`DATABASE_URL` at the throwaway container — the wipe guard in
`tests/test_state_smoke.py::_same_database` exists because production was wiped
on 2026-06-25, and a restore drill is precisely the shape of job that could do
it again.

## Small cleanups from the 2026-08-23 sweep

Grouped because none of them individually justifies a PR; take them alongside
whatever else touches the same file.

- **`_modal.js.j2` is inlined three times** in `index.html` (~12 KB total). The
  `window.SMModal = window.SMModal || (...)` guard makes it idempotent, so this
  is pure duplication, not a bug — but it is three copies of a focus-trap
  implementation that must not drift.
- **Unescaped `innerHTML` interpolation.** `auth.js:241` and
  `scan-digest.js:93` build rows by string concatenation, interpolating
  `r.gics_sector` without escaping. **Not exploitable** — theme names come from
  `config/themes.yaml` via the pipeline, so the data is repo-controlled — but it
  is the kind of thing that becomes exploitable the day any of it comes from
  somewhere else. Hardening only.
- **Each price parquet is read twice per scan.** `_cache_is_fresh` does a full
  `pd.read_parquet` to inspect the index, then `fetch_prices` immediately reads
  the same file again on a cache hit. 20 tickers × 2 reads. Trivially fixed by
  having `_cache_is_fresh` return the frame it already loaded.

**Swept and found clean** (recorded so the sweep is not repeated): no
`TODO`/`FIXME`/`XXX`/`HACK` anywhere in `src/`, `dashboard/` or `scan.py`; no
`datetime.utcnow()`; no mutable default arguments; no SQL injection surface —
every f-string in a query interpolates an internal constant (`_COLUMNS`,
`_HEALTH_COLUMNS`, `_SCAN_CHILD_TABLES`) with all values parameterized.

**RLS verified live** against production with the published anon key on
2026-08-23: `scans`, `scores`, `signals`, `sentiment_signals`, `positions`,
`alert_prefs`, `v_latest_scores` and `v_recent_scores` **all return
`401 / 42501`** to `anon`. The 7-day content gate cannot be walked around by
querying the Data API directly — it holds at the database, not only in the baked
HTML.

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

---

# Done

## Persisted align_cohort_asof's dropped-theme count to health (2026-08-23)

`align_cohort_asof` already computed `asof_dropped` (the sorted list of
tickers it drops for lagging the cohort's modal as-of date by more than
`MAX_ASOF_LAG_DAYS`) into its `stats_out` dict, but `scan.py` never carried it
into the persisted `_health` dict — only `asof`/`asof_spread_days` made it in
earlier the same day. A dropped ticker removes its whole theme from the run;
the 80% coverage guard in `scan.py` tolerates up to 3 of 18 themes vanishing
that way and still shipping the scan, with nothing on the health panel saying
so. `prices_failed` doesn't cover it — a drop is a stale cache, not a fetch
failure, the same distinction `scan.py`'s `_prices_fetched` comment already
draws for the coverage guard itself.

Same shape as `prices_asof`/`asof_spread_days`: a self-migrating `scans`
column (`asof_dropped_count INTEGER`), threaded through the single shared
`_HEALTH_COLUMNS` list so `init_db()`'s ALTER loop, `save_scan`'s INSERT, and
both `get_latest_health`/`get_health_for_scan`'s SELECTs stay in sync by
construction. New badge in `dashboard/health.py` — green at 0, red for any
drop, deliberately **no amber tier** (unlike `prices`): a drop isn't a fetch
failure with a fuzzy severity, it's `align_cohort_asof` deciding a theme's
price series is too stale to score at all, which is binary. Rendered in the
Themes row of the health footer, next to the existing coverage badge.

11 tests added across `tests/test_scan_smoke.py` (the `_persist_scan` health
dict, including a dedicated test that a real dropped-ticker *list* persists
as its *count* — the scans column is `INTEGER`, not the list),
`tests/test_state_health.py` (write→read round trip with a real value, plus
the shared column-list tests inherited it for free), and `tests/test_health.py`
(badge logic, `build_health_context` wiring, and a real Jinja render). All
four layers sabotage-verified (scan.py never sets the key, the column dropped
from the shared list, the badge hardcoded green, the footer line removed) —
each caught by a different test.

Verified live against the real built page: an old scan (predating this
column) renders `badge-unknown` in the Themes row — not a false green, not a
crash.

**Full-branch code review (8 finder angles) caught two real issues before
merge, both fixed:**

- **The footer's fallback read a genuinely-unknown value the same as a
  confirmed healthy zero.** The first draft used `{{ health.asof_dropped_count
  or 0 }}`, so an old scan row (predating this column, value `None`) rendered
  "0 dropped" — a reader skimming the number rather than the grey badge color
  reads that as a clean bill of health, exactly the false-green trap this same
  file's `finbert` badge comment already warns against. Now
  `{{ ... if ... is not none else '?' }}`, matching the coverage badge's own
  `'?'` convention for the identical state one line above it in the same row.
  Safe specifically because `_health_row_from_df` (shared by
  `get_latest_health`/`get_health_for_scan`) always includes this key —
  never Jinja `Undefined` on the real render path, which is what made
  `prices_asof`'s own guard (right above it) avoid the same `is not none`
  form for a different, older reason.
- **`tests/test_health.py` copy-pasted its Jinja-render harness.** The new
  `TestAsofDroppedDisplay` duplicated `TestPricesAsofDisplay`'s `_render`/
  `_base_health` verbatim — its own docstring said "mirrors
  TestPricesAsofDisplay" without acting on it. Factored into a shared
  `_FooterRenderHelper` base class both now inherit.

Two redundant assertions were also trimmed (one in `test_scan_smoke.py`, one
in `test_health.py`) that re-proved a fallback branch or a generic `any()`
fold already covered elsewhere — flagged by the review's simplification
angle.

**One review finding recorded rather than fixed here** — see *Health panel
has no signal for themes missing for reasons other than a stale as-of drop*,
below: a real scope question (a general "why did this theme not produce a
signal" mechanism vs. one column per cause), not a defect in what shipped.

**One review finding declined**: no structural/typed link ties `align_cohort_asof`'s
`stats_out` key names to what `scan.py` reads and what `_HEALTH_COLUMNS`
expects — correctness rests on the test suite, not the type system. Real, but
identical to the pre-existing exposure on `prices_asof`/`asof_spread_days`
(shipped the same day, same pattern, no structural link either) — hardening
just the newest field would be inconsistent altitude, not a real fix.

## Added scope="col" to every <th> — WCAG 1.3.1 (2026-08-23)

Found live in a browser 2026-08-23: `#leaderboard-table` alone had 107 `<th>`
elements, zero with a `scope` attribute; the nested drill-down instruments
table (which sits *inside* leaderboard rows), the scan-index, backtest, and
badge-scorecard tables on `index.html.j2`, both tables on `_validation.html.j2`
(forward-return and holding-period), and the News table on `sentiment.html.j2`
were all the same.

**One correction to how the item was originally scoped:** it named `auth.js` /
`scan-history.js` as row builders needing the fix. Checked before touching
them — neither emits a `<th>` at all. Both only ever rebuild `<tbody>` rows
(the signed-in live-upgrade path and the History tab's client-side render);
every `<thead>` on both pages is baked once by Jinja and never rebuilt
client-side. The actual fix touched four Jinja templates
(`index.html.j2` ×4 tables, `_validation.html.j2` ×2, `sentiment.html.j2` ×1)
and one Python HTML builder (`dashboard/breakdown.py`'s
`_build_instruments_html`, which is not reachable by resolving `{% include %}`
directives — it's injected as a pre-rendered string via `row.breakdown_html`,
not a Jinja include, so it needed its own test).

3 tests added to `tests/test_a11y_landmarks.py`. Sabotage-verified against 4
mutations (one leaderboard `<th>` unscoped, the include-only-reachable
validation table, the Python-built breakdown table, the sentiment page) — all
caught. Verified live in a rebuilt browser: 239 `<th>` on the leaderboard page,
0 unscoped; clicking a sortable header still re-sorts correctly (scope has no
effect on `auth.js`'s `thead th[onclick]` selector, confirmed by clicking one
and watching rank order reverse).

The rest of the 2026-08-23 a11y sweep stays **clean** — no missing `alt`, no
control without an accessible name, no duplicate `id`, and
`documentElement.lang` correctly flips to `sv` on the language toggle.

## Indexed signals/scores/sentiment_signals on (scan_id, region) (2026-08-23)

Confirmed live against production 2026-08-23 that these three tables — the
only ones every scan-scoped read/write touches — had zero non-primary-key
indexes: `pg_indexes` for schema `public` returned exactly four rows, all PKs
(`alert_prefs_pkey`, `alert_prefs_ntfy_topic_key`, `positions_pkey`,
`scans_pkey`). Postgres does not auto-index a foreign-key column, so the
same-UTC-day replace's `DELETE FROM signals WHERE scan_id IN (...)` and every
`WHERE scan_id = %s AND region = ...` read in `dashboard/build.py` were
seq-scanning `signals` (21k rows / 1.5 MB, growing ~250 rows/day) on every
scan and every build.

Three `CREATE INDEX IF NOT EXISTS ... (scan_id, region)` statements added to
`init_db()`'s existing self-migrating loop — the same idempotent pattern
already used for every `scans` health column. The composite covers both real
query shapes via the leftmost-prefix rule: the scan_id-only `DELETE`, and the
scan_id+region reads.

4 tests added: 2 mocked-cursor (SQL text + idempotency, mirroring
`test_init_db_adds_health_columns`), sabotage-verified against 4 mutations
(loop removed, `IF NOT EXISTS` dropped, region dropped from the index, only
one of three tables indexed — all caught); 1 real end-to-end test querying
`pg_indexes` directly against the throwaway test database via the existing
`db_conn` fixture, which already calls the real `init_db()` — no mocking. No
local Postgres was available to run it by hand; it runs for real in CI's
`postgres:17` service container.

## RRG tab rendered an empty chart for every guest (2026-08-23)

PR #242 (the Plotly bundle bump) merged first and, as flagged when this branch
was opened, its Done entry's mention of this bug had queued a section for it
without fixing it. Merging `main` into this branch reproduced exactly the
"item in both Queued and Done" symptom `/backlog-sync` exists to catch — no
conflict markers (BACKLOG.md's `merge=union` driver combined both additions
silently), caught instead by the drift check CLAUDE.md prescribes after any
merge touching this file. The now-redundant Queued section was deleted as
part of this merge.

`dashboard/build.py`'s content-gating block re-caps every per-scan data source
to the lagged scan a guest actually sees. `all_scores_df` and `history_df` are
fetched with `n_scans=20` — comfortably wider than the ~7-scan lag `LAG_DAYS=7`
produces against daily scans — so filtering them to `scan_id <= lb_scan_id`
still leaves rows. `rrg_df` was fetched with `n_scans=6`, **narrower** than the
lag, so the same filter discarded every row every time. Confirmed live:
`get_rrg_history(conn)` returned 108 healthy rows (6 scans × 18 themes, zero
NaN in `rs_ratio`/`rs_momentum`) — the data was fine, it was fetched as the
wrong 6 scans and then discarded.

This was the steady state, not an edge case — the RRG tab has rendered empty
for every guest since content gating and the 6-scan RRG window first
coexisted, silently.

**Fix:** `_recent_scan_filter` (src/state.py) gained an `end_scan_id`
parameter that anchors "last n_scans" to end AT a given scan rather than at
the true newest one — `WHERE scan_id <= %s ORDER BY scan_id DESC LIMIT %s`
instead of the unbounded `ORDER BY ... LIMIT %s`. `get_rrg_history` (and its
`get_theme_rrg_history` wrapper) expose it. The gating block in `build.py` now
re-fetches `rrg_df = get_rrg_history(conn, n_scans=6, end_scan_id=lb_scan_id)`
instead of filtering the unrelated newest-6 window — the same "re-fetch
anchored at the rendered scan" shape as the `signals_df`/`sentiment_signals_df`/
`health_row` re-fetches immediately above it in the same block.

7 tests added across `tests/test_theme_state.py` (SQL/param shape of
`end_scan_id`, including a direct unit test on `_recent_scan_filter` pinning
the `(end_scan_id, n_scans)` param order — a swap silently caps the scan COUNT
at the scan ID and vice versa, invisible to a query-text-only assertion) and
`tests/test_health.py` (source-scan of the gating block itself, mirroring the
existing `TestHealthPanelGating` pattern for the same class of leak). Five
sabotages confirmed caught: filter-only revert (the original bug),
re-fetch-without-`end_scan_id`, call-but-assign-elsewhere, import removed, and
params swapped inside `_recent_scan_filter`.

Verified against the real database, not just source-scanned: rebuilt the gated
page and confirmed `COHORT_CHARTS.THEME.rrg.data` went from 0 traces to 19 (18
themes + benchmark), 6 points each; a real browser screenshot shows the RRG
scatter fully populated with quadrant labels. Also rebuilt with
`SUPABASE_PUBLISHABLE_KEY` unset (the ungated/local path) to confirm it was
already correct and stays unaffected — 19 traces, 6 points, same as before.
## Plotly bumped 2.27.0 → 3.7.0; vendored bundles now verified by SHA-256 (2026-08-23)

The served plotly.js had drifted **two majors** behind the plotly.py writing the
figure JSON — `plotly-cartesian-2.27.0` (©2023) against plotly.py 6.9.0, whose
`get_plotlyjs_version()` reports 3.7.0. Nothing errored, because an older
runtime silently ignores attributes it does not know; the skew was invisible
until measured.

Shipped together, since they share the same ~40 lines and the bump invalidates
any recorded digest:

- **`PLOTLY_CDN` → `plotly-cartesian-3.7.0.min.js`**, with
  `test_plotly_cdn_version_matches_the_installed_plotly_py` pinning the two in
  step — the next `plotly` bump in `requirements.lock` now fails a test instead
  of drifting again.
- **`_ensure_plotly_bundle` / `_ensure_supabase_bundle` collapsed into one
  `_ensure_bundle(..., required=)`**, keyed on **content hash, not file
  existence**. Pinning a version pins a URL, not an artifact; these bytes are
  served to readers, and `supabase.min.js` runs with access to a signed-in
  reader's auth session. A digest mismatch is fatal for *both* bundles and the
  bytes are never written to disk — caching them would serve them on the next
  build before the check could run again. `required=False` still softens a
  download *failure* (no login beats no dashboard), which is a different
  condition from a substitution.

Hash-keying is also what makes a bump *land*. `dashboard/assets/` is gitignored,
so the old existence check meant any machine holding 2.27.0 kept serving it
forever after the constant moved — the bump would have taken effect in CI (cold
cache every run) and on no developer's laptop. Confirmed on the real rebuild:
the stale bundle was detected and replaced automatically.

**Verified in a browser, both themes, 1400×1000** (a collapsed viewport makes
Plotly size charts to 2px and emit `<text> attribute y: "-Infinity"` — an
artifact that cost some time to rule out):

| chart | on 3.7.0 |
|---|---|
| Drill-down | 5 traces, 5 lines + 65 points |
| Movers | 18 bars |
| History | 20 traces, 20 lines + 239 points |
| Backtest | 2 lines, legend and axes intact |
| Correlation | 18×18 heatmap, colorbar, both axes labelled |

Dark/light re-theming round-trips (`paper` `#F5F0E6` ↔ `#2A2619`, trace
`#5A6F49` ↔ `#A9C48E`, all 20 history traces surviving the `newPlot` re-theme),
and a clean tab sweeping all six chart tabs in both themes logs **zero console
errors**.

Two things were A/B'd against 2.27.0 on an otherwise byte-identical page rather
than assumed:

- the **correlation heatmap's narrow aspect** (304×304 inside a 1296px svg) is
  **pixel-identical** on both bundles — pre-existing layout, not a 3.x default
  change;
- the **empty RRG tab** is empty on both — a real bug, but a pre-existing data
  one. Logged as its own Queued item rather than folded into this PR.

**12 test cases added** — 2 static in `test_build_assets.py`, 10 in the new
`tests/test_bundle_integrity.py`. Sabotage-verified: existence-check-instead-of-hash,
write-before-verify, mismatch-tolerated-when-not-required, version-reverted, and
plotly-wired-to-the-supabase-digest are each caught by the intended test.

**Review caught a real gap in that set.** The original sabotage list claimed
"digests-swapped", but only the *call site* had been sabotaged; swapping the two
constants' **values** moved both sides of every assertion together and left
12/12 green. Nothing tied a pinned digest to the bytes its own URL serves.
Closed by two tests reading the vendored artifact itself —
`test_pinned_digest_is_the_digest_of_the_vendored_artifact` (parametrized over
both bundles) and `test_vendored_plotly_is_the_version_plotly_cdn_names`, which
chains URL → digest → bytes so a self-consistent but wrong pin cannot read as
healthy. Both skip when the artifact is absent (clean checkout), where
`_ensure_bundle` fails the build loudly anyway. Re-running the reviewer's exact
sabotage now fails both parametrized cases.

- **The price as-of date is now persisted, and shown in the health panel** (2026-08-23).

  Closes the last remaining bullet of the "As-of alignment — remaining
  consumers and observability" Parked item, which is deleted with this change.

  `align_cohort_asof` (`src/data/prices.py`) already computed `asof` and
  `asof_spread_days` into its `stats_out` dict, and `scan.py` already read
  that into a local `_price_stats` dict — but never carried the two values
  into the `_health` dict that `save_scan` actually persists. "Which date was
  this snapshot scored on?" was answerable only from a scan's log line, or —
  how the weekend cache-staleness bug (Done, 2026-08-22) was actually found —
  by hand-diffing the price cache against `date.today()`.

  Not a Supabase migration: this repo's convention for a new `scans` column is
  entirely code-side. `init_db()` runs an idempotent `ALTER TABLE ... ADD
  COLUMN IF NOT EXISTS` for every health column on each scan/build run, so
  adding `prices_asof DATE` and `asof_spread_days INTEGER` to that list is the
  whole migration — no CLI, no view, no RLS surface, since `get_latest_health`
  reads `scans` directly over the same privileged connection that writes it.

  The health panel's existing "Prices" row now appends `· as of 2026-08-06 (2d
  spread)` when the column is populated — verified live by simulating the
  fragment client-side, since the DB's current latest scan predates this
  column and legitimately has nothing to show yet (confirmed: no literal
  "None" leaks, the row degrades to its pre-existing three fields). Guarded on
  `health.prices_asof` truthiness rather than `is not none`, deliberately: old
  scan rows carry the column with a null value, not a missing key, and Jinja's
  Undefined is falsy the same way None is — so the truthiness check is the one
  guard that is correct whether the key is null or genuinely absent.

  **Deliberately not built: a colored staleness badge.** The backlog text said
  persisting this "would have made the weekend bug visible in the health
  panel" — a bare informational value already delivers that (a reader on a
  Saturday scan seeing "as of Thursday" is the same mismatch caught earlier by
  hand-querying SQL). Green/amber/red thresholds and whether weekends need
  special-casing are a real design decision the backlog text never asked for;
  shipped the plain value and left the badge as a follow-on rather than
  silently inventing thresholds.

  **Code review's own sabotage-verification caught a real coverage gap.**
  Removing the two columns from `get_latest_health`'s `_health_cols` list left
  every existing test in `test_state_health.py` green, because every test that
  exercises that function mocks `_read_sql` wholesale and hands back an
  already-complete row — none of them inspect the constructed SQL text, so a
  column silently dropped from the SELECT (which a real DB would then simply
  never return) had no test watching for it. Added a test that reads the
  actual query `_read_sql` was called with, mirroring how
  `test_init_db_adds_health_columns` already checks `init_db`'s executed SQL
  rather than trusting a mocked return value.

  Also added: a `_persist_scan` unit test (scan.py), which had zero coverage
  at that granularity before this change.

  **Code review found a real content-gating leak this change would have made
  worse, and it's fixed.** `dashboard/build.py`'s lag-gating block re-caps
  every other per-scan data source (`all_scores_df`, `history_df`, `rrg_df`,
  `signals_df`, `sentiment_signals_df`) to the lagged scan a guest actually
  sees — `health_row` was the one source never touched by it.
  `get_latest_health(conn)` ran once, unconditionally, before the gate, so a
  guest's health panel showed the **true** latest scan's `run_at` regardless
  of the 7-day lag — and, once this branch added `prices_asof`/
  `asof_spread_days`, would have leaked the true price as-of date too. Same
  class of leak the `sentiment_signals_df` re-fetch already exists to
  prevent, with an explicit comment saying so — the health panel was missed
  when that fix landed.

  Fixed with a new `get_health_for_scan(conn, scan_id)`, mirroring the
  existing `get_signals_for_scan`/`get_sentiment_signals_for_scan` pattern,
  called from inside the gating block. **Verified live, both directions**:
  reverted the fix, rebuilt with gating active (auth configured locally) —
  the panel showed `2026-08-23T07:04:01` (today's true latest scan, scan_id
  170); restored the fix, rebuilt again — the panel showed
  `2026-08-16T08:09:22` (scan_id 163, the lagged scan `apply_leaderboard_lag`
  actually selected for this build). Confirmed in the browser as well as the
  built HTML.

  Review's second finding — the three-way hand-duplicated `_health_cols` list
  across `init_db`, `save_scan` and `get_latest_health` — is fixed in the
  same change: extracted to a single module-level `_HEALTH_COLUMNS`, shared
  by `save_scan`, `get_latest_health` and the new `get_health_for_scan`
  (`init_db`'s own DDL loop keeps separate `(name, type)` pairs, since a
  plain name list can't carry the SQL type and the two lists can't
  meaningfully diverge — `init_db` only ever *adds* columns). A new test
  reads the actual SQL `get_health_for_scan` sends, asserting it requests
  every name in `_HEALTH_COLUMNS`, so the two functions can't silently drift
  apart on which columns they read.

- **Desktop now shows the scan id/date on the Sentiment page** (2026-08-23).

  Split off the "Sentiment page pinned to lag" item once its copy-fix half
  shipped (2026-08-22): the empty state named the snapshot, but the *populated*
  state — sentiment actually showing — still didn't, on desktop. There was no
  desktop element for it at all: `index.html.j2` gets its date from the Stage 4
  summary strip (`.strip-subline`), but `sentiment.html.j2` has no strip of its
  own, and the shared header's own comment claiming "the desktop meta-cluster
  chips already carry the scan id/date" was stale — they carry SPY/VIX only.

  Added a `.desktop-scan-meta` span to `_header.html.j2`, guarded on
  `active_segment != "sectors" and scan_date` — the same guard, same place, and
  same reasoning as the market-context chips the Stage 4 review restored to
  this header for the identical reason (the sentiment page sharing a header but
  not a strip).

  **The obvious move — reuse `.scan-meta` — was the trap.** That class already
  exists (the auth email label's style) and is hidden at `max-width: 420px`.
  Reusing it here would have shown the scan date TWICE between 421px and
  600px: once from `.mobile-scan-meta` (hidden above 600px), once from a
  wrongly-still-visible `.scan-meta`. Gave it its own class instead, hidden at
  the exact complement of `.mobile-scan-meta`'s boundary — verified live at
  600px (mobile) and 601px (desktop): exactly one visible at each, no gap, no
  overlap. Also confirmed `index.html.j2` renders no duplicate — its own strip
  still covers it there.

  Renamed `mobile_scan_prefix` → `scan_prefix` in the same change: it now
  labels three renderers (the mobile echo, the strip subline, and this new
  desktop indicator), and the old name would have been actively wrong the
  moment a desktop element used it.

  **A CSS regex test-authoring bug, caught before it shipped.** The first
  version of the boundary tests used `[^}]*` to scope a check inside
  `@media (max-width: 600px) { ... }` — which cannot cross the closing brace of
  `.mobile-scan-meta { ... }`, a sibling rule inside the *same* media query, and
  the file has **four** separate `@media (max-width: 600px)` blocks, so a
  looser match would have silently landed in the wrong one. Same block-boundary
  hazard CLAUDE.md documents for BACKLOG.md's `## ` scan, hit here in CSS
  instead of Markdown. Fixed with a matcher that only accepts a closing `}` at
  column 0 (real rules are always indented) and enumerates every matching
  block rather than trusting the first one found.

  **Code review found two real cleanup defects, both fixed.** `.desktop-scan-meta`
  had been given a byte-identical, second copy of `.scan-meta`'s four
  declarations rather than sharing them — three independent review angles
  flagged it, since nothing tied the two rule bodies together and a future
  visual tweak (e.g. the dark-theme retint already on the backlog) could hit
  one and silently miss the other. Merged into one comma-selector rule; the
  two classes still get separate `display` rules in `_responsive.css.j2`,
  which is the only thing that legitimately needs to differ between them.
  Fixing this broke `test_scan_meta_meets_floor` — its `_rule_font_size()`
  helper required a selector to be followed immediately by `{`, which a
  comma-selector list never is. Generalized the helper to accept `,` or `{`
  and added `test_desktop_scan_meta_meets_floor` alongside it, so the new
  class carries its own floor assertion rather than inheriting one silently.
  Separately, the page-scoping guard (`active_segment != "sectors"`) was
  checked twice — once per conditional block in the meta-cluster — instead of
  once around both; consolidated into a single wrapping `{% if %}`, each inner
  block keeping only the condition specific to it. Both fixes verified live
  (chips and scan-meta both still render correctly at 1440px) and pinned by
  new regression tests, each sabotage-verified.


- **Sentiment empty state now explains itself** (2026-08-22).

  Reported as a bug — the Sentiment tab read "No news sentiment for this
  snapshot" while the leaderboard beside it looked live. It was not a bug: the
  7-day content gate was dragging an 11-day hole in the data across the page
  (FinBERT was dead for scans 153–163, 2026-08-06→08-16), and the page was
  faithfully reporting an absence. It just could not explain itself.

  Three things made it unreadable, and the last one is why the copy alone was
  not the whole fix:

  - It said "the snapshot shown here" while **never naming the snapshot**. On
    desktop this page shows no date anywhere: `.mobile-scan-meta` is
    `display:none` above the mobile breakpoint and the lag banner is
    index-only.
  - It implied a fresher view existed ("or when this page is showing an earlier
    scan than the most recent one") without saying that **signing in cannot
    reach it here** — while the leaderboard beside it visibly does jump to
    today. The empty state is a build-time Jinja branch, so that element cannot
    change per reader. (This entry first said the page "loads no auth.js at
    all"; review proved that false — `_footer.html.j2` loads it under
    `{% if auth %}`. Sign-in does work here; what is missing is any client-side
    re-fetch of `sentiment_signals`, since `upgradeLeaderboard()` returns early
    without a leaderboard table. The reader-visible claim in the copy is
    unaffected.)
  - It pointed nowhere. Because of the lag, this page reflects an outage about
    a week late. The health panel flags one the same day (a FinBERT failure
    records `0/N` and scores a red badge — see the 2026-08-16 entry), so that
    is where a reader asking "is it broken *now*?" belongs.

  The gated branch now names the scan and date, states the gate rule from
  `gating.LAG_DAYS` (passed through as `lag_days`, never written into the copy
  as a literal), says signing in will not move it, and points at the health
  panel. The first draft said "runs N days behind the newest scan", which
  review caught as wrong: `_pick_lagged_scan` takes the newest scan at least N
  days old *relative to now* and never measures against the newest scan, so if
  scans stop the lagged pick becomes the newest scan and the data can be weeks
  stale while the copy still claims exactly N days — wrong in exactly the
  outage the copy exists to explain. Now phrased as the rule ("the newest scan
  at least N days old"), which stays true however stale the data gets. Guarded on `auth`: an unconfigured build has `lag_active = False` and
  renders the *latest* scan, where every word about lag and signing in would be
  false — that branch keeps the "source recorded nothing" wording.

  Interpolated values sit outside the `data-i18n` spans, following the lag
  banner's prefix/value/suffix split: `applyLang()` replaces an element's whole
  `textContent` from the SV table, so a scan id inside one would be wiped the
  moment a reader switched language.

  **A defect the tests missed and the browser caught.** All assertions passed
  while the page rendered *"which runs 7days behind"* — each test checked its
  own substring in isolation and none looked at the seam between an
  interpolated `<strong>` and the span after it. Fixed with a text node between
  the elements (not leading whitespace inside the translatable span, which the
  SV string would drop), and pinned by a test that strips tags and rejects a
  digit running into a word. Verified live in both languages afterwards: the
  scan id, date and lag survive the EN→SV switch intact.

  The signed-in-upgrade half of the original item was **declined**, not
  deferred — see Queued for the reasoning.


- **Non-scan price consumers: correlation aligned, forward-return windows capped** (2026-08-22).

  Ships two queued items that turned out to touch the same files — and the
  backlog was wrong about one of them, which changed what got built.

  **"Same defect, lower stakes" did not hold.** The as-of item listed
  `correlation.py`, `badges.py` and `validation.py` together as consumers that
  should call `align_cohort_asof`. But that function slices every series to a
  shared as-of date and **drops** tickers lagging the cohort by more than
  `MAX_ASOF_LAG_DAYS` (4). That is right for a cross-section and wrong for
  `badges.py`/`validation.py`, which measure per-ticker forward returns from
  past scan dates: aligning them would delete themes from the sample and
  shorten its newest forward windows. Applying it there would have been a
  regression dressed as a fix.

  All **seven** `fetch_prices` callers, checked individually:

  | caller | `end` | shape | outcome |
  |---|---|---|---|
  | `scan.py` | today | cross-sectional | already aligned |
  | `correlation.py` | today | cross-sectional | **now aligns** |
  | `macro.py` | today | two indices vs own history | neither needed |
  | `backtest.py` | today | replay over history | neither needed |
  | `scripts/horizon_sweep.py` | today | replay over history | neither needed |
  | `badges.py` | +15d | per-ticker forward returns | **`end` clamped** |
  | `validation.py` | +30d | per-ticker forward returns | **`end` clamped** |

  **The clamp — and where it lives.** New `capped_end()` in
  `src/data/prices.py`, called by **`fetch_prices` itself** rather than by each
  caller. `end` is exclusive and is the mechanism keeping an in-progress session
  out, while `_cache_is_fresh` never receives `end` and always measures against
  today — so *one* caller passing a future `end` poisons the shared
  `data/cache/` for every other caller, `scan.py` included. Enforcing it at the
  chokepoint makes that structural instead of a rule seven call sites have to
  remember.

  It was written the opt-in way first, and review is what moved it: the initial
  audit said "five callers" and **missed two** (`backtest.py`,
  `scripts/horizon_sweep.py` — both safe, both already `end=today`, but the
  table presented itself as exhaustive). Undercounting the callers of an
  invariant while implementing it per-caller is precisely the failure mode the
  chokepoint removes.

  One claim that did not survive review either: "clamping returns identical
  data" is false after the close. `end` is exclusive, so a post-close cache miss
  now forgoes today's completed bar and one forward-return observation. That is
  the same bar `scan.py` gives up, for the same reason — nothing downstream can
  distinguish a partial candle from a real close once it is cached — but the
  justification was wrong and is now stated properly in `capped_end`.

  **The alignment.** `_compute_correlation_matrix` builds `pd.DataFrame(closes)`
  across the **union** of every ticker's dates, then takes `returns.tail(60)`.
  One series running three days past the rest contributes three rows that are
  NaN for everyone else, so each pair gets fewer than 60 usable observations —
  silently, because `.corr()` drops NaN pairwise and still returns a number.
  Verified live after the change: today's cohort logs *"all 18 ticker(s)
  already as-of 2026-08-21"*, so the matrix is unchanged and the guard is in
  place for when they do stagger. (That 2026-08-21 as-of on a Saturday is the
  weekend-staleness fix from earlier the same day working.)

  Two things found while doing it, both recorded rather than glossed:

  - `correlation_date` is computed and returned in the page context but **no
    template renders it**, so switching it from `max(all_dates)` to the aligned
    as-of has no visible effect today. Fixed anyway (the correct value is free
    now) and the test says plainly that it is currently unrendered — the first
    draft of that test claimed it "labels the heatmap", which is not true.
  - The module docstring asserted "Callers pass `end=today`" as a flat fact
    while two callers did not. Rewritten to state the invariant *and* name the
    two callers that need `capped_end` to hold it up.

  Sabotage-verifying the new tests caught two of them passing when they should
  have failed — the same comment-trap that bit repeatedly during the leaderboard
  work. Deleting the real `capped_end(...)` call left "See capped_end()." in the
  comment beside it, and deleting the `align_cohort_asof(prices)` call left the
  name in the import line. Both now strip `#` comments and match on the call
  (`align_cohort_asof(prices`), not the bare name.


- **Trend badge moved beside the setup badge; Trend column removed** (2026-08-22).

  Closes the last open finding of the 2026-08-09 design audit ("badge/trend
  hierarchy is inverted against the copy"), which completes that item — its
  Queued section is deleted with this change.

  **The finding as written was stale.** It described `▲ Enter` as a loud tinted
  pill against a Trend column reduced to "a 9.84px arrow". Re-measured in the
  browser before touching anything: the setup badge is 60.4×22.4px (1351px²,
  12.25px/600) and the Trend badge is 49–63×24px (**1185–1523px²**, 12px/600) —
  identical weight, identical tint alpha, and Trend is the *larger* of the two
  in the rising state. The `.traj-badge` restyling had already closed the
  loudness gap the audit measured. Nothing to fix there.

  **What was actually wrong was distance, not weight.** `_methodology.html.j2`
  does not treat the pairing as a contradiction — it prescribes it: the badge
  describes the *band*, "a theme collapsing fast but still ranked first will
  read Enter", and "the separate Trend column is what tells you it is
  deteriorating — the two are meant to be read together." That makes adjacency a
  layout requirement, and desktop failed it: the two badges measured **651–763px
  apart** at 1440px wide, four columns between them. Mobile already had it right
  (77–82px, inside one card).

  Worse, the two disagreed about which signal led. Desktop gave the
  name-adjacent slot to the setup badge and put Trend in column 6; the mobile
  card put *Trend* on line 1 beside the theme name and demoted the setup badge
  to a row below. Same two signals, opposite priority, same product.

  The badge now renders inside `.theme-cell` after the setup badge, and the
  Trend column is gone (6 → 5). Measured after: **8px apart, same row**, no
  horizontal overflow at 1440px or 375px.

  Three things that made this far smaller than it looked, all verified rather
  than assumed:

  - Every `cells[...]` reference in the codebase addresses index **0 or 1**, so
    removing the *last* column shifted nothing.
  - The Trend `<th>` carried no `onclick`, so `sortTable(0..4)` was untouched;
    the trend filter chips read `tr.dataset.trend`, not the column.
  - `renderMobileCards()` finds both badges by **class**, not cell index —
    mobile needed **zero** changes and re-measured byte-identical (77px gap,
    Trend still on line 1). The new
    `test_mobile_cards_find_both_badges_by_class_not_by_cell_index` pins that,
    because addressing the cells positionally is the obvious "simplification"
    that would re-break it on the next column change.

  Two details that would have been silent losses:

  - The removed `<th>` held `title="Rank slope over last 3–5 scans"` — the only
    on-page explanation of what Trend measures. Rehomed onto the badge as
    `title` + `data-i18n-title="trend_tip"` (new SV string; `col_trend` deleted
    as dead).
  - `.traj-word`'s `font-family: var(--font-body)` existed to undo
    `tbody td:nth-child(n+4)`'s mono, which no longer applies at column 2. Rule
    kept as defensive, its comment rewritten — it had become actively
    misleading about why it exists.

  Also found while measuring, and **not fixed here** — logged as its own queued
  item: the rescore path's `traj.textContent = ...` destroys the badge's inner
  spans and would degrade Trend back to a bare glyph, which is precisely the
  audit's original complaint. Dormant only because the control that triggers it
  is withdrawn.

  `colspan` went 6 → 5 in four places; the fifth `colspan="6"` in the file
  belongs to the History *scans* table, which genuinely has six columns and was
  verified before being left alone. Two pre-existing tests
  (`..._emit_the_same_column_count`, `..._colspans_match_the_column_count`)
  already guard the cross-builder invariants and stayed green throughout.

  **Code review caught three regressions, all in the paths the first live check
  could not see.** Worth recording because two of them were invisible precisely
  *because* of how the badge is gated:

  - `applyHorizonBadges()` appended the setup badge to the theme cell, landing
    it **after** the Trend badge. On the build that ships this is the only
    writer — `badges_gated` bakes no badge — so production would have had the
    reverse of the order this change exists to create, while an ungated build
    had it right. The first live verification missed it because the check
    hand-injected the badge in the intended position instead of letting the
    real function place it; re-verified afterwards by driving
    `applyHorizonBadges()` itself, which is what the fix is pinned on.
  - `auth.js` fell back to `"—"` when a row had no trajectory. Harmless in its
    own `<td>`; inside the theme cell it is a bare text node, and
    `.theme-cell > * + *` spaces only *elements*, so it rendered jammed onto the
    ticker as `URA—`. Now empty — absence of a badge is already how the board
    says "no trend", which is the same reasoning that dropped
    `scan-history.js`'s placeholder in this change.
  - The rehomed tooltip was added to the template but not to `auth.js`, so the
    explanation would have been missing for exactly the readers who see a setup
    badge to pair Trend with. The test now asserts it across both builders
    rather than the template alone.


- **Weekend scans scored Thursday's close, not Friday's** (2026-08-22).

  `_cache_is_fresh` (`src/data/prices.py`) took `expected` =
  `_expected_latest_close(today)` and then applied a 1-day grace on top. On a
  weekday the two agree exactly — `end` is exclusive and callers pass
  `end=today`, so a cache fetched today legitimately ends yesterday, and the
  grace is what makes that pass rather than slack. On a **weekend** `expected`
  had already walked back to Friday, a COMPLETED session, and the grace then
  walked back one more — so a Saturday or Sunday run accepted a cache written
  Friday morning (last bar: Thursday) and never fetched Friday's close.

  Fixed by requiring the last completed session strictly before today
  (`_expected_latest_close(today - 1 day)`) with no grace. Verified this
  preserves all six pre-existing freshness behaviours exactly: on a weekday the
  new boundary and the old grace boundary are the same date, so the only
  behaviour that changes is the weekend one that was wrong.

  **No market-hours/timezone logic was needed**, which the Queued item had
  assumed ("a correct rule has to be market-hours aware"). Because `end` is
  exclusive, a session's bar only becomes obtainable the calendar day *after*
  that session regardless of what time the run happens — so "newest obtainable
  close" is a pure calendar question. The holiday grace turned out not to be
  load-bearing either: the pre-existing
  `test_cache_stale_friday_on_tuesday_after_holiday` already asserted that a
  Monday holiday causes a refetch and called it harmless.

  **Confirmed live on the production cache**, on the Saturday this shipped:
  20 of the 50 cached tickers held Thursday 2026-08-20 as their last bar while
  Friday 2026-08-21 was a completed session — all 20 would have been served
  stale to that day's scan under the old rule, and are correctly refetched
  under the new one. 1014 passed.

- **Desktop controls row — curated filter chips + a "More filters"
  disclosure** (2026-08-22). Plan:
  `sector_momentum-notes/plans/2026-08-22-controls-row-chip-curation.md`.

  - **One "Controls row" per the spec, not two.** The old horizon row and
    filter-bar row merge: HORIZON control and four stats stay left, three
    curated chips (`Top 5`, `↑ Rising`, `Composite > 0`) plus a dashed
    `More filters` chip sit right-aligned. The full nine-chip set moves,
    unmodified, into a new `<details class="more-filters">` — the same
    native disclosure `.rank-settings` already used one control over,
    right down to its popover positioning and mobile full-width override.
  - **Two structural decisions resolved with the user before planning,
    not guessed**: curated chips are duplicates sharing state with the
    full set (not a physical move — that would leave the Trend group
    showing only `Flat`/`Falling` and the Threshold group showing only
    `Positive change` once disclosed), and `More filters` is a native
    `<details>`/`<summary>`, matching `.rank-settings` beside it.
  - **The sync mechanism**: `_wireFilterChips()`/`clearFilters()` widen
    from `#leaderboard-filter-bar .filter-chip` to any element carrying
    `data-filter-group`/`data-filter-value`, and a new `_syncChipState()`
    updates every element sharing a group/value pair — clicking either a
    curated chip or its full-set counterpart updates both.
    `applyFilters()`'s own matching logic is untouched.
  - **Two follow-on fixes the restructuring itself required**:
    `setFilterBarVisible()` (guest/history gating, `scan-history.js`) now
    also hides/shows the curated chips, not just the disclosed set — past
    scan rows carry no filter data-attributes, so a curated chip left
    visible would silently do nothing. `filter-count`/`filter-clear` move
    out of the disclosure into the always-visible row, so collapsing
    `More filters` after picking a full-set-only chip doesn't hide the
    only sign a filter is active.
  - **Stage 3's mobile scroll hack for `.filter-bar` is retired**, not
    just superseded quietly — the full set is behind a disclosure now,
    not always visible, so wrapping reads better than a forced
    horizontal scroll. `BACKLOG.md`'s own note on this item predicted the
    exact reversal when Stage 3 shipped the stopgap.
    `test_control_row_scrolls_horizontally_not_wraps` is deleted, not
    skipped — the property it guarded is no longer true by design.
  - **Four bugs found and fixed live, none by the plan's own tests**: a
    regex requiring `\S+` broke against the two-word Swedish translation
    "Fler filter"; a sync-mechanism test checked the wrong function's
    body for the selector construction (the implementation extracted it
    into a sibling function, `_syncChipState`, that
    `_wireFilterChips()` merely calls); a `@media (max-width: 600px)`
    block-delimiting regex matched clean across a boundary into an
    unrelated `@media (pointer: coarse)` block, since this file has
    several separate `max-width: 600px` blocks; and `.control-chips` had
    no `flex-wrap` of its own, so its six children overflowed 7.66px past
    a 375px viewport instead of wrapping — measured via
    `getBoundingClientRect()`, since `document.documentElement.scrollWidth`
    read 375 throughout and never flagged it.
  - **Three more bugs found by a whole-branch review pass, after both
    tasks were green:** `.control-chips` had no right-alignment
    mechanism (`.horizon-row` is a plain flex-wrap row with no
    `justify-content`) despite this same Done entry's own text above
    claiming the chips "sit right-aligned" — fixed with `margin-left:
    auto`, measured live: an 811px gap at 1280px before the fix.
    `.control-chip`/`.more-filters summary` were missing from the
    `pointer: coarse` 44px touch-target rule every sibling control in
    the row already gets. `clearFilters()` hand-rolled its own copy of
    the `_syncChipState()` pressed-state toggle — extracted into a
    shared `_setChipPressed(el, active)` both now call. 1010 passed.
  - **Deliberately left as follow-up, not fixed here:** `.more-filters`'s
    popover CSS duplicates `.rank-settings`'s almost verbatim instead of
    sharing it (the missing-touch-target bug above is a direct
    consequence of that duplication already drifting once) — a real
    DRY opportunity, but merging them touches `_sentiment.css.j2`,
    untouched by this branch, for a currently-cosmetic gap. Two
    independent `<details>` popovers (`.more-filters`, `.rank-settings`)
    have no mutual-exclusion, so both could render stacked if opened
    together — currently unreachable, since `.rank-settings` only
    renders when `SENTIMENT_RANKING_ENABLED` is true. Neither is worth
    the risk of touching more surface area for an inactive path.

- **Leaderboard horizon segmented control — Stage 5 of the leaderboard
  redesign, the last stage** (2026-08-22). Plan:
  `sector_momentum-notes/plans/2026-08-22-leaderboard-redesign-stage5-horizon-control.md`.

  - **`.horizon-btn` pill buttons (Medium/Long) replace the visible
    `<select id="horizon-select">`**, matching `.theme-toggle`'s pattern
    under new class names (`.horizon-toggle`/`.horizon-btn` — not literally
    shared, so a future `.theme-btn` change doesn't silently reach the
    horizon control). The `<select>` stays in the DOM, `display: none` —
    `switchHorizon()` reads it back internally at two points, so removing it
    would mean rewiring those reads (Stage 1's spec had already resolved
    this: keep it, visually hidden). A click forwards to the existing
    `switchHorizon(key)` unchanged; a new `updateHorizonToggleUI(key)` keeps
    the buttons' `aria-pressed` in sync from both `initHorizonSelect()` and
    `switchHorizon()`, so a programmatic call (a deep link, a test, the
    hidden select's own `onchange`) never leaves the two controls
    disagreeing. `label` became a plain eyebrow `<span>` (the element it
    labelled is no longer the focus target), which also meant dropping the
    trailing colon from `horizon_label`'s copy — restyled as an eyebrow, not
    a form label.
  - **A fourth backtest stat, "sell past rank N".** Reuses
    `Horizon.exit_rank` (`src/horizons.py`, already `top_n + buffer`) rather
    than re-deriving the formula a third time in JS — threaded through
    `horizons_json` alongside the three fields already there. Stats now
    render in the spec's exact order: held, sell past rank, trades/yr,
    median hold (the pre-Stage-5 order was held, median hold, trades).
  - **Two test bugs found and fixed while executing the plan, not by the
    plan's own review:** a `text.count(...)` assertion that always read 1
    against raw Jinja template source, since a `{% for %}` loop appears once
    textually no matter how many horizons it renders; and a sabotage-verify
    pass that passed against commented-out code, because the substring it
    checked for survives inside the comment itself — the same "comments are
    page content" trap Stage 3 (once) and Stage 4 (twice) already hit, a
    fourth time now.
  - **Dead CSS caught while reviewing the plan, before writing any code:**
    `.horizon-row label` styled the `<label>` this stage removes; left
    behind it would have no matching element.
  - **A fourth dead-CSS find, this time by whole-branch review after both
    tasks were green:** `#horizon-select` was still listed in the
    `pointer: coarse` 44px touch-target rule (`_responsive.css.j2`) even
    though it is now permanently hidden — dead weight on a control that no
    longer needs it, while `.horizon-btn`, the real tap target, was never
    added. The regression test hit "comments are page content" a fifth
    time while being written: its own explanatory comment named both
    selectors in prose, so a bare substring check on either assertion
    passed against the comment alone — fixed by stripping `/* */` block
    comments before asserting. 994 passed.

  Deliberately out of scope, split into its own Queued item so it isn't
  lost: the desktop "Controls row" filter-chip curation (a curated `Top 5` /
  `↑ Rising` / `Composite > 0` subset + a "More filters" disclosure) the
  same spec section describes — the spec's own "Suggested order of work"
  list separates Stage 5 as "the horizon segmented control + the four
  stats" only, matching the 1:1 mapping every prior stage followed between
  a Suggested-order-of-work bullet and what it shipped.

- **Leaderboard summary strip — Stage 4 of the leaderboard redesign**
  (2026-08-21). Plan:
  `sector_momentum-notes/plans/2026-08-21-leaderboard-redesign-stage4-summary-strip.md`.

  - **A 3-cell strip between the command bar and the tab bar.** Cell A
    "Today's read", Cell B "In the buy band", Cell C "Market context" — a
    1px grid gap over a `--border` background draws the dividers, so no cell
    carries its own border and none doubles at the seams.
  - **The sentence rule departs from the spec, deliberately.** The spec
    suggested naming "the top-2 themes' shared character if the top 4 share a
    tag". **No tag concept exists** — `config/themes.yaml` themes carry only
    `ticker`, `gdelt_keywords`, `unbuyable` — so that branch had no data
    behind it, and inventing a taxonomy would have made the headline sentence
    assert groupings the data cannot support, which is the exact failure the
    spec's own risk note warns about. The shipped rule states only what the
    data supports: the rank-1 theme leads, and the second clause is the sign
    of the mean change score across the bottom half, with a `DRIFT_EPS = 0.05`
    dead band so the cell does not flip between "picking up" and "sliding" on
    noise. `dashboard/digest.py`, 17 tests.
  - **Python returns facts, never prose** (`{"lead_theme", "drift"}`). Every
    word lives in the template behind a `data-i18n` key. This is not style: the
    i18n pass has no variable-interpolation mechanism, so a sentence assembled
    in Python could not be translated at all without inventing a `data-i18n-*`
    attribute the pass does not read — which is silently inert. Theme name
    first, since theme names are never translated and theme-first reads
    correctly in both EN and SV.
  - **Cell B cannot be baked, and isn't.** Buy-band membership depends on the
    *active* horizon — client-side state the reader switches at runtime — so
    baking it would be wrong for every reader on Long and stale the moment
    anyone switched. `renderBuyBand()` reads `.in-band-rail`, which
    `applyHorizonBadges()` already writes from the active horizon's `top_n`,
    and hangs off the same `applyBandBoundaries()` funnel Stage 3's
    `renderMobileCards()` uses. Verified live: Medium → 4 pills, Long → 5
    (Defense joins), filters and sorts followed, all without a reload.
  - **Three bugs found at the browser gate, none by the test suite:**
    (1) `applyLang()` inside `renderBuyBand()` recursed —
    `applyFilters()` → `applyBandBoundaries()` → `renderBuyBand()` →
    `applyLang()` → … until the stack blew. Reachable *only* by filtering the
    band empty, so nothing else would have hit it. The empty-state note is now
    baked into the template and toggled, needing no runtime translate call.
    (2) Pills followed table order, so sorting by Theme rendered "1 2 4 3" —
    each pill prints its own rank, so that reads as a bug rather than as the
    sort. The band is a set; it is now always rank-ordered.
    (3) The strip's subline repeated the scan id and date that
    `.mobile-scan-meta` already prints a couple of hundred pixels above it on
    a phone; hidden at that width, same reasoning as Cell C.
  - **The mobile header overflow is fixed as a side effect** — the Queued item
    for it is deleted rather than rewritten. `.meta-cluster` was 484px of
    children in a 375px header, blowing the layout viewport out to 499px and
    rendering the whole page at ~75% scale. Moving the 201px chips button into
    Cell C leaves three children at 274px: measured 375/375, no overflow.
  - **Comments are page content, and three source-scanning tests proved it.**
    Stage 3 already lost a round to a leaderboard-row tag inside a JS comment;
    this stage hit it twice more — `applyLang` inside a comment matched a test
    forbidding the call, and "Scan #161" inside a CSS comment parsed as the hex
    colour `#161` for `test_no_hardcoded_hex_outside_foundation`. The JS test
    now strips `//` lines before asserting; the CSS comment avoids a literal
    id. Worth remembering for Stage 5.
  - **Three more bugs found by a whole-branch review pass, after the plan's
    own tasks were already green:** (1) `todays_read()` was called with
    `leaderboard_rows` (all cohorts) instead of `theme_rows` (THEME-only) —
    ranks are per-cohort, so an unmixed call could have named a retired
    US/EU sector as the lead theme had a scan_id ever mixed cohorts. Not
    exercised by current data; fixed defensively, since this is exactly the
    failure mode CLAUDE.md's `region` warning describes. (2) Cell C's inner
    `{% if macro %}` only hid the chips, not the eyebrow trigger around
    them — the outer `{% if macro or auth %}` the old header block had was
    dropped when Cell C replaced it, so a build with neither still rendered
    a "what these mean" button with nothing beside it to explain. (3) The
    bigger one: `sentiment.html.j2` shares `_header.html.j2` but has no
    summary strip of its own — the plan's Task 2 file list never mentioned
    it, so deleting the header's SPY/VIX/Live chips for Cell C's sake
    silently removed them from the Sentiment page too, with no replacement.
    Restored as a header-only echo gated to non-leaderboard pages
    (`active_segment != "sectors"`), reusing the same `#market-context-chips`
    id `markLive()` already targets. All three caught by the review's own
    finder angles, not by the plan's tests — a reminder that a plan's Task N
    tests only ever check what Task N's author thought to check.

  Deliberately out of scope, left for Stage 5 or their own items: the `.card`
  wrapper drop and panel/tab-bar padding (spec Screen 1 items 3–4), the footer
  `Data health` link (item 9), and Cell B's signed-in Enter/Exit counts line —
  that one needs the gating rules `applyHorizonBadges()` applies, and guessing
  at them is how a guest-safe variant leaks. 984 passed (after the review fixes above).

- **Leaderboard table redesign — Stage 3 of the leaderboard redesign**
  (2026-08-21). Plan:
  `sector_momentum-notes/plans/2026-08-20-leaderboard-redesign-stage3-mobile-cards.md`.

  - **Mobile cards replace the pinned-column table.** `renderMobileCards()`
    (`index.html.j2`) reads the table's own live DOM
    (`.leaderboard-row`/`.band-cut-row`) and rebuilds `.leaderboard-cards`
    below 601px — a read-projection of the table, not a fourth independently
    built row-format, which is what keeps sort/filter/horizon-switch/band-cuts
    working on mobile with no card-specific reimplementation of any of them.
    The old pinned-first-two-columns treatment (`#leaderboard-table
    td/th:nth-child(1)/(2)`) is deleted from `_responsive.css.j2`.
    Wired from the single `applyBandBoundaries()` funnel point (verified
    before implementing, not assumed, that `sortTable()`/`applyFilters()`/
    `applyHorizonBadges()` all already call it) plus directly from
    `scan-history.js`'s `showScan()`, which never calls
    `applyBandBoundaries()` — the same reason Stage 2's band cut rows needed
    their own wiring there.
  - **Real, browser-verified bugs found and fixed during implementation:**
    the composite bar rendered at 0 width (`.cbar-wrap`'s `flex: 1` had no
    flex-container parent once only `compositeCell.innerHTML` was copied,
    not `.composite-cell` itself — fixed by making `.card-line2` a flex
    container); the embedded breakdown copy overflowed ~130px at 375px
    (`.breakdown-grid`'s internals are wider than a card even collapsed to
    one column — fixed with a scoped `overflow-x: auto` fallback, a full
    breakdown redesign judged out of scope for this stage); and
    `applyBandBoundaries()`'s early `return` for non-rank sorts would have
    made the new `renderMobileCards()` call unreachable on those sorts —
    restructured to an `if (rankOrder) { … }` block instead, found by
    tracing control flow rather than trusting a substring-match test.
  - **Mobile header scan-meta row, scrollable control row, stacked footer.**
    A new `.mobile-scan-meta` row (`_header.html.j2`) echoes the desktop
    chips' scan id/date/SPY/VIX below 600px, guarded on `scan_date` (slicing
    an undefined `scan_date` raises in Jinja, unlike a bare `{{ }}` reference
    — caught by `test_leaderboard_filters.py`'s minimal render context, which
    passes no `scan_date`). `.filter-bar` scrolls horizontally as one row
    instead of wrapping across several (`.horizon-row` deliberately excluded
    — it carries prose, not chips). `.site-footer` stacks vertically instead
    of squeezing the disclaimer text against the Methodology/Alerts buttons.
  - **Review finding, verified live and fixed:** every card was emitted with
    `role="button"`/`tabindex="0"`/`aria-expanded` and a toggle handler
    unconditionally, but `scan-history.js`'s past-scan rows carry no
    `data-sector-id` and emit no `.breakdown-row`, so `bdContent` is always
    empty on exactly the path this stage newly wired. Confirmed at 375px on
    scan #159: all 18 cards announced themselves as expandable disclosures and
    revealed a 0px-tall empty panel. The disclosure affordances and the
    `.card-breakdown` div now hang off one `expandable` condition, and the
    click handler is scoped to `[role="button"]`. Three tests pin it (all
    three fail against the pre-fix template). Worth noting for Stages 4–5:
    the first draft of the fix put a literal leaderboard-row tag in a JS
    comment, which `test_badge_gating.py`'s row-tag regex matched in the
    rendered page — template comments are page content, not source-only.

- **Leaderboard table redesign — Stage 2 of the leaderboard redesign**
  (2026-08-20). Plan:
  `sector_momentum-notes/plans/2026-08-20-leaderboard-redesign-stage2-band-cut-rows.md`.

  - **Labelled band cut rows** replace the old invisible `border-bottom`
    cut lines. `applyBandBoundaries()` now inserts real
    `<td colspan="6">` rows reading "BUY BAND ENDS" / "SELL LINE" after the
    horizon's top_n and exit-rank rows, idempotently (removes its own
    previously-inserted rows before recomputing, since sort/filter/horizon-switch
    all call it repeatedly). The `#band-legend` swatch legend is gone —
    redundant now that the rows self-label.
  - Wired into the scan-history view (`showScan()`), which genuinely lacked
    any band cut before this — its rows deliberately carry no `data-rank`
    attribute, so the shared `applyBandBoundaries()` DOM pass can never find
    them; `scan-history.js` now computes its own cut positions from its
    in-memory rank data and builds the row markup via a new shared
    `window.buildBandCutRowHtml()`. The signed-in path needed **no new
    wiring** — `applyHorizonBadges()` (already triggered by
    `sm:leaderboard-upgraded`/`sm:positions-changed`) already called
    `applyBandBoundaries()` at its own end; the plan's premise that this was
    missing was a research error, caught and reverted by whole-branch review.
  - **Real, browser-verified bugs found and fixed during implementation:**
    a first-pass `calc(100% - 260px)` rule width wrapped the note onto two
    lines (switched to a flex `<div>` wrapper inside the `<td>`, not the
    `<td>` itself — avoiding Stage 1's flex-collapse trap); the band-cut
    text was resetting to English on every sort/filter for a Swedish reader,
    since `applyBandBoundaries()` reruns far more often than the
    once-per-row-rebuild cases the existing `data-i18n`-and-wait pattern was
    designed for — fixed with a new `window.translate(key, fallbackEn)`
    helper that bakes in the correct language at build time; and the mobile
    pinned-column CSS (`#leaderboard-table td:nth-child(1)`) was catching the
    new row's single `<td>`, pinning a full-width banner into a 44px sticky
    box — fixed the same way `.breakdown-row` already handles this,
    `position: static` on `.band-cut-row td`.

- **Leaderboard table redesign — Stage 1 of the leaderboard redesign**
  (2026-08-19). Design spec:
  `sector_momentum-notes/specs/2026-08-18-leaderboard-redesign-design.md`;
  plan:
  `sector_momentum-notes/plans/2026-08-18-leaderboard-redesign-stage1-tokens-and-table.md`
  (private companion repo).

  - **6 columns, down from 8**: dropped the always-empty Sentiment column
    (sentiment is alpha and excluded from the ranking, so it was "—" in every
    row) and merged the separate Level and Change columns into one
    stacked-bar cell.
  - **Rank pill with a left rail**: the rank cell got a pill badge plus a
    3px in-band rail, replacing the plain numeral.
  - **Composite bar rescaled 1.5 → 1.6**, values signed with U+2212 (not
    ASCII hyphen) instead of the unsigned formatting the old bar used.
  - **Trend badge gained a word** ("surging", "rising", "flat", "falling",
    "sliding") alongside the existing glyph (↑↑/↑/→/↓/↓↓).
  - Three independent places build leaderboard row HTML and had to change
    together: `dashboard/rows.py` (baked), `renderLatestRows()` in
    `dashboard/assets/auth.js` (signed-in), and the row loop in
    `dashboard/assets/scan-history.js` (past-scan view). A new
    `window.THEME_TICKERS` global (build.py → template → JS) lets all three
    show the theme's US ETF ticker.

  Stages 2–5 of the redesign (band cut rows, mobile cards, summary strip,
  horizon control) are still queued/not yet planned — see the design spec
  for the full roadmap.

- **Acted on 3 of 6 deferred GDELT bulk-fetch polish findings** (2026-08-17)
  — see Queued for the 3 that remain deferred.

  - **Partial-slice-failure warning**: `fetch_theme_headlines_bulk` now logs
    `logger.warning` when `0 < ok < len(urls)`, not just at total failure
    (`ok == 0`). A degraded-but-not-dead day (e.g. 3 of 96 slices unreadable)
    previously only showed up in the `ok/total` INFO line. Sabotage-verified:
    reverting the new branch made the new test fail with the expected
    assertion error, confirming it actually exercises the code path.
  - **Dead code removed**: `GDELT_SECTOR_THEMES` (an 11-GICS-sector theme-code
    map, left over from the sector-cohort retirement) and `_build_query`
    (superseded by `_build_keyword_query`) deleted from `news_sentiment.py`,
    along with the one test class that existed purely to assert on the dead
    constant.
  - **http → https checked, not switched**: confirmed *not* free, contrary to
    the original finding's "switch if free." `data.gdeltproject.org` serves
    HTTPS on port 443, but the certificate is issued for
    `*.storage.googleapis.com` (the GCS bucket fronting it), not for the
    GDELT hostname — every request fails
    `SSLCertVerificationError: Hostname mismatch`. Verified directly with
    `requests` (the library the code uses), not just `curl`. `GKG_BASE`
    stays on `http://`; the comment in `gdelt_gkg.py` explaining why was
    already accurate, just unconfirmed.
  - **Not touched**: `csv.field_size_limit()` global mutation (the original
    review already verified it harmless — no code change was ever indicated,
    just a note) and the "no slices could be read" warning wording — both
    still fine to defer, no new information changes that.

  `pytest -q` → 898 passed, 17 skipped (same total as before: 2 tests
  removed with the dead code, 2 added for the new warning).

- **Widened `gdelt_keywords` for the two themes still below `MIN_ARTICLES`
  after the GDELT bulk-fetch shipped** (2026-08-17) — the cheap lever named
  in *GDELT source alternatives* (see Queued), tried before reaching for
  BigQuery.

  **AgTech & Food Innovation** (`agtech`, `precision agriculture`, `food
  technology`, `vertical farming` — 0 matches) had jargon too narrow to
  appear in general headlines. Added 7 broader terms mapping to KROP's
  actual holdings: alt-protein, indoor farming, ag biotech. Measured against
  the live bulk GKG feed: **0 → 36 headlines** in one run, comfortably above
  the floor (a later run measured 11 — the 24h window is a rolling feed, so
  the exact count varies run to run; both are well above 0).
  **Deliberately excludes "food security"**, tried and then dropped: plain
  substring matching (not topic classification) pulled 4 of that run's 36
  matches from war/famine/geopolitics headlines ("Russia cutting off
  Ukraine's grain exports") with nothing to do with KROP's holdings —
  contamination the widening's own goal argues against. Code review caught
  this.

  **Shipping** (`container shipping`, `freight rates`, `shipping industry`,
  `tanker rates` — 1 match) turned out to be keyworded for the wrong freight
  market: BOAT tracks Baltic Exchange **dry-bulk** futures
  (Capesize/Panamax/Supramax), not container or tanker shipping. Added 7
  dry-bulk-specific terms on top of the existing ones (kept, not removed).
  Measured: **1 → 3 headlines** on the bulk pass — better, but still below
  the `MIN_ARTICLES = 5` floor. The DOC API fallback reads the same widened
  list; a live probe confirmed the longer query (233 chars, 11 OR-clauses,
  up from ~90 chars/4 clauses) is not rejected as malformed — got GDELT's
  429 rate-limit body, not a 400/414 — but could not confirm actual result
  content: this IP is still under the same long-window throttle documented
  in the GDELT bulk-fetch work, independent of this change. Shipping may
  simply be a structurally thin news topic some days regardless of keywords.

  Bulk measurements taken with `src.data.gdelt_gkg.fetch_theme_headlines_bulk`
  against the real 24h GKG window (20s, no rate limit) — the same tool the
  daily scan uses. No test pins the theme keyword lists' content, so nothing
  needed updating beyond `config/themes.yaml`.

- **Sentiment table leaked past the guest lag gate; empty-state copy blamed a
  source outage that wasn't one** (2026-08-17) — a follow-up from the GDELT
  bulk-fetch fix. Once sentiment started working again (scan 164), the Data
  ⇄ Sentiment scatter (built from `history_df`, capped to the lagged scan for
  guests) and the News-sentiment table below it (read via
  `get_sentiment_signals_for_latest_scan`, always the true latest scan)
  disagreed about which scan "latest" meant: a guest could see today's
  News-sentiment table sitting above an empty scatter captioned "usually a
  temporary source outage or rate limit," when the real cause was simply that
  the site's 7-day content lag hadn't reached that scan yet.

  Added `get_sentiment_signals_for_scan(conn, scan_id)` (`src/state.py`),
  mirroring the existing `get_signals_for_scan` pattern (including its
  `sentiment_signals`-table `ORDER BY`, per `_latest_scan_query`'s
  documented rationale), and wired it into `dashboard/build.py`'s lag block
  so `sentiment_signals_df` is re-fetched at `lb_scan_id` alongside
  `signals_df` — the same scan the scatter was already capped to. Reworded
  the empty-state copy (EN + SV) to name both real causes — a genuine source
  gap that day, or the page simply showing an earlier scan — instead of
  guessing at "usually an outage."

  Verified against the live DB: the guest build (auth configured) now shows
  an empty scatter *and* an empty table at the same lagged scan (157); the
  auth-disabled local build still shows the full 18-theme table at the true
  latest scan (164), unaffected.

- **GDELT bulk GKG feed replaces the rate-limited DOC API as the primary
  sentiment source** (2026-08-16) — the daily scan's headline fetch spent
  ~87 minutes, 80% of it (70 min) waiting out 429 backoffs, with 7 of 18
  themes still coming back with zero headlines despite that.

  **Why the API was demoted.** GDELT's DOC 2.0 rate limiter is stateful over
  a long window: a client IP kept failing ~80% of requests even at GDELT's
  own documented 5s spacing, and GDELT's guidance is that high-volume users
  should move to the bulk feed instead. A `User-Agent` change that fixed
  this for another client was tested here too and made no difference
  (browser UA 0/4 successful vs default UA 1/4).

  **What shipped.** Bulk GKG files — published every 15 minutes, no rate
  limit — are now the primary source: 96 slices covering a 24h window
  download in parallel, get matched locally against title text plus GKG
  themes/orgs/names, and are deduped by URL and title
  (`src/data/gdelt_gkg.py`). The DOC API is demoted to a fallback, used only
  for themes still below `MIN_ARTICLES` after the bulk pass, itself bounded
  by a wall-clock budget so a throttled run can't regress to the old
  runtime (`src/data/news_sentiment.py::fetch_headlines`).

  **Live measured results** (2026-08-16, real GDELT): the bulk phase alone
  took 20 seconds, read 96/96 slices, and yielded 50,432 titles covering
  13/18 themes. With the bounded API fallback added on top, coverage
  reached 16/18 themes above `MIN_ARTICLES` (was 11/18), totaling 1,140
  matched headlines. A single slice measured 3.2 MB at 2.11 MB/s.

  **The honest caveat.** An *unbounded* fallback was also measured, and took
  ~89 minutes to rescue just 3 themes on a heavily-throttled IP — as slow as
  the pre-bulk baseline this change exists to fix. That's why the fallback
  carries a wall-clock budget rather than running to completion: the bulk
  path is the actual speedup, and the fallback is now bounded, not fast.

  **Deliberately out of scope**, and now tracked as their own Queued items
  rather than left as a footnote here: *GDELT source alternatives — Web
  NGrams and BigQuery* (both researched and ruled out for now, with the
  reasoning and the cheaper first lever — widening the two thin themes'
  keywords — written down), and *Deferred polish from the GDELT bulk-fetch
  reviews*, which carries the minor findings from the per-task and
  whole-branch reviews with their triage reasons.

  Spec: `/Users/jonasbarte/AI Projects/sector_momentum-notes/specs/2026-08-16-gdelt-bulk-fetch-design.md`.
  Plan: `/Users/jonasbarte/AI Projects/sector_momentum-notes/plans/2026-08-16-gdelt-bulk-fetch.md`.

- **FinBERT sentiment pipeline restored — dead for 10 days** (2026-08-16) —
  the Sentiment tab had shown no data at all since 2026-08-05. Investigated
  end to end (GDELT → FinBERT → persistence → build → render) after the tab
  was reported permanently empty.

  **Root cause.** The sector-cohort retirement (`1ff80d8`, 2026-08-05 11:04)
  deleted the module-level `_finbert_pipeline = None` from
  `src/data/news_sentiment.py` while leaving `_load_finbert_pipeline()`'s
  `global _finbert_pipeline` / `if _finbert_pipeline is None:` in place.
  Reading an unassigned global raises `NameError`, so **every** FinBERT call
  failed. Scan 152 ran 09:53 that morning and is the last scan with sentiment;
  scan 153 the next day was the first to fail. Confirmed in the production log
  for scan 162: `FinBERT sentiment failed (name '_finbert_pipeline' is not
  defined) — sentiment stays NULL for this scan`.

  **Blast radius.** Scans 153–162: `sentiment_signals` gained zero rows,
  `scores.sentiment_score` was 100% NULL, and the three `scans` health columns
  (`finbert_scored`, `finbert_total`, `gdelt_articles`) stayed NULL. The
  dashboard was never at fault — it correctly rendered its "no news sentiment
  for this snapshot" empty state, faithfully reporting the absence.

  **A second, latent bug behind the first.** The same commit also left
  `_compute_finbert_sentiment` concatenating theme-keyed rows (`theme`) into
  an accumulator seeded with `region`/`gics_sector` columns, so both key
  columns arrived NaN against `NOT NULL` schema columns. It had never fired in
  production only because the `NameError` skipped the code entirely — fixing
  the first bug alone would have swapped a silent failure for a failing
  INSERT. Fixed by re-keying the rows before the concat.

  **Why the suite stayed green through all of it.** Every test in
  `test_news_sentiment.py` patched `src.data.news_sentiment._load_finbert_pipeline`
  — mocking out the exact function that was broken. The fix is a test that
  stubs the **external** boundary (the `transformers` module in `sys.modules`)
  so our own loader body actually executes: mock what you don't own, run what
  you do. The stub helper deliberately does *not* create the missing global as
  a side effect, or it would paper over the very defect it exists to catch.
  Added 7 tests, including a real-DB test (`TEST_DATABASE_URL`-gated, runs in
  CI) that the built frame survives an actual `save_scan` INSERT — the layer
  where the second bug bites and where no unit test could have caught it.

  **The detection gap, closed.** Raised by `/code-review` and worth more than
  either bug fix: a FinBERT *failure* and a deliberate `--no-finbert` *skip*
  both left all three health metrics NULL, and `_footer.html.j2` renders that
  as a muted "Skipped" with no badge and no warning dot. The dashboard was
  reporting a 10-day outage as a user preference. A failure now records
  `0/N` instead of NULL, which scores a **red** badge through the existing
  `dashboard.health._badge` and trips `health_any_warn` — springing the health
  panel open on load. No schema change needed. `--no-finbert` still records
  NULL and still reads as "Skipped", so the two stay distinguishable. A second
  review pass caught two ways this could still go quiet — a 0 denominator
  (which makes `_badge` return None and the footer's `or 'green'` fallback
  paint an outage green) and hardcoding `gdelt_articles = 0`, which in the
  real 2026-08-05 shape would have blamed a healthy GDELT for a FinBERT bug.
  Both fixed. A third pass then found the *success* path could reach the
  same 0/0 state, so the final fix went one layer down instead of patching
  scan.py twice: `dashboard.health._badge` now returns **red**, never None,
  for a 0 denominator — covering every branch that reaches it, present and
  future. Finally, the shared root of all of it: `_footer.html.j2` defaulted
  every badge to `badge-green` when `_badge()` returned None — asserting
  health the data does not support. All three metrics (prices, coverage,
  finbert) now fall back to a neutral `badge-unknown`, so "cannot judge"
  never again reads as "healthy". Guarded by twelve tests, all
  sabotage-verified.

  **Also observed, not fixed:** GDELT rate-limits hard. Scan 162 spent
  **87 minutes** (06:49→08:16) fetching 1522 headlines with 60/120/240s
  backoffs, all discarded one line later by the `NameError`. The pacing is now
  at least buying something, but it remains the dominant cost of the daily
  scan — its own item if it becomes a problem.

  838 → 857 tests (20 new; one is `TEST_DATABASE_URL`-gated so it runs in CI,
  not locally). Verified locally end to end with GDELT/FinBERT stubbed: health
  metrics populate, `sentiment_score` fills, and the INSERT tuples carry zero
  NULLs. First real production output lands on the next daily scan.

- **Sub-12px typography floor** (2026-08-15) — closes the last open item of
  "Design review findings (2026-08-09 audit)": "474 elements render under
  12px." Full spec/plan at `sector_momentum-notes/specs/2026-08-15-sub-12px-typography-design.md`
  / `plans/2026-08-15-sub-12px-typography.md`.

  **Re-measurement.** A live, full-page browser walk (every tab, both pages)
  found 72% of the audit's 474-element figure lived inside the collapsed
  per-theme `.breakdown-row` drill-down panel — an opt-in expert view a
  reader only sees after clicking to expand a specific theme, not something
  rendered by default. Excluding it, the always-visible surface was 177
  elements, which collapsed to a small set of shared CSS rules since every
  offender is styled by a class or tag selector, not an inline style.

  **Fixed: 34 CSS rules across 6 files** (`_tables.css.j2`, `_chrome.css.j2`,
  `_guides.css.j2`, `_health.css.j2`, `_sentiment.css.j2`, `_charts.css.j2`)
  raised to a 12px floor — table headers, rank-delta arrows, the Trend
  badge, the filter bar, guide-modal subsection headings, the language
  toggle, market-context chips, the site footer, the gate/lag banner, the
  health panel, tab notes, cohort-selector labels, the signed-in email
  label, the auth email field and status message, the entire Alerts modal
  (intro/warn/status/hz-note/hz-warn/topic-code), the Methodology modal's
  inline code, the horizon-note/review-status/band-legend cluster, the
  scan-history "Showing" badge, and — the most consequential single fix —
  **`.setup-badge`** (the ▲ Enter / ▼ Exit signal badge, the leaderboard's
  single most important piece of UI) and `.unbuyable-badge`, both previously
  9.8px.

  **First pass measured 15 rules, missed 19.** The original design-phase
  measurement was one live browser session, signed out, no modals open —
  structurally blind to anything gated behind sign-in (`.setup-badge`,
  `.scan-meta`) or rendered only inside a closed modal whose markup still
  exists in the DOM (`.alert-prefs` cluster). Caught during this same PR's
  own Task 3 verification: a systematic *static* grep of every `font-size`
  declaration in every CSS file — not just what one browser session happened
  to render — found the remaining 19, cross-checked and confirmed against
  markup. Presented the corrected, doubled scope back before continuing
  rather than silently expanding the shipped plan; decided to close all of
  it in this PR rather than leave a known gap.

  **Two "deliberately quiet" exemptions decided, not defaulted.**
  `.alpha-badge` (9px) stays exempt — its own code comment documents it as
  deliberately the quietest thing in the command bar, and a floor would
  defeat that intent. `.unbuyable-badge` carries an almost identical
  comment but was bumped anyway: unlike alpha-badge (a short qualifier
  tag), it conveys substantive information ("⊘ Not buyable") a reader needs
  to read and understand, not just a decorative qualifier — quiet and
  illegible are different goals. Also exempt, unchanged: `.chevron`, the
  `thead th` sort-direction ▲/▼ glyphs, and `.cc-info` — icon glyphs
  recognized by shape, not read as text.

  **Verification.** New `tests/test_typography_floor.py` (35 tests: 31
  in-scope + 4 exempt, sabotage-verified per finding). Live in-browser
  re-measurement after the fix found exactly the 5 exempted selectors'
  known instances remaining on both pages, nothing else. Forced the Alerts
  modal open and simulated a signed-in ▲ Enter badge render (both normally
  invisible without a real session) to visually confirm no wrapping or
  cramped layout at the new sizes — screenshotted clean.

  803 → 838 tests (35 new: 16 from the first pass, 19 from the follow-up);
  16 skipped unchanged.

- **Deferred UI/code polish sweep** (2026-08-15) — the four small,
  deliberately-deferred findings recorded under "Deferred UI/code polish (small,
  grouped sweep)". Two were genuine and fixed; two were re-verified against
  the current code first and turned out already resolved by unrelated work
  since the finding was written — same "verify before trusting a queued
  claim" discipline as the P2 a11y pass below.

  **Fixed: position-star tooltips are now translatable.**
  `dashboard/assets/positions.js`'s ★/☆ toggle set `title`/`aria-label` to a
  literal English string with no i18n hookup — Swedish readers kept seeing
  "Held — click to remove" after a language switch, unlike every other
  control on the page. It now carries `data-i18n-title`/`data-i18n-aria`
  (new keys `position_held_tip`/`position_mark_held_tip` in
  `i18n/_core.js.j2`). New `window.applyLangToEl(el, lang?)` in
  `_i18n.html.j2` translates one element's title/aria-label in place — a
  scoped counterpart to `apply()`'s four document-wide `querySelectorAll`
  passes, added after `/code-review` (see below) flagged the first draft's
  page-wide `window.applyLang()` call as unencapsulated, easy to forget at a
  future call site, and needlessly re-triggering `apply()`'s own
  `applyFilters()` side effect on every star click. Verified in-browser: EN
  ↔ SV both directions, both held states, and confirmed `applyFilters` does
  *not* fire from the scoped path.

  **Fixed: one shared Supabase client instead of three.** `auth.js`,
  `positions.js` *and* `alert-prefs.js` — one more caller than the finding
  named — each called `window.supabase.createClient()` independently,
  producing Supabase's "Multiple GoTrueClient instances detected" console
  warning for every extra one (harmless — all three read/write the same
  localStorage-persisted session — but noisy). New
  `dashboard/assets/supabase-client.js` creates the one client, exposed as
  `window.SMSupabase`, loaded right after `supabase.min.js` and before any
  consumer; the three files now reuse it and no longer call `createClient`
  themselves. Verified in-browser: the warning is gone and
  `window.SMSupabase` is the one instance in play. 3 new tests guarding the
  no-second-caller rule, the script load order, and that `build.py`'s
  `docs_assets/` copy block (gated the same as its three consumers, under
  `if auth_ctx["auth"]:`) actually includes the new file — sabotage-verified
  against all three (`tests/test_build_assets.py`'s existing regression test
  also independently caught the missing-copy case for free).

  **Stale: "themes-page setup badges are lagged for signed-in users."**
  Written 2026-07-26 against a since-deleted `themes.html.j2` — a *separate*
  page from the sector leaderboard with its own table, retired 2026-08-04
  when every cohort was unified onto one page (`index.html.j2`). The
  Enter/Hold/Exit badge system itself (`applyHorizonBadges()`, with its
  `sm:leaderboard-upgraded` listener) didn't exist until 2026-08-10 — *after*
  both the finding and the unification — and was built explicitly to
  recompute every row's badge from live data after the sign-in upgrade
  rebuilds `#leaderboard-table`. Verified live in a browser rather than
  trusted from the code comments alone: forced a row's `data-rank` from 1
  (badged `▲ Enter`) to 999 and re-dispatched `sm:leaderboard-upgraded` — the
  badge correctly disappeared, proving the recompute is live, not lagged.

  **Stale: dead guard branches in `scripts/walkforward_weights.py`.** The
  file doesn't exist — deleted whole in `1ff80d8` ("retire sector cohort from
  scan, backtest, reports and config"), the same refactor that produced the
  above. Confirmed the named `bench_returns`/`base_bench` `None`-guards were
  genuinely in that deleted version (`git show 1ff80d8~1:scripts/walkforward_weights.py`)
  before concluding there's nothing left to remove.

  **`/code-review` (8 angles, 6 agents) round:** the `applyLangToEl` rework
  above came directly from it — three angles independently converged on the
  same root cause (full-page rescan doing one element's job) from different
  directions (duplication, coupling risk, wasted work), which is why it's the
  fix rather than a smaller patch. Also caught: a duplicated 4-line rationale
  comment pasted into all three shared-client consumers, collapsed to a
  one-line pointer; and that this branch's name and commit type didn't match
  CLAUDE.md's `feature:`/`fix:` convention — both real fixes were genuine bug
  fixes, so `chore/…` and `chore:` became `fix/…` and `fix:`. One finding
  didn't survive independent verification: a reviewer flagged this entry's
  test-count line as wrong (794→801/17 skipped, not 795→802/16), measured
  from a fresh worktree — checked against a from-scratch worktree build of
  `main` myself and got 795/16, matching the original claim exactly; the
  reviewer's worktree simply hadn't run `dashboard/build.py`, so
  `test_unbuyable.py`'s documented "docs/ not built" skip fired once more
  than it should have. Not a defect in this change.

  795 → 803 tests (8 new, all sabotage-verified); 16 skipped unchanged.

- **P2 landmark and heading-outline findings fixed** (2026-08-15) — the
  remainder of the 2026-08-09 design review that wasn't already resolved. Both
  claims re-verified against the code first (the audit's `↗`-link finding
  turned out stale — see Queued — and the sub-12px finding needed re-scoping
  rather than a blind fix).

  **Landmarks: `<footer>` was the page's only one.** No `<main>`, no skip
  link, `<div class="command-bar">` instead of `<header>`, and
  `<nav role="tablist">` — an explicit ARIA role always overrides an
  element's implicit one, so a tablist inside `<nav>` can never expose a
  navigation landmark, and this was the page's *only* `<nav>`. Fixed by
  demoting the tab bar to a plain `<div role="tablist">` rather than trying
  to reconcile the two roles (a tablist isn't page navigation in the
  landmark sense anyway — ARIA's own authoring practices don't nest it in
  `<nav>`), and adding `<main id="main-content" tabindex="-1">` +
  `<header class="command-bar">`. Verified in-browser: landmarks are now
  `HEADER`, `MAIN`, `FOOTER` on both pages (was `FOOTER` alone), and the tab
  bar's click-to-switch behaviour is unchanged (`.tabs`/`role="tablist"`
  were always what CSS and JS keyed off, never the tag name).

  **The skip link's target needed `tabindex="-1"`, not just an `id`.**
  Found by testing the real interaction, not just the markup: clicking a
  bare `<a href="#main-content">` against a `<main>` with no `tabindex`
  updates the URL hash and scrolls the viewport, but `document.activeElement`
  falls back to `<body>` — keyboard focus never actually moves, so a
  keyboard user would have to tab back through the whole header to reach
  content, defeating the skip link. `tabindex="-1"` makes the element a
  valid focus target without adding it to the normal Tab sequence. Verified
  with a real keyboard `Tab` press (not just `.focus()` — a synthetic
  `:focus-visible` doesn't always trigger from script) landing on the
  skip link, then a click moving `activeElement` to `<main>`.

  **Heading outline: `h2 → h4` inside the tab-guide modals, on both pages,
  and in the Swedish translation.** The guide modal's own title is `h2`;
  every subsection inside it was `h4` with no `h3` between. Fixed by
  renaming to `h3` in `index.html.j2`, `sentiment.html.j2` **and**
  `i18n/_guides.js.j2` — the last one is swapped in wholesale via
  `data-i18n-html` on language switch, so missing it there would have
  reintroduced the skip only after switching to Swedish, the exact shape of
  bug this codebase has shipped before (badge/label i18n gaps, twice).

  **Two more skips found while verifying the audit's literal claim, not
  named in it:** "Badge scorecard" and "News sentiment" were `h3` with no
  `h2` before them anywhere on their pages (`h1 → h3`). Promoted both to
  `h2`, pure tag renames with their original inline styles untouched — no
  visual change, since both were already using inline `style=` attributes
  rather than a CSS class that would need a matching selector rename (unlike
  the modal `h4`s, which did need `.tab-guide-body h4` → `h3` in
  `_guides.css.j2`).

  10 new tests (`tests/test_a11y_landmarks.py`), including a reusable
  in-order heading-level-skip checker. Sabotage-verified: reverting any one
  of the three structural fixes (the heading rename, the nav→div change, or
  the `tabindex`) independently fails its test with a clear message;
  restoring passes. Two test bugs caught and fixed during that process — a
  bare substring check for `"<nav"` (and later a naive tag-boundary regex)
  both matched this fix's own explanatory HTML comment, which mentions
  `<nav>` in prose; fixed by stripping HTML comments before searching.

  **Code review caught a real gap in the heading-skip checker's own
  coverage claim.** It scanned only each page's own raw template text, so a
  skip introduced inside an *included* partial — `_header.html.j2`'s `<h1>`,
  `_footer.html.j2`'s Alerts `<h2>`, `_methodology.html.j2`'s `<h2>`/`<h3>`
  sequence, `_validation.html.j2`'s two `<h3>`s — could not have been caught,
  despite the checker's own docstring claiming it was "general enough to
  catch this class of regression anywhere." Fixed with a small recursive
  `{% include %}` resolver so the test scans the sequence actually rendered
  on the page. Sabotage-verified against the exact gap: demoting the
  footer's Alerts `<h2>` to `<h5>` (deep inside a partial, invisible to the
  pre-fix version of the test) now fails with "heading level jumps from h3
  to h5 — skips h4"; restoring passes.

- **Badges mute between reviews instead of firing on every scan** (2026-08-15).
  Ships the one committed piece of the 2026-08-07 horizon spec that shipping
  stopped short of — "the dashboard states the next rebalance date and whether
  today is one" — using the design agreed 2026-08-13 (option B1) and recorded
  in `sector_momentum-notes/specs/2026-08-12-horizon-cost-and-cadence-design.md`
  § Thread B.

  **Server: a forward review calendar with no price data to derive it from.**
  `src.backtest.replay.forward_rebalance_dates` — last *weekday* of each period
  (pandas' business-day aliases), not last *trading* day: a future market
  holiday calendar doesn't exist to consult, and the design accepted that
  divergence (2024-03-29 was both the last weekday of March and Good Friday) as
  cheaper than a holiday-calendar dependency, given `due-until-acknowledged`
  only costs a review surfacing one day early. `since` is inclusive — a build
  running ON a review day says so rather than skipping to next period, verified
  by test. `src.horizons.review_dates()` wraps it per-preset and feeds
  `build.py`'s `horizons_json` (next 6 dates, ISO strings).

  **One design bullet turned out moot, not a mistake to carry forward:** "pin
  the `2M` parity explicitly" assumed a preset on a 2-month cadence, which
  existed when that direction was chosen (2026-08-13) but was gone by the time
  this shipped — the 2026-08-14 preset cut left both `medium` and `long` on one
  monthly cadence, a property `test_presets_share_one_cadence` already
  enforces. `forward_rebalance_dates` still supports `2M`/`Q`/etc. generally
  (tested), but nothing live exercises the parity concern that motivated the
  bullet.

  **Client: `Rescore.reviewStatus`** (Node-tested, no DOM) decides due vs.
  muted from the calendar + the reader's own clock + a per-preset
  `localStorage` acknowledgment — never baked, so it stays correct on a page
  built days ago. Between-reviews (no calendar date has come due yet) is the
  *expected* state on most days of the month and mutes correctly, not a
  failure; only genuinely missing/malformed input fails open to the
  pre-existing always-on behaviour, on the reasoning that a board stuck
  permanently quiet is a worse failure than one that never mutes.
  `applyHorizonBadges()` toggles a `.muted` class on `entry`/`exit` badges
  only — `Hold` never mutes ("do nothing" has no action to hold back) — and
  badge *text* is untouched, matching "mute, don't hide."

  **`.muted` drops the tinted background, keeps the border and text colour** —
  found, while implementing, that this makes contrast *better* than the active
  state, not worse: tinting a badge's background toward its own text colour
  (the idiom several 2026-08-11/12 WCAG fixes had to correct) always reduces
  contrast, so the muted state beats its own active state in both themes:
  entry 5.16:1/8.29:1 muted vs. 4.53:1/6.76:1 active, exit 4.76:1/6.06:1 muted
  vs. 4.53:1/5.72:1 active (light/dark). Pinned as a regression test, not just
  asserted.

  New UI: a "Review due [Done ✓]" / "Next review: <date>" chip beside the
  horizon note, i18n'd (Swedish was the exact bug class that shipped once
  already on `horizon_label`/`horizon_note` — added and tested this time, not
  discovered after the fact).

  25 new tests across 4 files (`test_rebalance_horizon.py`, `test_horizons.py`,
  `test_review_due.py`, `test_color_theme.py`); `test_dashboard_js.py`'s
  existing `horizons_json` fixture was also updated — its own docstring
  requires staying in step with `build.py`'s real construction, and it would
  otherwise have silently stopped catching a missing `review_dates` key. Two
  of the new tests sabotage-verified live in this session (the fail-open
  default, and the `.muted` CSS rule) — both caught their reverted bug and
  passed clean restored.

  **Code review caught two real bugs, both fixed in-branch, both with a
  sabotage-verified test:**

  - **`currentReviewStatus()` called `window.Rescore` with no guard**, unlike
    every other call site in this file (`applyHorizonBadges`, `auth.js`). It
    runs BEFORE `applyHorizonBadges()` in both `initHorizonSelect()` and
    `switchHorizon()`, so a stale cache or blocked script wouldn't just lose
    the review chip — it would throw and abort badge rendering, band
    boundaries and the Done-button binding too. Fixed with the same guard
    shape used everywhere else; `applyHorizonBadges()`'s own now-redundant
    guard was simplified since `currentReviewStatus()` guards itself.
  - **The review calendar was baked from the server's UTC date with no
    margin.** A reader whose LOCAL calendar date trails the server's (anyone
    west of UTC, for part of every day) could have the review date that is
    due *today* for them already excluded from the baked array — and since
    every later build's window only moves forward, it would never reappear
    for that cycle. `review_dates()` now pads `since` two days into the past
    before generating the calendar (long enough to absorb any timezone in the
    world, short enough that monthly-or-longer cadences can never pull in an
    extra, unwanted date). Centralised in `review_dates()` itself, not the
    caller, so a future second caller can't forget it and reintroduce the bug.

- **Preset provenance corrected in two places, both pointing at superseded
  evidence** — found by asking what is written down that a newcomer would trust
  and act on.

  - `config/weights.yaml` still said the cells were "chosen from the 2026-08-08
    sweep" and pointed at the 0/10 bps result files. Those describe the original
    three-preset selection; the live cells were re-picked 2026-08-14 on warm
    history at 70 and 100 bps. Corrected, with the old files labelled as history
    and the reproducing command given instead — a command beats a snapshot now
    that the harness is trustworthy.
  - `sector_momentum-notes/specs/2026-08-12-horizon-cost-and-cadence-design.md`
    was **actively misleading**: it called the starved 100 bps sweep "the most
    rigorous preset-selection work done so far", stated it would not
    second-guess it, and told the reader to choose between four "survivors"
    (`M/5/7`, `M/4/8`, `M/4/5`, `M/4/4`) that came from the invalid harness —
    plus a false claim that `long` survives. Anyone following it would have
    redone bad work and possibly adopted `M/5/7`. Now carries a SUPERSEDED
    banner at the top; its Thread A cost research is unaffected and says so.

  **Reading the whole file then found four more stale claims in
  `config/weights.yaml` alone**, none of which any test or renderer touches: the
  header still described the withdrawn sentiment blend control and called Google
  Trends an info-only source (it is removed entirely); the cost table concluded
  "the Short preset is the WORST of the three" for a preset that no longer
  exists; the minimum-fee caveat reasoned about when Short "starts winning
  again"; and the `default:` note pointed at the per-user horizon backlog item
  that was closed as deliberately-not-built. All corrected, with the cost table
  kept but labelled historical — its shape (the ranking inverts with cost) is
  still why the value is never 0 again.

  **`/handover` was extended as a result.** Its sweep only covered the three
  markdown files, so it could not have caught any of this. It now sweeps
  `config/*.yaml` comment blocks too, and adds a category for **provenance
  claims** — "chosen from the <date> sweep", "see <file> for results" — which
  outlive the decision they describe. Prefer naming the command that regenerates
  a result over pointing at a snapshot of it.

  Both `2026-08-08-horizon-sweep-results-*.md` files were checked and are
  **warm** (`--start 2003-01-01`, the fetch start), so they are wrong only about
  which lineup exists, not about their own numbers. The full warm frontier from
  2026-08-14 was deliberately **not** archived: it lives in a scratchpad that is
  gone, and a stale table is worse than a one-line command that regenerates a
  trustworthy one. *(2026-08-14)*

- **`/handover` verification pass — one real gap found and fixed** — ran the new
  command against merged `main` (60d95ed). State clean: no open PRs, one branch,
  757 tests passing, dashboard builds.

  **`SUPABASE_PUBLISHABLE_KEY` was missing from README's env-var table** while
  being read by `dashboard/build.py` and set in both CI workflows. It is in
  `.env.example`, so the omission was only in the table a newcomer actually
  reads when setting up — and the failure is **silent**: `_auth_ctx()` returns
  auth-disabled with no log when the key is absent, so a fresh install gets no
  sign-in, no badges, no position stars and no alerts, with nothing saying why.
  Documented, including that symptom, since "the dashboard looks feature-poor"
  is the shape the bug actually takes.

  Everything else verified correct, which is what makes the above worth
  trusting: no phantom CLI flags (the `--no-cache` hits are the sentences saying
  it is gone), every file named in the docs exists, `DATABASE_URL` and
  `SUPABASE_SERVICE_KEY` are read where claimed, theme count (18), signal count
  (8) and pillar split (50/50) match config, both presets' sell-past ranks (9 and
  13) match `top_n + buffer`, no duplicate Queued or Done headings, and the
  actionable-day item correctly labels `M/4/5` as shipped and `M/5/7` as not
  adopted.

  The command also caught two false positives in its own sweeps on first run
  (a missing entry point, and 23 phantom file misses from bare basenames), both
  fixed in `.claude/commands/handover.md` before it shipped. *(2026-08-14)*

- **Handover sync: docs and backlog audited against the code** — five PRs landed
  after the 2026-08-13 documentation pass, and `CLAUDE.md` had never been
  re-checked at all. Everything below was verified against the code, not
  inferred from the Done list.

  **`CLAUDE.md` was the worst of it, and it is the first file an agent reads.**
  Its Project overview still described the sector era — *"US SPDR (GICS 11) +
  STOXX Europe 600 sectors (14, incl. standalone sub-sectors)"* — for a project
  that has been 18 themes in a single cohort since 2026-08-05. It also
  documented a `trends-cache` bucket and a `--no-cache` flag for a **Google
  Trends pipeline that no longer exists anywhere in the code** (no module, no
  references, and `scan.py` has no such flag). Both corrected, with `region`'s
  load-bearing legacy role spelled out.

  **README** lost the same phantom `--no-cache` flag and the `trends-cache`
  bucket from its env-var table, and gained a line stating sentiment is alpha
  and moves nothing.

  **ARCHITECTURE**: §4 claimed *"the dashboard offers a client-side toggle to
  blend it at a chosen weight"* — that control was withdrawn the day before;
  §11 said per-user alert horizon was "queued" when it had been closed as
  deliberately-not-built; §12 described the dead `trends-cache` as live. Added
  the shared-modal note (methodology, tab guides, market-context chips, alerts)
  and the market-context and alerts-modal designs, and refreshed the stale
  "Last updated" stamp.

  **Backlog audit — two items were wrong:**

  - *Design review findings (2026-08-09) — P1/P2 remainder* listed two P1s that
    are fixed: mobile (sticky columns plus seven 44px rules, shipped
    2026-08-12) and the gate modal (`auth.js` now binds `SMModal`). It also
    cited "the ⚙ that changes ranking weights is 7×18px" — that control no
    longer exists. Rewritten to the genuine remainder; both P2s re-verified as
    still open (no `<main>`, no skip link, and `breakdown.py`'s `↗` link still
    has no accessible name).
  - *Badges don't say whether today is an actionable day* **carried a materially
    wrong number.** Notes in this repo said the cadence gap had narrowed to 4
    trades/yr after the preset re-pick. That was `M/5/7`, which was **not
    adopted** — `M/4/5` shipped, and its gap is **10** (22 acting weekly vs 12
    modelled) against the old preset's 11. The item's value is close to its
    original sizing, not a quarter of it. Corrected with the measured table.

  The other eleven queued items were checked against the code and are accurate:
  the sentiment flag is off, the composite still scores 8 signals, `region`/
  `gics_sector` are still in the schema, `positions.js` still hardcodes English
  star tooltips, yfinance is still the only price source (the stooq references
  are comments explaining its removal), the weekend cache grace is still there,
  and `correlation.py` still does not call `align_cohort_asof`. No union-merge
  artefacts. 757 tests pass. *(2026-08-14)*

- **Alerts now state which band they run on, and warn when it is not yours —
  instead of the per-user horizon column that was filed** — the queued item
  proposed a `horizon` column on `alert_prefs` plus per-user band-crossing
  evaluation in the scan. Checked before building, and the premise did not hold
  up well enough to justify it:

  - **The impact today is zero.** `horizons.default` is `medium` and Jonas runs
    Medium, so alerts and board already agree. The plumbing would have changed
    nothing that reaches the inbox.
  - **The item's own deferral rationale still stands** — *"one shared default is
    defensible until more than one person uses the dashboard"* — and it is still
    one person. A schema migration plus per-user evaluation is multi-user
    machinery for a single user, on the one code path where a bug means a missed
    or spurious email.
  - **The item was also wrong about where the behaviour lives.**
    `src/alerts.py` never names a horizon; `detect_badge_events` calls
    `_compute_setup(row)` with no horizon and that falls back to
    `default_horizon()`. Right conclusion, wrong location.

  The real risk is not multi-user, it is the single user's UI selection silently
  disagreeing with their alerts — the selector persists to `localStorage` and
  never reaches the scan. So the alerts modal now says which band is in force,
  and spells out the disagreement concretely when the reader has switched:
  running Long against Medium alerts, *"Alerts flag Exit past rank 9, where your
  board waits until 13, and Enter within the top 4 against your board's 5."*

  Recomputed on open, not once at load, since the reader can switch and reopen
  without a reload. Numbers live in their own nodes with the words carrying
  `data-i18n` — interpolating a whole sentence would be wiped on the first
  language switch, the same reason `renderHorizonStats()` is built that way.

  Fixed in passing: `sentiment_ranking_enabled` was **duplicated** in the sectors
  context and **absent** from the sentiment one, where it had been working only
  because an undefined Jinja variable is falsy.

  **Three self-inflicted bugs, all whitespace or comment handling, all caught in
  the browser rather than by reading the diff:** a fragment beginning with a word
  rendered as `top4 against`; the fix's `{#- -#}` comment stripped the very space
  it was preserving; and rewriting that comment to quote the delimiters
  terminated it early and **leaked a sentence of explanatory prose into the
  modal**. The last one is now a test that scans every template for comments
  containing comment delimiters — mutation-verified, and it would have caught the
  leak on its own.

  **A fourth bug, found while testing the finished feature:** `switchHorizon()`
  never synced the `<select>` — it is only set at init. The usual path is the
  select's own `onchange`, so it already matches, but any programmatic call (a
  deep link, a test) left the dropdown displaying one horizon while the stats
  strip, the band cut lines and this new alerts notice all described another.
  One line, and verified both ways round. 757 tests pass. *(2026-08-14)*

- **Alerts moved into a modal opened from the footer** — it had been a
  permanently-rendered block appended *below* the site footer, after the
  disclaimer and Methodology link: an odd reading order, and an odd home for a
  control only signed-in readers can use. Now it mirrors the Methodology
  treatment sitting beside it.

  Accessibility comes entirely from the shared `_modal.js.j2` helper —
  focus trap, Escape, backdrop close, focus restore — rather than a third
  hand-rolled implementation. That helper exists precisely because the
  2026-08-09 audit's P1 finding was a modal declaring `aria-modal="true"` and
  implementing none of it.

  **Both traps the queued item named were real and are handled:**

  1. `alert-prefs.js` owns `#alert-prefs`'s `hidden` attribute to mean *"alerts
     are available to this reader"*, while a modal overlay uses `hidden` to mean
     *"closed"*. The overlay is therefore a **new wrapper** with the existing
     section nested inside, so `alert-prefs.js` keeps working untouched.
     Verified in the browser: opening and closing the dialog leaves
     `#alert-prefs.hidden` exactly as it was.
  2. The footer link is inside `{% if auth %}`, so guests never see a link to a
     dialog that cannot exist.

  Went further than the item on one point: the link also **starts hidden and is
  revealed only once alerts are actually available**, because availability is
  only known after an auth round-trip. A signed-in reader whose `alert_prefs`
  table is missing would otherwise open an empty dialog. Availability is now
  written in one place — `setAvailable()` owns both the panel and the link, so
  they cannot drift — and the `#alerts` deep link fires from there rather than
  beside the trigger binding, since the hash arrives long before the data.

  The panel also lost its own card chrome (max-width, centring margin,
  background, border): the dialog already supplies all of it, and keeping both
  drew a box inside a box.

  **A test caught a bug in its own test, worth recording.** The auth-gate test
  passed under mutation — removing the real `{% if auth %}` did not fail it —
  because the *comment* explaining the gate contains the literal string
  `{% if auth %}`, and the search matched the comment. The test now strips Jinja
  comments before analysing. Prose inside a template is not inert to a
  source-scanning test.

  `tests/test_alerts_modal.py` (8 tests) pins the wrapper separation, the auth
  gate, the hidden-by-default link, single-writer availability, use of the shared
  helper, the accessible name, and the deferred deep link. 750 tests pass.
  *(2026-08-14)*

- **RSS/Atom feed removed** — a second public surface that had to stay correct as
  the product changed, with no known subscriber. It had already survived the
  sector→theme migration and the rebrand without anyone reading it. Deleted
  rather than maintained.

  Removed: `dashboard/feed.py` (122 lines) and `dashboard/templates/feed.xml.j2`;
  `build.py`'s import block and section 6, which rendered and wrote
  `docs/feed.xml`; the footer `RSS` link; the `<link rel="alternate">` tag in
  both page heads; and the `feed.py (Atom)` entry in ARCHITECTURE's module list.
  `dashboard_url` and `feed_url` lived inside section 6 and are used nowhere
  else, so nothing was stranded.

  One thing the queued footprint missed: a comment above the scan-index build
  named the feed as one of three consumers of `all_scores_df`. Corrected to two
  rather than left describing a module that no longer exists.

  **No tests referenced the feed**, which is exactly why this needed a build
  check rather than a green suite. Verified by deleting `docs/` entirely and
  rebuilding from scratch — the item warns that a stale `feed.xml` lingers in a
  gitignored local `docs/` and would mask the change. The fresh build completes,
  logs nothing about a feed, emits no `feed.xml`, and neither built page contains
  the string. Footer renders `Analytical tooling, not investment advice.
  Methodology`, the Methodology modal still works, and the console is clean.
  742 tests pass.

  **Accepted consequence:** GitHub Pages keeps serving the last-deployed
  `feed.xml` until the next CI run, and any subscriber that does exist then gets
  a frozen feed rather than a 404. Shipping a final "feed retired" entry first
  was considered and declined — nobody subscribes, and it is ceremony that would
  itself need the generator kept alive to publish. *(2026-08-14)*

- **Market-context chips are now one tappable control with a shared
  explanation** — `Live` / `SPY` / `VIX` were the first thing on the page and the
  least explained: a reader could not tell what they measured, what the colour
  asserted, or what to do differently because of them.

  The good framing already existed — *"They change no scores. They tell you how
  much to trust the board"* — but it was buried in the leaderboard tab guide,
  behind a modal, on one page only, reachable only if you already suspected there
  was something to learn. And the chips' `title` tooltips **do not exist on
  touch**, so since the mobile work shipped they were permanently unexplained on
  a phone. That is why this became a tap target rather than better tooltip copy.

  - The three chips are now a single `<button>` opening a dedicated
    `guide_body_market_context`, in a **shared partial** included by both pages —
    the header is shared, so the explanation had to be too. The leaderboard guide
    keeps a one-line pointer instead of a second copy.
  - The guide states what the old copy did not: what `Live` means (you are seeing
    the latest scan, not the delayed public snapshot), that rank is *relative*,
    and **why these are context and not signals** — SPY-vs-200-DMA was measured
    as a scoring input in July 2026 and deliberately parked, because no
    regime-conditional scheme beat the fixed 50/50 split in both regions. An
    evidence-backed decision not to act, rather than an omission.
  - `Live` was a hardcoded English word with no tooltip and no explanation
    anywhere. It now carries `data-i18n`/`data-i18n-title` and is inserted into
    the control rather than loose in the meta cluster.
  - Listed in the `pointer: coarse` 44px rule. It already measured 44px from the
    flex row; now that is guaranteed rather than incidental.

  **Two bugs caught by checking in the browser rather than trusting the diff:**

  1. The dialog heading became **"SPY +10.1% VIX 14.6 ⓘ"** — the shared guide
     dispatch titles the modal from the trigger's `textContent`, which here is
     live data. The dispatch now prefers an explicit `.cc-label`, a
     visually-clipped span that the existing `[data-i18n]` pass translates for
     free. Verified every other guide button still mirrors its own wording.
  2. **Two invented `data-i18n-*` attributes were nearly shipped dead.**
     `data-i18n-label` and `data-i18n-guide-label` are not implemented by
     `_i18n.html.j2`, so they do nothing — no error, the string just stays English
     for Swedish readers. Fixed by adding generic `data-i18n-aria` support
     (mirroring the existing title arm) and moving the heading into the span
     above.

  `tests/test_market_context_chips.py` (15 tests) pins all of it, including a
  general guard that **enumerates the attributes `_i18n.html.j2` implements and
  fails on any `data-i18n-*` in use that is not among them** — mutation-checked
  with a fake attribute. That is the test that would have caught (2) on its own,
  and it now protects every future control. Verified in both languages, on both
  pages, at desktop and 375px. 742 tests pass. *(2026-08-14)*

- **Union-merge resurrections in `BACKLOG.md` now have a named check on both
  sides of a merge** — `merge=union` (`.gitattributes`) keeps both sides' lines,
  which is correct for concurrent Done additions and has two silent failure
  modes: concurrent edits to one paragraph get concatenated, and **deletions get
  undone**. On 2026-08-14 that resurrected a 51-line queued item — #208 deleted
  it, #209 still carried the lines — leaving it in Queued *and* Done at once,
  after a merge git reported as clean.

  Two checks, because the symptom differs either side of the merge:

  - **`CLAUDE.md`** now carries the pre-merge check, a `diff` of the Queued
    section headings against `origin/main`. Every `>` line must be an item the
    branch intends to add; anything else is a resurrection. This is the one that
    actually caught it, and it belongs there rather than in the command because
    it applies to every branch that touches the file.
  - **`/backlog-sync`** gained step 3, an after-the-fact sweep for when it has
    already merged and there is no base left to diff: an item present in both
    Queued and Done (the resurrection signature), plus exact `sort | uniq -d`
    checks for a duplicated Queued heading and a duplicated Done headline. It
    runs before classification, and its findings are drift to fix in the pass
    rather than items to classify — where an item appears twice, the Done entry
    is the truth.

  The command's "When to run" section now points at the pre-merge check too, so
  the two are discoverable from each other rather than being separate folklore.

  **The check caught a real bug in the commit that added it.** Removing the
  queued item this replaces, the edit found the section's end by searching for the
  next `## ` — which matched inside the item's own fenced code block (`## ') \`),
  truncating the deletion and leaving a 33-line fragment as the first "item" in
  Queued. The heading diff flagged it immediately. `CLAUDE.md` now warns against
  ending a section-delete on a `## ` search for that reason, and notes that a
  flagged line which is not a plausible heading means a malformed edit rather than
  a resurrection.

  Filed and implemented in one change rather than left queued: the whole point is
  that the failure is invisible, so a check sitting in the backlog protects
  nothing. 721 tests pass (documentation only — no code paths touched).
  *(2026-08-14)*

- **Sentiment marked alpha, withdrawn from the ranking, and its column hidden**
  — the state of the FinBERT/GDELT output is not known well enough for it to move
  the board, so it now moves nothing and says so.

  - **The "Ranking" cogwheel is no longer rendered** (`SENTIMENT_RANKING_ENABLED`
    in `dashboard/build.py`). **Not hidden — absent.** The sentiment wiring in
    `index.html.j2` early-returns when the control is missing, and that is what
    stops the blend being re-applied on load from a stale
    `localStorage.sentimentEnabled`. A `display:none` would have hidden the
    control and left that path live. Verified in the browser by seeding
    `sentimentEnabled=true` and confirming the ranking still came up by pure
    composite.
  - **The leaderboard's Sentiment column is hidden by CSS**, deliberately rather
    than removed: the column indices are positional (`sortTable(6)`, `data-col`,
    `tbody td:nth-child(n+4)`), so dropping the cell would shift every reference
    after it. Confirmed `data-col="6"` still resolves to Rank Δ.
  - **An `alpha` badge** sits on the Sentiment nav segment on both pages, and the
    page's note was rewritten — it had claimed "Sentiment weighting affects the
    leaderboard ranking only", which stopped being true the moment the control
    was withdrawn: it now affects nothing.

  **The stored composite was already clean** and did not need changing: `scan.py`
  has always passed `blend_sentiment=False`, so `scores.composite` is pure price
  data and always was. Sentiment only ever reached the ranking through the
  client-side slider. `tests/test_sentiment_alpha_gate.py` now pins that
  invariant at its call site — mutation-checked by flipping it to `True` and
  watching the suite fail — alongside both directions of the flag, so the gate
  cannot silently become a deletion.

  One i18n trap caught on the way: the page note moved from `data-i18n` to
  `data-i18n-html`, and those resolve from **different bundles** (`SV` vs
  `SV_HTML`). Leaving the Swedish string in `SV` would have left it silently
  unused. Moved to `SV_HTML` in `_sentiment.js.j2`.

  An existing test asserted the control was present; updated to assert its
  absence, with a note that `RESCORE_DATA` and `rescore.js` stay unconditional —
  the slider is only one of their consumers, the badge rules and trajectory
  maths are the others. 727 tests pass. Restoring it is queued. *(2026-08-14)*
- **Fund costs (TER): closed with no model change — the item's premise was
  wrong, and the holdings question is answered** — filed on the reasoning that
  the backtest ignores an annual drag and should take "a flat annual haircut of
  roughly 0.5%", plus a smaller one on the benchmark.

  **That would have double-counted.** An ETF's price series is already net of the
  fund's expenses — NAV is struck after fees — so the modelled CAGR carries the
  US fund's TER and ACWI's series carries ACWI's. Subtracting a full TER on top
  removes the same cost twice. It would have looked like prudence and been an
  error, which is the reason this is recorded rather than quietly dropped.

  The correct quantity is the **UCITS-minus-US differential**. Measured across 9
  of the 17 themes with a recorded equivalent (public fund pages, 2026-08-14
  snapshot): BOTZ→XAIX −0.33pp, ICLN→IQQH +0.26pp, ITA→DFEN +0.17pp,
  QTUM→QUTM +0.15pp, LIT→LI7U −0.15pp, CIBR→W1TB −0.14pp, BLOK→DAVV −0.08pp,
  SOXX→VVSM +0.02pp, GDX→G2X +0.02pp. **Mean −0.01pp, mixed sign.** The
  assumption that the UCITS wrapper costs more does not hold — US thematic ETFs
  are frequently the pricier side. Nothing systematic to model, so no code
  changed.

  **The data question is answered (2026-08-14): the recorded UCITS entries ARE
  what is held.** Jonas buys the fund named on each leaderboard entry, so the
  differential table describes real costs and the queued config audit is
  unnecessary.

  One legacy exception, deliberately not acted on: the AI & Robotics exposure is
  **L&G ROBO Global Robotics** (0.8%/yr ongoing + 0.03% transaction), predating
  the project, where the config records **XAIX at 0.35%**. That flips this
  theme's differential from −0.33pp to about +0.12pp — roughly 0.45pp more drag
  than recorded on one of four or five equal-weight positions, so ~0.1pp of
  portfolio per year (~300 SEK on 300k). It **retires itself**: the next time AI
  & Robotics leaves the band, the replacement is the recorded XAIX. Not worth a
  config exception for a position that resolves on its own next Exit.

  Still true and untouched: `Shipping` has no UCITS entry, consistent with it
  being flagged unbuyable. *(2026-08-14)*

- **README and ARCHITECTURE brought back in line with the code, with tests so
  they cannot drift silently again** — both were rewritten 2026-08-09 and were
  accurate then; a week of changes had moved past them and nothing checked it.

  Corrected:

  - **`README.md` advertised "Short / Medium / Long".** There are two presets,
    both monthly, and it now names their actual bands — `Medium` (hold 4, sell
    past rank 9) and `Long` (hold 5, sell past rank 13) — plus why the weekly one
    was removed: cadence contributes little, band width does the work.
  - **The badge rule was described as "Entry / Exit".** It is Enter / Hold /
    Exit, action-aware against marked holdings, and signed-in only.
  - **The band cut lines were undocumented** — now described, including that they
    appear only under a rank sort, since a reader who sorted by Composite and saw
    them vanish would read that as a bug.
  - **The fetch-versus-evaluation window split was undocumented** and now has its
    own subsection in ARCHITECTURE §9, with the concrete failure it caused: the
    starved sweep put `M/5/7` 1.9pp ahead of `M/5/4`, warm it lands 0.8pp behind,
    and the 2.1pp disagreement between the two harnesses on an identical cell is
    what exposed it. A trap that has already produced one wrong analysis belongs
    somewhere more findable than a code comment.
  - **ARCHITECTURE §6** now records that `long` holds *more* names than `medium`
    (deliberate — concentration is a risk choice, holding period is a horizon
    choice), that `applyHorizonBadges()` is the single client-side badge writer,
    and that four copies of `rank <= 3` had drifted out of step with `top_n`
    before `inBuyBand` collapsed them.
  - `scripts/` was missing from the README's project tree, including
    `horizon_sweep.py` — the tool that actually chooses the presets.

  **New `tests/test_docs_match_config.py` (5 tests)** pins the claims a config
  change can falsify: no retired preset label may appear in either document,
  every shipped label must appear in the README, the stated preset *count* must
  match `horizons()`, and the quoted sell thresholds must equal
  `top_n + buffer`. Each was mutation-checked — reintroducing "Short", writing
  "three presets", and shifting a band edge to rank 14 each fail the suite — so
  they are not vacuous. The retired-label vocabulary is a deliberate literal
  list, since only an explicit set distinguishes "Short is gone" from "Short was
  never mentioned".

  Deliberately narrow: they pin statements the config owns, not the prose.

  **Corrected in review:** the first draft of ARCHITECTURE §9 claimed
  `validate_eval_start()` "rejects a window that starts inside the warm-up",
  which overpromises. It proves the *fetch* was not truncated; it cannot promise
  every ticker is warm, because warm-up is bounded by each fund's inception. At
  the default run's first evaluated date (2008-03-31, set by ACWI's inception)
  only **7 of 18 tickers have 200 bars behind them** — the other 11 did not exist
  yet, which no fetch window can fix and which `score_calendar`'s
  `min_members=top_n` is what actually handles. Measured, then written down.

  721 tests pass. *(2026-08-14)*

- **`backtest.py --start` no longer starves the warm-up — and the two harnesses
  now agree** — the twin of the sweep bug fixed on 2026-08-13. `backtest.py`
  passed `args.start` straight to `fetch_prices`, and `run_theme_track` then
  derived the rebalance calendar from that truncated index, so any windowed run
  scored its opening ~200 bars on a NaN `above_200dma`.

  Latent rather than live — CI and every scheduled run use the default — but it
  is the same defect that inverted the horizon preset ranking when it bit the
  sweep, so it would have misled the next person to run a windowed backtest by
  hand.

  **Fixed by moving the shared rule into `src/backtest/replay.py`** rather than
  copying the sweep's constants into a second file: `FETCH_START`,
  `WARMUP_DAYS`, `DEFAULT_EVAL_START` and `validate_eval_start()` now live next
  to `rebalance_dates`, where `since=` already lived, and
  `scripts/horizon_sweep.py` imports them under the names it already used. Two
  copies of a safety constant is exactly how these two entry points came to
  disagree about which window they were running.

  `run_theme_track` gained `since=None` — opt-in, so every existing caller is
  unaffected — threaded down to `rebalance_dates`, and `backtest.py` now fetches
  from `FETCH_START` while `--start` bounds evaluation only. A window inside the
  warm-up is rejected with exit 1 rather than silently filtering nothing.

  Verified end to end, not assumed:

  - the default run reproduces the committed `backtests/summary.json` exactly,
    metric for metric and window for window (the data begins 2008-03, so the
    default calendar was never affected);
  - `--start 2003-06-01` exits 1 with the reason;
  - **`--start 2015-01-01` now matches `scripts/horizon_sweep.py` at the same
    window and cost to the decimal** — medium 17.6%/0.90/−24.1%, long
    15.5%/0.83/−32.8%. The two harnesses disagreeing by 2.1pp on an identical
    cell is what exposed the original bug; they now agree.

  **Two guards added in review, both hazards this fix itself introduced** by
  making windowed runs useful for the first time:

  - `--out` defaults to the git-tracked `backtests/`, so an exploratory
    `--start 2015-01-01` would have replaced the *published* curve with a
    windowed one and exited 0. A non-default window writing to the default
    directory is now refused, with the escape (`--out`) named in the message.
  - An over-late `--start` left every track `None`, and `write_results` happily
    wrote `{"tracks": {"medium": null, "long": null}}` over a good artifact and
    returned 0. An all-empty result now fails without writing.

  Eleven new test functions (13 cases with parametrisation), all offline: three
  pin `since` at the engine boundary (it bounds the curve, `since=None` is
  byte-identical to the old behaviour, an over-late window takes the existing
  "not enough data" path rather than raising), and eight
  cover the CLI by replacing `fetch_prices` with a probe — including the direct
  assertion that **`--start` never reaches `fetch_prices`**, which is the bug
  itself. 712 pass. *(2026-08-14)*

- **The highlighted rank badge follows the active horizon instead of a
  hardcoded 3** — `.rank-badge.top3` marked ranks 1-3 from a literal that knew
  nothing about the preset. Harmless until the band cut lines shipped
  (2026-08-14); after that, three gold badges sat above a buy-band line drawn
  under the *fourth* row, reading as one of them being wrong, and `long` (top 5)
  widened the disagreement to two rows.

  **The literal existed in four places, none aware of the horizon:** the server
  bake, the sentiment rescore, the signed-in rebuild (`auth.js`) and the
  scan-history rebuild — two more than the item recorded when it was filed. All
  four now go through one rule, `Rescore.inBuyBand(rank, horizon)`, alongside
  `setupForRank`. The two paths that end by triggering `applyHorizonBadges()`
  (rescore, signed-in rebuild) dropped their copy entirely rather than writing a
  class the single writer immediately overwrites.

  Renamed `top3` -> `in-buy-band`: a class name asserting "3" is what let it
  drift, and the same name is referenced from the template, the CSS, the
  scan-history path and the WCAG contrast tests.

  Written in `applyHorizonBadges()`, NOT `applyBandBoundaries()`, because it is a
  per-row property rather than a boundary — it must stay correct when the table
  is sorted by Theme or Composite, where the cut lines are deliberately
  withdrawn. Verified: sorting by Theme keeps the highlight and drops the lines.

  **Caught during implementation:** `_compute_setup` also sets `in_buy_band`,
  but `build.py` only called it on the *ungated* branch, so the shipped (gated)
  build rendered every badge unhighlighted — reintroducing the same disagreement
  from the other side. The call now runs before the gate, with `setup` still
  withheld from guests immediately after; two tests pin it, one on build.py's
  branch order and one end-to-end through the template.

  Also verified in the browser after a cache-bust (a stale `rescore.js` made the
  first check look like a logic bug): Medium highlights 1-4 with the cut after 4,
  Long highlights 1-5 with the cut after 5. 704 tests pass, including four new
  cross-language `inBuyBand` cases covering average ranks (4.5) and the
  `null <= 4 === true` trap in JavaScript. *(2026-08-14)*

- **Band boundaries drawn in the leaderboard, so the Horizon control is
  visibly doing something** — the selector claimed to "set the Enter/Exit band"
  while rendering byte-identical badges for any book held at the top of the
  table, because `Hold` spans the whole band and every preset contained those
  ranks. The band was always real; nothing drew it. Two solid rules now mark the
  cuts: after the last row inside the buy band (`top_n`) and after the last row
  still held (`top_n + buffer`), with a small legend beside the selector.
  Switching preset moves both lines — Medium cuts after ranks 4 and 9, Long
  after 5 and 13.

  Solid, because rows already carry a dashed separator: a dashed cut would have
  read as one more row divider.

  **Anchored to the last qualifying VISIBLE row, not to a fixed rank.** Ranks
  tie (`rankAverage` yields 4.5, so `rank === top_n` can match nothing), and
  filters hide rows, which would strand a line on a hidden row. Anchoring to the
  last visible row in the band keeps "everything above this line is inside the
  band" true under every filter — verified by hiding ranks 3–4 and watching the
  buy cut move to rank 2, and by hiding every row and watching both lines and
  the legend disappear rather than linger.

  Two rendering traps, both caught by checking rather than assuming:

  1. **The pinned columns swallowed the line on mobile.** Below 600px the first
     two cells are `position: sticky` with an opaque background stacked above
     the row, so a `border-bottom` on the `<tr>` is hidden behind them — the cut
     would have started one column in. Redrawn as an inset shadow on those
     cells, the same workaround the file already used for the vertical edge.
  2. **The fix then lost the cascade on column 2 only.** The existing right-edge
     rule is `#leaderboard-table td:nth-child(2)`, and an ID outranks any number
     of classes, so the class-only override was silently discarded on exactly
     one cell. All four rules now carry the `#leaderboard-table` prefix.

  Verified in the browser at desktop and 375px, in light and dark: the cuts
  render in both themes, the legend is hidden when no lines are drawn, and the
  colours are distinct (sage `--up` for the buy cut, terracotta `--down` at
  heavier weight for the exit cut, which is the more consequential of the two).
  Shown regardless of badge gating — `top_n`/`buffer` are already public in the
  page's `HORIZONS`, so the lines leak nothing, and a guest gets a selector that
  visibly does something. 698 tests pass. *(2026-08-14)*

- **Cut from three horizon presets to two: `medium` → M/4/5, `long` → M/5/8,
  `short` removed** — the lineup is now **one cadence, two band widths**. The
  reader's only dial is how far a holding may fall before it is sold (50% of the
  universe vs 72%).

  | preset | was | now | CAGR | Sharpe | trades/yr | hold | max DD |
  |---|---|---|---|---|---|---|---|
  | `medium` | M/5/4 | **M/4/5** | 15.0 → **15.6%** | 0.84 → **0.85** | 16.8 → **12.9** | 94 → 120d | −35.4 → **−30.8%** |
  | `long` | 2M/4/6 | **M/5/8** | 14.1 → 14.0% | 0.73 → **0.78** | 7.4 → **6.9** | 182 → 183d | −31.4 → −35.4% |
  | `short` | W/3/6 | removed | | | | | |

  `M/4/5` is the best cell in the whole grid on both windows — top Sharpe *and*
  shallowest drawdown — and beats the cell it replaces on return, churn and
  drawdown at once. `M/5/8` delivers what `long` existed for without needing a
  slower cadence, and beats the 2M cell it replaces on Sharpe in both windows.

  **`short` was removed rather than re-tuned** because it was a *cadence* choice,
  and the warm sweep says cadence is not where the return is: every weekly cell
  was dominated, and the five best cells overall are monthly or bi-weekly with a
  50–67% band. Its last incarnation (W/3/6, shipped 2026-08-13) had already
  drifted to trading *less* per year than `medium`, which made a three-way
  Short/Medium/Long lineup actively misleading.

  **Corrects the entry below**, which recorded `long` as unchanged "because
  nothing beat it in either window". That check had been run on starved data.
  Re-run warm, 2M/4/6 is beaten in both windows by six cells. Done is
  append-only, so the error stands there and is corrected here.

  Two invariant tests changed, both deliberately:
  `test_presets_are_ordered_by_holding_period` regained its trade-count
  assertion (relaxed a day earlier for `short`; with one cadence, holding period
  and churn order together again), and `test_long_holds_fewer_names_than_medium`
  became `test_long_tolerates_more_drift_than_medium` — `long` now holds one name
  *more*, because concentration is a risk choice while holding period is a
  horizon choice, and the old lineup conflated them. A new
  `test_presets_share_one_cadence` pins the design so a future preset cannot
  quietly reintroduce the cadence dimension.

  Copy rewritten in both languages (three horizons → two, and "Long is not
  slower to react, it is more forgiving"). Orphaned `equity_short.csv` /
  `holdings_short.csv` deleted. A stale `sm_horizon=short` in `localStorage`
  falls back to the default — verified in the browser, not assumed. 697 tests
  pass. *(2026-08-14)*

- **Horizon presets re-picked at realistic cost — `short` → `W/3/6`, the other
  two deliberately unchanged** — closes the item filed after the 0 bps sweep
  was found to have picked all three cells under free-trading assumptions. Run
  on warm history (post-fix sweep, above) at 100 bps, and again at the ~70 bps
  an ISK account really pays, requiring a challenger to beat the incumbent on
  CAGR *and* Sharpe in both 2008– and 2015–:

  | preset | outcome |
  |---|---|
  | `short` | **W/3/5 → W/3/6.** 14.3%/0.68 vs 14.4%/0.68 on 2008–, 17.1%/0.76 vs 15.9%/0.72 on 2015–; trades/yr **21.4 → 15.7** |
  | `medium` | **unchanged (M/5/4).** Beats every challenger on both metrics in both windows; `M/5/7` costs 0.8pp CAGR and 0.06 Sharpe to halve modelled churn |
  | `long` | **unchanged (2M/4/6).** Nothing beat it in either window |

  `short` ships on **churn alone, not return** — the CAGR and Sharpe difference
  is inside noise, and 27% fewer trades is cost actually paid. Same winners at
  70 and 100 bps, so the choice doesn't hinge on the cost input; the shipped 100
  is conservative for this account.

  **`short` now trades less per year than `medium` (15.7 vs 16.8), by design.**
  The presets are ordered by holding period (63 / 94 / 182 days), which still
  holds. trades/yr is a consequence of band width, not the definition: a wide
  band on a weekly cadence churns less than a narrow band on a monthly one while
  still turning over faster. `test_presets_are_ordered_by_holding_period` was
  relaxed accordingly — it now asserts the holding-period ordering plus both
  presets trading more than `long`, which is the comparison that would actually
  mislead if it broke.

  Config-only: `config/weights.yaml` plus the regenerated `backtests/` artifact.
  Verified in the built dashboard — the stats strip reads `3 / ~63d / 16` and the
  exit rank moves 8 → 9. A reader holding ranks 1–4 sees **no badge change**;
  the effect is on when a holding is told to leave, not on today's board.
  696 tests pass. *(2026-08-13)*

- **Horizon sweep starved its own warm-up, inverting the preset ranking** —
  `scripts/horizon_sweep.py` passed `--start` straight to `fetch_prices`, then
  derived the rebalance calendar from that truncated index. Evaluation therefore
  began on the first fetched bar with no history behind it, and
  `compute_ma_structure` returns NaN for `above_200dma` until 200 bars have
  accumulated ([`src/signals/technical.py`](src/signals/technical.py)) — so a
  `--start 2008-01-01` run scored roughly the first eight months, i.e. the whole
  GFC crash, on a degraded signal set. `--start 2015-01-01` had the same defect.

  **This produced a wrong recommendation, not just noisy numbers.** Starved,
  `M/5/7` beat `M/5/4` by 1.9pp CAGR and 0.09 Sharpe; warm, it *loses* by 0.8pp
  and 0.06 in both windows. The ranking inverted. Every table in the
  "Re-pick the horizon presets" queued item came from starved runs and has been
  deleted rather than annotated. Caught because a same-data comparison of the
  old and new presets through `backtest.py` disagreed with the sweep by 2.1pp on
  an identical cell — the two harnesses had never actually been compared.

  Fixed by separating the two concerns the flag conflated: `FETCH_START` is a
  module constant (`2003-01-01`) that history is always fetched from, and
  `--start` now bounds the evaluation window only, via a new `since=` parameter
  on `replay.rebalance_dates`. Filtering happens *after* the period grouping on
  purpose — slicing the index first shifts which months a multi-period cadence
  like `2M` treats as review months, so the naive fix would have silently
  evaluated a different calendar than the presets were picked on. Regression
  test covers exactly that, cutting mid-parity so a lucky January boundary
  can't make it pass vacuously.

  Verified: the fixed `--start 2008-01-01` reproduces the full-history run to
  the decimal (correct — the data itself begins 2008-03-28), while
  `--start 2015-01-01` still bounds to a genuinely different, warm window. The
  sweep and `backtest.py` now agree exactly on every shared cell. 691 tests
  pass. `backtest.py` carries the same defect on its own `--start` and is
  filed as its own Queued item — latent, since CI never passes the flag.
  *(2026-08-13)*

- **Backtest artifact item closed — all three defects resolved** — the item
  filed 2026-08-05 bundled three problems with `backtests/`. (2)
  non-reproducibility and (3) `--start` being ignored on a warm cache each got
  their own fix and Done entry on 2026-08-12. (1) "the committed artifact is 11
  years narrower than a fresh run" is now resolved too, and its open product
  question — *which window is intended* — was answered in practice by the
  cohort migration: `summary.json` was regenerated 2026-08-10 covering
  **2008-03-28 → 2026-08-07** (full available history from `DEFAULT_START`,
  bounded by ACWI's first date) with only the theme tracks, at the real 100 bps
  cost. That is exactly the resolution the item's own sequencing note called
  for — "regenerate the artifact once, with only the THEME track in it", once
  the sector cohort was retired. The dashboard therefore no longer shows a
  backtest that excludes the GFC: max drawdown reads −44.3% / −35.4% / −31.4%
  across the three horizons rather than the understated −18.4% the defect
  flagged. Verified against the committed artifact, not assumed.
  *(2026-08-12)*

- **Mobile leaderboard is usable** — at 375px the table showed ~37% of itself
  with no pinned columns, so scrolling out to Rank Δ / Trend took the rank and
  theme name off screen and Trend was unreachable blind. **Root cause of the
  cramped width was not a missing rule but a dead one:** a
  `@media (max-width: 600px)` block lived in `_foundation.css.j2` (first CSS
  include) while `_chrome.css.j2` (second) re-declared `.card` margin and
  `.tab-panel` padding unconditionally — and media queries add no specificity,
  so the desktop values won even though the query matched. 104px of a 375px
  viewport was going to margins that were supposed to have shrunk. Fixed by
  moving every responsive override into a new `css/_responsive.css.j2` included
  **last**, consolidating the stragglers from `_sentiment` and `_tables` so the
  convention holds without exceptions. On top of that: the first two columns
  (rank, theme) are now `position: sticky` on mobile, so identity stays visible
  while the data scrolls under them. **Measured 37% → 56% visible, and Trend is
  now reachable with the theme name still on screen.** Touch targets went
  **34 → 2** under 44px; the two left are a 22px checkbox inside an 81×57 label
  (the label is the tap target) and the RSS link, which is 44px tall and queued
  for deletion anyway. The ⚙ ranking control the audit measured at 7×18px is now
  79×44. Desktop verified unchanged (query does not match, 28px margin, static
  columns, table still fits without scrolling). Two regression tests pin
  `_responsive.css.j2` as the last include and fail if any width-based media
  query reappears earlier in the cascade. *(2026-08-12)*

- **Design review P0s** — verified each against the code before changing
  anything, which changed the work: of the four, one was already fixed and one
  was stale. **Fixed:** (a) the `DATA` column, a verbatim duplicate of
  `COMPOSITE` in 20/20 rows (sentiment weighs 0, so `composite = data_score` by
  construction), removed from all three row builders — the server template, the
  signed-in live upgrade in `auth.js`, and the scan-history view — with the
  colspans; (b) `COMPOSITE` given a centre-origin diverging bar plus its number,
  on a fixed ±1.5 scale so bar lengths stay comparable between scans;
  (c) the breakdown `.z-bar`, which filled from the left with width ∝ |z| so
  −2.5 and +2.5 rendered identically, now diverges from a zero line (extracted to
  a testable `_z_bar`); (d) focus indicators — one shared `:focus-visible` ring
  now covers tabs, sortable headers, leaderboard rows and footer links, none of
  which had one. **Already fixed / stale:** `RANK Δ` is populated (13/20) since
  the weekend dedup fix, and the two cited contrast failures now pass (headers
  4.53:1, traj-badge 4.76-5.26:1) after the token retint — so only the `DATA`
  column was genuinely dead, not three columns. Guard tests pin the three row
  builders to the same column count, the colspans to the header count, and the
  Python/JS composite-bar implementations to each other. Verified in-browser in
  both themes: bar geometry measured (track 54px, positives start at the 27px
  centre, negatives end at it), a real Tab press shows a ring, no console errors.
  *(2026-08-12)*
- **Gate modal accessibility** — the sign-in/landing modal declared
  `aria-modal="true"` and implemented none of it: no focus move, no trap, no
  Escape, no backdrop close, so the first Tab landed *behind* the overlay. It now
  binds to the shared `window.SMModal` helper (`templates/_modal.js.j2`) that the
  methodology and tab-guide modals already used — the helper was complete and
  explicitly waiting for this, since it touches auth. Fail-open: if the helper is
  absent, `auth.js` falls back to the plain hidden toggle rather than taking
  sign-in down. Only the explicit "Continue as guest" press still sets
  `guest_dismissed`; Escape and backdrop-click just close, so a stray Escape
  cannot permanently hide the sign-in prompt. Also corrected two comments the
  audit caught pointing the wrong way — `_methodology.html.j2` claimed to mirror
  the gate modal when the helper was extracted *from* it, and `_modal.js.j2`
  still described the gate modal as unmigrated. Verified in-browser: focus moves
  into the dialog on open, Tab and Shift+Tab both wrap inside it, Escape and
  backdrop close while a click *inside* the panel does not, and focus returns to
  the exact element that opened it. *(2026-08-12)*

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
