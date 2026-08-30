"""A sweep cell whose SELL line cannot fire is not evidence about that cell.

Bands are stored as ABSOLUTE ranks (`exit_rank = top_n + buffer`), so nothing
can rank past the line until the scored universe is larger than it. The theme
universe grew from 10 priced names in 2015 to 18 in 2023, so on any long window
the high-buffer cells spend part of it unable to sell at all — they degenerate
into buy-and-hold, and a bull market then makes them look like the best cells
on the board.

Measured 2026-08-30 on the monthly 2015- calendar: exit_rank 13 (the shipped
`long` preset) can fire on 69% of rebalance dates, exit_rank 15 on 51%, and
exit_rank 18 never. The sweep reported all of them as ordinary cells, and the
return/churn frontier — which rewards low churn — selected the most degenerate
ones. These tests pin the guard that makes that visible instead of silent.
"""
import pandas as pd
import pytest

from scripts.horizon_sweep import (
    MIN_LIVE_SHARE,
    _band_live_share,
    _write,
)


def _scored(sizes):
    """A score_by_date-shaped mapping whose frames have the given row counts."""
    return {
        pd.Timestamp("2020-01-31") + pd.Timedelta(days=30 * i):
            pd.DataFrame({"composite": range(n)})
        for i, n in enumerate(sizes)
    }


def test_live_share_is_one_when_the_universe_always_exceeds_the_exit_rank():
    assert _band_live_share(_scored([18, 18, 18]), exit_rank=9) == 1.0


def test_live_share_is_zero_when_the_exit_rank_covers_the_whole_universe():
    """exit_rank == universe size is already dead: rank 18 of 18 is inside the band."""
    assert _band_live_share(_scored([18, 18, 18]), exit_rank=18) == 0.0


def test_live_share_counts_only_the_dates_where_the_line_can_fire():
    # exit_rank 13 needs a universe of 14+, so the 10-theme date cannot sell
    assert _band_live_share(_scored([10, 14, 18]), exit_rank=13) == pytest.approx(2 / 3)
    # and one theme fewer moves exactly one date across the line
    assert _band_live_share(_scored([10, 13, 18]), exit_rank=13) == pytest.approx(1 / 3)


def test_live_share_of_an_empty_calendar_is_zero_not_a_crash():
    assert _band_live_share({}, exit_rank=9) == 0.0


class _Args:
    start, end, cost_bps = "2015-01-01", None, 100.0


def _row(top_n, buffer, cagr, tpy, live):
    return {"freq": "M", "top_n": top_n, "buffer": buffer, "band": 0.5,
            "cagr": cagr, "bench_cagr": 0.11, "sharpe": 1.0, "max_dd": -0.2,
            "turnover": 0.2, "trades_per_year": tpy, "median_hold": 100,
            "rebalances": 50, "ppy": 12, "live": live}


def test_a_degenerate_cell_is_kept_out_of_the_frontier(tmp_path):
    """The frontier rewards low churn, so a cell that cannot sell always wins it."""
    out = tmp_path / "sweep.md"
    rows = [
        _row(4, 5, 0.18, 20.0, 1.0),    # honest cell
        _row(5, 13, 0.25, 0.4, 0.0),    # degenerate: never sells, best return, least churn
    ]
    _write(rows, _Args(), "ACWI", out)
    frontier = out.read_text().split("## Return / churn frontier")[1]
    assert "| M | 4 | 5 |" in frontier
    assert "| M | 5 | 13 |" not in frontier


def test_the_cell_table_still_lists_degenerate_cells_and_flags_them(tmp_path):
    """Excluded from the frontier is not the same as hidden — the number is the finding."""
    out = tmp_path / "sweep.md"
    _write([_row(5, 13, 0.25, 0.4, 0.0)], _Args(), "ACWI", out)
    table = out.read_text().split("## Return / churn frontier")[0]
    assert "| M | 5 | 13 |" in table
    assert "0%" in table


def test_min_live_share_demands_a_band_that_is_live_nearly_always():
    """A cell live on half its dates is half buy-and-hold, not a tested rule."""
    assert 0.8 <= MIN_LIVE_SHARE <= 1.0
