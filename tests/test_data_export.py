"""Unit tests for the docs/data.json payload builder."""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard.data_export import build_config_block, build_data_export
from src.cohorts import cohorts
from src.horizons import default_horizon, horizons


def _theme_rows():
    return [
        {"theme": "Semiconductors", "rank": 1, "delta_rank": 0.0,
         "trajectory_state": "flat", "setup": None, "_raw_composite": 0.61},
    ]


def _theme_scores():
    return pd.DataFrame([
        {"gics_sector": "Semiconductors", "composite": 0.61, "level_score": 0.6,
         "change_score": 0.5, "data_score": 0.55, "sentiment_score": float("nan")},
    ])


def test_top_level_shape_and_metadata():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=412, scan_date="2026-07-23 06:00 UTC",
                          lagged=False, generated_at="2026-07-23T06:00:00Z")
    # 2 since the config block (2026-09-19). Additive: every v1 key is unchanged.
    assert d["schema_version"] == 2
    assert d["generated_at"] == "2026-07-23T06:00:00Z"
    assert d["scan_id"] == 412
    assert d["scan_date"] == "2026-07-23 06:00 UTC"
    assert d["lagged"] is False
    assert len(d["themes"]) == 1


def test_raw_numeric_types_and_nan_to_null():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=True, generated_at="t")
    semis = next(t for t in d["themes"] if t["theme"] == "Semiconductors")
    assert semis["rank"] == 1 and isinstance(semis["rank"], int)
    assert semis["composite"] == 0.61 and isinstance(semis["composite"], float)
    assert semis["level"] == 0.6
    assert semis["delta_rank"] == 0.0 and isinstance(semis["delta_rank"], float)
    assert semis["sentiment"] is None            # NaN -> None
    assert semis["trajectory"] == "flat"
    assert semis["setup"] is None
    assert d["lagged"] is True


def test_themes_render_even_with_empty_scores_df():
    d = build_data_export(_theme_rows(), pd.DataFrame(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    t = d["themes"][0]
    assert t["theme"] == "Semiconductors"
    assert t["rank"] == 1
    assert t["level"] is None                    # empty df -> null raw scores
    assert t["setup"] is None


def test_output_is_json_serializable():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=False, generated_at="t")
    text = json.dumps(d)                         # must not raise
    assert '"schema_version": 2' in text
    assert "NaN" not in text


def test_scan_id_none_is_null():
    d = build_data_export([], pd.DataFrame(),
                          scan_id=None, scan_date="x", lagged=False, generated_at="t")
    assert d["scan_id"] is None
    assert d["themes"] == []


_CFG = {
    "benchmark": "ACWI",
    "themes": {
        "Semiconductors": {"ticker": "SOXX"},
        "Shipping": {"ticker": "BOAT", "unbuyable": True},
    },
    "ucits": {
        "Semiconductors": [{
            "ticker": "VVSM", "name": "VanEck Semiconductor UCITS ETF",
            "isin": "IE00BMC38736", "ter": "0.35%", "issuer": "VanEck",
            "match": "close", "url": "https://example.test/vvsm",
        }],
    },
}


def _config():
    return build_config_block(_CFG, cohorts(_CFG), horizons(), default_horizon(), "2026-09-19")


def test_config_carries_every_configured_horizon_verbatim():
    """The app must never hardcode top_n / buffer_frac: it reads them from
    here, so a preset retune in weights.yaml reaches it without a release."""
    c = _config()
    assert [h["key"] for h in c["horizons"]] == [h.key for h in horizons()]
    for got, h in zip(c["horizons"], horizons()):
        assert (got["label"], got["rebalance"], got["top_n"], got["buffer_frac"]) == \
               (h.label, h.rebalance, h.top_n, h.buffer_frac)
        assert got["review_dates"] and all(len(d) == 10 for d in got["review_dates"])
    assert c["default_horizon"] == default_horizon().key


def test_config_names_the_cohort_regions():
    """v_recent_scores has no region filter; the app filters with this list,
    exactly as the web table filters with window.COHORTS."""
    assert _config()["cohorts"] == ["THEME"]


def test_universe_has_ticker_unbuyable_and_ucits():
    u = {e["theme"]: e for e in _config()["universe"]}
    assert set(u) == {"Semiconductors", "Shipping"}
    assert u["Semiconductors"]["ticker"] == "SOXX"
    assert u["Semiconductors"]["unbuyable"] is False
    assert u["Semiconductors"]["ucits"][0]["isin"] == "IE00BMC38736"
    assert u["Shipping"]["unbuyable"] is True
    assert u["Shipping"]["ucits"] == []
    assert all(e["region"] == "THEME" for e in u.values())


def test_config_leaks_nothing_derived_from_a_scan():
    """data.json is public and gated. The config block must stay config: the
    moment it carries a rank or a badge, it bypasses the gate for everyone."""
    text = json.dumps(_config())
    for forbidden in ('"rank"', '"composite"', '"setup"', '"level"', '"change"',
                      '"scan_id"', '"exit_rank_today"', '"in_buy_band"'):
        assert forbidden not in text, forbidden


def test_config_is_attached_when_given():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=True, generated_at="t", config=_config())
    assert d["schema_version"] == 2
    assert d["config"]["cohorts"] == ["THEME"]
    assert json.loads(json.dumps(d))["config"]["default_horizon"] == default_horizon().key


def test_config_is_omitted_when_not_given():
    d = build_data_export(_theme_rows(), _theme_scores(), scan_id=1, scan_date="x",
                          lagged=True, generated_at="t")
    assert "config" not in d
