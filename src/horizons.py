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

import math
from dataclasses import dataclass
from pathlib import Path

import yaml

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "weights.yaml"

# Used when config carries no horizons block at all. Mirrors the shipped
# "medium" preset (M/4/5, ~120-day hold) so a config-less run holds the same
# book and the same band as the default it claims to copy. It drifted out of
# step once already — it stayed at 5/3 through two preset retunes, quietly
# handing a missing-config run a different strategy from the documented one —
# so update it whenever `medium` moves in config/weights.yaml.
#
# The point is to degrade to a sane strategy rather than to top_n=0 (hold
# nothing) or buffer_frac=0 (maximum churn).
_FALLBACK = {
    "key": "medium", "label": "Medium", "rebalance": "M",
    "top_n": 4, "buffer_frac": 5 / 18,
}


def _round_half_up(x: float) -> int:
    """int(round-half-away-from-zero(x)).

    Python's builtin round() uses banker's rounding (round-half-to-even) for
    exact .5 ties -- round(4.5) == 4, round(2.5) == 2 -- which is not what
    "half away from zero" means and would silently diverge from JS's
    Math.round(), which already rounds .5 up for the non-negative inputs this
    is always called with (buffer_frac * universe_size, both >= 0). Used by
    Horizon.exit_rank so a rank-band boundary is computed identically on the
    server and in the client-side mirror.
    """
    return int(math.floor(x + 0.5))


@dataclass(frozen=True)
class Horizon:
    key: str                    # "medium" | "long"
    label: str                  # human-readable, e.g. "Medium"
    rebalance: str              # a src.backtest.replay REBALANCE_FREQS key
    top_n: int                  # positions held
    buffer_frac: float          # hysteresis band width, as a FRACTION of the
                                 # scored universe -- not an absolute rank
                                 # count. See exit_rank().

    # No cagr/trades_per_year/median_holding_days here -- those are
    # backtest-observed statistics, not a property of the preset mechanism
    # itself, and a config-driven copy of them goes stale the moment the
    # scored universe changes (2026-08-31 churn-figure-honesty fix). The
    # dashboard reads them live from backtests/summary.json at the point
    # of display instead -- same principle as exit_rank(), which is
    # likewise never cached as a static field. See
    # sector_momentum-notes/specs/2026-08-31-long-churn-honesty-design.md.

    def exit_rank(self, universe_size: int) -> int:
        """Ranks above this leave the hold band, for a scored universe of
        `universe_size` themes. Positions are kept while `rank <= exit_rank`,
        which is what makes a one-rank wobble a non-event.

        Resolved fresh from `universe_size` on every call rather than stored
        as a fixed number: the buffer is a FRACTION of the universe, so
        exit_rank widens or narrows as the theme universe grows or shrinks.
        A stale, cached exit_rank is exactly the bug this design replaces —
        see sector_momentum-notes/specs/2026-08-30-fractional-hysteresis-band-design.md.
        """
        return self.top_n + _round_half_up(self.buffer_frac * universe_size)


#: Used when config carries no `costs:` block. Non-zero on purpose: a zero
#: default is what let the presets be selected under free-trading assumptions,
#: and a silently-absent config should not quietly restore that.
_FALLBACK_ROUND_TRIP_BPS = 50.0


def _cfg(path: str | Path | None = None) -> dict:
    p = Path(path) if path else _DEFAULT_PATH
    if not p.exists():
        return {}
    return yaml.safe_load(p.read_text()) or {}


def _load(path: str | Path | None = None) -> dict:
    return _cfg(path).get("horizons") or {}


def round_trip_bps(path: str | Path | None = None) -> float:
    """All-in cost of replacing one position, in basis points.

    Read by `backtest.py` and `scripts/horizon_sweep.py` so the figures shown
    beside each preset, and the sweep that picks the presets in the first
    place, share one assumption. A negative or non-numeric value falls back
    rather than raising: a config typo should not silently produce a strategy
    that is paid to trade.
    """
    raw = (_cfg(path).get("costs") or {}).get("round_trip_bps",
                                              _FALLBACK_ROUND_TRIP_BPS)
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return _FALLBACK_ROUND_TRIP_BPS
    return val if val >= 0 else _FALLBACK_ROUND_TRIP_BPS


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
            buffer_frac=float(entry.get("buffer_frac", _FALLBACK["buffer_frac"])),
        ))
    return out


#: `since` (the server's UTC build date) is padded this many days into the
#: past before generating the calendar. A reader's LOCAL calendar date can
#: trail the server's UTC date for part of every day (anyone west of UTC) or
#: lead it (anyone east) — and once a review date drops out of the array
#: because the server's date passed it, no later build ever re-adds it, since
#: every build's window only moves forward. Two days covers any timezone in
#: the world (max offset ±14h, i.e. under one full day of skew) plus slop for
#: the build not running at exactly UTC midnight, with no risk of pulling in
#: an extra, unwanted review date: cadences are weekly-or-longer, so two days
#: of margin can only ever recover the ONE boundary that just passed, never
#: reach back into the one before it.
_CLOCK_SKEW_MARGIN_DAYS = 2


def review_dates(horizon: Horizon, since: str, count: int = 6) -> list[str]:
    """This preset's next `count` review dates, as ISO date strings.

    `since` has no default deliberately: this module is pure config -> data
    with no clock of its own, and a hidden `datetime.now()` here would make
    every caller's tests non-deterministic. The caller (`dashboard/build.py`,
    at build/scan time) is the one place "now" should be decided — but this
    function, not the caller, is responsible for padding it defensively (see
    `_CLOCK_SKEW_MARGIN_DAYS`): centralising that here means a future second
    caller cannot forget it and reintroduce the bug it fixes.

    Thin wrapper over `replay.forward_rebalance_dates`, reading the preset's
    OWN `rebalance` cadence rather than assuming "M" — so a future preset on a
    different cadence gets its own calendar shape. Returns strings, not
    `pandas.Timestamp`, because this feeds `dashboard/build.py`'s JSON context
    directly and a Timestamp is not JSON-serialisable.
    """
    from datetime import date, timedelta
    from src.backtest.replay import forward_rebalance_dates
    padded_since = date.fromisoformat(since) - timedelta(days=_CLOCK_SKEW_MARGIN_DAYS)
    dates = forward_rebalance_dates(horizon.rebalance, padded_since.isoformat(), count=count)
    return [d.date().isoformat() for d in dates]


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
