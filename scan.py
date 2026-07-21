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
import math
import os
import subprocess
import sys
import time

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
        "--no-alerts",
        action="store_true",
        help="Skip threshold alert notifications after scan.",
    )
    parser.add_argument(
        "--no-finbert",
        action="store_true",
        help="Skip FinBERT news sentiment step (avoids ~400MB model download).",
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

    for region in ("US", "EU"):
        region_df = scored_df_for_db[scored_df_for_db["region"] == region]
        if region_df.empty:
            continue
        region_sorted = region_df.sort_values("rank", ascending=True)
        print(f"\n  Top 5 {region} by composite score:")
        for _, row in region_sorted.head(5).iterrows():
            rank = int(row["rank"])
            sector = row["gics_sector"]
            composite = row["composite"]
            print(f"    #{rank:2d}  {sector:<28}  composite={composite:.3f}")

    emerging = scored_df_for_db[scored_df_for_db.get("emerging_flag", False) == True] if "emerging_flag" in scored_df_for_db.columns else pd.DataFrame()
    if not emerging.empty:
        print(f"\n  Emerging sectors (improving rank & composite vs prior scan):")
        for _, row in emerging.iterrows():
            print(f"    🌱  {row['gics_sector']} ({row['region']})")
    else:
        print("\n  No emerging sectors detected (or first scan).")

    print(f"\n{'='*60}\n")


def run(args: argparse.Namespace) -> int:
    """Execute the full scan pipeline. Returns exit code."""
    _t0 = time.time()
    from src.data.prices import fetch_prices, load_universe
    from src.scoring import score_all, zscore_cross_section
    from src.sector_map import load_parent_map
    from src.state import init_db, save_scan, load_last_scan, compute_deltas, save_theme_scan
    from src.report import build_ranked_table, build_movers, build_swedish_overlay, write_report

    # ------------------------------------------------------------------
    # Step 2: Load config
    # ------------------------------------------------------------------
    logger.info("Loading universe config …")
    universe = load_universe("config/universe.yaml")
    _parent_map = load_parent_map()
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
    eu_sectors: dict[str, str] = universe.get("eu_sectors", {})
    us_benchmark: str = universe["us_benchmark"]
    eu_benchmark: str = universe["eu_benchmark"]

    all_tickers: list[str] = (
        list(us_sectors.values())
        + list(eu_sectors.values())
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
    _price_stats: dict[str, int] = {}
    prices = fetch_prices(
        tickers=unique_tickers,
        start=str(start_date),
        end=str(end_date),
        stats_out=_price_stats,
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
    # Step 8: Sentiment — FinBERT only (Google Trends retired 2026-07-19)
    # ------------------------------------------------------------------
    sentiment_score = pd.Series(float("nan"), index=wide_df.index, dtype=float)
    sentiment_signals_df = pd.DataFrame(
        columns=["region", "gics_sector", "signal_name", "value"]
    )
    _health_finbert_scored: int | None = None
    _health_finbert_total: int | None = None
    _health_gdelt_articles: int | None = None
    # ------------------------------------------------------------------
    # Step 8d: FinBERT news sentiment (signed polarity from GDELT headlines)
    # ------------------------------------------------------------------
    if not args.no_finbert:
        logger.info("Fetching GDELT headlines + FinBERT scoring …")
        try:
            from src.data.news_sentiment import (
                fetch_news_headlines, score_headlines, zscore_polarity,
                apply_polarity_to_keys, build_news_signal_rows,
            )
            _headlines = fetch_news_headlines()
            _total_articles = sum(len(h) for h in _headlines.values())
            logger.info("GDELT: %d headlines across %d sectors",
                        _total_articles, len(_headlines))

            _finbert_scores = score_headlines(_headlines)
            _finbert_z = zscore_polarity(_finbert_scores)

            _live_finbert = sum(1 for v in _finbert_z.values() if not math.isnan(v))
            logger.info("FinBERT: %d/%d sectors scored", _live_finbert, len(_finbert_z))

            _health_finbert_scored = _live_finbert
            _health_finbert_total = len(_finbert_z)
            _health_gdelt_articles = _total_articles

            if _live_finbert >= 2:
                sentiment_score = apply_polarity_to_keys(
                    sentiment_score, _finbert_z, _parent_map,
                )
                logger.info("sentiment_score overwritten with FinBERT polarity z-scores")

            _finbert_signal_rows = build_news_signal_rows(
                _finbert_scores, universe, _parent_map,
            )
            sentiment_signals_df = pd.concat(
                [sentiment_signals_df, pd.DataFrame(_finbert_signal_rows)],
                ignore_index=True,
            )
        except Exception as exc:
            logger.warning("FinBERT sentiment failed (%s) — sentiment stays NULL for this scan", exc)
    else:
        logger.info("FinBERT sentiment skipped (--no-finbert)")

    logger.info("Scoring sectors …")
    # Per-region cohort scoring: US and EU each ranked within their own pool.
    scored_parts = []
    for region_prefix in ("US", "EU"):
        mask = wide_df.index.str.startswith(f"{region_prefix}|")
        region_df = wide_df[mask]
        if region_df.empty:
            continue
        region_sentiment = sentiment_score[mask] if sentiment_score is not None else None
        region_scored = score_all(
            region_df,
            weights_path="config/weights.yaml",
            sentiment_score=region_sentiment,
            blend_sentiment=False,
        )
        scored_parts.append(region_scored)
    scored = pd.concat(scored_parts)
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

        # Build long-format signals for DB, with per-region z-scores
        z_parts = []
        for region_prefix in ("US", "EU"):
            mask = wide_df.index.str.startswith(f"{region_prefix}|")
            if mask.any():
                z_parts.append(zscore_cross_section(wide_df[mask]))
        z_df = pd.concat(z_parts)
        long_signals_df = _build_long_signals_df(rows, z_wide_df=z_df)

        # ------------------------------------------------------------------
        # Step 12: Persist (unless --dry-run)
        # ------------------------------------------------------------------
        if args.dry_run:
            logger.info("DRY RUN — skipping DB write and report generation.")
        else:
            logger.info("Saving scan to DB …")
            run_at = datetime.now(timezone.utc)
            _health = {
                "duration_s": round(time.time() - _t0, 1),
                "prices_total": len(unique_tickers),
                "prices_cache": _price_stats.get("cache", 0),
                "prices_stooq": _price_stats.get("stooq", 0),
                "prices_yfinance": _price_stats.get("yfinance", 0),
                "prices_failed": len(unique_tickers) - len(prices),
                "sectors_expected": expected_sectors,
                "sectors_produced": len(rows),
                "finbert_scored": _health_finbert_scored,
                "finbert_total": _health_finbert_total,
                "gdelt_articles": _health_gdelt_articles,
            }
            scan_id = save_scan(
                conn=conn,
                run_at=run_at,
                region_sector_signals=long_signals_df,
                scores_df=scored_with_deltas,
                sentiment_signals_df=sentiment_signals_df,
                health=_health,
            )
            logger.info("Saved scan_id=%d", scan_id)

            # Themes track (Phase 1): score a thematic-ETF universe vs a global
            # benchmark and persist to theme tables under the same scan_id. Fully
            # non-fatal — a themes failure must not affect the sector scan.
            try:
                with open("config/themes.yaml", "r") as _fh:
                    _themes_cfg = yaml.safe_load(_fh) or {}
                _theme_tickers = sorted({
                    *(cfg["ticker"] if isinstance(cfg, dict) else cfg
                      for cfg in _themes_cfg.get("themes", {}).values()),
                    _themes_cfg.get("benchmark", "ACWI"), "SPY",
                })
                _theme_prices = fetch_prices(
                    tickers=_theme_tickers, start=str(start_date), end=str(end_date),
                )
                _theme_rows = build_theme_signals_rows(_themes_cfg, _theme_prices, signal_params=signal_params)
                if _theme_rows:
                    _theme_wide = pd.DataFrame(_theme_rows).set_index("sector_key")[SIGNAL_COLUMNS]

                    # Themes are price-pillars only (Google Trends retired
                    # 2026-07-19; FinBERT covers sectors, not themes).
                    _theme_scored = score_all(
                        _theme_wide, sentiment_score=None, blend_sentiment=False,
                    )
                    _theme_scores_df = _build_scored_df_for_db(_theme_scored)
                    _theme_z = zscore_cross_section(_theme_wide)
                    _theme_signals_df = _build_long_signals_df(_theme_rows, z_wide_df=_theme_z)
                    save_theme_scan(
                        conn, scan_id, _theme_scores_df, _theme_signals_df,
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

        # ------------------------------------------------------------------
        # Step 15: Threshold alerts (non-fatal)
        # ------------------------------------------------------------------
        if not args.dry_run and not args.no_alerts:
            try:
                from src.alerts import send_alerts
                send_alerts(conn, scan_date)
            except Exception as exc:
                logger.warning("Alert step failed: %s", exc)
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
