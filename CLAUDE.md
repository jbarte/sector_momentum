# sector_momentum — Claude instructions

## Git workflow

Always branch before making changes. Never commit directly to `main`.

1. **Create a branch** from `main` using the pattern `feature/<short-slug>` or `fix/<short-slug>`.
2. **Commit** changes to that branch with clear, conventional commit messages (`feat:`, `fix:`, `refactor:`, `chore:`, etc.).
3. **Push** the branch and **open a PR** against `main` via `gh pr create`. The PR description should summarise what changed and why.
4. **Stop there.** Do not merge. Jonas reviews and merges manually.

## Commit style

Follow conventional commits:
- `feat:` — new behaviour or feature
- `fix:` — bug fix
- `refactor:` — restructuring without behaviour change
- `chore:` — config, deps, tooling
- `docs:` — documentation only

Keep the subject line under 72 characters. No body unless the change needs context that isn't obvious from the diff.

## Project overview

Sector momentum scanner: US SPDR + STOXX Europe 600 sectors → GICS 11 → data-pillar signals → composite score → SQLite snapshots → static dashboard (GitHub Pages).

- Entry point: `scan.py`
- Dashboard build: `dashboard/build.py` → `docs/`
- Config: `config/` (universe, weights, sector maps)
- DB: `data/momentum.db`
- CI: `.github/workflows/scan.yml` (runs every 2 days, commits DB + dashboard back to repo)

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires API keys in .env)
python3 scan.py

# Run tests
pytest
```
