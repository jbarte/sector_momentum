"""Serialize backtest results to a committed backtests/ directory."""
from __future__ import annotations

import json
import os

import pandas as pd


def write_theme_results(track: dict | None, out_dir: str = "backtests_themes",
                        generated_at: str = "", top_n: int = 3) -> str:
    os.makedirs(out_dir, exist_ok=True)
    summary = {"generated_at": generated_at, "top_n": top_n, "track": track}
    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)
    if track and track.get("equity_curve"):
        pd.DataFrame(track["equity_curve"]).to_csv(
            os.path.join(out_dir, "equity_THEME.csv"), index=False)
    if track and track.get("holdings"):
        hold_rows = [{"date": h["date"], "sectors": ", ".join(h["sectors"])}
                     for h in track["holdings"]]
        pd.DataFrame(hold_rows).to_csv(
            os.path.join(out_dir, "holdings_THEME.csv"), index=False)
    return summary_path


def write_results(tracks: dict, out_dir: str = "backtests",
                  generated_at: str = "", top_n: int = 5,
                  rotations: list | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    summary = {"generated_at": generated_at, "top_n": top_n,
               "tracks": tracks, "rotations": rotations or []}

    summary_path = os.path.join(out_dir, "summary.json")
    with open(summary_path, "w") as fh:
        json.dump(summary, fh, indent=2)

    for region, track in tracks.items():
        if not track:
            continue
        pd.DataFrame(track["equity_curve"]).to_csv(
            os.path.join(out_dir, f"equity_{region}.csv"), index=False)
        # Flatten holdings (sectors list -> comma-joined string)
        hold_rows = [{"date": h["date"], "sectors": ", ".join(h["sectors"])}
                     for h in track["holdings"]]
        pd.DataFrame(hold_rows).to_csv(
            os.path.join(out_dir, f"holdings_{region}.csv"), index=False)

    return summary_path


def load_summary(out_dir: str = "backtests") -> dict | None:
    path = os.path.join(out_dir, "summary.json")
    if not os.path.exists(path):
        return None
    with open(path) as fh:
        return json.load(fh)
