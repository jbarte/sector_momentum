# /handover — make the repo describe itself accurately

Before handing this project to another person or agent, verify that every
document shaping their mental model is **true**. Fix what isn't, and ship it as
one `chore:` PR.

The audience is someone with no context. They will trust `CLAUDE.md` first,
`README.md` second, and `BACKLOG.md` when deciding what to work on. A wrong
sentence in any of those costs them hours before they think to doubt it.

## Why reading the docs is not enough

Every drift found on 2026-08-14 was **invisible to a careful read** — each
document was internally coherent and confidently worded. They were only wrong
against the code:

- `CLAUDE.md`'s Project overview described "US SPDR (GICS 11) + STOXX Europe 600
  sectors" nine days after the sector cohorts were retired. It reads perfectly
  well. It is simply not this project.
- `CLAUDE.md` and `README.md` documented a `--no-cache` flag and a
  `trends-cache` bucket for a **Google Trends pipeline that no longer exists in
  the code at all**. A newcomer would have run a flag that does not parse.
- `ARCHITECTURE.md` described "a client-side toggle to blend sentiment at a
  chosen weight" one day after that control was removed.
- `BACKLOG.md` listed two P1 design findings that were already fixed, and one
  item carried a measured figure from a **candidate that was never adopted**,
  which would have led the next person to deprioritise real work.

So: **verify claims against the code, never against another document** — and
especially never against `BACKLOG.md`'s own Done list, which records what was
*intended*, not necessarily what shipped.

## Procedure

### 1. Establish state

```bash
git fetch --prune
git status --short && git log --oneline -1
gh pr list --state open --json number,title -q '.[] | "#\(.number) \(.title)"'
git branch
```

Report anything unmerged **explicitly** — a handover that silently omits an open
PR hands over a half-finished change. Delete local branches only after
confirming `MERGED` per the `CLAUDE.md` rule. Confirm `pytest` passes and
`python3 dashboard/build.py` completes.

### 2. Mechanical sweep — do this before reading anything

This finds what a careful read misses. Every one of these is a factual claim
that can be checked with a command:

```bash
# Every CLI flag the docs name must exist in the code. Include EVERY entry
# point — omitting restore.py once made `--list` look like a phantom flag.
grep -oh '`--[a-z-]*`' README.md CLAUDE.md | tr -d '`' | sort -u
grep -oh '"--[a-z-]*"' scan.py backtest.py restore.py dashboard/build.py \
    scripts/*.py | tr -d '"' | sort -u

# Every file the docs name must exist. Resolve by BASENAME too: the module
# lists write `rows.py`, not `dashboard/rows.py`, and a naive -e test reports
# 23 phantom misses.
grep -ohE '`[a-z_/]+\.(py|yaml|js|j2|md)`' README.md ARCHITECTURE.md CLAUDE.md \
  | tr -d '`' | sort -u | while read -r f; do
      [ -e "$f" ] && continue
      git ls-files "*/$(basename "$f")" "$(basename "$f")" | grep -q . \
        || echo "MISSING: $f"
    done

# Env vars. Eyeball this one: the pattern also catches prose in caps
# (`MERGED` from the branch-deletion rule is not an env var).
grep -ohE '`[A-Z_]{4,}`' README.md CLAUDE.md | tr -d '`' | sort -u

# Retired concepts. Extend this list whenever something is removed.
for t in "GICS" "STOXX" "SPDR" "sector map" "trends-cache" "no-cache" \
         "Google Trends" "RSS" "feed.xml" "Short / Medium / Long"; do
  printf '%-22s %s\n' "$t" "$(grep -ril "$t" README.md ARCHITECTURE.md CLAUDE.md | tr '\n' ' ')"
done
```

A hit is not automatically drift — a document may name a retired thing precisely
to say it is retired (ARCHITECTURE's header note does exactly that, correctly).
Read each hit and decide. What matters is that none of them is a claim about the
**present**.

### 3. Audit the three documents against the code

For each of `CLAUDE.md`, `README.md`, `ARCHITECTURE.md`, check the categories
that have actually failed here:

- **The opening description of the project.** The single highest-value sentence
  in the repo and the easiest to leave behind after an architectural change.
- **Anything describing a UI control.** Controls get withdrawn, gated or moved;
  the prose describing them rarely follows.
- **Anything saying "queued", "planned" or "deferred".** Cross-check against
  `BACKLOG.md`'s Queued section — if it is not there, the doc is stale.
- **"Last updated" stamps.** Refresh, or delete them rather than lie.
- **Counts and named values** — number of themes, preset names and bands,
  signal counts. `tests/test_docs_match_config.py` pins the horizon subset;
  everything else is manual.

### 4. Audit the backlog

Run `/backlog-sync` and follow its procedure, including its union-merge sweep.
Then add the check that command cannot make, because it needs judgement:

- **Are the item's numbers from the thing that shipped?** A figure measured on a
  candidate that was rejected is worse than no figure — it looks authoritative
  and points the wrong way. Trace every quoted measurement back to the config
  that is live now.

### 5. Cross-document consistency

The same fact often appears in `BACKLOG.md`, `ARCHITECTURE.md` and a code
comment. When you correct one, grep the other two for the old value.

### 6. Ship

One branch `chore/handover-sync-<date>` (or fold into the current branch if a
handover is already in flight), one commit, one PR. Note the merge order if it
stacks on open work. Follow the `CLAUDE.md` git workflow: never commit to
`main`, and update `BACKLOG.md` in the same PR.

## Report

Close with a handover summary the recipient can act on:

- **Open PRs and merge order**, or "none".
- **What changed in this pass**, and what was verified as already correct — the
  second half is what makes the first trustworthy.
- **Corrections to anything previously stated**, called out plainly. If a number
  or claim given earlier was wrong, say so; the recipient may have it in their
  notes.
- **The highest-value remaining work**, with the reason it is next.
- **Anything only the owner can decide**, which no amount of auditing resolves.

## When to run

- Before handing the project to another person or agent.
- After a long working session that shipped several PRs — documentation drifts
  per-PR and nothing fails when it does.
- When picking the project back up after time away: the same audit tells *you*
  what is no longer true.
