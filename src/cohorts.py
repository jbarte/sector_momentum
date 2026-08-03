"""Cohort definitions — the configured groups a scan ranks within.

A cohort is one cross-sectional scoring universe: US sectors, EU sectors, or
themes. Composite scores are z-scored *within* a cohort and never across them,
so the cohort list is what every per-region loop in the dashboard is really
iterating.

This module exists so those loops stop hardcoding ("US", "EU"). When the sector
cohorts are eventually retired they disappear from config, and every consumer
follows with no code change — on the config side. That guarantee does NOT
extend to the data side: every current consumer still sources its dataframe
from `get_scan_history` (src/state.py), whose default `regions` argument is
`SECTOR_REGIONS` — i.e. `("US", "EU")` only. Passing a themes cohort into
`cohorts()` without also widening the caller's `get_scan_history(regions=...)`
past `SECTOR_REGIONS` produces an empty dataframe for that cohort, which reads
as a silent half-state (empty section, unsorted block, "0 obs" row) rather
than an error. See the `cohorts()` docstring below for the same point made at
the call site.

Pure config -> data. No I/O, no database, no network.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Must equal src.state.THEME_REGION, which is the value actually written to
# scores.region / signals.region. Duplicated rather than imported to keep this
# module free of the psycopg2 dependency; tests/test_cohorts.py pins them equal.
THEME_REGION = "THEME"


@dataclass(frozen=True)
class Cohort:
    """One cross-sectional scoring universe."""

    region: str                  # matches scores.region / signals.region
    label: str                   # human-readable, e.g. "US Sectors"
    benchmark: str               # ticker the cohort's relative strength is measured against
    # {"US|Technology": "XLK", ...}. Excluded from the generated __hash__ (a
    # dict field on a frozen dataclass is otherwise unhashable at call time);
    # this restores hash((region, label, benchmark)) while equality still
    # compares all four fields.
    instruments: dict[str, str] = field(hash=False)


# (config key, region, label, benchmark key, benchmark default)
_SECTOR_COHORTS = (
    ("us_sectors", "US", "US Sectors", "us_benchmark", "RSP"),
    ("eu_sectors", "EU", "EU Sectors", "eu_benchmark", "EXSA.DE"),
)


def _theme_ticker(cfg) -> str:
    """themes.yaml entries are dicts with a `ticker` key; tolerate a bare
    string, matching src/pipeline.py:build_theme_signals_rows."""
    return cfg["ticker"] if isinstance(cfg, dict) else cfg


def cohorts(universe: dict, themes_cfg: dict | None = None) -> list[Cohort]:
    """Every configured cohort, sector cohorts first (US, then EU).

    Themes are included ONLY when `themes_cfg` is supplied. Consumers that
    render sector-only surfaces pass nothing, which preserves today's
    behaviour; the unified page (cohort-unification PR 5) passes the config.
    Passing `themes_cfg` here is only half the wiring PR 5 needs: the caller's
    dataframe also has to come from a `get_scan_history(...)` call whose
    `regions` argument has been widened past the `SECTOR_REGIONS` default
    (src/state.py) to include `THEME_REGION` — otherwise the theme cohort
    returned here has no matching rows and the consumer degrades silently
    (empty section, unsorted heatmap block, zero-observation row) instead of
    raising.

    Each cohort's `instruments` dict is keyed exactly `"{region}|{name}"` —
    consumers may split on the first "|" to recover `name`, as
    `dashboard/correlation.py` does.

    A cohort with no configured members is omitted entirely.
    """
    result: list[Cohort] = []

    for cfg_key, region, label, bench_key, bench_default in _SECTOR_COHORTS:
        members = universe.get(cfg_key) or {}
        if not members:
            continue
        result.append(Cohort(
            region=region,
            label=label,
            benchmark=universe.get(bench_key) or bench_default,
            instruments={
                f"{region}|{name}": ticker for name, ticker in members.items()
            },
        ))

    themes = (themes_cfg or {}).get("themes") or {}
    if themes:
        result.append(Cohort(
            region=THEME_REGION,
            label="Themes",
            benchmark=(themes_cfg or {}).get("benchmark") or "ACWI",
            instruments={
                f"{THEME_REGION}|{name}": _theme_ticker(cfg)
                for name, cfg in themes.items()
            },
        ))

    return result


def instrument_map(cohort_list: list[Cohort]) -> dict[str, str]:
    """Flatten cohorts into one {region|name: ticker} map."""
    out: dict[str, str] = {}
    for cohort in cohort_list:
        out.update(cohort.instruments)
    return out
