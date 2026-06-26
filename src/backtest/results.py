"""Serialize backtest results to a committed backtests/ directory."""
from __future__ import annotations

import json
import os

import pandas as pd


def write_results(tracks: dict, out_dir: str = "backtests",
                  generated_at: str = "", top_n: int = 5) -> str:
    os.makedirs(out_dir, exist_ok=True)
    summary = {"generated_at": generated_at, "top_n": top_n, "tracks": tracks}

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
