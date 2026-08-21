"""Derived facts for the leaderboard summary strip's "Today's Read" cell.

Returns FACTS, never prose. Every user-visible word lives in
templates/index.html.j2 behind a data-i18n key, because the i18n pass has no
variable-interpolation mechanism — a sentence assembled here could not be
translated at all without inventing a data-i18n-* attribute that the pass does
not read, which would be silently inert (see the warning comment in
_header.html.j2).

The rule (decided 2026-08-21): the lead clause names the rank-1 theme; the
second clause is the direction of the mean change score across the bottom half.
The design spec suggested naming a shared "tag" across the top 4 instead, but
no tag concept exists in config/themes.yaml (themes carry ticker,
gdelt_keywords, unbuyable) — and the spec's own risk note says a wrong sentence
is worse than no sentence, so this states only what the data supports.
"""
from __future__ import annotations

# Dead band around zero. Without it the cell flips between "picking up" and
# "sliding" scan to scan on noise, which reads as the board changing its mind
# rather than as the market moving.
DRIFT_EPS = 0.05


def _rank_of(row: dict) -> float | None:
    """rows.py writes int ranks, or the string "—" when rank is missing."""
    try:
        return float(row.get("rank"))
    except (TypeError, ValueError):
        return None


def todays_read(rows: list[dict]) -> dict | None:
    """Facts for the "Today's Read" cell, or None when there is nothing to say.

    rows are dashboard/rows.py leaderboard rows: rank, sector, _raw_change.
    Returns {"lead_theme": str, "drift": "rising"|"falling"|"flat"}.
    """
    ranked = [(r, _rank_of(r)) for r in rows]
    ranked = [(r, k) for r, k in ranked if k is not None]
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[1])

    lead_theme = ranked[0][0].get("sector")
    if not lead_theme:
        return None

    # Lower-ranked half. An odd count puts the middle row in the bottom half:
    # with 3 themes, "the bottom half" a reader sees is the two below the leader.
    # max(1, …) keeps the lead row out of its own bottom half — otherwise a
    # single-theme board describes the leader as "the bottom half", which is
    # the one thing this sentence must never do.
    bottom = [r for r, _ in ranked[max(1, len(ranked) // 2):]]
    changes = []
    for r in bottom:
        v = r.get("_raw_change")
        if v is None:
            continue
        try:
            changes.append(float(v))
        except (TypeError, ValueError):
            continue

    if not changes:
        drift = "flat"
    else:
        mean_change = sum(changes) / len(changes)
        if mean_change > DRIFT_EPS:
            drift = "rising"
        elif mean_change < -DRIFT_EPS:
            drift = "falling"
        else:
            drift = "flat"

    return {"lead_theme": lead_theme, "drift": drift}
