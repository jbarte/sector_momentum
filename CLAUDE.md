# sector_momentum — Claude instructions

## Git workflow

Always branch before making changes. Never commit directly to `main`.

**Exception: `chore:` and `docs:` commits may go straight to `main`**, skipping
branch/PR/review — config, deps, tooling, or documentation-only changes (per Commit
style below), and nothing else. If a change mixes chore/docs work with anything that
touches application code, tests, or behavior, it doesn't qualify for the exception —
branch it normally. Still run the test suite before pushing.

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

Thematic ETF momentum scanner, published as **ETF Momentum**: 18 thematic and
niche ETFs (AI & robotics, semiconductors, uranium, space, gold miners, …) →
data-pillar signals → composite score → Supabase/Postgres snapshots → static
dashboard (GitHub Pages).

The US/EU **sector** cohorts this started as were retired on 2026-08-05; themes
are now the only cohort. `region` survives in the schema as the cohort
discriminator and is always `THEME` — it reads as legacy but is load-bearing,
since retired sector rows are still in those tables and `region` is the filter
that keeps them out of every read.

- Entry point: `scan.py`
- Dashboard build: `dashboard/build.py` → `docs/`
- Config: `config/` — `themes.yaml` (the scoring universe + UCITS equivalents),
  `weights.yaml` (pillar split, signal params, horizon presets, trading cost),
  `universe.yaml` (scan-wide settings)
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
additions auto-combine instead of conflicting. Union keeps **both sides' lines**,
which is right for additions and has two failure modes, both silent:

- **Concurrent edits to the same paragraph** get concatenated verbatim rather
  than one winning.
- **Deletions get undone.** If another branch deleted a section and yours still
  carries those lines, union brings it back. This resurrected a whole 51-line
  queued item on 2026-08-14 (#208 deleted it, #209 still had it), leaving the
  item in Queued *and* Done at once. Git reported a clean merge.

**Run this after any merge or rebase of `main` into a branch that touches
`BACKLOG.md`** — reading the merge output will not show either problem:

```bash
diff <(git show origin/main:BACKLOG.md | awk '/^# Queued/,/^# Done/' | grep '^## ') \
     <(awk '/^# Queued/,/^# Done/' BACKLOG.md | grep '^## ')
```

Every `>` line must be an item your branch *intends* to add. Anything else is a
resurrection — delete it. Then hand-dedupe if a paragraph got doubled. Once
merged there is no base left to diff against, which is why `/backlog-sync` also
sweeps for the after-the-fact symptom (an item in both Queued and Done).

A `>` line that is not a plausible item heading (`## ') \`) means a **malformed
edit**, not a resurrection. **When deleting a section, do not find its end by
searching for the next `## `** — several items contain fenced code blocks whose
lines start with `## `, so that match lands inside the fence and truncates the
deletion, leaving a fragment behind. Match the next item's known heading text
instead. This is how the check earned its keep the day it was written: it caught
exactly this in the commit that added it.

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

## When to use the superpowers workflow (brainstorm → spec → plan → execute)

Not every change earns the full `brainstorming` → `writing-plans` →
`subagent-driven-development` pipeline. Match the process to the size of the
decision, not the size of the diff.

**Skip straight to implementation** (branch, code, tests, PR — this file's
normal Git workflow) when the work is a bug fix, a defect with a clear cause,
or a backlog item that already carries its own reasoning and measurements —
i.e. the design decisions are already made, and what's left is executing
them. Examples from this repo: the badge-gating review fixes, flagging
Shipping unbuyable. Do the measurement/verification inline as part of the
change; it doesn't need its own spec document.

**Use the full flow** when there's real design work with no existing
answer — multiple non-obvious choices, a meaningful blast radius, or
anything where a wrong early call is expensive to unwind later. Signal: if
you'd have to make up an answer to proceed, that's a question for
`brainstorming`, not a judgment call to make silently. Example: the dark
theme — palette character, trigger mechanism, control shape, and chart
re-theming architecture were all open questions with real trade-offs.

When in doubt, ask which one before starting rather than guessing — the two
paths diverge immediately (branch-and-code vs. brainstorm-and-ask).

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

Google Trends was removed from the pipeline; the `--no-cache` flag it used is
gone, and nothing reads or writes the `trends-cache` bucket any more — though
the bucket itself still exists in the Supabase project (10 stale objects,
85 KB, last written 2026-07-19) and can be deleted by hand. Sentiment now
comes from GDELT headlines scored by FinBERT (`--no-finbert` skips it), and
is **experimental** — excluded from the composite and from the ranking. See
ARCHITECTURE § 4.

## Dev commands

```bash
# Rebuild dashboard from existing DB
python3 dashboard/build.py

# Run full scan (requires API keys in .env)
python3 scan.py

# Run tests
pytest
```
