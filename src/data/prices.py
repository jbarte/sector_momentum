"""
Price data loader.

Fetches daily OHLCV price data for a list of tickers via yfinance — a fragile
free source, so aggressive caching minimises live fetches.

stooq was the second leg of a two-source fallback until 2026-08-09, when it
was retired: stooq's CSV endpoint has required solving a JavaScript
proof-of-work challenge since at least 2026-07-21 (confirmed by direct probe —
every request returns either an HTTP 404 for the plain `requests` user-agent
or a `noscript` challenge page for any other, never CSV data). No header or
URL fix gets past that; see BACKLOG.md Done for the investigation. yfinance
had already been carrying 100% of live fetches in practice.

Cache location: data/cache/<ticker>_prices.parquet
Cache validity: the cache must reach the most recent expected trading day
(the last weekday on or before today, with a 1-day grace for single market
holidays). On a normal weekday this means yesterday's or today's close is
required; over weekends, Friday's close bridges to Monday.
See `_cache_is_fresh`.

`end` is **EXCLUSIVE**: the last bar returned is the last completed session
strictly before `end`. Callers pass `end=today`, so this is what keeps an
in-progress session out of the data — Yahoo returns a partial candle for the
current day during market hours, and `_cache_is_fresh` would then treat that
half-formed close as a real one and never refetch it.

Callers that score a cohort cross-sectionally should still run the result
through `align_cohort_asof`: per-ticker cache freshness is evaluated
independently, so a dict returned by `fetch_prices` can mix as-of dates even
when every ticker came from the same source.
"""

import logging
import os
from datetime import date, timedelta

import pandas as pd
import yaml

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


def _cache_is_fresh(path: str, start: str | None = None) -> bool:
    """Return True if the cache file exists, its last date reaches the most
    recent expected trading day (weekday walk-back from today, with a 1-day
    grace for single market holidays), and — when ``start`` is given — its
    earliest date covers the requested range."""
    if not os.path.exists(path):
        return False
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return False
        last_cached = df.index.max().date() if hasattr(df.index.max(), "date") else df.index.max()
        expected = _expected_latest_close(date.today())
        # 1-day grace, walked back over weekends: the grace boundary is the
        # prior *trading* day before `expected`, not merely a calendar day
        # earlier (which would land on a weekend after a Monday `expected`).
        grace_boundary = _expected_latest_close(expected - timedelta(days=1))
        if last_cached < grace_boundary:
            return False
        if start is not None:
            cached_start = df.index.min().date() if hasattr(df.index.min(), "date") else df.index.min()
            requested_start = pd.Timestamp(start).date()
            if cached_start > requested_start + timedelta(days=7):
                return False
        return True
    except Exception:
        return False


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


def _fetch_single(ticker: str, start: str, end: str) -> tuple[str | None, pd.DataFrame | None]:
    """Fetch one ticker via yfinance. Returns ("yfinance", DataFrame) or (None, None).

    yfinance is the only source — see the module docstring for why stooq was
    retired. The (source, df) return shape is kept, rather than collapsing to
    just a DataFrame, so a second source can be reintroduced without touching
    every caller.
    """
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
) -> dict[str, pd.DataFrame]:
    """
    Returns a dict mapping ticker -> DataFrame with columns:
        Close, Open, High, Low, Volume
    All indexed by date (DatetimeIndex, ascending).

    Tickers that fail to fetch are logged and omitted from the returned dict
    (soft failure — never raises).
    """
    os.makedirs(cache_dir, exist_ok=True)
    result: dict[str, pd.DataFrame] = {}
    source_counts: dict[str, int] = {"cache": 0, "yfinance": 0}
    live_attempted = 0

    for ticker in tickers:
        path = _cache_path(ticker, cache_dir)

        if _cache_is_fresh(path, start):
            try:
                df = pd.read_parquet(path)
                df.index = pd.to_datetime(df.index)
                # Trim to the requested window. _cache_is_fresh only rejects a
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
                logger.warning("Cache read failed for %s: %s — re-fetching", ticker, exc)

        live_attempted += 1
        source, df = _fetch_single(ticker, start, end)
        if df is None:
            logger.warning("Skipping %s — fetch failed", ticker)
            continue

        source_counts[source] += 1

        try:
            tmp_path = path + ".tmp"
            try:
                df.to_parquet(tmp_path)
                os.replace(tmp_path, path)
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
        "Price sources: yfinance %d/%d, cache %d/%d",
        source_counts["yfinance"], total, source_counts["cache"], total,
    )
    if live_attempted > 0 and source_counts["yfinance"] == 0:
        logger.warning(
            "yfinance: 0/%d succeeded — source may be down", live_attempted,
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


def load_universe(config_path: str = "config/universe.yaml") -> dict:
    """Load universe.yaml and return the parsed dict."""
    with open(config_path, "r") as fh:
        return yaml.safe_load(fh)
