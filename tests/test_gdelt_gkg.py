"""Tests for src/data/gdelt_gkg.py — GDELT bulk GKG file access.

No network, no sleeping: slice URLs are pure computation, and parsing runs
against synthetic zip bytes built in-test.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

import pytest

from src.data.gdelt_gkg import slice_urls, parse_slice


def _gkg_zip(rows: list[list[str]]) -> bytes:
    """Build a .gkg.csv.zip byte payload from raw column lists.

    Joined manually rather than via csv.writer: GKG is plain tab-separated
    with no quoting, and csv.writer rejects the QUOTE_NONE/quotechar combo
    needed to reproduce that faithfully.
    """
    text = "\n".join("\t".join(r) for r in rows)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("20260816091500.gkg.csv", text)
    return out.getvalue()


def _row(title="Chip maker posts record quarter", url="https://ex.com/a",
         v1themes="ECON_STOCKMARKET", v2themes="EPU_ECONOMY,120",
         v1orgs="acme corp", v2orgs="Acme Corp,55", names="Acme Corp,55"):
    """A 27-column GKG row with the fields we read populated."""
    row = [""] * 27
    row[0] = "20260816091500-0"
    row[1] = "20260816091500"
    row[4] = url
    row[7] = v1themes
    row[8] = v2themes
    row[13] = v1orgs
    row[14] = v2orgs
    row[23] = names
    row[26] = f"<PAGE_TITLE>{title}</PAGE_TITLE>" if title is not None else "<PAGE_LINKS>x</PAGE_LINKS>"
    return row


class TestSliceUrls:
    def test_24h_yields_96_quarter_hour_slices(self):
        end = datetime(2026, 8, 16, 9, 47, 12, tzinfo=timezone.utc)
        urls = slice_urls(end, hours=24)
        assert len(urls) == 96

    def test_end_is_aligned_down_to_the_previous_quarter_hour(self):
        """09:47 must resolve to the 09:45 slice — 09:47 does not exist."""
        end = datetime(2026, 8, 16, 9, 47, 12, tzinfo=timezone.utc)
        urls = slice_urls(end, hours=1)
        assert urls[-1].endswith("20260816094500.gkg.csv.zip")

    def test_slices_are_oldest_first_and_15_minutes_apart(self):
        end = datetime(2026, 8, 16, 9, 45, tzinfo=timezone.utc)
        urls = slice_urls(end, hours=1)
        assert [u.split("/")[-1] for u in urls] == [
            "20260816090000.gkg.csv.zip",
            "20260816091500.gkg.csv.zip",
            "20260816093000.gkg.csv.zip",
            "20260816094500.gkg.csv.zip",
        ]

    def test_crosses_a_day_boundary(self):
        end = datetime(2026, 8, 16, 0, 15, tzinfo=timezone.utc)
        names = [u.split("/")[-1] for u in slice_urls(end, hours=1)]
        assert "20260815234500.gkg.csv.zip" in names
        assert "20260816000000.gkg.csv.zip" in names

    def test_crosses_a_month_boundary(self):
        end = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
        names = [u.split("/")[-1] for u in slice_urls(end, hours=1)]
        assert "20260831231500.gkg.csv.zip" in names


class TestParseSlice:
    def test_extracts_title_url_and_match_fields(self):
        recs = parse_slice(_gkg_zip([_row()]))
        assert len(recs) == 1
        r = recs[0]
        assert r["title"] == "Chip maker posts record quarter"
        assert r["url"] == "https://ex.com/a"
        assert "ECON_STOCKMARKET" in r["themes"]
        assert "EPU_ECONOMY" in r["themes"]
        assert "acme corp" in r["orgs"].lower()
        assert "Acme Corp" in r["names"]

    def test_skips_records_with_no_page_title(self):
        recs = parse_slice(_gkg_zip([_row(title=None), _row(title="Kept")]))
        assert [r["title"] for r in recs] == ["Kept"]

    def test_skips_short_rows_without_crashing(self):
        recs = parse_slice(_gkg_zip([["only", "three", "cols"], _row(title="Kept")]))
        assert [r["title"] for r in recs] == ["Kept"]

    def test_handles_very_large_fields(self):
        """GKG's GCAM column routinely exceeds csv's default field limit."""
        row = _row(title="Big")
        row[17] = "wc:24," + ",".join(f"c{i}.{i}:{i}" for i in range(20000))
        recs = parse_slice(_gkg_zip([row]))
        assert [r["title"] for r in recs] == ["Big"]

    def test_empty_slice_yields_no_records(self):
        assert parse_slice(_gkg_zip([])) == []

    def test_malformed_zip_raises_so_the_caller_can_skip_it(self):
        with pytest.raises(Exception):
            parse_slice(b"this is not a zip file")
