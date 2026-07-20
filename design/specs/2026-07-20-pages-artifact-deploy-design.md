# Deploy Pages via artifact instead of committing docs/

## Problem

`docs/` is a 2.4 MB generated tree committed to `main` by two workflows
(`scan.yml` daily, `build-docs.yml` on source change). 116 commits have
touched it. This causes:

- Recurring merge conflicts — the cron and every feature branch each
  regenerate the same tree.
- Git-history bloat (~1 MB/day of churn).
- A standing rule in `CLAUDE.md` ("never `git add docs/` on a feature
  branch") that exists only to work around the above.

## Solution

Switch GitHub Pages from legacy branch-deploy (`main:/docs`) to
Actions artifact deploy (`actions/upload-pages-artifact` +
`actions/deploy-pages`), then untrack `docs/` entirely.

Rolled out in two phases so the risky, untestable step (the Pages
settings flip) stays decoupled from the irreversible one (untracking).

## Why this is safe

`dashboard/reports.py:_generate_scan_reports` iterates every `scan_id`
in `all_scores_df` (loaded from the DB) and writes any report file that
does not already exist. The `if report_path.exists(): continue` check is
a skip optimization, not a dependency on committed history. A fresh
runner with an empty `docs/` regenerates the full tree — the database is
a complete source of truth.

## Phase 1 (PR 1): add artifact deploy, keep `docs/` committed

Both `scan.yml` and `build-docs.yml` gain, after their existing
"Build dashboard" step:

```yaml
permissions:
  contents: write      # phase 1 still commits docs/
  pages: write
  id-token: write

environment:
  name: github-pages
  url: ${{ steps.deployment.outputs.page_url }}
```

```yaml
- name: Upload Pages artifact
  uses: actions/upload-pages-artifact@v3
  with:
    path: docs

- name: Deploy to GitHub Pages
  id: deployment
  uses: actions/deploy-pages@v4
```

The existing commit steps stay — they are the rollback path.

`concurrency: group: commit-to-main` is unchanged in this phase; both
workflows still commit, and the shared group also serializes the Pages
deploys.

### Post-merge manual steps (phase 1)

1. Flip the Pages source:
   `gh api -X PUT repos/jbarte/sector_momentum/pages -f build_type=workflow`
2. Manually trigger `build-docs.yml` (`workflow_dispatch`).
3. Verify the live site: `index.html`, `themes.html`, `sentiment.html`,
   one `reports/report_<id>.md`, and `feed.xml` all serve correctly.
4. Confirm `.nojekyll` is present in the deployed artifact.

**Rollback during phase 1:** flip `build_type` back to `legacy`. The
committed `docs/` is still on `main` and resumes serving immediately.

## Phase 2 (PR 2): drop `docs/` from git

Only after phase 1 verification passes.

1. `git rm -r --cached docs/`
2. Add `/docs/` to `.gitignore`
3. Delete the "Commit results" step from `scan.yml` and the
   "Commit rebuilt docs" step from `build-docs.yml`
4. Drop `contents: write` from both `permissions` blocks — nothing is
   committed anymore
5. Rename `concurrency.group` from `commit-to-main` to `pages-deploy`
   in both workflows; it no longer describes commits
6. Keep `build-docs.yml`'s `paths:` filter (still correctly limits
   rebuilds to source changes). The `[skip ci]` guard in the deleted
   commit step goes away with it — no self-triggering commit remains.

### Documentation updates (phase 2)

- **`CLAUDE.md`** — delete the "Generated artifacts — do not commit from
  feature branches" section covering `docs/`. The rule becomes
  unenforceable and unnecessary. Keep the `BACKLOG.md` union-merge note
  in that section.
- **`ARCHITECTURE.md`** — update the workflow table (lines 195-196) to
  say each workflow deploys a Pages artifact rather than commits
  `docs/`; delete the "Generated artifact policy" paragraph (lines
  203-204).
- **`README.md:48`** — reword if it implies `docs/` is a committed tree.
- **`BACKLOG.md`** — delete the queued "Deploy Pages via artifact"
  section, add a Done entry at the top of Done.

## Out of scope

- **Rewriting git history.** Existing `docs/` blobs stay in the repo's
  past. Untracking stops new bloat; a `filter-repo` rewrite would shrink
  history but break every existing clone. Not doing it.
- Consolidating the two workflows into a reusable `workflow_call` file.
  The repo already duplicates checkout/setup-python/install across both;
  a third file for two call sites is not worth the inconsistency.
- Changing what `dashboard/build.py` generates.

## Known cost

Every build now regenerates all per-scan reports from scratch (27 today,
growing ~365/year) because the runner starts with an empty `docs/`. Each
report is markdown rendered from an already-in-memory DataFrame, so the
cost is small, but it grows linearly with scan history.

## Verification

**Phase 1:** after the settings flip and manual trigger, all four pages
plus a report and `feed.xml` load from the live site.

**Phase 2:** after merge, confirm (a) the next `build-docs.yml` run
deploys with no commit to `main`, (b) a fresh clone shows `docs/`
untracked, (c) the site still serves.
