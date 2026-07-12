# /backlog-sync — audit BACKLOG.md against reality

Audit every item in the **Queued** and **Parked** sections of `BACKLOG.md`
against git history, merged PRs, and the actual code, then fix any drift.

## Procedure

1. **Fetch first:** `git fetch --prune` so merge state is current. Work from
   `main` (or note if the local checkout is behind `origin/main`).
2. **For each Queued/Parked item**, verify its claim independently — do not
   trust the backlog text:
   - Search commit history: `git log --all --oneline --grep="<keywords>"`
   - Search merged PRs: `gh pr list --state merged --search "<keywords>" --json number,title`
   - **Check the code itself** — the item's cited file/line/function. Line
     numbers drift; search for the described content, not the number.
   - Cross-check the Done section: an item with a matching Done entry but
     still sitting in Queued is drift.
3. **Classify each item:** SHIPPED (fully done), PARTIAL (some sub-points
   done), OPEN (still accurate), or STALE (the premise no longer exists in
   the code — e.g. references a module that was deleted).
4. **Report the findings as a table** (item / verdict / evidence), then fix:
   - SHIPPED → delete the Queued section; ensure a Done entry exists (add
     one, dated from the merge commit, if missing).
   - PARTIAL → rewrite the Queued section to describe only what remains.
   - STALE → delete or rewrite, and say why.
   - OPEN → leave untouched.
5. Follow the lifecycle rules at the top of `BACKLOG.md` (delete, don't
   strikethrough; Done is append-at-top).
6. **Ship the fix per the CLAUDE.md git workflow**: branch
   `chore/backlog-sync-<date>`, single commit
   `chore: sync BACKLOG.md with shipped work`, push, open a PR. If nothing
   drifted, report "backlog is in sync" and change nothing.

## When to run

- After any merge/rebase that produced conflicts (or union-merge surprises)
  in `BACKLOG.md`.
- Periodically, when picking the next item to work on — verify it's still
  open before designing.
