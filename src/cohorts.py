"""Cohort definitions — the configured groups a scan ranks within.

A cohort is one cross-sectional scoring universe. Composite scores are z-scored
*within* a cohort and never across them, so the cohort list is what every
per-cohort loop in the dashboard is really iterating.

Since the sector cohorts were retired there is exactly one: themes. The list
shape is kept rather than collapsed to a bare value because every consumer
already iterates it, and because it is the seam that made retiring sectors a
config change instead of a rewrite — the same property is worth keeping for
whatever the next cohort turns out to be.

Pure config -> data. No I/O, no database, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Must equal src.state.THEME_REGION, which is the value actually written to
# scores.region / signals.region. Duplicated rather than imported to keep this
# module free of the psycopg2 dependency; tests/test_cohorts.py pins them equal.
THEME_REGION = "THEME"

DEFAULT_THEME_BENCHMARK = "ACWI"


@dataclass(frozen=True)
class Cohort:
    """One cross-sectional scoring universe."""

    region: str                  # matches scores.region / signals.region
    label: str                   # human-readable, e.g. "Themes"
    benchmark: str               # ticker the cohort's relative strength is measured against
    # {"THEME|Semiconductors": "SOXX", ...}. Excluded from the generated
    # __hash__ (a dict field on a frozen dataclass is otherwise unhashable at
    # call time); this restores hash((region, label, benchmark)) while equality
    # still compares all four fields.
    instruments: dict[str, str] = field(hash=False)


def _theme_ticker(cfg) -> str:
    """themes.yaml entries are dicts with a `ticker` key; tolerate a bare
    string, matching src/pipeline.py:build_theme_signals_rows."""
    return cfg["ticker"] if isinstance(cfg, dict) else cfg


def cohorts(themes_cfg: dict | None = None) -> list[Cohort]:
    """Every configured cohort.

    Each cohort's `instruments` dict is keyed exactly `"{region}|{name}"` —
    consumers may split on the first "|" to recover `name`, as
    `dashboard/correlation.py` does.

    A cohort with no configured members is omitted entirely, so an empty or
    missing themes config yields `[]` rather than a phantom cohort.
    """
    themes = (themes_cfg or {}).get("themes") or {}
    if not themes:
        return []

    return [Cohort(
        region=THEME_REGION,
        label="Themes",
        benchmark=(themes_cfg or {}).get("benchmark") or DEFAULT_THEME_BENCHMARK,
        instruments={
            f"{THEME_REGION}|{name}": _theme_ticker(cfg)
            for name, cfg in themes.items()
        },
    )]


def instrument_map(cohort_list: list[Cohort]) -> dict[str, str]:
    """Flatten cohorts into one {region|name: ticker} map."""
    out: dict[str, str] = {}
    for cohort in cohort_list:
        out.update(cohort.instruments)
    return out
