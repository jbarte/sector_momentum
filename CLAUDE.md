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
- CI: `.github/workflows/scan.yml` (runs every 2 days, commits dashboard back to repo)

## Backlog

All queued and completed work lives in `BACKLOG.md` in the project root. When asked about the backlog, read that file — not memory. When finishing a task that appears in `BACKLOG.md`, move it to the Done section with the completion date — in the same branch that ships the work (see Git workflow step 3).

To catch drift after the fact, run `/backlog-sync`: it audits each queued item against git history, merged PRs, and the actual code, then offers to move anything already shipped to Done.

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires API keys in .env)
python3 scan.py

# Run tests
pytest
```
