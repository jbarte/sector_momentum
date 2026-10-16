"""
Price data loader.

Fetches daily OHLCV price data for a list of tickers via yfinance — a fragile
free source, so aggressive caching minimises live fetches. Tiingo is the
fallback: tried per ticker, only after yfinance returned nothing for it, only
when TIINGO_API_KEY is set, and only for a caller that asks for it
(`fetch_prices(..., fallback=True)`; the daily scan does, nothing else does).
Insurance against a Yahoo outage or API break, not a second opinion; unset or
not asked for, behaviour is exactly yfinance-only. Tiingo carries US listings
only and allows 50 requests an hour, which is why the scored universe's scan
is the one caller — see `_fetch_tiingo` for the licence limit on what may be
published from it.

stooq was the first second source and was retired on 2026-08-09: its CSV
endpoint has required solving a JavaScript proof-of-work challenge since at
least 2026-07-21 (confirmed by direct probe —
every request returns either an HTTP 404 for the plain `requests` user-agent
or a `noscript` challenge page for any other, never CSV data). No header or
URL fix gets past that; see BACKLOG.md Done for the investigation. yfinance
had already been carrying 100% of live fetches in practice.

Cache location: data/cache/<ticker>_prices.parquet
Cache validity: the cache must reach the last completed session strictly
before today — the newest bar a fetch could actually return, since `end` is
exclusive (below). On a normal weekday that is yesterday's close; on a
Saturday, Sunday or Monday it is Friday's.
See `_cache_is_fresh`.

`end` is **EXCLUSIVE**: the last bar returned is the last completed session
strictly before `end`. No caller passes an `end` beyond today, so this is what
keeps an in-progress session out of the data — Yahoo returns a partial candle
for the current day during market hours, and `_cache_is_fresh` would then treat
that half-formed close as a real one and never refetch it.

That invariant is not free: `dashboard/badges.py` and
`dashboard/validation.py` size their window from a forward-return horizon
(`+15` / `+30 days` past the newest scan), which overshoots today. Both route
`end` through `capped_end` to restore it — see that function.

Callers that score a cohort cross-sectionally should still run the result
through `align_cohort_asof`: per-ticker cache freshness is evaluated
independently, so a dict returned by `fetch_prices` can mix as-of dates even
when every ticker came from the same source.
"""

import json
import logging
import math
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_OHLCV_COLS = ["Close", "Open", "High", "Low", "Volume"]


def _sanitize_ticker(ticker: str) -> str:
    """Replace characters that are unsafe in filenames."""
    return ticker.replace(".", "_").replace("/", "_")


def _cache_path(ticker: str, cache_dir: str) -> str:
    return os.path.join(cache_dir, f"{_sanitize_ticker(ticker)}_prices.parquet")


def _expected_latest_close(ref: date) -> date:
    """Return the most recent weekday on or before *ref*."""
    weekday = ref.weekday()  # Mon=0 … Sun=6
    if weekday == 5:  # Saturday
        return ref - timedelta(days=1)
    if weekday == 6:  # Sunday
        return ref - timedelta(days=2)
    return ref


def capped_end(when) -> str:
    """Clamp a requested `end` to today and render it as `YYYY-MM-DD`.

    For callers whose window is sized from a forward horizon rather than from
    the calendar — `dashboard/badges.py` and `dashboard/validation.py` measure
    returns some number of days past each scan date, which runs past today for
    the most recent scans.

    No bar exists past today, so clamping returns identical data. What it
    prevents is a cache hazard: `end` is EXCLUSIVE and is the mechanism that
    keeps an in-progress session out of the data, while `_cache_is_fresh` never
    receives `end` and always measures the cache against today. An `end` in the
    future lets yfinance return today's partial candle, and that check then
    judges the half-formed close fresh — in the same shared `data/cache/`
    directory `scan.py` later reads and scores from.

    The trade-off is not quite "identical data": `end` is exclusive, so after
    the close a clamped request stops at yesterday and gives up today's
    completed bar, costing at most one forward-return observation on the newest
    scan. That is the same bar `scan.py` forgoes, for the same reason — a bar
    fetched during market hours is a partial candle, and nothing downstream can
    tell the two apart once it is cached.

    Accepts an ISO `YYYY-MM-DD` string, a pandas Timestamp, a datetime, or a
    plain `date`.
    """
    if isinstance(when, str):
        when = date.fromisoformat(when)
    elif hasattr(when, "date"):
        when = when.date()
    return min(when, date.today()).strftime("%Y-%m-%d")


def _meta_path(path: str) -> str:
    """Sidecar recording the window a cache file was fetched over."""
    return path + ".meta.json"


def _write_cache_meta(path: str, start: str) -> None:
    """Record the requested start alongside a freshly written cache file.

    Written atomically and best-effort: a missing sidecar only costs us the
    legacy coverage heuristic, never correctness of the prices themselves.
    """
    meta_path = _meta_path(path)
    tmp = meta_path + ".tmp"
    try:
        with open(tmp, "w") as fh:
            json.dump({"start": str(pd.Timestamp(start).date())}, fh)
        os.replace(tmp, meta_path)
    except Exception as exc:
        logger.debug("Could not write cache metadata for %s: %s", path, exc)
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def _read_cache_meta_start(path: str):
    """The start this cache was fetched from, or None when unrecorded."""
    try:
        with open(_meta_path(path)) as fh:
            return pd.Timestamp(json.load(fh)["start"]).date()
    except Exception:
        return None


def _load_fresh_cache(path: str, start: str | None = None) -> pd.DataFrame | None:
    """Return the cached DataFrame if it is fresh, else None.

    Fresh means: the file exists, its last date reaches the last completed
    session strictly before today (the newest bar an `end`-exclusive fetch
    could return), and — when ``start`` is given — its earliest date covers
    the requested range.

    A market holiday makes the required session produce no bar, so the cache
    cannot reach it and is refetched until the next real session — the same
    harmless extra fetch the pre-2026-08-22 rule already made after a Monday
    holiday (see test_cache_stale_friday_on_tuesday_after_holiday).

    Returns the DataFrame it already loaded rather than a bare bool: `fetch_prices`
    used to call the bool-returning predecessor of this function and then, on a
    cache hit, immediately call `pd.read_parquet` a SECOND time on the exact
    same file to actually load it — 20 tickers x 2 reads per scan. Found in the
    2026-08-23 sweep. `_cache_is_fresh` (below) stays a thin bool wrapper over
    this for the many callers/tests that only want a yes/no answer and never
    reload the frame from a stale mmap after the freshness check ran."""
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        # The newest bar a fetch could actually return: the last weekday
        # strictly before today. `end` is EXCLUSIVE, so today's own session is
        # never obtainable — which is what keeps this boundary a pure calendar
        # question, with no market-hours or timezone logic.
        #
        # This function does NOT receive the caller's `end` and always measures
        # against today. That used to be a gap: `dashboard/badges.py` and
        # `dashboard/validation.py` sized their window from a forward-return
        # horizon and passed an `end` days past today, so for them this boundary
        # did not rule out a same-day partial candle. Closed 2026-08-22 by
        # clamping `end` inside `fetch_prices` (see `capped_end`), which makes
        # "no caller requests a future `end`" an enforced invariant rather than
        # an assumption this comment has to keep restating.
        #
        # This boundary replaced an `_expected_latest_close(today)` + 1-day
        # grace pair (fixed 2026-08-22). On a weekday the two agree exactly,
        # but on a weekend `expected` had already walked back to Friday — a
        # COMPLETED session — and the grace then walked back one more, so a
        # Saturday or Sunday run accepted a cache written Friday morning whose
        # last bar is Thursday, and never fetched Friday's close. Confirmed
        # live against the cache on 2026-08-09; scans 155 and 156 re-scored
        # Thursday twice.
        required = _expected_latest_close(date.today() - timedelta(days=1))
        if last_cached < required:
            return None
        if start is not None:
            requested_start = pd.Timestamp(start).date()
            fetched_start = _read_cache_meta_start(path)
            if fetched_start is not None:
                # We know the window this cache was fetched over. It covers the
                # request whenever we asked Yahoo for an equal-or-earlier start
                # — regardless of when the instrument itself began trading.
                return df if fetched_start <= requested_start else None
            # No metadata (cache predates this being recorded): fall back to
            # judging by the first row. This is wrong for any instrument younger
            # than `start` — it can never hold data it never had — but it is the
            # previous behaviour, and the cache self-heals on the next refetch.
            cached_start = df.index.min().date() if hasattr(df.index.min(), "date") else df.index.min()
            if cached_start > requested_start + timedelta(days=7):
                return None
        return df
    except Exception:
        return None


def _cache_is_fresh(path: str, start: str | None = None) -> bool:
    """Whether the cache at `path` is fresh — see `_load_fresh_cache` for the
    rules. Thin bool wrapper: callers that only need yes/no (and every
    existing test pinning the freshness rules themselves) use this; the one
    caller that goes on to actually use the cached prices (`fetch_prices`)
    calls `_load_fresh_cache` directly instead, to avoid reading the same
    parquet file twice."""
    return _load_fresh_cache(path, start) is not None


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Ensure the DataFrame has exactly the required OHLCV columns."""
    # yfinance may return MultiIndex columns when downloading a single ticker.
    # Level ordering is version-dependent: check if level 0 is all the same
    # value (ticker symbol repeated), and if so use level 1 instead.
    if isinstance(df.columns, pd.MultiIndex):
        level0_vals = df.columns.get_level_values(0)
        if len(set(level0_vals)) == 1:
            # All values on level 0 are the same (e.g. the ticker) — use level 1
            df.columns = df.columns.get_level_values(1)
        else:
            df.columns = df.columns.get_level_values(0)

    # Normalise column names: title-case the first letter so "close" -> "Close"
    rename = {}
    for col in df.columns:
        title = col.strip().title()
        if title in _OHLCV_COLS and col != title:
            rename[col] = title
    if rename:
        df = df.rename(columns=rename)

    # Keep only the columns we care about (in a consistent order)
    present = [c for c in _OHLCV_COLS if c in df.columns]
    return df[present].copy()


def _fetch_yfinance(ticker: str, start: str, end: str) -> pd.DataFrame:
    import yfinance as yf  # type: ignore

    # yfinance's `end` is already exclusive, which is this module's contract —
    # pass it straight through. Do NOT "helpfully" add a day to make it
    # inclusive: that pulls in Yahoo's partial candle for the current session
    # when a run happens during market hours, and _cache_is_fresh would then
    # accept that half-formed close and never refetch the real one.

    # multi_level_index=False avoids MultiIndex columns (yfinance >= 0.2.31).
    # Fall back to the old call signature on older versions.
    try:
        df = yf.download(
            ticker,
            start=start,
            end=end,
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
    except TypeError:
        df = yf.download(ticker, start=start, end=end, auto_adjust=True, progress=False)
    return df


_TIINGO_URL = "https://api.tiingo.com/tiingo/daily/{ticker}/prices"

# How long the fallback stays off after Tiingo said it is rate limiting (HTTP
# 429): the free tier is 50 requests an hour. A deadline on the monotonic clock
# rather than a flag cleared per fetch_prices() call, because one job calls
# fetch_prices several times and each call would otherwise ask once more.
_RATE_LIMIT_PAUSE_S = 3600.0
_tiingo_off_until = 0.0     # time.monotonic() deadline; math.inf after an auth error


class _TiingoRateLimited(Exception):
    """Tiingo answered 429: stop calling it for a while."""


class _TiingoRejectedKey(Exception):
    """Tiingo answered 401/403: the token is wrong or revoked, so every further
    request this process would make is wasted."""


def _tiingo_key() -> str | None:
    key = os.environ.get("TIINGO_API_KEY", "").strip()
    return key or None


def _fetch_tiingo(ticker: str, start: str, end: str, key: str) -> pd.DataFrame | None:
    """Daily split- and dividend-adjusted OHLCV from Tiingo, shaped like the
    yfinance result (auto_adjust=True): Close/Open/High/Low/Volume.

    The licence matters here: Tiingo's free tier is "Internal Use Only" (personal
    use). Scores derived from these prices are published on the dashboard; the
    prices themselves must never be — not in docs/, not in data.json, not in any
    committed file. The price cache is gitignored, which keeps it that way.

    `end` is EXCLUSIVE in this module. Tiingo's docs do not say whether its
    `endDate` is inclusive, so the request asks for the day before and any bar
    dated on or after `end` is dropped here regardless.

    The token travels in a header, never the URL, and a failure is reported by
    its type and status only: this repo and its CI logs are public, and
    requests' own exception text carries the request URL.

    Returns None when Tiingo has no usable data for the ticker. Raises
    `_TiingoRateLimited` on HTTP 429 and `_TiingoRejectedKey` on 401/403.
    """
    last_day = (pd.Timestamp(end) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
    resp = requests.get(
        _TIINGO_URL.format(ticker=ticker.replace(".", "-")),
        params={"startDate": start, "endDate": last_day, "format": "json"},
        headers={"Authorization": f"Token {key}", "Content-Type": "application/json"},
        timeout=30,
    )
    if resp.status_code == 429:
        raise _TiingoRateLimited()
    if resp.status_code in (401, 403):
        raise _TiingoRejectedKey()
    if resp.status_code != 200:
        logger.warning("Tiingo returned HTTP %s for %s", resp.status_code, ticker)
        return None
    rows = resp.json()
    if not isinstance(rows, list) or not rows:
        return None
    frame = pd.DataFrame(rows)
    wanted = {"adjClose": "Close", "adjOpen": "Open", "adjHigh": "High",
              "adjLow": "Low", "adjVolume": "Volume"}
    if "date" not in frame.columns or "adjClose" not in frame.columns:
        return None
    frame = frame.rename(columns=wanted)
    frame.index = pd.to_datetime(frame["date"], utc=True).dt.tz_convert(None).dt.normalize()
    frame = frame[[c for c in _OHLCV_COLS if c in frame.columns]]
    frame = frame.loc[frame.index < pd.Timestamp(end)].sort_index(ascending=True)
    if frame.empty or frame["Close"].isna().all():
        return None
    return frame


def _fetch_fallback(ticker: str, start: str, end: str) -> tuple[str | None, pd.DataFrame | None]:
    """Tiingo, for a ticker yfinance could not supply. Never raises."""
    global _tiingo_off_until
    key = _tiingo_key()
    if key is None or time.monotonic() < _tiingo_off_until:
        return None, None
    try:
        df = _fetch_tiingo(ticker, start, end, key)
    except _TiingoRateLimited:
        _tiingo_off_until = time.monotonic() + _RATE_LIMIT_PAUSE_S
        logger.warning("Tiingo is rate limiting (HTTP 429) — fallback paused for an hour")
        return None, None
    except _TiingoRejectedKey:
        _tiingo_off_until = math.inf
        logger.warning("Tiingo rejected TIINGO_API_KEY (HTTP 401/403) — fallback "
                       "disabled for this run; check the token")
        return None, None
    except Exception as exc:
        # Type only: the message of a requests error includes the request URL.
        logger.warning("Failed to fetch %s via Tiingo: %s", ticker, type(exc).__name__)
        return None, None
    if df is None:
        logger.warning("Tiingo had no usable data for ticker %s", ticker)
        return None, None
    # Tiingo's newest bar can lag Yahoo's. A short series is still returned (the
    # cohort alignment step copes with mixed as-of dates), but say so.
    expected = _expected_latest_close((pd.Timestamp(end) - pd.Timedelta(days=1)).date())
    if df.index.max().date() < expected:
        logger.warning("Tiingo's newest bar for %s is %s, expected %s",
                       ticker, df.index.max().date(), expected)
    return "tiingo", df


def _fetch_single(ticker: str, start: str, end: str,
                  fallback: bool = False) -> tuple[str | None, pd.DataFrame | None]:
    """Fetch one ticker: yfinance first, then (if `fallback`) Tiingo.

    Returns (source, DataFrame) with source "yfinance" or "tiingo", or
    (None, None) when nothing could supply it.
    """
    source, df = _fetch_primary(ticker, start, end)
    if df is not None or not fallback:
        return source, df
    return _fetch_fallback(ticker, start, end)


def _fetch_primary(ticker: str, start: str, end: str) -> tuple[str | None, pd.DataFrame | None]:
    """The yfinance leg of `_fetch_single`."""
    try:
        df = _fetch_yfinance(ticker, start, end)
        if df is None or df.empty:
            logger.warning("Empty response from yfinance for ticker %s", ticker)
            return None, None
        df = _normalize_columns(df)
        if df.empty or "Close" not in df.columns:
            logger.warning("No usable columns from yfinance for ticker %s", ticker)
            return None, None
        if df["Close"].isna().all():
            logger.warning("All-NaN Close column from yfinance for ticker %s", ticker)
            return None, None
        df.index = pd.to_datetime(df.index)
        df = df.sort_index(ascending=True)
        return "yfinance", df
    except Exception as exc:
        logger.warning("Failed to fetch %s via yfinance: %s", ticker, exc)
    return None, None


def fetch_prices(
    tickers: list[str],
    start: str,
    end: str,
    cache_dir: str = "data/cache",
    stats_out: dict[str, int] | None = None,
    fallback: bool = False,
) -> dict[str, pd.DataFrame]:
    """
    Returns a dict mapping ticker -> DataFrame with columns:
        Close, Open, High, Low, Volume
    All indexed by date (DatetimeIndex, ascending).

    Tickers that fail to fetch are logged and omitted from the returned dict
    (soft failure — never raises).

    `fallback=True` lets a ticker yfinance cannot supply be tried on Tiingo (when
    TIINGO_API_KEY is set). Opt-in: Tiingo covers US listings only and allows 50
    requests an hour, so only the scan of the scored universe uses it; the
    UCITS monitor's exchange-suffixed tickers and the dashboard builds do not.

    `end` is clamped to today here rather than trusted from the caller. It is
    EXCLUSIVE and is the one thing keeping an in-progress session out of the
    cache, while `_cache_is_fresh` never receives it and always measures against
    today — so a single caller passing a future `end` can poison the shared
    cache for every other caller. Seven call sites reach this function; making
    each one remember is the kind of invariant that holds until someone adds
    the eighth. Clamping is a no-op for the six that already pass `end=today`
    or earlier. See `capped_end`.
    """
    end = capped_end(end)
    os.makedirs(cache_dir, exist_ok=True)
    result: dict[str, pd.DataFrame] = {}
    source_counts: dict[str, int] = {"cache": 0, "yfinance": 0, "tiingo": 0}
    live_attempted = 0

    for ticker in tickers:
        path = _cache_path(ticker, cache_dir)

        cached_df = _load_fresh_cache(path, start)
        if cached_df is not None:
            try:
                df = cached_df
                df.index = pd.to_datetime(df.index)
                # Trim to the requested window. _load_fresh_cache only rejects a
                # cache that is too *short*; without this a cache holding more
                # history than asked for silently widened the run, so `--start`
                # had no effect whenever the cache was fresh.
                if start is not None:
                    df = df.loc[df.index >= pd.Timestamp(start)]
                result[ticker] = df
                source_counts["cache"] += 1
                logger.debug("Loaded %s from cache (%s rows)", ticker, len(df))
                continue
            except Exception as exc:
                # The parquet read itself already succeeded inside
                # _load_fresh_cache; a failure here is in the index-conversion
                # or start-trim above.
                logger.warning("Cache processing failed for %s: %s — re-fetching", ticker, exc)

        live_attempted += 1
        source, df = _fetch_single(ticker, start, end, fallback=fallback)
        if df is None:
            logger.warning("Skipping %s — fetch failed", ticker)
            continue

        source_counts[source] += 1

        try:
            tmp_path = path + ".tmp"
            try:
                df.to_parquet(tmp_path)
                os.replace(tmp_path, path)
                _write_cache_meta(path, start)
            except Exception:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
                raise
        except Exception as exc:
            logger.warning("Could not write cache for %s: %s", ticker, exc)

        result[ticker] = df
        logger.debug("Fetched %s via %s (%s rows)", ticker, source, len(df))

    total = len(tickers)
    logger.info(
        "Price sources: yfinance %d/%d, tiingo %d/%d, cache %d/%d",
        source_counts["yfinance"], total, source_counts["tiingo"], total,
        source_counts["cache"], total,
    )
    if live_attempted > 0 and source_counts["yfinance"] == 0:
        logger.warning(
            "yfinance: 0/%d succeeded — source may be down%s", live_attempted,
            f"; {source_counts['tiingo']} came from the Tiingo fallback"
            if source_counts["tiingo"] else "",
        )

    if stats_out is not None:
        stats_out.update(source_counts)

    return result


# Calendar days a ticker may lag the cohort's modal last date before it is
# dropped rather than allowed to drag everyone back to its date. Four days
# tolerates roughly two trading days across a weekend (mode=Monday,
# laggard=Thursday) — enough for a single foreign market holiday, not enough
# for a delisted ticker frozen in the cache.
MAX_ASOF_LAG_DAYS = 4


def align_cohort_asof(
    prices: dict[str, pd.DataFrame],
    max_lag_days: int = MAX_ASOF_LAG_DAYS,
    stats_out: dict | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.Timestamp | None]:
    """Slice every series to one shared as-of date. Returns (prices, as_of).

    Signals read the last row of whatever series they are handed
    (`src/pipeline.py`), so a dict whose members end on different dates scores
    a cohort at mixed as-of dates — and the composite z-scores each signal
    *across* that cohort, which is precisely where mixed dates distort a
    ranking. Nothing upstream guarantees a common end date: per-ticker cache
    freshness is decided independently (`_cache_is_fresh`), so one refetched
    ticker alongside 19 cache hits is enough to stagger them.

    The as-of date is the newest date every *kept* ticker has a bar for, i.e.
    the minimum of the per-ticker last dates. A ticker lagging the cohort's
    modal last date by more than ``max_lag_days`` calendar days is dropped
    instead of counted in that minimum — otherwise a single stale series would
    pull the whole cohort back to its date. Dropping a theme's ETF makes that
    theme vanish from the run, which the caller's coverage guard is there to
    catch; silently rescoring everything a month late would not be caught at
    all.

    Tickers with no rows at or before the as-of date are dropped. Returns
    ``({}, None)`` when there is nothing usable to align.

    ``stats_out``, if given, is updated with ``asof`` (ISO date string or
    None), ``asof_spread_days`` (max−min lag in calendar days, before drops)
    and ``asof_dropped`` (sorted list of dropped tickers).
    """
    last_dates = {
        ticker: pd.Timestamp(df.index.max())
        for ticker, df in prices.items()
        if df is not None and not df.empty
    }

    if not last_dates:
        if stats_out is not None:
            stats_out.update({"asof": None, "asof_spread_days": 0, "asof_dropped": []})
        logger.warning("align_cohort_asof: no non-empty price frames to align")
        return {}, None

    newest = max(last_dates.values())
    oldest = min(last_dates.values())
    spread_days = (newest - oldest).days

    # Modal last date; ties break toward the later date so the cohort anchors
    # on the fresher of two equally-sized groups.
    counts: dict[pd.Timestamp, int] = {}
    for d in last_dates.values():
        counts[d] = counts.get(d, 0) + 1
    modal = max(counts, key=lambda d: (counts[d], d))

    kept, dropped = {}, []
    for ticker, d in last_dates.items():
        if (modal - d).days > max_lag_days:
            dropped.append(ticker)
        else:
            kept[ticker] = d

    if dropped:
        logger.warning(
            "align_cohort_asof: dropping %d stale ticker(s) lagging the cohort "
            "(modal last bar %s) by more than %d days: %s",
            len(dropped), modal.date(), max_lag_days,
            ", ".join(f"{t}@{last_dates[t].date()}" for t in sorted(dropped)),
        )

    as_of = min(kept.values())

    out: dict[str, pd.DataFrame] = {}
    for ticker in kept:
        sliced = prices[ticker][prices[ticker].index <= as_of]
        if sliced.empty:
            logger.warning(
                "align_cohort_asof: %s has no bars at or before %s — dropping",
                ticker, as_of.date(),
            )
            dropped.append(ticker)
            continue
        out[ticker] = sliced

    if spread_days:
        logger.info(
            "align_cohort_asof: cohort last bars spanned %d day(s) (%s → %s); "
            "scoring all %d ticker(s) as-of %s",
            spread_days, oldest.date(), newest.date(), len(out), as_of.date(),
        )
    else:
        logger.info(
            "align_cohort_asof: all %d ticker(s) already as-of %s",
            len(out), as_of.date(),
        )

    if stats_out is not None:
        stats_out.update({
            "asof": as_of.date().isoformat(),
            "asof_spread_days": spread_days,
            "asof_dropped": sorted(dropped),
        })

    return out, as_of
