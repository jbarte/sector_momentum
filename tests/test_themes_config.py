"""Integrity checks on the real config/themes.yaml.

The theme cohort is the product's scoring universe, so a typo here is not a
config bug — it silently changes what gets scored. These assert the invariants
the pipeline and dashboard rely on, against the shipped file rather than a
fixture.
"""
import re
from pathlib import Path

import pytest
import yaml

CONFIG = Path(__file__).resolve().parent.parent / "config" / "themes.yaml"


@pytest.fixture(scope="module")
def cfg():
    with open(CONFIG) as fh:
        return yaml.safe_load(fh)


def test_benchmark_is_present(cfg):
    assert cfg.get("benchmark"), "themes.yaml needs a benchmark for relative strength"


def test_every_theme_has_a_ticker_and_keywords(cfg):
    for name, entry in cfg["themes"].items():
        assert isinstance(entry, dict), f"{name}: expected a mapping"
        assert isinstance(entry.get("ticker"), str) and entry["ticker"], \
            f"{name}: missing or non-string ticker"
        kw = entry.get("gdelt_keywords")
        assert isinstance(kw, list) and kw, f"{name}: missing gdelt_keywords"
        assert all(isinstance(k, str) and k for k in kw), f"{name}: blank keyword"


def test_scoring_tickers_are_unique_across_every_cohort(cfg):
    """dashboard/correlation.py raises on a ticker shared across cohorts, and
    build.py swallows that into a warning — so the heatmap silently vanishes
    from the dashboard. Caught in review when a theme was given XLE, which is
    already the US Energy sector instrument.

    Checks themes against the sector universe too. Once the sector cohort is
    retired those keys disappear from universe.yaml and this degrades to a
    themes-only check, which is then the whole universe anyway.
    """
    universe_path = CONFIG.parent / "universe.yaml"
    universe = yaml.safe_load(universe_path.read_text()) if universe_path.exists() else {}

    seen: dict[str, str] = {}
    for key in ("us_sectors", "eu_sectors"):
        for name, ticker in (universe.get(key) or {}).items():
            seen[ticker] = f"{key}:{name}"

    for name, entry in cfg["themes"].items():
        ticker = entry["ticker"]
        assert ticker not in seen, \
            f"ticker {ticker} used by both {seen[ticker]!r} and theme {name!r}"
        seen[ticker] = f"theme:{name}"


def test_ucits_blocks_reference_real_themes(cfg):
    """A theme rename that misses the ucits block leaves an orphan that renders
    nowhere — silently, since the block is optional per theme."""
    themes = set(cfg["themes"])
    orphans = sorted(set(cfg.get("ucits", {})) - themes)
    assert not orphans, f"ucits entries name no theme: {orphans}"


def test_ucits_entries_are_well_formed(cfg):
    """These are the instruments actually traded, so a malformed ISIN or a
    missing url is a real problem, not cosmetic."""
    isin_re = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$")
    for theme, entries in cfg.get("ucits", {}).items():
        assert isinstance(entries, list) and entries, f"{theme}: empty ucits block"
        for e in entries:
            for field in ("ticker", "name", "isin", "ter", "issuer", "match", "url"):
                assert e.get(field), f"{theme}: ucits entry missing {field}"
            assert isin_re.match(e["isin"]), f"{theme}: malformed ISIN {e['isin']}"
            assert e["match"] in {"exact", "close", "partial"}, \
                f"{theme}: unknown match value {e['match']!r}"
            assert e["isin"] in e["url"], \
                f"{theme}: url does not point at ISIN {e['isin']}"
