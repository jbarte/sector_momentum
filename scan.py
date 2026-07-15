#!/usr/bin/env python3
"""
scan.py — Sector Momentum Scanner entrypoint.

Run this to execute a full scan:
    python scan.py

Options:
    --dry-run       Fetch prices and compute signals, but don't write to DB or disk.
    --no-dashboard  Skip dashboard build step after scan.

"""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yaml

# ---------------------------------------------------------------------------
# Logging setup (must be before any src imports that use logging)
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("scan")

from src.data.prices import fetch_prices
from src.data.constituents import fetch_sp500_constituents
from src.signals.breadth import compute_constituent_breadth
from src.backup import backup_to_storage
from src.pipeline import SIGNAL_COLUMNS, build_signals_rows, build_theme_signals_rows

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sector Momentum Scanner — runs the full scoring pipeline."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute signals but do not write to DB or disk.",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Skip dashboard build step after scan.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip writing the database backup after the scan.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the durable Trends day-cache (always live-fetch, no save).",
    )
    return parser.parse_args()


def _load_config(universe_path: str = "config/universe.yaml") -> dict:
    with open(universe_path, "r") as fh:
        return yaml.safe_load(fh)


def _inject_constituent_breadth(rows: list[dict], start: str, end: str) -> None:
    """Mutate rows in place: set breadth_above_50dma to true constituent breadth
    for US sectors (NaN if unavailable/under-covered), and NaN for EU sectors.
    Fully non-fatal — any failure leaves all breadth values NaN."""
    nan = float("nan")
    breadth: dict[str, float] = {}
    try:
        constituents = fetch_sp500_constituents()
        if constituents:
            all_tickers = sorted({t for ts in constituents.values() for t in ts})
            logger.info("Fetching prices for %d S&P 500 constituents …", len(all_tickers))
            cons_prices = fetch_prices(tickers=all_tickers, start=start, end=end)
            breadth = compute_constituent_breadth(cons_prices, constituents)
        else:
            logger.warning("Constituent breadth unavailable — leaving NaN")
    except Exception as exc:
        logger.warning("Constituent breadth step failed (%s) — leaving NaN", exc)

    for row in rows:
        if row.get("region") == "US":
            row["breadth_above_50dma"] = breadth.get(f"US|{row['gics_sector']}", nan)
        else:
            row["breadth_above_50dma"] = nan


def _build_long_signals_df(rows: list[dict], z_wide_df=None) -> pd.DataFrame:
    """
    Convert wide-format rows to long format expected by save_scan().

    Columns: region, gics_sector, signal_name, raw_value, z_value
    Pass z_wide_df (index=sector_key, columns=signal names) to populate z_value.
    """
    if not rows:
        return pd.DataFrame(columns=["region", "gics_sector", "signal_name", "raw_value", "z_value"])

    wide = pd.DataFrame(rows)
    long = wide.melt(
        id_vars=["region", "gics_sector", "sector_key"],
        value_vars=SIGNAL_COLUMNS,
        var_name="signal_name",
        value_name="raw_value",
    )
    long["z_value"] = float("nan")

    if z_wide_df is not None:
        z_long = z_wide_df.reset_index().melt(
            id_vars=["sector_key"],
            value_vars=[c for c in SIGNAL_COLUMNS if c in z_wide_df.columns],
            var_name="signal_name",
            value_name="z_value_new",
        )
        long = long.merge(z_long[["sector_key", "signal_name", "z_value_new"]],
                          on=["sector_key", "signal_name"], how="left")
        long["z_value"] = long["z_value_new"].where(long["z_value_new"].notna(), long["z_value"])
        long = long.drop(columns=["z_value_new"])

    long = long.drop(columns=["sector_key"])
    return long.reset_index(drop=True)



def _build_scored_df_for_db(scored: pd.DataFrame) -> pd.DataFrame:
    """
    scored has index = "region|gics_sector". Split index back into columns
    and return a DataFrame ready for save_scan() scores table.
    """
    df = scored.copy().reset_index()
    df.rename(columns={"index": "sector_key"}, inplace=True)
    parts = df["sector_key"].str.split("|", n=1, expand=True)
    df.insert(0, "region", parts[0])
    df.insert(1, "gics_sector", parts[1])
    df = df.drop(columns=["sector_key"])
    return df


def _print_summary(scan_date: str, scored_df_for_db: pd.DataFrame) -> None:
    """Print a human-readable summary to stdout."""
    n_sectors = len(scored_df_for_db)
    print(f"\n{'='*60}")
    print(f"  Sector Momentum Scan — {scan_date}")
    print(f"  Sectors scanned: {n_sectors}")
    print(f"{'='*60}")

    if n_sectors == 0:
        print("  No sectors were scored.")
        return

    sorted_df = scored_df_for_db.sort_values("rank", ascending=True)

    print("\n  Top 5 by composite score:")
    for _, row in sorted_df.head(5).iterrows():
        rank = int(row["rank"])
        sector = row["gics_sector"]
        region = row["region"]
        composite = row["composite"]
        print(f"    #{rank:2d}  {sector:<28}  ({region})  composite={composite:.3f}")

    emerging = scored_df_for_db[scored_df_for_db.get("emerging_flag", False) == True] if "emerging_flag" in scored_df_for_db.columns else pd.DataFrame()
    if not emerging.empty:
        print(f"\n  Emerging sectors (improving rank & composite vs prior scan):")
        for _, row in emerging.iterrows():
            print(f"    🌱  {row['gics_sector']} ({row['region']})")
    else:
        print("\n  No emerging sectors detected (or first scan).")

    print(f"\n{'='*60}\n")


def _fetch_theme_sentiment(
    themes_cfg: dict,
    theme_index,
    anchor: str,
    region_geos: dict,
    cache: dict | None,
) -> tuple[pd.Series, pd.DataFrame]:
    """Fetch Google Trends sentiment for the theme cohort.

    Mirrors the sector sentiment path (scan Step 8/8b/8c) but keyed by the
    ``THEME|<name>`` convention and pulled worldwide. Returns:
      - a sentiment ``pd.Series`` (z-scored slope) reindexed to ``theme_index``
        for feeding ``score_all`` (populates ``theme_scores.sentiment_score``);
      - a long DataFrame (``theme, signal_name, value, text_value``) of the
        info-only derived signals + comparative attention + rising queries, for
        ``save_theme_scan(..., sentiment_signals_df=...)``.

    Comparative interest and rising queries are each best-effort — a failure in
    either logs a warning and yields no rows for that signal, but the headline
    sentiment + derived signals still return.
    """
    import json as _json

    from src.data.trends_symbols import (
        build_theme_symbol_map, load_theme_entities, fetch_symbol_trends,
        score_symbol_sentiment, derived_signals, fetch_comparative_interest,
        fetch_rising_queries, _MIN_LIVE_THEMES,
    )

    _sym_map = build_theme_symbol_map(themes_cfg)
    # Theme entities are keyed by search term, not ticker, so the sector entity
    # map is irrelevant here — use only the theme overrides.
    _theme_entities = load_theme_entities(themes_cfg)

    _trends = fetch_symbol_trends(
        _sym_map, anchor=anchor, entities=_theme_entities, region_geos=region_geos,
        cache=cache, timeframe="today 12-m", window=52,
    )
    _sentiment = score_symbol_sentiment(_trends, min_live=_MIN_LIVE_THEMES).reindex(theme_index)
    logger.info("Theme sentiment: %d/%d themes have live Trends data",
                len(_trends), len(theme_index))

    def _theme_of(key: str) -> str:
        return key.partition("|")[2]

    rows: list[dict] = []
    for _key, _series in _trends.items():
        _theme = _theme_of(_key)
        for _name, _val in derived_signals(_series).items():
            rows.append({"theme": _theme, "signal_name": _name,
                         "value": _val, "text_value": None})

    try:
        _attn = fetch_comparative_interest(
            _sym_map, sleep_s=20.0, max_retries=3,
            entities=_theme_entities, region_geos=region_geos, cache=cache,
        )
        for _key, _val in (_attn or {}).items():
            rows.append({"theme": _theme_of(_key), "signal_name": "attention_level",
                         "value": _val, "text_value": None})
        logger.info("Theme comparative interest: %d themes scored", len(_attn or {}))
    except Exception as exc:
        logger.warning("Theme comparative interest failed (%s) — continuing", exc)

    try:
        _rising = fetch_rising_queries(
            _sym_map, sleep_s=20.0, max_retries=3,
            entities=_theme_entities, region_geos=region_geos, cache=cache,
        )
        for _key, _queries in (_rising or {}).items():
            rows.append({"theme": _theme_of(_key), "signal_name": "rising_queries",
                         "value": None, "text_value": _json.dumps(_queries)})
        logger.info("Theme rising queries: %d themes with results", len(_rising or {}))
    except Exception as exc:
        logger.warning("Theme rising queries failed (%s) — continuing", exc)

    return _sentiment, pd.DataFrame(rows)


def run(args: argparse.Namespace) -> int:
    """Execute the full scan pipeline. Returns exit code."""
    from src.data.prices import fetch_prices, load_universe
    from src.scoring import score_all, zscore_cross_section
    from src.state import init_db, save_scan, load_last_scan, compute_deltas, save_theme_scan
    from src.report import build_ranked_table, build_movers, build_swedish_overlay, write_report

    # ------------------------------------------------------------------
    # Step 2: Load config
    # ------------------------------------------------------------------
    logger.info("Loading universe config …")
    universe = load_universe("config/universe.yaml")
    weights_cfg = _load_config("config/weights.yaml")
    signal_params = weights_cfg.get("signal_params", {})

    # ------------------------------------------------------------------
    # Step 3: Determine date range
    # ------------------------------------------------------------------
    lookback_days = universe.get("price_lookback_days", 252)
    end_date = date.today()
    # Add a buffer to ensure we have enough trading days
    start_date = end_date - timedelta(days=int(lookback_days * 1.5))
    scan_date = end_date.strftime("%Y-%m-%d")

    logger.info("Date range: %s → %s (lookback_days=%d)", start_date, end_date, lookback_days)

    # ------------------------------------------------------------------
    # Step 4: Collect all tickers and fetch prices
    # ------------------------------------------------------------------
    us_sectors: dict[str, str] = universe.get("us_sectors", {})
    eu_sectors: dict[str, str | list[str]] = universe.get("eu_sectors", {})
    us_benchmark: str = universe["us_benchmark"]
    eu_benchmark: str = universe["eu_benchmark"]

    def _flatten(values) -> list[str]:
        out: list[str] = []
        for v in values:
            out.extend(v if isinstance(v, list) else [v])
        return out

    all_tickers: list[str] = (
        _flatten(us_sectors.values())
        + _flatten(eu_sectors.values())
        + [us_benchmark, eu_benchmark]
    )
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_tickers: list[str] = []
    for t in all_tickers:
        if t not in seen:
            seen.add(t)
            unique_tickers.append(t)

    logger.info("Fetching prices for %d tickers …", len(unique_tickers))
    prices = fetch_prices(
        tickers=unique_tickers,
        start=str(start_date),
        end=str(end_date),
    )
    logger.info("Received price data for %d / %d tickers", len(prices), len(unique_tickers))

    # ------------------------------------------------------------------
    # Step 5 + 6: Compute per-sector signals → wide rows
    # ------------------------------------------------------------------
    logger.info("Computing signals …")
    rows = build_signals_rows(universe, prices, signal_params=signal_params)

    if not rows:
        logger.error("No signal rows produced — all sectors failed. Aborting.")
        return 1

    expected_sectors = len(universe.get("us_sectors", {})) + len(universe.get("eu_sectors", {}))
    coverage = len(rows) / expected_sectors if expected_sectors else 0
    if coverage < 0.8:
        logger.error(
            "Partial scan: only %d/%d sectors (%.0f%%) produced signals — aborting.",
            len(rows), expected_sectors, coverage * 100,
        )
        return 1

    logger.info("Signals computed for %d sectors", len(rows))

    # ------------------------------------------------------------------
    # Step 6b: Inject true constituent breadth (non-fatal)
    # ------------------------------------------------------------------
    logger.info("Computing true constituent breadth …")
    _inject_constituent_breadth(rows, start=str(start_date), end=str(end_date))

    # ------------------------------------------------------------------
    # Step 7: Build wide DataFrame for scoring
    # ------------------------------------------------------------------
    wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]

    # ------------------------------------------------------------------
    # Step 8: Sentiment (thin Google Trends) + Score
    # ------------------------------------------------------------------
    logger.info("Fetching symbol-based Google Trends sentiment …")
    from src.data.trends_symbols import (
        build_symbol_map, fetch_symbol_trends, score_symbol_sentiment,
        load_entities, derived_signals, load_geo_config, _MIN_LIVE_SECTORS,
    )
    with open("config/sector_etfs.yaml", "r") as _fh:
        _sector_etfs = yaml.safe_load(_fh) or {}
    try:
        with open("config/trends_blocklist.yaml", "r") as _fh:
            _blocklist = set(yaml.safe_load(_fh) or [])
    except FileNotFoundError:
        _blocklist = set()
    _symbol_map = build_symbol_map(universe, _sector_etfs, blocklist=_blocklist)
    _entities = load_entities("config/trends_entities.yaml")
    _resolved = sum(1 for syms in _symbol_map.values() for s in syms if s in _entities)
    _total = sum(len(syms) for syms in _symbol_map.values())
    logger.info("Trends entities: %d/%d ticker-slots resolved to a mid (rest fall back to strings)",
                _resolved, _total)
    _anchor, _region_geos = load_geo_config("config/trends_geo.yaml")
    logger.info("Trends geos: %s (anchor=%s)",
                ", ".join(f"{r}→{'/'.join(g)}" for r, g in _region_geos.items()), _anchor)
    from src.data import trends_cache
    _use_cache = not args.no_cache
    _cache_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _cache = trends_cache.load_cache(_cache_date) if _use_cache else None
    _trends_by_key = fetch_symbol_trends(
        _symbol_map, anchor=_anchor, entities=_entities, region_geos=_region_geos,
        cache=_cache, timeframe="today 12-m", window=52,
    )
    sentiment_score = score_symbol_sentiment(_trends_by_key)
    sentiment_score = sentiment_score.reindex(wide_df.index)
    _live = len(_trends_by_key)
    _total = len(wide_df.index)
    logger.info(
        "Symbol sentiment: %d/%d sectors have live Trends data (guard threshold: %d)",
        _live, _total, _MIN_LIVE_SECTORS,
    )
    if _live < _MIN_LIVE_SECTORS:
        logger.warning(
            "Symbol sentiment: live count %d < threshold %d — all scores NULLed for this scan",
            _live, _MIN_LIVE_SECTORS,
        )

    # Derived Trends signals (info-only; not blended into the composite). One long
    # row per sector-key × signal, keyed for the sentiment_signals table.
    _sent_signal_rows = []
    for _key, _series in _trends_by_key.items():
        _region, _, _sector = _key.partition("|")
        for _name, _val in derived_signals(_series).items():
            _sent_signal_rows.append(
                {"region": _region, "gics_sector": _sector,
                 "signal_name": _name, "value": _val}
            )
    sentiment_signals_df = pd.DataFrame(_sent_signal_rows)

    # ------------------------------------------------------------------
    # Step 8b: Comparative cross-sector interest (attention_level)
    # ------------------------------------------------------------------
    logger.info("Fetching comparative cross-sector interest …")
    from src.data.trends_symbols import fetch_comparative_interest
    try:
        _attention = fetch_comparative_interest(
            _symbol_map, sleep_s=20.0, max_retries=3,
            entities=_entities, region_geos=_region_geos, cache=_cache,
        )
        if _attention:
            _attn_rows = []
            for _key, _val in _attention.items():
                _region, _, _sector = _key.partition("|")
                _attn_rows.append({
                    "region": _region, "gics_sector": _sector,
                    "signal_name": "attention_level", "value": _val,
                })
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_attn_rows)],
                ignore_index=True,
            )
            logger.info("Comparative interest: %d sectors scored", len(_attention))
        else:
            logger.info("Comparative interest: no results (skipped or failed)")
    except Exception as exc:
        logger.warning("Comparative interest failed (%s) — continuing without", exc)

    # ------------------------------------------------------------------
    # Step 8c: Rising / breakout queries per sector
    # ------------------------------------------------------------------
    logger.info("Fetching rising queries …")
    from src.data.trends_symbols import fetch_rising_queries
    try:
        _rising = fetch_rising_queries(
            _symbol_map, sleep_s=20.0, max_retries=3,
            entities=_entities, region_geos=_region_geos, cache=_cache,
        )
        if _rising:
            import json as _json
            _rising_rows = []
            for _key, _queries in _rising.items():
                _region, _, _sector = _key.partition("|")
                _rising_rows.append({
                    "region": _region, "gics_sector": _sector,
                    "signal_name": "rising_queries", "value": None,
                    "text_value": _json.dumps(_queries),
                })
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_rising_rows)],
                ignore_index=True,
            )
            logger.info("Rising queries: %d sectors with results", len(_rising))
        else:
            logger.info("Rising queries: no results (skipped or failed)")
    except Exception as exc:
        logger.warning("Rising queries failed (%s) — continuing without", exc)

    if _use_cache:
        trends_cache.save_cache(_cache_date, _cache)

    logger.info("Scoring sectors …")
    # Canonical composite stays pure-data; sentiment is stored but not blended.
    scored = score_all(
        wide_df,
        weights_path="config/weights.yaml",
        sentiment_score=sentiment_score,
        blend_sentiment=False,
    )
    logger.info("Scoring complete. %d sectors ranked.", len(scored))

    # ------------------------------------------------------------------
    # Step 9–11: DB + deltas
    # ------------------------------------------------------------------
    logger.info("Connecting to Supabase …")
    conn = init_db()

    try:
        if not args.no_backup:
            try:
                name = backup_to_storage(conn)
                logger.info("Pre-run DB backup uploaded to Storage (%s)", name)
            except Exception as exc:  # non-fatal: a backup failure must not fail the scan
                logger.warning("Pre-run backup failed (%s) — continuing", exc)

        prior_scan = load_last_scan(conn)
        if prior_scan is not None:
            logger.info("Prior scan found (%d sectors) — computing deltas …", len(prior_scan))
        else:
            logger.info("No prior scan found — this is the first run.")

        # Build scored_df_for_db (with region + gics_sector columns)
        scored_df_for_db = _build_scored_df_for_db(scored)

        # Compute deltas (adds delta_composite, delta_rank, emerging_flag columns)
        scored_with_deltas = compute_deltas(scored_df_for_db, prior_scan)

        # Build long-format signals for DB, with cross-sectional z-scores
        z_df = zscore_cross_section(wide_df)
        long_signals_df = _build_long_signals_df(rows, z_wide_df=z_df)

        # ------------------------------------------------------------------
        # Step 12: Persist (unless --dry-run)
        # ------------------------------------------------------------------
        if args.dry_run:
            logger.info("DRY RUN — skipping DB write and report generation.")
        else:
            logger.info("Saving scan to DB …")
            run_at = datetime.now(timezone.utc)
            scan_id = save_scan(
                conn=conn,
                run_at=run_at,
                region_sector_signals=long_signals_df,
                scores_df=scored_with_deltas,
                sentiment_signals_df=sentiment_signals_df,
            )
            logger.info("Saved scan_id=%d", scan_id)

            # Themes track (Phase 1): score a thematic-ETF universe vs a global
            # benchmark and persist to theme tables under the same scan_id. Fully
            # non-fatal — a themes failure must not affect the sector scan.
            try:
                with open("config/themes.yaml", "r") as _fh:
                    _themes_cfg = yaml.safe_load(_fh) or {}
                _theme_tickers = sorted({
                    *_themes_cfg.get("themes", {}).values(),
                    _themes_cfg.get("benchmark", "ACWI"), "SPY",
                })
                _theme_prices = fetch_prices(
                    tickers=_theme_tickers, start=str(start_date), end=str(end_date),
                )
                _theme_rows = build_theme_signals_rows(_themes_cfg, _theme_prices, signal_params=signal_params)
                if _theme_rows:
                    _theme_wide = pd.DataFrame(_theme_rows).set_index("sector_key")[SIGNAL_COLUMNS]

                    # Theme sentiment (Google Trends). Info-only like sector
                    # sentiment: stored but never blended (blend_sentiment=False).
                    # Isolated in its own try so a Trends failure still lets the
                    # price-based theme scores persist below.
                    _theme_sentiment = None
                    _theme_sent_df = None
                    try:
                        _theme_sentiment, _theme_sent_df = _fetch_theme_sentiment(
                            _themes_cfg, _theme_wide.index,
                            anchor=_anchor, region_geos=_region_geos, cache=_cache,
                        )
                    except Exception as exc:  # non-fatal: keep price-based scores
                        logger.warning("Theme sentiment failed (%s) — themes scored without it", exc)
                    finally:
                        # Persist whatever theme batches were fetched, even on a
                        # mid-fetch failure, so a same-day re-run reuses them
                        # (the 429 mitigation) instead of re-hitting Google.
                        if _use_cache:
                            trends_cache.save_cache(_cache_date, _cache)

                    _theme_scored = score_all(
                        _theme_wide, sentiment_score=_theme_sentiment, blend_sentiment=False,
                    )
                    _theme_scores_df = _build_scored_df_for_db(_theme_scored)
                    _theme_z = zscore_cross_section(_theme_wide)
                    _theme_signals_df = _build_long_signals_df(_theme_rows, z_wide_df=_theme_z)
                    save_theme_scan(
                        conn, scan_id, _theme_scores_df, _theme_signals_df,
                        sentiment_signals_df=_theme_sent_df,
                    )
                    logger.info("Themes: scored and saved %d themes", len(_theme_rows))
                else:
                    logger.warning("Themes: no themes with price data — skipping")
            except FileNotFoundError:
                logger.info("Themes: config/themes.yaml not found — skipping themes track")
            except Exception as exc:  # non-fatal
                logger.warning("Themes pass failed (%s) — sector scan unaffected", exc)

            try:
                logger.info("Writing report …")
                ranked_table = build_ranked_table(scored_with_deltas)
                movers = build_movers(scored_with_deltas)
                swedish = build_swedish_overlay(scored_with_deltas)
                report_path = write_report(
                    scan_date=scan_date,
                    ranked_table=ranked_table,
                    movers=movers,
                    swedish=swedish,
                )
                logger.info("Report written to %s", report_path)
            except Exception as exc:
                logger.warning("Report generation failed (%s) — scan data saved", exc)

        # ------------------------------------------------------------------
        # Step 13: Dashboard (unless --dry-run or --no-dashboard)
        # ------------------------------------------------------------------
        if not args.dry_run and not args.no_dashboard:
            try:
                dashboard_script = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "dashboard", "build.py"
                )
                if os.path.exists(dashboard_script):
                    logger.info("Running dashboard build …")
                    result = subprocess.run(
                        [sys.executable, dashboard_script],
                        capture_output=False,
                    )
                    if result.returncode != 0:
                        logger.warning("Dashboard build exited with code %d", result.returncode)
                else:
                    logger.info("dashboard/build.py not found — skipping dashboard build (expected in Phase 2).")
            except Exception as exc:
                logger.warning("Dashboard build failed (%s) — scan data saved", exc)

        # ------------------------------------------------------------------
        # Step 14: Print summary
        # ------------------------------------------------------------------
        _print_summary(scan_date, scored_with_deltas)
    finally:
        conn.close()

    return 0


def main() -> None:
    args = _parse_args()
    try:
        exit_code = run(args)
    except Exception as exc:
        logger.error("Fatal error in scan pipeline: %s", exc, exc_info=True)
        sys.exit(1)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
