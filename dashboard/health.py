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

    if metric == "finbert":
        if denominator is None or denominator == 0:
            return None
        ratio = value / denominator
        if ratio >= 1.0:
            return "green"
        if ratio >= 0.5:
            return "amber"
        return "red"

    return None


def build_health_context(health: dict | None) -> dict:
    """Build template context for the data-health footer panel.

    ``health`` is the dict returned by ``get_latest_health`` (or None).
    """
    if health is None:
        return {
            "health": None,
            "health_badges": {},
            "health_any_warn": False,
        }

    badges = {
        "coverage": _badge(
            "coverage",
            health.get("sectors_produced"),
            health.get("sectors_expected"),
        ),
        "prices": _badge("prices", health.get("prices_failed"), None),
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
    }
