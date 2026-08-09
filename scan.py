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
from src.backup import backup_to_storage
from src.pipeline import SIGNAL_COLUMNS, build_theme_signals_rows

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


def _load_config(path: str) -> dict:
    with open(path, "r") as fh:
        return yaml.safe_load(fh)


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

    for region in scored_df_for_db["region"].drop_duplicates():
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


def _compute_finbert_sentiment(wide_df, themes_cfg, args):
    """Compute FinBERT news-sentiment z-scores from GDELT headlines.

    Returns (sentiment_score, sentiment_signals_df, finbert_health). Non-fatal:
    on any failure, or when --no-finbert is set, sentiment_score stays all-NaN
    and the signals frame stays empty. finbert_health carries the three health
    metrics (None when not computed)."""
    sentiment_score = pd.Series(float("nan"), index=wide_df.index, dtype=float)
    sentiment_signals_df = pd.DataFrame(
        columns=["region", "gics_sector", "signal_name", "value"]
    )
    finbert_health = {
        "finbert_scored": None, "finbert_total": None, "gdelt_articles": None,
    }

    if args.no_finbert:
        logger.info("FinBERT sentiment skipped (--no-finbert)")
        return sentiment_score, sentiment_signals_df, finbert_health

    logger.info("Fetching GDELT headlines + FinBERT scoring …")
    try:
        from src.data.news_sentiment import (
            fetch_theme_headlines, score_headlines,
            zscore_polarity, build_theme_news_signal_rows,
        )
        _headlines = fetch_theme_headlines(themes_cfg)
        _total_articles = sum(len(h) for h in _headlines.values())
        logger.info("GDELT: %d headlines across %d themes",
                    _total_articles, len(_headlines))

        _finbert_scores = score_headlines(_headlines)
        _finbert_z = zscore_polarity(_finbert_scores)

        _live_finbert = sum(1 for v in _finbert_z.values() if not math.isnan(v))
        logger.info("FinBERT: %d/%d themes scored", _live_finbert, len(_finbert_z))

        finbert_health["finbert_scored"] = _live_finbert
        finbert_health["finbert_total"] = len(_finbert_z)
        finbert_health["gdelt_articles"] = _total_articles

        # Themes are keyed directly by name — there is no parent/sub-sector
        # rollup to resolve, which is what apply_polarity_to_keys existed for.
        if _live_finbert >= 2:
            for key in wide_df.index:
                z = _finbert_z.get(key.split("|", 1)[1])
                if z is not None and not math.isnan(z):
                    sentiment_score[key] = z
            logger.info("sentiment_score overwritten with FinBERT polarity z-scores")

        sentiment_signals_df = pd.concat(
            [sentiment_signals_df,
             pd.DataFrame(build_theme_news_signal_rows(_finbert_scores))],
            ignore_index=True,
        )
    except Exception as exc:
        logger.warning("FinBERT sentiment failed (%s) — sentiment stays NULL for this scan", exc)

    return sentiment_score, sentiment_signals_df, finbert_health


def _persist_scan(conn, run_at, long_signals_df, scored_with_deltas,
                  sentiment_signals_df, finbert_health, *, t0, price_stats,
                  prices_total, prices_failed, sectors_expected, sectors_produced):
    """Assemble the health dict and persist the scan. Returns scan_id."""
    from src.state import save_scan
    _health = {
        "duration_s": round(time.time() - t0, 1),
        "prices_total": prices_total,
        "prices_cache": price_stats.get("cache", 0),
        "prices_stooq": price_stats.get("stooq", 0),
        "prices_yfinance": price_stats.get("yfinance", 0),
        "prices_failed": prices_failed,
        "sectors_expected": sectors_expected,
        "sectors_produced": sectors_produced,
        "finbert_scored": finbert_health["finbert_scored"],
        "finbert_total": finbert_health["finbert_total"],
        "gdelt_articles": finbert_health["gdelt_articles"],
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
    return scan_id


def _run_dashboard_build():
    """Run dashboard/build.py as a subprocess. Non-fatal."""
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


def _send_threshold_alerts(conn, scan_date):
    """Send post-scan threshold alerts. Non-fatal."""
    try:
        from src.alerts import send_alerts
        send_alerts(conn, scan_date)
    except Exception as exc:
        logger.warning("Alert step failed: %s", exc)


def run(args: argparse.Namespace) -> int:
    """Execute the full scan pipeline. Returns exit code."""
    _t0 = time.time()
    from src.data.prices import align_cohort_asof, fetch_prices
    from src.scoring import score_all, zscore_cross_section
    from src.state import init_db, load_last_scan, compute_deltas
    from src.report import build_ranked_table, build_movers, write_report
    from src.cohorts import cohorts

    # 1. Load config
    logger.info("Loading config …")
    scan_cfg = _load_config("config/universe.yaml")
    themes_cfg = _load_config("config/themes.yaml")
    weights_cfg = _load_config("config/weights.yaml")
    signal_params = weights_cfg.get("signal_params", {})
    cohort_list = cohorts(themes_cfg)
    if not cohort_list:
        logger.error("No cohorts configured in config/themes.yaml — aborting.")
        return 1

    # 2. Determine date range
    lookback_days = scan_cfg.get("price_lookback_days", 252)
    end_date = date.today()
    # Add a buffer to ensure we have enough trading days
    start_date = end_date - timedelta(days=int(lookback_days * 1.5))
    scan_date = end_date.strftime("%Y-%m-%d")
    logger.info("Date range: %s \u2192 %s (lookback_days=%d)", start_date, end_date, lookback_days)

    # 3. Collect all tickers and fetch prices
    benchmark = themes_cfg.get("benchmark") or "ACWI"
    themes: dict = themes_cfg.get("themes", {})
    unique_tickers = sorted({
        *(cfg["ticker"] if isinstance(cfg, dict) else cfg for cfg in themes.values()),
        benchmark, "SPY",   # SPY is the documented benchmark fallback
    })
    logger.info("Fetching prices for %d tickers \u2026", len(unique_tickers))
    _price_stats: dict[str, object] = {}
    prices = fetch_prices(
        tickers=unique_tickers,
        start=str(start_date),
        end=str(end_date),
        stats_out=_price_stats,
    )
    logger.info("Received price data for %d / %d tickers", len(prices), len(unique_tickers))
    # prices_failed counts fetch failures only; the alignment step below can
    # also drop tickers, and that is a different condition.
    _prices_fetched = len(prices)

    # 3b. Pin every ticker to one as-of date before scoring. The composite
    #     z-scores each signal across the theme cohort, so a cohort whose
    #     members end on different dates ranks Tuesday's reading against
    #     Wednesday's. Nothing upstream guarantees a common end date — cache
    #     freshness is decided per ticker.
    prices, as_of = align_cohort_asof(prices, stats_out=_price_stats)
    if as_of is None:
        logger.error("No usable price data after as-of alignment — aborting.")
        return 1
    logger.info("Scoring as-of %s (%d tickers)", as_of.date(), len(prices))

    # 4. Compute per-theme signals + coverage guard
    logger.info("Computing signals \u2026")
    rows = build_theme_signals_rows(themes_cfg, prices, signal_params=signal_params)
    if not rows:
        logger.error("No signal rows produced \u2014 all themes failed. Aborting.")
        return 1
    expected_sectors = len(themes)
    coverage = len(rows) / expected_sectors if expected_sectors else 0
    if coverage < 0.8:
        logger.error(
            "Partial scan: only %d/%d themes (%.0f%%) produced signals \u2014 aborting.",
            len(rows), expected_sectors, coverage * 100,
        )
        return 1
    logger.info("Signals computed for %d themes", len(rows))

    # 5. Build wide DataFrame for scoring.
    #    breadth_above_50dma stays in SIGNAL_COLUMNS but is always NaN: it needs
    #    a constituent list, which themes structurally do not have. This is the
    #    state themes have always been in \u2014 the column is kept rather than dropped
    #    so SIGNAL_COLUMNS, weights.yaml and stored history stay stable.
    wide_df = pd.DataFrame(rows).set_index("sector_key")[SIGNAL_COLUMNS]

    # 6. FinBERT news sentiment (non-fatal)
    sentiment_score, sentiment_signals_df, _finbert_health = _compute_finbert_sentiment(
        wide_df, themes_cfg, args,
    )

    # 7. Score. One cohort today, so one cross-sectional pool \u2014 but this stays a
    #    per-cohort loop because composites are z-scores *within* a cohort and
    #    must never be computed across them if a second cohort is added.
    logger.info("Scoring \u2026")
    scored_parts, z_parts = [], []
    for cohort in cohort_list:
        mask = wide_df.index.str.startswith(f"{cohort.region}|")
        cohort_df = wide_df[mask]
        if cohort_df.empty:
            continue
        cohort_sentiment = sentiment_score[mask] if sentiment_score is not None else None
        scored_parts.append(score_all(
            cohort_df,
            weights_path="config/weights.yaml",
            sentiment_score=cohort_sentiment,
            blend_sentiment=False,
        ))
        z_parts.append(zscore_cross_section(cohort_df))
    if not scored_parts:
        logger.error("No cohort produced scores \u2014 aborting.")
        return 1
    scored = pd.concat(scored_parts)
    z_df = pd.concat(z_parts)
    logger.info("Scoring complete. %d items ranked.", len(scored))

    # 9. Connect DB + pre-run backup
    logger.info("Connecting to Supabase …")
    conn = init_db()
    try:
        if not args.no_backup:
            try:
                name = backup_to_storage(conn)
                logger.info("Pre-run DB backup uploaded to Storage (%s)", name)
            except Exception as exc:  # non-fatal: a backup failure must not fail the scan
                logger.warning("Pre-run backup failed (%s) — continuing", exc)

        # 10. Load prior scan + compute deltas
        prior_scan = load_last_scan(conn)
        if prior_scan is not None:
            logger.info("Prior scan found (%d sectors) — computing deltas …", len(prior_scan))
        else:
            logger.info("No prior scan found — this is the first run.")
        scored_df_for_db = _build_scored_df_for_db(scored)
        scored_with_deltas = compute_deltas(scored_df_for_db, prior_scan)
        long_signals_df = _build_long_signals_df(rows, z_wide_df=z_df)

        if args.dry_run:
            logger.info("DRY RUN — skipping DB write and report generation.")
        else:
            # 12. Persist scan
            logger.info("Saving scan to DB …")
            run_at = datetime.now(timezone.utc)
            scan_id = _persist_scan(
                conn, run_at, long_signals_df, scored_with_deltas,
                sentiment_signals_df, _finbert_health, t0=_t0,
                price_stats=_price_stats, prices_total=len(unique_tickers),
                prices_failed=len(unique_tickers) - _prices_fetched,
                sectors_expected=expected_sectors, sectors_produced=len(rows),
            )

            # 13. Write report (non-fatal)
            try:
                logger.info("Writing report …")
                ranked_table = build_ranked_table(scored_with_deltas, cohort_list)
                movers = build_movers(scored_with_deltas)
                report_path = write_report(
                    scan_date=scan_date,
                    ranked_table=ranked_table,
                    movers=movers,
                )
                logger.info("Report written to %s", report_path)
            except Exception as exc:
                logger.warning("Report generation failed (%s) — scan data saved", exc)

        # 15. Dashboard build (non-fatal)
        if not args.dry_run and not args.no_dashboard:
            _run_dashboard_build()

        # 16. Print summary
        _print_summary(scan_date, scored_with_deltas)

        # 17. Threshold alerts (non-fatal)
        if not args.dry_run and not args.no_alerts:
            _send_threshold_alerts(conn, scan_date)
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
