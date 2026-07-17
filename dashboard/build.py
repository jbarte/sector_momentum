"""
Static dashboard builder.

Reads Supabase/Postgres -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--out docs]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("dashboard.build")

# Ensure project root is on sys.path so absolute imports work
# whether invoked as `python dashboard/build.py` or `python -m dashboard.build`
_project_root = str(Path(__file__).resolve().parent.parent)
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


# Re-export public API so existing imports keep working (alphabetized)
from dashboard.badges import (                      # noqa: E402, F401
    build_badge_scorecard,
    build_page_context as _badges_ctx,
)
from dashboard.breakdown import (                   # noqa: E402, F401
    _build_breakdown_html,
    _build_instruments_html,
    _SIGNAL_DESCRIPTIONS,
    _SIGNAL_META,
)
from dashboard.feed import (                         # noqa: E402, F401
    build_feed_entries,
    feed_updated_timestamp,
)
from dashboard.figures import (                      # noqa: E402, F401
    _SCORE_SIGNAL_COLORS,
    _WARM_PALETTE,
    _build_backtest_context,
    _build_backtest_figures,
    _build_drilldown_data,
    _build_history_figure,
    _build_movers_figure,
    _build_rescore_data,
    _build_rotation_figures,
    _build_rrg_figure,
    _build_scan_history_data,
    _build_sentiment_scatter_figure,
    _build_theme_backtest_context,
    build_sectors_context as _figures_sectors_ctx,
    build_themes_context as _figures_themes_ctx,
)
from dashboard.macro import (                        # noqa: E402, F401
    build_macro_context,
    build_page_context as _macro_ctx,
    fetch_macro_data,
)
from dashboard.reports import (                      # noqa: E402, F401
    build_scan_index,
    _generate_scan_reports,
)
from dashboard.rows import (                         # noqa: E402, F401
    _build_leaderboard_rows,
    _build_theme_leaderboard_rows,
    _compute_rank_trajectories,
    _compute_setup,
    _format_raw_value,
    _safe_float,
)
from dashboard.sentiment import (                    # noqa: E402, F401
    _build_sentiment_signal_rows,
    build_page_context as _sentiment_ctx,
)


# ---------------------------------------------------------------------------
# Plotly bundle management
# ---------------------------------------------------------------------------

PLOTLY_CDN = "https://cdn.plot.ly/plotly-basic-2.27.0.min.js"
_ASSETS_DIR = Path(__file__).parent / "assets"


def _ensure_plotly_bundle() -> Path:
    """Download plotly.min.js once to dashboard/assets/ if not present."""
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bundle = _ASSETS_DIR / "plotly.min.js"
    if not bundle.exists():
        import requests

        logger.info("Downloading Plotly bundle from %s …", PLOTLY_CDN)
        try:
            resp = requests.get(PLOTLY_CDN, timeout=30)
            resp.raise_for_status()
            bundle.write_bytes(resp.content)
            logger.info("Downloaded plotly bundle (%d KB)", len(resp.content) // 1024)
        except Exception as exc:
            logger.error(
                "Failed to download Plotly bundle from %s: %s\n"
                "Fix: manually download plotly.min.js from https://cdn.plot.ly/ "
                "and place it at dashboard/assets/plotly.min.js",
                PLOTLY_CDN, exc
            )
            sys.exit(1)
    return bundle


# ---------------------------------------------------------------------------
# Template rendering
# ---------------------------------------------------------------------------


def _disable_jekyll(out_dir: Path) -> Path:
    """Write an empty ``.nojekyll`` so GitHub Pages serves the site as-is."""
    out_dir.mkdir(parents=True, exist_ok=True)
    nojekyll = out_dir / ".nojekyll"
    nojekyll.write_text("", encoding="utf-8")
    return nojekyll


def _render(
    template_path: Path,
    out_path: Path,
    context: dict,
) -> None:
    from jinja2 import Environment, FileSystemLoader, select_autoescape

    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )

    def js_json_filter(value):
        """Escape </ sequences in JSON for safe embedding in <script> tags."""
        if isinstance(value, str):
            return value.replace("</", r"<\/")
        return value
    env.filters["js_json"] = js_json_filter

    template = env.get_template(template_path.name)
    html = template.render(**context)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    logger.info("Dashboard written to %s (%d KB)", out_path, len(html) // 1024)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build static dashboard from Supabase")
    parser.add_argument("--out", default="docs", metavar="DIR",
                        help="Output directory for docs/index.html (default: docs)")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    # Resolve paths relative to project root (parent of dashboard/)
    project_root = Path(__file__).parent.parent
    out_dir = project_root / args.out

    # 1. Ensure plotly bundle
    _ensure_plotly_bundle()

    # 2. Open DB + load history
    sys.path.insert(0, str(project_root))
    from src.state import (
        init_db, get_scan_history, get_signals_for_latest_scan, get_rrg_history,
        get_sentiment_signals_for_latest_scan,
        get_theme_signals_for_latest_scan, get_theme_scan_history,
        get_theme_rrg_history, get_theme_sentiment_signals_for_latest_scan,
    )

    conn = init_db()
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)
    sentiment_signals_df = get_sentiment_signals_for_latest_scan(conn)
    theme_signals_df = get_theme_signals_for_latest_scan(conn)
    theme_sentiment_signals_df = get_theme_sentiment_signals_for_latest_scan(conn)
    theme_history_df = get_theme_scan_history(conn)
    rrg_df = get_rrg_history(conn, n_scans=6)
    theme_rrg_df = get_theme_rrg_history(conn, n_scans=6)

    logger.info("Building scan index + per-scan reports …")
    all_scores_df = get_scan_history(conn, n_scans=None)
    scan_index = build_scan_index(all_scores_df)
    active_scan_id = scan_index[0]["scan_id"] if scan_index else None
    _generate_scan_reports(all_scores_df, out_dir / "reports")

    conn.close()

    if history_df.empty:
        print("No scans in database yet. Run scan.py first.")
        sys.exit(0)

    logger.info("Loaded %d rows from %d scans", len(history_df), history_df["scan_id"].nunique())

    # Load config for breakdown panel
    import yaml as _yaml
    with open(project_root / "config/universe.yaml") as _fh:
        _universe = _yaml.safe_load(_fh)
    with open(project_root / "config/weights.yaml") as _fh:
        _weights = _yaml.safe_load(_fh)
    _etfs_path = project_root / "config/sector_etfs.yaml"
    _sector_etfs = _yaml.safe_load(_etfs_path.read_text()) if _etfs_path.exists() else {}

    _themes_path = project_root / "config/themes.yaml"
    _themes_cfg = _yaml.safe_load(_themes_path.read_text()) if _themes_path.exists() else {}

    # ------------------------------------------------------------------
    # Shared dependencies for module context builders
    # ------------------------------------------------------------------
    shared = {
        "project_root": project_root,
        "all_scores_df": all_scores_df,
        "history_df": history_df,
        "theme_history_df": theme_history_df,
        "rrg_df": rrg_df,
        "theme_rrg_df": theme_rrg_df,
        "universe": _universe,
        "sentiment_signals_df": sentiment_signals_df,
        "theme_sentiment_signals_df": theme_sentiment_signals_df,
    }

    # ------------------------------------------------------------------
    # Page-specific context that stays in build.py (complex, stable)
    # ------------------------------------------------------------------

    # Themes leaderboard rows
    theme_trajectories = _compute_rank_trajectories(theme_history_df)
    theme_rows = _build_theme_leaderboard_rows(
        theme_history_df, theme_signals_df, _themes_cfg, _weights, theme_trajectories,
    )

    # Leaderboard rows + enrichment
    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(history_df)
    trajectories = _compute_rank_trajectories(history_df)

    latest_scan_id = history_df["scan_id"].max()
    latest_scores  = history_df[history_df["scan_id"] == latest_scan_id]
    for row in leaderboard_rows:
        key = f"{row['region']}|{row['sector']}"
        row["key"]       = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        row["trajectory_label"] = traj["label"]
        row["trajectory_state"] = traj["state"]
        _compute_setup(row)
        mask = (
            (latest_scores["region"]      == row["region"]) &
            (latest_scores["gics_sector"] == row["sector"])
        )
        score_slice = latest_scores[mask]
        score_row_dict = {} if score_slice.empty else score_slice.iloc[0].to_dict()
        if not signals_df.empty:
            sig_mask = (
                (signals_df["region"]      == row["region"]) &
                (signals_df["gics_sector"] == row["sector"])
            )
            row_signals = signals_df[sig_mask].to_dict("records")
        else:
            row_signals = []
        row["breakdown_html"] = _build_breakdown_html(
            key, score_row_dict, row_signals, _universe, _weights, _sector_etfs
        )

    # 4. Copy plotly.min.js into docs/assets/ so GitHub Pages can serve it
    import shutil
    docs_assets = out_dir / "assets"
    docs_assets.mkdir(exist_ok=True)
    plotly_src = _ASSETS_DIR / "plotly.min.js"
    if plotly_src.exists():
        shutil.copy2(plotly_src, docs_assets / "plotly.min.js")
    rescore_src = _ASSETS_DIR / "rescore.js"
    if rescore_src.exists():
        shutil.copy2(rescore_src, docs_assets / "rescore.js")
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
    scan_digest_src = _ASSETS_DIR / "scan-digest.js"
    if scan_digest_src.exists():
        shutil.copy2(scan_digest_src, docs_assets / "scan-digest.js")
    plotly_bundle_rel = "assets/plotly.min.js"

    # ------------------------------------------------------------------
    # 5. Assemble + render pages via module context builders
    # ------------------------------------------------------------------
    template_dir = Path(__file__).parent / "templates"

    # Compute cross-page contexts once (macro makes a network call)
    logger.info("Fetching macro regime data …")
    macro_page_ctx = _macro_ctx(shared)

    # --- Sectors page ---
    logger.info("Building sectors page context …")
    sectors_ctx = {
        "scan_date": scan_date,
        "scan_index": scan_index,
        "active_scan_id": active_scan_id,
        "leaderboard_rows": leaderboard_rows,
        "plotly_bundle": plotly_bundle_rel,
    }
    sectors_ctx.update(_figures_sectors_ctx(shared))
    sectors_ctx.update(_badges_ctx(shared))
    sectors_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "index.html.j2",
        out_path=out_dir / "index.html",
        context=sectors_ctx,
    )

    # --- Sentiment page ---
    logger.info("Building sentiment page context …")
    sentiment_ctx = {
        "scan_date": scan_date,
        "active_scan_id": active_scan_id,
        "plotly_bundle": plotly_bundle_rel,
    }
    sentiment_ctx.update(_sentiment_ctx(shared))
    sentiment_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "sentiment.html.j2",
        out_path=out_dir / "sentiment.html",
        context=sentiment_ctx,
    )

    # --- Themes page ---
    logger.info("Building themes page context …")
    themes_ctx = {
        "scan_date": scan_date,
        "active_scan_id": active_scan_id,
        "theme_rows": theme_rows,
        "plotly_bundle": plotly_bundle_rel,
    }
    themes_ctx.update(_figures_themes_ctx(shared))
    themes_ctx.update(macro_page_ctx)

    _render(
        template_path=template_dir / "themes.html.j2",
        out_path=out_dir / "themes.html",
        context=themes_ctx,
    )

    # 6. Atom feed
    logger.info("Building Atom feed …")
    feed_entries = build_feed_entries(all_scores_df, n_entries=30)
    dashboard_url = "https://jbarte.github.io/sector_momentum/"
    feed_url = dashboard_url + "feed.xml"

    from jinja2 import Environment, FileSystemLoader
    feed_env = Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        keep_trailing_newline=True,
    )
    feed_template = feed_env.get_template("feed.xml.j2")
    feed_xml = feed_template.render(
        entries=feed_entries,
        feed_updated=feed_updated_timestamp(feed_entries),
        dashboard_url=dashboard_url,
        feed_url=feed_url,
    )
    feed_path = out_dir / "feed.xml"
    feed_path.write_text(feed_xml, encoding="utf-8")
    logger.info("Feed written to %s (%d entries)", feed_path, len(feed_entries))

    # 7. Disable Jekyll on GitHub Pages (the published artifact is static).
    _disable_jekyll(out_dir)

    print(f"Dashboard built: {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
