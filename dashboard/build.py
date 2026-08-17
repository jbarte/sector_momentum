"""
Static dashboard builder.

Reads Supabase/Postgres -> renders docs/index.html via Jinja2 + embedded Plotly JSON.
Run after scan.py:
    python dashboard/build.py [--out docs]
"""

from __future__ import annotations

import argparse
import logging
import os
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
from src.universe import is_unbuyable, unbuyable_names   # noqa: E402
from dashboard.figures import (                      # noqa: E402, F401
    _SCORE_SIGNAL_COLORS,
    _WARM_PALETTE,
    _build_backtest_context,
    _build_backtest_figures,
    _build_drilldown_data,
    _build_history_figure,
    _build_movers_figure,
    _build_rescore_data,
    _build_rrg_figure,
    _build_scan_history_data,
    _build_sentiment_scatter_figure,
    build_chart_dark_map,
    build_cohort_chart_context as _figures_cohort_charts_ctx,
    build_sectors_context as _figures_sectors_ctx,
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
    _compute_rank_trajectories,
    _compute_setup,
    _format_raw_value,
    _safe_float,
)
from dashboard.sentiment import (                    # noqa: E402, F401
    _build_sentiment_signal_rows,
    build_page_context as _sentiment_ctx,
)
from dashboard.health import (                         # noqa: E402, F401
    build_health_context,
)
from dashboard.correlation import (                    # noqa: E402, F401
    build_correlation_context,
)
from dashboard.gating import apply_leaderboard_lag  # noqa: E402
from dashboard.validation import (                    # noqa: E402, F401
    build_validation_context as _validation_ctx,
)


# Sentiment is ALPHA: the FinBERT/GDELT output has not been validated well
# enough to let it move the board. While this is False:
#
#   - the "Ranking" cogwheel — the control that blends sentiment into the
#     composite client-side — is NOT RENDERED, rather than hidden. The wiring in
#     index.html.j2 early-returns when the control is absent, so a reader who
#     once enabled it cannot have it silently re-applied from localStorage on the
#     next visit. CSS hiding alone would leave that path live.
#   - the leaderboard's Sentiment column is hidden by CSS rather than removed.
#     Column indices are positional (sortTable(6), data-col, nth-child), so
#     dropping the cell would shift every reference after it.
#
# The STORED composite has never included sentiment — scan.py passes
# blend_sentiment=False — so this governs presentation and the client-side blend
# only. See BACKLOG for what restoring it involves.
SENTIMENT_RANKING_ENABLED = False

# ---------------------------------------------------------------------------
# Plotly bundle management
# ---------------------------------------------------------------------------

PLOTLY_CDN = "https://cdn.plot.ly/plotly-cartesian-2.27.0.min.js"
# Pinned supabase-js v2 UMD build, vendored like Plotly (downloaded once,
# gitignored). Bump deliberately; the dashboard has no JS build toolchain.
SUPABASE_JS_CDN = (
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.110.7"
    "/dist/umd/supabase.min.js"
)
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


def _ensure_supabase_bundle() -> Path | None:
    """Download supabase.min.js once to dashboard/assets/ if not present.

    Fail-open (returns None) unlike the Plotly bundle: a missing auth bundle
    degrades to a dashboard without login, not a broken build.
    """
    _ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    bundle = _ASSETS_DIR / "supabase.min.js"
    if not bundle.exists():
        import requests

        logger.info("Downloading supabase-js bundle from %s …", SUPABASE_JS_CDN)
        try:
            resp = requests.get(SUPABASE_JS_CDN, timeout=30)
            resp.raise_for_status()
            bundle.write_bytes(resp.content)
        except Exception as exc:
            logger.warning(
                "Failed to download supabase-js bundle: %s — auth disabled", exc
            )
            return None
    return bundle


def _auth_ctx() -> dict:
    """Browser auth config: project URL + publishable key, or disabled.

    Only these two values may reach the browser; the publishable key is
    public by design (protection is RLS/grants, not key secrecy).
    """
    import json as _json

    key = os.environ.get("SUPABASE_PUBLISHABLE_KEY", "").strip()
    if not key:
        return {"auth": None, "auth_config_json": ""}
    try:
        from src.storage_backup import _base_url
        url = _base_url()
    except Exception as exc:
        logger.warning("Auth disabled: cannot resolve Supabase URL (%s)", exc)
        return {"auth": None, "auth_config_json": ""}
    cfg = {"url": url, "key": key}
    return {"auth": cfg, "auth_config_json": _json.dumps(cfg)}


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
        get_latest_health,
        get_signals_for_scan,
        get_sentiment_signals_for_scan,
    )

    conn = init_db()
    # These all take src.state.DEFAULT_REGIONS, which is THEME only. Do NOT
    # pass regions=None here: the retired US/EU sector rows were deliberately
    # kept in the database, so "every cohort" would pull 41 scans of dead
    # sector history into the leaderboard, the charts and the movers list.
    history_df = get_scan_history(conn, n_scans=20)
    signals_df = get_signals_for_latest_scan(conn)
    sentiment_signals_df = get_sentiment_signals_for_latest_scan(conn)
    rrg_df = get_rrg_history(conn, n_scans=6)

    all_scores_df = get_scan_history(conn, n_scans=None)
    health_row = get_latest_health(conn)

    if history_df.empty:
        print("No scans in database yet. Run scan.py first.")
        conn.close()
        sys.exit(0)

    logger.info("Loaded %d rows from %d scans", len(history_df), history_df["scan_id"].nunique())

    # ------------------------------------------------------------------
    # Content gating: compute auth + lag early so EVERY downstream
    # artefact (SCAN_HISTORY, charts, scan index, reports, theme rows)
    # is capped at the lagged scan boundary for guests.
    # ------------------------------------------------------------------
    auth_ctx = _auth_ctx()
    if auth_ctx["auth"] and _ensure_supabase_bundle() is None:
        auth_ctx = {"auth": None, "auth_config_json": ""}

    lag_active = bool(auth_ctx["auth"])
    # The ▲ Entry / ▼ Exit badge is the actionable layer, so it is a signed-in
    # feature. Same switch as the lag: when auth is configured, the baked page
    # IS the guest view. With no auth configured there is nobody to sign in, so
    # a local build keeps its badges.
    badges_gated = lag_active
    lb_history_df, lb_scan_id, lag_banner_date = apply_leaderboard_lag(
        history_df, lag_active=lag_active
    )
    if lag_active and lb_scan_id is not None:
        logger.info("Content gating active — baked data capped at scan %s (%s)",
                     lb_scan_id, lag_banner_date)
        all_scores_df = all_scores_df[all_scores_df["scan_id"] <= lb_scan_id].copy()
        history_df = history_df[history_df["scan_id"] <= lb_scan_id].copy()
        rrg_df = rrg_df[rrg_df["scan_id"] <= lb_scan_id].copy()
        signals_df = get_signals_for_scan(conn, lb_scan_id)
        # sentiment_signals_df otherwise reads the true latest scan
        # unconditionally (see get_sentiment_signals_for_latest_scan below) —
        # that leaked the current scan's News sentiment table to guests while
        # the Data <-> Sentiment scatter above it stayed capped at lb_scan_id,
        # so the two surfaces disagreed about what "latest" meant.
        sentiment_signals_df = get_sentiment_signals_for_scan(conn, lb_scan_id)

    # Load config. Themes are needed early — the per-scan reports below are
    # generated per-cohort.
    import yaml as _yaml
    from src.cohorts import cohorts
    with open(project_root / "config/universe.yaml") as _fh:
        _universe = _yaml.safe_load(_fh)

    with open(project_root / "config/weights.yaml") as _fh:
        _weights = _yaml.safe_load(_fh)

    _themes_path = project_root / "config/themes.yaml"
    _themes_cfg = _yaml.safe_load(_themes_path.read_text()) if _themes_path.exists() else {}

    cohort_list = cohorts(_themes_cfg)

    from src.horizons import horizons, default_horizon, round_trip_bps, review_dates
    horizon_list = horizons()
    _default_horizon = default_horizon()
    _round_trip_bps = round_trip_bps()
    # "Now", decided once here rather than inside review_dates (which has no
    # clock of its own — see its docstring) — every preset's calendar must be
    # generated from the same instant, or a build straddling midnight could
    # embed presets that disagree about what day it is.
    from datetime import datetime, timezone
    _review_since = datetime.now(timezone.utc).date().isoformat()

    # The scan index and per-scan reports used to need a sector-only
    # slice of all_scores_df, because that frame was widened to carry THEME
    # alongside US/EU and those surfaces were sector-only. Both halves of that
    # are gone: readers are THEME-scoped by default, and themes are the only
    # cohort, so all_scores_df is already exactly what these want.
    logger.info("Building scan index + per-scan reports …")
    scan_index = build_scan_index(all_scores_df)
    active_scan_id = lb_scan_id if lb_scan_id is not None else (
        scan_index[0]["scan_id"] if scan_index else None
    )
    _generate_scan_reports(all_scores_df, out_dir / "reports", cohort_list)

    # ------------------------------------------------------------------
    # Shared dependencies for module context builders
    # ------------------------------------------------------------------
    shared = {
        "project_root": project_root,
        "all_scores_df": all_scores_df,
        "history_df": history_df,
        "rrg_df": rrg_df,
        "universe": _universe,
        "themes_cfg": _themes_cfg,
        "sentiment_signals_df": sentiment_signals_df,
    }

    # ------------------------------------------------------------------
    # Page-specific context that stays in build.py (complex, stable)
    # ------------------------------------------------------------------

    # Leaderboard rows + enrichment — one path for every cohort (US, EU,
    # THEME). lb_history_df already carries all three cohorts because
    # history_df above was fetched with regions=None.
    logger.info("Building leaderboard …")
    leaderboard_rows, scan_date = _build_leaderboard_rows(lb_history_df)
    trajectories = _compute_rank_trajectories(lb_history_df)

    latest_scan_id = lb_scan_id
    latest_scores  = lb_history_df[lb_history_df["scan_id"] == lb_scan_id]

    for row in leaderboard_rows:
        key = f"{row['region']}|{row['sector']}"
        row["key"]       = key
        row["sector_id"] = key.replace("|", "-").replace(" ", "_")
        traj = trajectories.get(key, {"label": "→", "state": "flat"})
        row["trajectory_label"] = traj["label"]
        row["trajectory_state"] = traj["state"]
        # Gating has to happen here, not in the template: `setup` also reaches
        # the reader through data-setup on the row and through data.json's
        # theme rows, so suppressing only the rendered span would leak it twice.
        # Themes with no route to purchase are scored but never held — they
        # shape the z-scores and stay on the board, but the board must not
        # prompt an entry it knows the reader cannot act on. Same config flag
        # the backtest reads, so the two describe one strategy.
        row["unbuyable"] = is_unbuyable(row["region"], row["sector"], _themes_cfg)
        # Always computed, even when gated: _compute_setup also sets
        # `in_buy_band`, which drives the highlighted rank badge and is NOT a
        # signed-in feature — top_n is already public in the page's HORIZONS,
        # and without this the guest build renders every rank badge unhighlighted
        # while the buy-band cut line is drawn right below the fourth row.
        # `setup` is still withheld from guests, immediately below.
        _compute_setup(row, _default_horizon)
        if badges_gated:
            row["setup"] = None
        elif row["unbuyable"] and row["setup"] == "entry":
            row["setup"] = None
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
            key, score_row_dict, row_signals, _universe, _weights,
            themes_cfg=_themes_cfg,
        )

    # {region, label} pairs for JS consumers that need the human-readable
    # label alongside the region code (COHORT_REGIONS in index.html.j2 only
    # carries the bare region strings — see the comment there for why that
    # shape stays as-is rather than being widened in place).
    import json as _json
    cohorts_json = _json.dumps(
        [{"region": c.region, "label": c.label} for c in cohort_list]
    )
    # data.json's published "themes" array keys entries by "theme" rather
    # than "sector" (see dashboard/data_export.py) — sourced from the same
    # unified leaderboard_rows, filtered to THEME and aliased, not a second
    # build.
    theme_rows = [
        {**row, "theme": row["sector"]}
        for row in leaderboard_rows if row["region"] == "THEME"
    ]

    conn.close()

    # 4. Copy plotly.min.js into docs/assets/ so GitHub Pages can serve it
    import shutil
    docs_assets = out_dir / "assets"
    docs_assets.mkdir(exist_ok=True)
    plotly_src = _ASSETS_DIR / "plotly.min.js"
    if plotly_src.exists():
        shutil.copy2(plotly_src, docs_assets / "plotly.min.js")
    theme_src = _ASSETS_DIR / "theme.js"
    if theme_src.exists():
        shutil.copy2(theme_src, docs_assets / "theme.js")
    rescore_src = _ASSETS_DIR / "rescore.js"
    if rescore_src.exists():
        shutil.copy2(rescore_src, docs_assets / "rescore.js")
    scan_hist_src = _ASSETS_DIR / "scan-history.js"
    if scan_hist_src.exists():
        shutil.copy2(scan_hist_src, docs_assets / "scan-history.js")
    scan_digest_src = _ASSETS_DIR / "scan-digest.js"
    if scan_digest_src.exists():
        shutil.copy2(scan_digest_src, docs_assets / "scan-digest.js")
    if auth_ctx["auth"]:
        supabase_client_src = _ASSETS_DIR / "supabase-client.js"
        if supabase_client_src.exists():
            shutil.copy2(supabase_client_src, docs_assets / "supabase-client.js")
        auth_src = _ASSETS_DIR / "auth.js"
        if auth_src.exists():
            shutil.copy2(auth_src, docs_assets / "auth.js")
        positions_src = _ASSETS_DIR / "positions.js"
        if positions_src.exists():
            shutil.copy2(positions_src, docs_assets / "positions.js")
        alert_prefs_src = _ASSETS_DIR / "alert-prefs.js"
        if alert_prefs_src.exists():
            shutil.copy2(alert_prefs_src, docs_assets / "alert-prefs.js")
        supabase_src = _ASSETS_DIR / "supabase.min.js"
        if supabase_src.exists():
            shutil.copy2(supabase_src, docs_assets / "supabase.min.js")
    plotly_bundle_rel = "assets/plotly.min.js"

    # ------------------------------------------------------------------
    # 5. Assemble + render pages via module context builders
    # ------------------------------------------------------------------
    template_dir = Path(__file__).parent / "templates"

    # Compute cross-page contexts once (macro makes a network call)
    logger.info("Fetching macro regime data …")
    # Hoisted so BOTH pages get them: the leaderboard needs them for its
    # selector, and the SHARED footer's alerts modal states which band alerts
    # actually use. `horizon_default_json` is the config default, and the config
    # default is exactly what alerts evaluate on — `detect_badge_events` calls
    # `_compute_setup(row)` with no horizon, which falls back to
    # `default_horizon()`. If that ever stops being true the modal starts lying,
    # which is why tests/test_alerts_horizon_notice.py pins it.
    _horizons_json = _json.dumps([
        {"key": h.key, "label": h.label, "rebalance": h.rebalance,
         "top_n": h.top_n, "buffer": h.buffer,
         "trades_per_year": h.trades_per_year,
         "median_holding_days": h.median_holding_days,
         # Next ~6 review dates, ISO strings, so the client can say whether
         # today is one without re-deriving the cadence rule in JS — see
         # BACKLOG.md "Badges don't say whether today is an actionable day".
         "review_dates": review_dates(h, since=_review_since)}
        for h in horizon_list
    ])
    _horizon_default_json = _json.dumps({
        "key": _default_horizon.key, "label": _default_horizon.label,
        "top_n": _default_horizon.top_n, "buffer": _default_horizon.buffer,
    })

    macro_page_ctx = _macro_ctx(shared)

    # --- Sectors page ---
    logger.info("Building sectors page context …")
    sectors_ctx = {
        "scan_date": scan_date,
        "scan_index": scan_index,
        "active_scan_id": active_scan_id,
        "leaderboard_rows": leaderboard_rows,
        "cohort_list": cohort_list,
        "horizon_list": horizon_list,
        "sentiment_ranking_enabled": SENTIMENT_RANKING_ENABLED,
        "round_trip_bps": _round_trip_bps,
        "horizons_json": _horizons_json,
        "horizon_default_json": _horizon_default_json,
        "cohorts_json": cohorts_json,
        # The signed-in rebuild (auth.js) sources rows from v_recent_scores,
        # which knows nothing about buyability — without this the marker and the
        # suppressed Enter prompt would both vanish the moment someone signs in.
        # Sourced from config, NOT from leaderboard_rows: those are lagged and
        # may be a smaller universe than the signed-in reader sees. See
        # breakdown.unbuyable_names().
        "unbuyable_json": _json.dumps(unbuyable_names(_themes_cfg)),
        "chart_dark_json": _json.dumps(build_chart_dark_map()),
        "has_any_rows": bool(leaderboard_rows),
        "badges_gated": badges_gated,
        "plotly_bundle": plotly_bundle_rel,
        "lag_banner_date": lag_banner_date,
    }
    sectors_ctx.update(_figures_sectors_ctx(shared))
    sectors_ctx.update(_figures_cohort_charts_ctx(shared))
    sectors_ctx.update(_badges_ctx(shared))
    sectors_ctx.update(_validation_ctx(shared))
    sectors_ctx.update(macro_page_ctx)
    sectors_ctx.update(auth_ctx)
    sectors_ctx.update(build_health_context(health_row))

    logger.info("Building correlation heatmap …")
    sectors_ctx.update(build_correlation_context(shared))

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
        # Both pages include the shared i18n bundle, and the backtest strings in
        # it interpolate the cost. Omit this and the sentiment page fails to
        # render on an Undefined, even though it shows no backtest itself.
        "round_trip_bps": _round_trip_bps,
        "chart_dark_json": _json.dumps(build_chart_dark_map()),
        # Was relying on an undefined Jinja variable being falsy here. Explicit
        # now — the CSS that hides the sentiment column reads it.
        "sentiment_ranking_enabled": SENTIMENT_RANKING_ENABLED,
        "horizons_json": _horizons_json,
        "horizon_default_json": _horizon_default_json,
    }
    sentiment_ctx.update(_sentiment_ctx(shared))
    sentiment_ctx.update(macro_page_ctx)
    sentiment_ctx.update(auth_ctx)

    _render(
        template_path=template_dir / "sentiment.html.j2",
        out_path=out_dir / "sentiment.html",
        context=sentiment_ctx,
    )

    # 6b. Machine-readable data export (fail-open — never breaks the HTML build)
    try:
        import json
        from datetime import datetime, timezone
        from dashboard.data_export import build_data_export

        data_payload = build_data_export(
            theme_rows=theme_rows,
            theme_latest_df=latest_scores,
            scan_id=active_scan_id,
            scan_date=scan_date,
            lagged=bool(auth_ctx["auth"]) and lb_scan_id is not None,
            generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        (out_dir / "data.json").write_text(
            json.dumps(data_payload, indent=2), encoding="utf-8")
        logger.info("Data export written to %s (%d themes)",
                    out_dir / "data.json", len(data_payload["themes"]))
    except Exception as exc:  # fail-open
        logger.warning("data.json export failed (%s) — continuing", exc)

    # 7. Disable Jekyll on GitHub Pages (the published artifact is static).
    _disable_jekyll(out_dir)

    print(f"Dashboard built: {out_dir / 'index.html'}")


if __name__ == "__main__":
    main()
