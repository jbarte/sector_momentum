"""
Static dashboard builder.

Reads Supabase/Postgres -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--out docs]
"""

from __future__ import annotations

import argparse
import json
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


# Re-export public API so existing imports keep working
from dashboard.rows import (                      # noqa: E402, F401
    _safe_float,
    _format_raw_value,
    _compute_rank_trajectories,
    _compute_setup,
    _build_leaderboard_rows,
    _build_theme_leaderboard_rows,
)
from dashboard.breakdown import (                 # noqa: E402, F401
    _build_breakdown_html,
    _build_instruments_html,
    _SIGNAL_META,
    _SIGNAL_DESCRIPTIONS,
)
from dashboard.figures import (                   # noqa: E402, F401
    _build_rrg_figure,
    _build_sentiment_scatter_figure,
    _build_drilldown_data,
    _build_movers_figure,
    _build_history_figure,
    _build_backtest_figures,
    _build_rotation_figures,
    _build_backtest_context,
    _build_rescore_data,
    _build_scan_history_data,
    _WARM_PALETTE,
    _SCORE_SIGNAL_COLORS,
)
from dashboard.sentiment import (                 # noqa: E402, F401
    _build_sentiment_signal_rows,
)
from dashboard.reports import (                   # noqa: E402, F401
    build_scan_index,
    _generate_scan_reports,
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
        get_theme_rrg_history,
    )

    conn = init_db()
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)
    sentiment_signals_df = get_sentiment_signals_for_latest_scan(conn)
    theme_signals_df = get_theme_signals_for_latest_scan(conn)
    theme_history_df = get_theme_scan_history(conn)
    rrg_df = get_rrg_history(conn, n_scans=6)
    theme_rrg_df = get_theme_rrg_history(conn, n_scans=6)

    logger.info("Building scan index + per-scan reports …")
    all_scores_df = get_scan_history(conn, n_scans=None)
    scan_index = build_scan_index(all_scores_df)
    active_scan_id = scan_index[0]["scan_id"] if scan_index else None
    _generate_scan_reports(all_scores_df, out_dir / "reports")

    logger.info("Building scan history data …")
    scan_history_data = _build_scan_history_data(all_scores_df)

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

    # Themes leaderboard rows (Phase 1 — read-only)
    _themes_path = project_root / "config/themes.yaml"
    _themes_cfg = _yaml.safe_load(_themes_path.read_text()) if _themes_path.exists() else {}
    theme_trajectories = _compute_rank_trajectories(theme_history_df)
    theme_rows = _build_theme_leaderboard_rows(
        theme_history_df, theme_signals_df, _themes_cfg, _weights, theme_trajectories,
    )

    # Theme figures — reuse the same builders used for sectors
    logger.info("Building theme figures …")
    theme_rrg_json = _build_rrg_figure(theme_rrg_df)
    theme_drilldown_data, theme_keys, _ = _build_drilldown_data(theme_history_df)
    theme_movers_json = _build_movers_figure(theme_history_df)
    theme_history_json = _build_history_figure(theme_history_df)

    # 3. Build figures
    logger.info("Building RRG figure …")
    rrg_json = _build_rrg_figure(rrg_df)

    logger.info("Building drill-down data …")
    sector_signal_data, sector_keys, signals_list = _build_drilldown_data(history_df)

    logger.info("Building movers figure …")
    movers_json = _build_movers_figure(history_df)

    logger.info("Building history figure …")
    history_json = _build_history_figure(history_df)

    logger.info("Building sentiment scatter …")
    sentiment_scatter_json = _build_sentiment_scatter_figure(history_df)
    sentiment_signal_rows = _build_sentiment_signal_rows(sentiment_signals_df)

    logger.info("Building rescore data …")
    rescore_data_json = json.dumps(_build_rescore_data(history_df))

    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(history_df)
    trajectories = _compute_rank_trajectories(history_df)

    # Enrich rows with breakdown HTML (keyed by sector_id for JS toggle)
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

    logger.info("Building backtest context …")
    backtest_ctx = _build_backtest_context(str(project_root / "backtests"))

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
    plotly_bundle_rel = "assets/plotly.min.js"

    # 5. Render template
    template_path = Path(__file__).parent / "templates" / "index.html.j2"
    out_path = out_dir / "index.html"

    _render(
        template_path=template_path,
        out_path=out_path,
        context=dict(
            scan_date=scan_date,
            scan_index=scan_index,
            active_scan_id=active_scan_id,
            leaderboard_rows=leaderboard_rows,
            rrg_data_json=rrg_json,
            drilldown_data=json.dumps(sector_signal_data),
            sector_keys=sector_keys,
            movers_json=movers_json,
            history_json=history_json,
            rescore_data_json=rescore_data_json,
            scan_history_json=json.dumps(scan_history_data),
            signals_list=signals_list,
            plotly_bundle=plotly_bundle_rel,
            backtest_json=backtest_ctx["backtest_json"],
            backtest_metrics=backtest_ctx["backtest_metrics"],
            has_backtest=backtest_ctx["has_backtest"],
            rotation_json=backtest_ctx["rotation_json"],
            has_rotations=backtest_ctx["has_rotations"],
        ),
    )

    _render(
        template_path=Path(__file__).parent / "templates" / "sentiment.html.j2",
        out_path=out_dir / "sentiment.html",
        context=dict(
            scan_date=scan_date,
            active_scan_id=active_scan_id,
            sentiment_scatter_json=sentiment_scatter_json,
            sentiment_signal_rows=sentiment_signal_rows,
            plotly_bundle=plotly_bundle_rel,
        ),
    )

    _render(
        template_path=Path(__file__).parent / "templates" / "themes.html.j2",
        out_path=out_dir / "themes.html",
        context=dict(
            scan_date=scan_date,
            active_scan_id=active_scan_id,
            theme_rows=theme_rows,
            plotly_bundle=plotly_bundle_rel,
            theme_rrg_json=theme_rrg_json,
            theme_drilldown_data=json.dumps(theme_drilldown_data),
            theme_keys=theme_keys,
            theme_movers_json=theme_movers_json,
            theme_history_json=theme_history_json,
        ),
    )

    # 6. Disable Jekyll on GitHub Pages (the published artifact is static).
    _disable_jekyll(out_dir)

    print(f"Dashboard built: {out_path}")


if __name__ == "__main__":
    main()
