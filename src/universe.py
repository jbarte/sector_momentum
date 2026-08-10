"""Universe properties read from config/themes.yaml.

Buyability lives here, not in the backtest engine or the dashboard, because
four surfaces need the same answer and any two of them disagreeing puts the
product back where this started — prompting a purchase the reader cannot make:

    leaderboard badge   dashboard/build.py, dashboard/breakdown.py
    drill-down panel    dashboard/breakdown.py
    push alerts         src/alerts.py
    backtest + sweep    src/backtest/engine.py, scripts/horizon_sweep.py

An earlier version had the dashboard matching on a bare theme name while the
backtest matched on the region-scoped `THEME|<name>` key. Nothing broke, because
the US/EU sector cohorts are retired and no other cohort has a name collision —
but a sector that happened to share a flagged theme's name would have been
treated as unbuyable by the board and as tradeable by the backtest. One
region-scoped predicate now, so that cannot happen.
"""
from __future__ import annotations

THEME_REGION = "THEME"


def _flagged(themes_cfg: dict | None) -> set[str]:
    themes = (themes_cfg or {}).get("themes") or {}
    return {name for name, cfg in themes.items()
            if isinstance(cfg, dict) and cfg.get("unbuyable")}


def unbuyable_names(themes_cfg: dict | None) -> list[str]:
    """Flagged theme names, sorted.

    Read from CONFIG, never derived from rendered rows: the baked dashboard is
    lagged and can carry a smaller universe than a signed-in reader sees, which
    would silently produce an empty list.
    """
    return sorted(_flagged(themes_cfg))


def unbuyable_keys(themes_cfg: dict | None) -> frozenset[str]:
    """Flagged names as region-scoped `THEME|<name>` sector keys."""
    return frozenset(f"{THEME_REGION}|{name}" for name in _flagged(themes_cfg))


def is_unbuyable(region: str, sector_name: str, themes_cfg: dict | None) -> bool:
    """Whether this row has no route to purchase.

    Region-scoped on purpose — the flag is a property of a theme, and only the
    THEME cohort can carry it.
    """
    return region == THEME_REGION and sector_name in _flagged(themes_cfg)
