#!/usr/bin/env python3
"""Dev-only: propose Google Knowledge Graph entities for each universe ticker.

Prints candidate `ticker: {mid, title}` YAML to stdout. Review by hand and copy
only the correct entries into config/trends_entities.yaml — do NOT pipe this
straight into the config. This script is never imported by scan.py or run in CI;
it exists solely to bootstrap/refresh the curated config.

Usage:
    python3 scripts/resolve_trends_entities.py
"""
import sys
import time

import yaml

sys.path.insert(0, ".")
from src.data.trends_symbols import build_symbol_map  # noqa: E402


def main() -> int:
    with open("config/universe.yaml") as fh:
        universe = yaml.safe_load(fh)
    with open("config/sector_etfs.yaml") as fh:
        sector_etfs = yaml.safe_load(fh) or {}
    try:
        with open("config/trends_blocklist.yaml") as fh:
            blocklist = set(yaml.safe_load(fh) or [])
    except FileNotFoundError:
        blocklist = set()

    symbol_map = build_symbol_map(universe, sector_etfs, blocklist=blocklist)
    tickers = sorted({s for syms in symbol_map.values() for s in syms})

    from pytrends.request import TrendReq
    client = TrendReq(hl="en-US", tz=0)

    print("# Proposed entities — REVIEW BY HAND before copying into "
          "config/trends_entities.yaml")
    for t in tickers:
        try:
            suggestions = client.suggestions(t)
        except Exception as exc:  # network/rate-limit — skip, note it
            print(f"# {t}: suggestions() failed ({exc})")
            time.sleep(2)
            continue
        if not suggestions:
            print(f"# {t}: no entity candidates (will fall back to string)")
            continue
        print(f"# {t} candidates:")
        for s in suggestions:
            print(f"#   mid={s.get('mid')}  type={s.get('type')}  title={s.get('title')}")
        top = suggestions[0]
        print(f"{t}:\n  mid: {top.get('mid')}\n  title: {top.get('title')}")
        time.sleep(2)  # be gentle with Trends
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
