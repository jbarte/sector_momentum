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
    _is_degenerate,
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
    row = next(l for l in table.splitlines() if l.startswith("| M | 5 | 13 |"))
    # band 50%, then live 0% carrying the flag — assert the live cell itself,
    # not just "0% appears somewhere", which the band column alone satisfies.
    assert row.split("|")[5].strip() == "0% ⚠", row


def test_an_honest_cell_carries_no_flag(tmp_path):
    out = tmp_path / "sweep.md"
    _write([_row(4, 5, 0.18, 20.0, 1.0)], _Args(), "ACWI", out)
    row = next(l for l in out.read_text().splitlines() if l.startswith("| M | 4 | 5 |"))
    assert row.split("|")[5].strip() == "100%", row


def test_the_flag_follows_the_printed_percentage_not_the_raw_float():
    """A live share that PRINTS as 90% must not be flagged 'below 90%'."""
    assert not _is_degenerate(0.897)      # renders 90%
    assert _is_degenerate(0.894)          # renders 89%
    assert not _is_degenerate(None)


def test_live_share_denominator_can_be_restricted_to_simulated_dates():
    """`simulate` drops the final scored date, and that date has the biggest
    universe — counting it would bias the share upward."""
    scored = _scored([10, 10, 18])
    keys = list(scored)
    assert _band_live_share(scored, exit_rank=13) == pytest.approx(1 / 3)
    assert _band_live_share(scored, exit_rank=13, dates=keys[:-1]) == 0.0


def test_a_window_that_cannot_measure_a_shipped_preset_says_so(tmp_path):
    """Silently dropping `medium` from the frontier reads as 'beaten', not
    'not measured' — the default 2004- window does exactly this."""
    out = tmp_path / "sweep.md"
    _write([_row(4, 5, 0.18, 20.0, 0.43), _row(3, 2, 0.12, 30.0, 1.0)],
           _Args(), "ACWI", out)
    text = out.read_text()
    assert "cannot evaluate every shipped preset" in text
    assert "`medium`" in text and "43%" in text
    assert "| M | 4 | 5 |" not in text.split("## Return / churn frontier")[1]


def test_no_preset_warning_when_every_shipped_preset_is_live(tmp_path):
    out = tmp_path / "sweep.md"
    _write([_row(4, 5, 0.18, 20.0, 1.0), _row(5, 8, 0.15, 10.0, 1.0)],
           _Args(), "ACWI", out)
    assert "cannot evaluate every shipped preset" not in out.read_text()


def test_min_live_share_demands_a_band_that_is_live_nearly_always():
    """A cell live on half its dates is half buy-and-hold, not a tested rule."""
    assert 0.8 <= MIN_LIVE_SHARE <= 1.0
