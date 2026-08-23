"""Data-health panel context for the sectors dashboard."""
from __future__ import annotations


def _badge(metric: str, value: int | float | None, denominator: int | None) -> str | None:
    """Return 'green', 'amber', 'red', or None (not applicable)."""
    if value is None:
        return None

    if metric == "coverage":
        if denominator is None or denominator == 0:
            return None
        ratio = value / denominator
        if ratio >= 1.0:
            return "green"
        if ratio >= 0.8:
            return "amber"
        return "red"

    if metric == "prices":
        if value == 0:
            return "green"
        if value <= 2:
            return "amber"
        return "red"

    if metric == "asof_dropped":
        # No amber tier, unlike "prices": a dropped ticker isn't a fetch
        # failure with a fuzzy severity — it's align_cohort_asof deciding a
        # theme's price series is too stale to score at all, which is
        # binary. Any drop is worth a red badge, since it means a theme the
        # reader expects to see is silently missing from the run.
        return "green" if value == 0 else "red"

    if metric == "finbert":
        if denominator is None:
            return None
        if denominator == 0:
            # `value is not None` above means a scan actually attempted
            # sentiment, so a 0 denominator is a broken state, not an
            # inapplicable one — nothing was scoreable. Never return None
            # here: _footer.html.j2 renders `badge-{{ ... or 'green' }}`, so
            # None would paint a total sentiment outage GREEN in a collapsed
            # panel. A deliberate --no-finbert skip never reaches this branch
            # (it leaves finbert_scored None, caught at the top).
            return "red"
        ratio = value / denominator
        if ratio >= 1.0:
            return "green"
        if ratio >= 0.5:
            return "amber"
        return "red"

    return None


def build_health_context(health: dict | None, same_asof_streak: int | None = None) -> dict:
    """Build template context for the data-health footer panel.

    ``health`` is the dict returned by ``get_latest_health`` (or None).
    ``same_asof_streak`` is ``get_same_asof_streak``'s count for the scan
    ``health`` describes -- 1 (or None) means nothing to note; the template
    only renders a note when it is 2 or more.
    """
    if health is None:
        return {
            "health": None,
            "health_badges": {},
            "health_any_warn": False,
            "same_asof_streak": None,
        }

    badges = {
        "coverage": _badge(
            "coverage",
            health.get("sectors_produced"),
            health.get("sectors_expected"),
        ),
        "prices": _badge("prices", health.get("prices_failed"), None),
        "asof_dropped": _badge("asof_dropped", health.get("asof_dropped_count"), None),
        "finbert": _badge(
            "finbert",
            health.get("finbert_scored"),
            health.get("finbert_total"),
        ),
    }

    any_warn = any(v in ("amber", "red") for v in badges.values() if v is not None)

    return {
        "health": health,
        "health_badges": badges,
        "health_any_warn": any_warn,
        "same_asof_streak": same_asof_streak,
    }
