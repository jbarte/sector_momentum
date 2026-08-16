"""GDELT GKG bulk file access.

GDELT's DOC 2.0 query API is rate limited with a stateful, long-window
limiter (see sector_momentum-notes/specs/2026-08-16-gdelt-bulk-fetch-design.md):
sustained use leaves a client failing ~80% of requests even at the documented
5s spacing. GDELT's own guidance is that high-volume users should take the
bulk feed instead, which is plain static file hosting with no rate limit at
all.

This module owns everything about that file format. A GKG slice is published
every 15 minutes at :00/:15/:30/:45 as a zipped, tab-separated 27-column CSV.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import sys
import zipfile
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

GKG_BASE = "http://data.gdeltproject.org/gdeltv2"

# Column indices, verified against a live slice (20260816094500). The GKG 2.1
# layout is fixed at 27 columns; anything shorter is a malformed row.
_COL_URL = 4
_COL_V1THEMES = 7
_COL_V2THEMES = 8
_COL_V1ORGS = 13
_COL_V2ORGS = 14
_COL_ALLNAMES = 23
_COL_EXTRAS = 26
_MIN_COLUMNS = _COL_EXTRAS + 1

_TITLE_RE = re.compile(r"<PAGE_TITLE>(.*?)</PAGE_TITLE>", re.S)

# GKG's GCAM column carries thousands of comma-separated codes and blows past
# csv's default 128KB field limit. Capped at 2**31-1 rather than sys.maxsize,
# which raises OverflowError on some platforms.
csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


def slice_urls(end: datetime, hours: int = 24) -> list[str]:
    """URLs for every 15-minute GKG slice in the `hours` window ending at `end`.

    `end` is aligned DOWN to the previous quarter hour: slices exist only at
    :00/:15/:30/:45, so a 09:47 scan wants the 09:45 file. Returned
    oldest-first purely so logs read chronologically.
    """
    aligned = end.replace(minute=(end.minute // 15) * 15, second=0, microsecond=0)
    count = hours * 4
    urls = [
        f"{GKG_BASE}/{(aligned - timedelta(minutes=15 * i)).strftime('%Y%m%d%H%M%S')}.gkg.csv.zip"
        for i in range(count)
    ]
    return list(reversed(urls))


def parse_slice(raw: bytes) -> list[dict]:
    """Parse one zipped GKG slice into match-ready records.

    Returns [{title, url, themes, orgs, names}]. Records without a
    <PAGE_TITLE> are skipped — the title is what FinBERT scores, so a record
    without one is useless to us. Raises on a malformed zip so the caller can
    count and skip that slice.
    """
    records: list[dict] = []
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        names = zf.namelist()
        if not names:
            return records
        with zf.open(names[0]) as fh:
            text = io.TextIOWrapper(fh, encoding="utf-8", errors="replace", newline="")
            for row in csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE):
                if len(row) < _MIN_COLUMNS:
                    continue
                match = _TITLE_RE.search(row[_COL_EXTRAS])
                if not match:
                    continue
                title = match.group(1).strip()
                if not title:
                    continue
                records.append({
                    "title": title,
                    "url": row[_COL_URL],
                    "themes": f"{row[_COL_V1THEMES]} {row[_COL_V2THEMES]}",
                    "orgs": f"{row[_COL_V1ORGS]} {row[_COL_V2ORGS]}",
                    "names": row[_COL_ALLNAMES],
                })
    return records


def queryable_themes(themes_cfg: dict) -> dict[str, list[str]]:
    """{theme: [lowercased keyword]} for every theme carrying gdelt_keywords.

    Shared by the bulk path and the API fallback so both agree on exactly
    which themes are fetchable.
    """
    themes = (themes_cfg or {}).get("themes") or {}
    return {
        name: [k.lower() for k in cfg["gdelt_keywords"]]
        for name, cfg in themes.items()
        if isinstance(cfg, dict) and cfg.get("gdelt_keywords")
    }


def match_themes(records: list[dict], themes_cfg: dict) -> dict[str, list[str]]:
    """Attribute records to themes by keyword, returning {theme: [title]}.

    Matches against the title AND GKG's own themes/orgs/names enrichment.
    Title-only matching halves the hit rate (measured: 6 vs 14 matches per
    slice), which is not enough to keep the low-volume themes above
    MIN_ARTICLES. Matching on metadata while scoring the title mirrors what
    the DOC API already does — it matches article body text and we score the
    headline — so this is consistent with existing behaviour, not a new
    compromise.

    Deduped by URL and by exact title: the same story is republished across
    slices and syndicated across outlets, and counting it once per outlet
    would weight it by syndication footprint rather than sentiment.
    """
    queryable = queryable_themes(themes_cfg)
    out: dict[str, list[str]] = {name: [] for name in queryable}
    seen_urls: dict[str, set] = {name: set() for name in queryable}
    seen_titles: dict[str, set] = {name: set() for name in queryable}

    for rec in records:
        title = rec["title"]
        haystack = " ".join((
            title, rec.get("themes", ""), rec.get("orgs", ""), rec.get("names", ""),
        )).lower()
        url = rec.get("url") or ""
        for name, keywords in queryable.items():
            if not any(k in haystack for k in keywords):
                continue
            if (url and url in seen_urls[name]) or title in seen_titles[name]:
                continue
            if url:
                seen_urls[name].add(url)
            seen_titles[name].add(title)
            out[name].append(title)
    return out
