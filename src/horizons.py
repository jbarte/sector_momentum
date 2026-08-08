"""Horizon presets — how long the strategy intends to hold a position.

A preset is a `(rebalance cadence, top_n, buffer)` triple. It is read by three
places that must agree or the dashboard will describe a strategy the backtest
never ran:

- `backtest.py`, which replays every preset into `backtests/summary.json`
- `dashboard/rows.py`, which derives the Entry/Exit band badge
- `dashboard/assets/rescore.js`, which re-derives that badge client-side when
  the reader switches preset

This module is the single source. Pure config -> data; no I/O beyond reading
the YAML, no database, no network.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "weights.yaml"

# Used when config carries no horizons block at all. Matches the "medium"
# preset, i.e. the cell the 2026-08-08 sweep put on the frontier at roughly a
# two-month hold. A missing config should degrade to a sane strategy, not to
# top_n=0 (hold nothing) or buffer=0 (maximum churn).
_FALLBACK = {
    "key": "medium", "label": "Medium", "rebalance": "M",
    "top_n": 5, "buffer": 3,
    "cagr": None, "trades_per_year": None, "median_holding_days": None,
}


@dataclass(frozen=True)
class Horizon:
    key: str                    # "short" | "medium" | "long"
    label: str                  # human-readable, e.g. "Medium"
    rebalance: str              # a src.backtest.replay REBALANCE_FREQS key
    top_n: int                  # positions held
    buffer: int                 # hysteresis band, IN RANKS

    # Backtested figures for this cell, carried so the UI can show the churn
    # cost beside the return. Optional: they describe one historical sweep and
    # are not required for the strategy to run.
    cagr: float | None = None
    trades_per_year: float | None = None
    median_holding_days: float | None = None

    @property
    def exit_rank(self) -> int:
        """Ranks above this leave the hold band. Positions are kept while
        `rank <= exit_rank`, which is what makes a one-rank wobble a non-event."""
        return self.top_n + self.buffer


def _load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return {}
    return (yaml.safe_load(p.read_text()) or {}).get("horizons") or {}


def horizons(path: str | Path | None = None) -> list[Horizon]:
    """Every configured preset, in config order. Never empty."""
    cfg = _load(path)
    presets = cfg.get("presets") or {}
    if not presets:
        return [Horizon(**_FALLBACK)]
    out = []
    for key, entry in presets.items():
        entry = entry or {}
        out.append(Horizon(
            key=key,
            label=entry.get("label") or key.title(),
            rebalance=entry.get("rebalance", "M"),
            top_n=int(entry.get("top_n", _FALLBACK["top_n"])),
            buffer=int(entry.get("buffer", _FALLBACK["buffer"])),
            cagr=entry.get("cagr"),
            trades_per_year=entry.get("trades_per_year"),
            median_holding_days=entry.get("median_holding_days"),
        ))
    return out


def default_horizon(path: str | Path | None = None) -> Horizon:
    """The preset the baked page renders and alerts use.

    Falls back to the first configured preset if `default:` names one that does
    not exist, rather than raising — a typo in config should not take the
    dashboard down.
    """
    cfg = _load(path)
    all_h = horizons(path)
    wanted = cfg.get("default")
    for h in all_h:
        if h.key == wanted:
            return h
    return all_h[0]
