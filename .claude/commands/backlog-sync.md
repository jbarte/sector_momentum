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
3. **Sweep for union-merge artefacts** before classifying anything. These are
   mechanical and catch drift no per-item reading will, because
   `BACKLOG.md`'s `merge=union` driver undoes deletions silently — a branch
   still carrying lines another branch deleted brings the section back, with no
   conflict markers (this resurrected a 51-line queued item on 2026-08-14).
   On `main` there is no base left to diff against, so look for the symptom:

   ```bash
   # a) an item in BOTH Queued and Done — the resurrection signature
   awk '/^# Queued/,/^# Done/' BACKLOG.md | grep '^## '
   awk '/^# Done/,0'          BACKLOG.md | grep '^- \*\*'

   # b) the same heading twice inside Queued
   awk '/^# Queued/,/^# Done/' BACKLOG.md | grep '^## ' | sort | uniq -d

   # c) the same Done headline twice
   awk '/^# Done/,0' BACKLOG.md | grep '^- \*\*' | sort | uniq -d
   ```

   (a) needs a read across the two lists — a resurrection duplicates the wording,
   so matching headings are the tell. (b) and (c) are exact and should return
   nothing. Anything found here is drift to fix in this pass, not an item to
   classify: for (a) the Done entry is the truth and the Queued section is the
   artefact.

4. **Classify each item:** SHIPPED (fully done), PARTIAL (some sub-points
   done), OPEN (still accurate), or STALE (the premise no longer exists in
   the code — e.g. references a module that was deleted).
5. **Report the findings as a table** (item / verdict / evidence), then fix:
   - SHIPPED → delete the Queued section; ensure a Done entry exists (add
     one, dated from the merge commit, if missing).
   - PARTIAL → rewrite the Queued section to describe only what remains.
   - STALE → delete or rewrite, and say why.
   - OPEN → leave untouched.
6. Follow the lifecycle rules at the top of `BACKLOG.md` (delete, don't
   strikethrough; Done is append-at-top).
7. **Ship the fix per the CLAUDE.md git workflow**: branch
   `chore/backlog-sync-<date>`, single commit
   `chore: sync BACKLOG.md with shipped work`, push, open a PR. If nothing
   drifted, report "backlog is in sync" and change nothing.

## When to run

- After any merge/rebase that produced conflicts (or union-merge surprises)
  in `BACKLOG.md`. Note that the *pre-merge* check — diffing Queued headings
  against `origin/main` while still on the branch — is the one that catches a
  resurrection before it lands; it lives in `CLAUDE.md`. Step 3 below is the
  after-the-fact sweep for when it already merged.
- Periodically, when picking the next item to work on — verify it's still
  open before designing.
