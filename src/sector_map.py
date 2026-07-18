# src/sector_map.py
"""Sub-sector → GICS parent mapping.

Makes ``stoxx_to_gics`` in config/sector_map.yaml live config: EU STOXX
sub-sectors (Banks, Chemicals, …) resolve to their GICS-11 parent for
consumers that only know GICS names (FinBERT news sentiment, Swedish
ticker matching). Names without a mapping resolve to themselves, so US
and unchanged EU sectors pass through untouched.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def load_parent_map(path: str = "config/sector_map.yaml") -> dict[str, str]:
    """Load the sub-sector → GICS parent map. Raises on missing/malformed file."""
    with Path(path).open() as fh:
        cfg = yaml.safe_load(fh)
    return dict(cfg["stoxx_to_gics"])


def parent_sector(name: str, parent_map: dict[str, str]) -> str:
    """Resolve a sector name to its GICS parent; unmapped names map to themselves."""
    return parent_map.get(name, name)
