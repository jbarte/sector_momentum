import backtest


def test_build_theme_ticker_list_includes_benchmark_and_spy_fallback():
    themes_cfg = {
        "benchmark": "ACWI",
        "themes": {"Semiconductors": {"ticker": "SOXX"}, "Space": {"ticker": "UFO"}},
    }
    tickers = backtest.build_theme_ticker_list(themes_cfg)
    assert tickers == ["SOXX", "UFO", "ACWI", "SPY"]


# ---------------------------------------------------------------------------
# Guards around the evaluation window
# ---------------------------------------------------------------------------
# `backtests/` is git-tracked and the dashboard renders it, so the CLI must not
# quietly replace it. Both guards return before any price fetch, so these run
# offline.

import argparse

import pytest

from src.backtest.replay import DEFAULT_EVAL_START, FETCH_START


def _args(**over):
    base = dict(top_n=5, start=DEFAULT_EVAL_START, out=backtest.DEFAULT_OUT,
                cost_bps=0.0, no_rotations=True, themes=True, no_themes=False,
                theme_top_n=3, rebalance="M", buffer=0)
    base.update(over)
    return argparse.Namespace(**base)


def test_cold_window_is_rejected_before_anything_is_written():
    """Inside the warm-up, above_200dma is NaN — the defect this whole split
    exists to prevent."""
    assert backtest.run(_args(start="2003-06-01", out="/tmp/does-not-matter")) == 1


def test_windowed_run_refuses_to_clobber_the_committed_artifact():
    """An exploratory `--start 2015-01-01` used to rewrite the shipped curve
    with windowed numbers and say nothing, because --out defaults to the
    tracked directory. Windowed runs only became useful when --start stopped
    truncating the fetch, so the hazard arrived with that fix."""
    assert backtest.run(_args(start="2015-01-01")) == 1


def _reaching_fetch(monkeypatch):
    """Replace fetch_prices with a probe that records its `start` and aborts.

    Lets a test assert what the CLI would have fetched without touching the
    network, and proves the guards were passed rather than merely not tripped.
    """
    import src.data.prices as prices_mod

    class Reached(Exception):
        pass

    def _probe(**kwargs):
        raise Reached(kwargs["start"])

    monkeypatch.setattr(prices_mod, "fetch_prices", _probe)
    return Reached


def test_windowed_run_is_allowed_with_an_explicit_out(tmp_path, monkeypatch):
    """The guard must name a way through, not just refuse."""
    Reached = _reaching_fetch(monkeypatch)
    with pytest.raises(Reached):
        backtest.run(_args(start="2015-01-01", out=str(tmp_path)))


def test_the_fetch_ignores_the_evaluation_window(tmp_path, monkeypatch):
    """THE bug, asserted directly: `--start` must not reach fetch_prices.

    Passing it there is what truncated the history and left above_200dma NaN
    over the opening months. The fetch always begins at FETCH_START; the window
    is applied afterwards, via rebalance_dates(since=).
    """
    Reached = _reaching_fetch(monkeypatch)
    with pytest.raises(Reached) as exc:
        backtest.run(_args(start="2015-01-01", out=str(tmp_path)))
    assert str(exc.value) == FETCH_START


def test_default_invocation_passes_both_guards(tmp_path, monkeypatch):
    """The canonical run must stay unguarded — a guard that blocks it would
    break CI silently, since CI passes no flags."""
    Reached = _reaching_fetch(monkeypatch)
    with pytest.raises(Reached):
        backtest.run(_args())


# ---------------------------------------------------------------------------
# Review findings, 2026-08-14
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("out", ["backtests/", "./backtests", "backtests/../backtests"])
def test_clobber_guard_is_not_fooled_by_an_equivalent_path(out):
    """The guard compared `args.out == "backtests"` as a raw string, so every
    spelling below named the tracked directory and slipped past it. Compared by
    realpath now."""
    assert backtest.run(_args(start="2015-01-01", out=out)) == 1


def test_a_partially_empty_run_writes_nothing(monkeypatch, tmp_path):
    """`long` needs one more scored theme than `medium`, so a thin window can
    produce a result for one preset and None for the other. That used to be
    written as `{"medium": {...}, "long": null}` with exit 0 — clobbering the
    artifact, orphaning a stale equity_long.csv, and silently dropping Long from
    the dashboard's Backtest tab. Any missing track now fails the run.
    """
    monkeypatch.setattr("src.data.prices.fetch_prices", lambda **kw: {"ACWI": object()})
    # medium returns a track, long returns None.
    seen = iter([{"metrics": {}, "equity_curve": []}, None])
    monkeypatch.setattr("src.backtest.engine.run_theme_track",
                        lambda *a, **k: next(seen))

    def _must_not_write(*a, **k):
        raise AssertionError("write_results must not be called on a partial run")
    monkeypatch.setattr("src.backtest.results.write_results", _must_not_write)

    assert backtest.run(_args(out=str(tmp_path))) == 1
