"""Drive the existing scoring pipeline as-of a historical date, per region."""
from __future__ import annotations

import pandas as pd

from src.pipeline import SIGNAL_COLUMNS, build_theme_signals_rows
from src.scoring import score_all


def truncate_prices(prices: dict[str, pd.DataFrame], as_of: pd.Timestamp) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in prices.items():
        sliced = df[df.index <= as_of]
        if not sliced.empty:
            out[ticker] = sliced
    return out


# Rebalance cadences, mapped to the pandas period they group by. The "every
# n-th" entry is applied after grouping: 2W is every second week-end, 2M every
# second month-end. There is no pandas period for those, and taking every second
# element of the finer calendar is exactly what a fortnightly rebalance means.
REBALANCE_FREQS: dict[str, tuple[str, int]] = {
    "W":  ("W", 1),
    "2W": ("W", 2),
    "M":  ("M", 1),
    "2M": ("M", 2),
    "Q":  ("Q", 1),
}


# Fetch window vs evaluation window
# --------------------------------
# Price history is ALWAYS fetched from FETCH_START, whatever window a caller
# wants to evaluate; the window is applied afterwards via `rebalance_dates(...,
# since=)`. Passing the evaluation start to fetch_prices instead is a trap both
# entry points fell into: signals with a trailing lookback return NaN until it
# fills (compute_ma_structure needs 200 bars for above_200dma), so evaluation
# beginning on the first fetched bar scores its opening months on a degraded
# signal set. On a 2008 start that is the whole crash, and in the sweep it was
# enough to INVERT the horizon preset ranking — `M/5/7` beat `M/5/4` by 1.9pp
# CAGR starved and lost to it by 0.8pp warm.
#
# These live here, next to `since`, rather than in each CLI: two copies of a
# safety constant is how the sweep and backtest.py came to disagree in the
# first place.
FETCH_START = "2003-01-01"

# How much history an evaluation window must leave behind its first date.
# above_200dma needs 200 TRADING bars; a calendar year is ~252 of them, so a
# year of margin clears it without needing a trading calendar here.
WARMUP_DAYS = 365

# Deliberately NOT FETCH_START: defaulting the evaluation start to the fetch
# start leaves the bare, default invocation — the one that produces the shipped
# figures — evaluating from the first fetched bar, i.e. the exact defect this
# separation exists to remove.
DEFAULT_EVAL_START = "2004-01-01"


def validate_eval_start(start: str) -> pd.Timestamp:
    """Parse an evaluation start, rejecting one that would evaluate cold.

    Raises rather than clamping: a silently-moved window is what produced a
    report header naming a start the run never used.
    """
    ts = pd.Timestamp(start)
    earliest = pd.Timestamp(FETCH_START) + pd.Timedelta(days=WARMUP_DAYS)
    if ts < earliest:
        raise ValueError(
            f"--start {ts.date()} leaves too little history to warm the signals: "
            f"prices are fetched from {FETCH_START}, and evaluation must begin no "
            f"earlier than {earliest.date()} ({WARMUP_DAYS} days later) so "
            f"above_200dma is populated on the first evaluated date."
        )
    return ts


def validate_eval_window(start: str, end: str | None):
    """Parse an evaluation window, rejecting a cold start or an empty range.

    `end` of None means "run to the last available bar" — the behaviour every
    sweep had before the bound existed, kept as the default so previously
    recorded results still describe the run that produced them.

    Rejects rather than clamping, for the same reason validate_eval_start does:
    a silently-moved bound produces a report header naming a window the run
    never evaluated.
    """
    start_ts = validate_eval_start(start)
    if end is None:
        return start_ts, None
    end_ts = pd.Timestamp(end)
    if end_ts <= start_ts:
        raise ValueError(
            f"--end {end_ts.date()} is not after --start {start_ts.date()}: "
            "the window would be empty, and the report would name a range with "
            "no cells under it."
        )
    return start_ts, end_ts


def rebalance_dates(index: pd.DatetimeIndex, freq: str = "M",
                    since: pd.Timestamp | str | None = None,
                    until: pd.Timestamp | str | None = None) -> list[pd.Timestamp]:
    """Last trading day of each period in `index`, for the given cadence.

    freq is one of REBALANCE_FREQS ("W", "2W", "M", "2M", "Q"). The default "M"
    reproduces month_end_dates exactly, which is the regression gate for adding
    cadence at all.

    `since` bounds the returned calendar without bounding the price history it
    was derived from. This is the difference between "evaluate from 2008" and
    "have no data before 2008": signals with a trailing window (above_200dma
    needs 200 bars) return NaN until their lookback fills, so evaluating from
    the first available bar scores the opening months on a degraded signal set.
    Callers should fetch full history and pass `since`, never truncate the
    fetch.

    `until` is the closing bracket, and is INCLUSIVE like `since`: a date
    landing exactly on either bound is inside that window. Without it every
    window ran to the last available bar, so any two runs necessarily
    OVERLAPPED — which makes "the same cell wins on both windows" much weaker
    evidence than it reads as, since the two windows shared most of their
    data. `since=X` and `until=X` partition a calendar cleanly, which is what
    a genuinely disjoint early-vs-late comparison needs.

    Filtering happens AFTER the period grouping on purpose, for BOTH bounds.
    Multi-period cadences ("2M") take every Nth period end counting from the
    start of `index`, so slicing the index first would shift which months are
    review months — the caller would silently evaluate a different calendar
    than the one the presets were picked on.
    """
    if freq not in REBALANCE_FREQS:
        raise ValueError(
            f"unknown rebalance freq {freq!r}; expected one of "
            f"{sorted(REBALANCE_FREQS)}"
        )
    if len(index) == 0:
        return []
    period, step = REBALANCE_FREQS[freq]
    s = pd.Series(index, index=index)
    # group by period, take the max (last) trading day in each
    last_per_period = s.groupby(index.to_period(period)).max()
    dates = [pd.Timestamp(d) for d in last_per_period.tolist()]
    dates = dates[::step]
    if since is not None:
        cutoff = pd.Timestamp(since)
        dates = [d for d in dates if d >= cutoff]
    if until is not None:
        ceiling = pd.Timestamp(until)
        dates = [d for d in dates if d <= ceiling]
    return dates


def month_end_dates(index: pd.DatetimeIndex) -> list[pd.Timestamp]:
    """Backwards-compatible alias for the monthly cadence."""
    return rebalance_dates(index, "M")


# Frequency aliases for FUTURE dates, where there is no price index to derive
# "last trading day" from. Pandas' business-day aliases give "last WEEKDAY of
# the period" — deliberately not holiday-aware, since a future market holiday
# calendar does not exist to consult. The two diverge rarely but really
# (2024-03-29 was both the last weekday of March and Good Friday); under a
# due-until-acknowledged UI the cost is a review surfacing one day early,
# which is not worth a holiday-calendar dependency. Same (period, step) shape
# as REBALANCE_FREQS, for the same every-Nth-period reason.
_FORWARD_FREQS: dict[str, tuple[str, int]] = {
    "W":  ("W-FRI", 1),
    "2W": ("W-FRI", 2),
    "M":  ("BME", 1),
    "2M": ("BME", 2),
    "Q":  ("BQE", 1),
}


# Deliberately-conservative (i.e. UNDER, never over) calendar-day length per
# _FORWARD_FREQS base, used only to size how many periods
# forward_rebalance_dates must generate from FETCH_START to comfortably
# reach an arbitrary future `since`. Using the true average (30.4 days/month)
# instead of a shortest-case bound under-generates periods for a `since` far
# from the epoch: at 23 years out, a 31-vs-30.4-day/period rounding error
# alone is enough to fall ~6 periods short, which is exactly what the first
# version of this fix did (caught by test_forward_count_is_respected).
# Shortest possible period (28-day February, 90-day quarter) guarantees this
# never happens, at the cost of a handful of extra, cheap date_range rows.
_FORWARD_BASE_DAYS: dict[str, int] = {"W-FRI": 7, "BME": 28, "BQE": 90}


def forward_rebalance_dates(freq: str, since: pd.Timestamp | str,
                             count: int = 6) -> list[pd.Timestamp]:
    """Approximate future review dates: the last weekday of each period from
    `since` onward, with no price data required. See `_FORWARD_FREQS` for why
    this is a distinct calendar from `rebalance_dates` rather than an
    extrapolation of it.

    `since` is INCLUSIVE: if it lands exactly on a period boundary, that date
    is the first result. This is the case that matters most — it is what lets
    a build running ON a review day say so, rather than reporting the next one.

    For a step>1 cadence (2W, 2M) the thinning that picks every Nth period is
    anchored at a FIXED epoch (`FETCH_START`, the same anchor `rebalance_dates`
    uses), never at `since`. Anchoring at `since` instead was the shipped
    behaviour until this comment was added, and it was a real, silent bug for
    any step>1 cadence: which periods count as "on" flipped depending on
    which day happened to fall inside the window, so a daily CI rebuild could
    hand the reader a completely different 2M calendar than yesterday's build
    -- `since=2026-09-02` gave Aug/Oct/Dec/Feb, `since=2026-09-05` (3 days
    later) gave Sep/Nov/Jan/Mar, disjoint. Anchoring at FETCH_START instead
    makes the calendar depend only on the cadence, matching the phase
    `rebalance_dates` actually measured backtest performance on (verified:
    both produce 2026 dates Jan 30 / Mar 31 / May 29 / Jul 31 / Sep 30 / Nov 30
    for 2M) rather than a coincidence of build timing.
    """
    if freq not in _FORWARD_FREQS:
        raise ValueError(
            f"unknown rebalance freq {freq!r}; expected one of "
            f"{sorted(_FORWARD_FREQS)}"
        )
    base_freq, step = _FORWARD_FREQS[freq]
    since_ts = pd.Timestamp(since).normalize()
    epoch = pd.Timestamp(FETCH_START)
    # Enough periods to comfortably span epoch -> since, plus count*step more
    # so the post-filter, post-thin result never falls short of `count`.
    span_days = max(0, (since_ts - epoch).days)
    periods = span_days // _FORWARD_BASE_DAYS[base_freq] + count * step + step
    raw = pd.date_range(start=epoch, periods=periods, freq=base_freq)
    thinned = [pd.Timestamp(d) for d in raw][::step]
    return [d for d in thinned if d >= since_ts][:count]


def score_themes_as_of(
    themes_cfg: dict,
    prices: dict[str, pd.DataFrame],
    as_of: pd.Timestamp,
) -> pd.DataFrame | None:
    truncated = truncate_prices(prices, as_of)
    rows = build_theme_signals_rows(themes_cfg, truncated)
    if not rows:
        return None
    wide = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]
    scored = score_all(wide, weights_path="config/weights.yaml",
                       sentiment_score=None, blend_sentiment=False)
    return scored
