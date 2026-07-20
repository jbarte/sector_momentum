# sector_momentum — Claude instructions

## Git workflow

Always branch before making changes. Never commit directly to `main`.

1. **Create a branch** from `main` using the pattern `feature/<short-slug>` or `fix/<short-slug>`.
2. **Implement** the feature on that branch with regular commits.
3. **Update `BACKLOG.md` in the same branch** — if the work completes a backlog
   item, **delete its Queued section** and add a Done entry (top of Done) in the
   *same* branch/PR that ships the code; if it partially completes one, rewrite
   the Queued section to only what remains. Never strikethrough-in-place in
   Queued, and never defer backlog hygiene to a separate sync PR — both are how
   the backlog drifts out of sync with what's actually shipped.
4. **Run a code review** when the implementation is complete (`/code-review`).
5. **Address review findings**, then push: `git push -u origin feature/<short-slug>`.
6. **Open a pull request** against `main` with `gh pr create` — Claude creates the PR
   (title = the conventional-commit subject; body = summary, tests, and any post-merge
   manual steps). End the PR body with the Claude Code attribution line.
7. **Stop there.** Do not merge. Jonas reviews and merges the PR manually.

**Branch deletion:** only delete a branch (local or remote) once its PR is confirmed
`MERGED` — check with `gh pr list --head <branch> --state all --json state`, don't infer
merge status from `git branch --merged`, since squash-merges leave the original branch
tip unreachable from `main` even though its PR merged. Verify and delete inline (one
command), never a blind bulk delete. `delete-branch-on-merge` is enabled, so this mostly
matters for manual cleanup of stray/duplicate branches.

## Commit style

Follow conventional commits:
- `feat:` — new behaviour or feature
- `fix:` — bug fix
- `refactor:` — restructuring without behaviour change
- `chore:` — config, deps, tooling
- `docs:` — documentation only

Keep the subject line under 72 characters. No body unless the change needs context that isn't obvious from the diff.

## Project overview

Sector momentum scanner: US SPDR (GICS 11) + STOXX Europe 600 sectors (14, incl. standalone sub-sectors) → data-pillar signals → composite score → Supabase/Postgres snapshots → static dashboard (GitHub Pages).

- Entry point: `scan.py`
- Dashboard build: `dashboard/build.py` → `docs/`
- Config: `config/` (universe, weights, sector maps)
- CI: `.github/workflows/scan.yml` (daily scan → deploys dashboard as a Pages
  artifact), `.github/workflows/build-docs.yml` (rebuilds and redeploys the
  Pages artifact on push to `main` when dashboard source changes)

## Generated artifacts — `docs/` is not committed

`docs/` (the published GitHub Pages dashboard, incl. `docs/reports/`) is a **build
output, not tracked in git** (gitignored). Build it locally to verify a change
(`python3 dashboard/build.py`); it's fine to have a local `docs/` on any branch since
it's never staged. CI rebuilds it fresh on every run and deploys it directly as a
GitHub Pages artifact (`actions/upload-pages-artifact` + `actions/deploy-pages`) — see
the `pages-artifact-deploy` design doc in `sector_momentum-notes` (private repo, see
below). There is no merge-conflict risk from `docs/` anymore; feature PRs should still
be **source-only** (`dashboard/templates/`, `dashboard/build.py`, `src/`, `config/`, tests).

`BACKLOG.md` uses a `merge=union` driver (`.gitattributes`) so concurrent Done-list
additions auto-combine instead of conflicting. This only works cleanly for pure
*additions* (new Done bullets). If two branches both *edit* the same existing paragraph
(e.g. rewording a queued item's "To activate" section), union merge concatenates both
versions verbatim instead of picking one — silently, with no conflict markers to flag
it. Check the diff after any merge/rebase that touches `BACKLOG.md` alongside another
branch's edits, and hand-dedupe if a paragraph got doubled.

## Design docs (specs & plans) — private companion repo, NOT `docs/` or `design/`

`sector_momentum` is a **public** repo (required for free-tier GitHub Pages hosting).
Brainstorming/writing-plans output does **not** live in this repo — it lives in the
private companion repo **`jbarte/sector_momentum-notes`**, under `specs/` and `plans/`
(no `design/` prefix there — the repo itself is the private container). Clone it
locally if it isn't already present (`gh repo clone jbarte/sector_momentum-notes`,
sibling directory to this repo), and write specs/plans there instead of the
brainstorming/writing-plans skills' `docs/superpowers/` default. See
`sector_momentum-notes/specs/2026-07-20-public-repo-privacy-audit-design.md` for why
this split exists — in short, this repo used to have a `design/` folder that was
public without anyone intending it to be; it moved out entirely on 2026-07-20.

## Backlog

All queued and completed work lives in `BACKLOG.md` in the project root. When asked
about the backlog, read that file — not memory. The lifecycle rules are at the top of
`BACKLOG.md` itself: one item per section; shipping **deletes** the Queued section and
adds a Done entry at the top of Done, in the same branch that ships the work (see Git
workflow step 3); Done is append-only.

**Before starting a queued item, verify it's still open** — check the Done section,
`git log --all --grep`, and the cited code. Queued text can be stale (line numbers
drift, premises get removed); the 2026-07-12 audit found an entire review-findings
section that had shipped without its Queued bullets being cleaned up.

To catch drift after the fact, run `/backlog-sync` (`.claude/commands/backlog-sync.md`):
it audits each Queued/Parked item against git history, merged PRs, and the actual code,
then fixes anything already shipped or stale via a `chore:` PR.

## Backups

The DB is backed up to a **private Supabase Storage bucket `db-backups`** (one
`backup_<UTC>.zip` per scan, taken *before* each run) — not git. Requires the
`SUPABASE_SERVICE_KEY` secret (CI) / env var (local) and the bucket to exist.
Restore with `python restore.py` (latest) / `--list` / `--local <dir>` (old git backups).

A second private bucket **`trends-cache`** holds the durable Google Trends day-cache
(`trends_cache_<UTC-date>.json`, one per day) so re-triggered scans reuse
already-fetched batches instead of re-hitting Google (429 mitigation). Same
`SUPABASE_SERVICE_KEY` credential as the backups; the cache is **fail-open**, so a
missing bucket or key only means scans run uncached. Bypass with `python3 scan.py
--no-cache`.

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires API keys in .env)
python3 scan.py

# Run tests
pytest
```
