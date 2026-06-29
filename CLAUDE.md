# sector_momentum — Claude instructions

## Git workflow

Always branch before making changes. Never commit directly to `main`.

1. **Create a branch** from `main` using the pattern `feature/<short-slug>` or `fix/<short-slug>`.
2. **Implement** the feature on that branch with regular commits.
3. **Update `BACKLOG.md` in the same branch** — if the work completes (or partially
   completes) a backlog item, move it to Done in the *same* branch/PR that ships the
   code. Never defer backlog hygiene to a separate sync PR; that's how the backlog
   drifts out of sync with what's actually shipped.
4. **Run a code review** when the implementation is complete (`/code-review`).
5. **Address review findings**, then push: `git push -u origin feature/<short-slug>`.
6. **Stop there.** Do not merge. Jonas reviews and merges manually.

## Commit style

Follow conventional commits:
- `feat:` — new behaviour or feature
- `fix:` — bug fix
- `refactor:` — restructuring without behaviour change
- `chore:` — config, deps, tooling
- `docs:` — documentation only

Keep the subject line under 72 characters. No body unless the change needs context that isn't obvious from the diff.

## Project overview

Sector momentum scanner: US SPDR + STOXX Europe 600 sectors → GICS 11 → data-pillar signals → composite score → Supabase/Postgres snapshots → static dashboard (GitHub Pages).

- Entry point: `scan.py`
- Dashboard build: `dashboard/build.py` → `docs/`
- Config: `config/` (universe, weights, sector maps)
- CI: `.github/workflows/scan.yml` (daily scan → commits data + dashboard),
  `.github/workflows/build-docs.yml` (rebuilds `docs/` on push to `main` when
  dashboard source changes)

## Generated artifacts — do not commit from feature branches

`docs/` (the published GitHub Pages dashboard, incl. `docs/reports/`) is a **generated
artifact owned by CI**. Build it locally to verify a change (`python3 dashboard/build.py`),
but **do not `git add docs/` on a feature branch** — leave it out of your commits. CI
rebuilds and commits `docs/` on `main` after merge (`build-docs.yml`). Committing `docs/`
from branches is what caused recurring merge conflicts (the cron and every branch each
regenerating the same large tree). Feature PRs should be **source-only**
(`dashboard/templates/`, `dashboard/build.py`, `src/`, `config/`, tests).

`BACKLOG.md` uses a `merge=union` driver (`.gitattributes`) so concurrent Done-list
additions auto-combine instead of conflicting.

## Backlog

All queued and completed work lives in `BACKLOG.md` in the project root. When asked about the backlog, read that file — not memory. When finishing a task that appears in `BACKLOG.md`, move it to the Done section with the completion date — in the same branch that ships the work (see Git workflow step 3).

To catch drift after the fact, run `/backlog-sync`: it audits each queued item against git history, merged PRs, and the actual code, then offers to move anything already shipped to Done.

## Backups

The DB is backed up to a **private Supabase Storage bucket `db-backups`** (one
`backup_<UTC>.zip` per scan, taken *before* each run) — not git. Requires the
`SUPABASE_SERVICE_KEY` secret (CI) / env var (local) and the bucket to exist.
Restore with `python restore.py` (latest) / `--list` / `--local <dir>` (old git backups).

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires API keys in .env)
python3 scan.py

# Run tests
pytest
```
