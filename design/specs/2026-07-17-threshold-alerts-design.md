# Threshold alerts — daily scan notifications

## Problem

The dashboard is pull-only. Rank transitions — a sector entering or exiting
the top 3 — are exactly the moments worth a push notification, but today the
only way to notice them is to open the dashboard and compare mentally against
yesterday.

## Goal

After each scan, detect top-3 rank entry/exit events for both sectors and
themes, and push a single human-readable notification to ntfy.sh. "No events,
no noise" — if nothing changed, nothing is sent.

## Success criteria

1. A scan where a sector enters or exits the top 3 produces a ntfy
   notification within seconds of the scan completing.
2. A scan with no top-3 changes produces no notification.
3. The alert step is fully non-fatal — a failure (network, missing topic,
   ntfy outage) logs a warning and the scan succeeds.
4. `--dry-run` and `--no-alerts` both suppress alerts.
5. Missing `NTFY_TOPIC` env var silently skips alerts (fail-open, like the
   Trends cache).

---

## Design

### Event detection

Compare the two most recent scans by loading `get_scan_history(conn, n_scans=2)`
and `get_theme_scan_history(conn, n_scans=2)`. For each cohort (US sectors,
EU sectors, themes):

- **Entry**: sector/theme has rank ≤ 3 in the latest scan AND either was
  absent from the previous scan or had rank > 3.
- **Exit**: sector/theme had rank ≤ 3 in the previous scan AND either is
  absent from the latest scan or has rank > 3.

Position changes within the top 3 (e.g. rank 1 → 3) are not events.

If only one scan exists (first-ever run), no comparison is possible — skip
alerts silently.

### Message format

One notification per scan. Title: `Sector Momentum — <date>`. Body is
Markdown, grouped by cohort:

```
Sectors — US
  ▲ Energy entered top 3 (rank 2)
  ▼ Health Care exited top 3 (was rank 3)

Sectors — EU
  (no changes)

Themes
  ▲ Uranium entered top 3 (rank 1)
```

Cohorts with no events are omitted from the body entirely (not shown as
"no changes"). If zero events across all cohorts, no notification is sent.

### Delivery

POST to `https://ntfy.sh/<NTFY_TOPIC>` with:
- `Title`: `Sector Momentum — <scan_date>`
- `Content-Type`: `text/markdown`
- Body: the formatted event summary
- `Tags`: `chart_with_upwards_trend` (ntfy emoji tag)
- `Priority`: `default` (3)

Uses `urllib.request` (stdlib) — no new dependency.

### Configuration

| Config | Source | Default |
|---|---|---|
| `NTFY_TOPIC` | env var / CI secret | `None` → alerts skipped |
| `--no-alerts` | CLI flag | `False` |
| Top-N threshold | hardcoded | `3` |

The top-N threshold is a module constant (`RANK_THRESHOLD = 3`), not
user-configurable. If a future need arises, it can be promoted to a CLI flag
or config file entry.

### Integration with scan.py

New Step 15 (after Step 14: Print summary), inside the existing
`if not args.dry_run` guard:

```python
# Step 15: Threshold alerts (non-fatal)
if not args.dry_run and not args.no_alerts:
    try:
        from src.alerts import send_alerts
        send_alerts(conn, scan_date)
    except Exception as exc:
        logger.warning("Alert step failed: %s", exc)
```

The `--no-alerts` flag is added to the argparse block alongside `--no-dashboard`
and `--no-backup`.

### Module: src/alerts.py

Public API:

```python
def send_alerts(
    conn: psycopg2.extensions.connection,
    scan_date: str,
) -> None:
```

Reads `NTFY_TOPIC` from `os.environ`. If absent, returns immediately.
Loads the two latest scans (sectors + themes), detects events, formats the
message, and POSTs to ntfy. Logs the event count on success.

Internal functions:

- `detect_top_n_events(history_df, n=3) -> list[dict]` — returns a list of
  `{"cohort": str, "sector": str, "event": "entry"|"exit", "rank": int}`
  dicts. Works on any DataFrame with the standard `scan_id, region,
  gics_sector, rank` columns (reusable for both sectors and themes).
- `format_alert_body(events: list[dict]) -> str` — groups by cohort,
  formats the Markdown body.
- `post_ntfy(topic: str, title: str, body: str) -> None` — HTTP POST to
  ntfy.sh.

### CI changes

Add `NTFY_TOPIC` secret to `.github/workflows/scan.yml` as an environment
variable for the scan step:

```yaml
env:
  NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
```

No new workflow, no new step — the existing `python3 scan.py` invocation
picks it up.

---

## Scope

### In scope

- `src/alerts.py` with event detection, formatting, and ntfy delivery
- `scan.py` integration (Step 15, `--no-alerts` flag)
- `.github/workflows/scan.yml` env var addition
- Tests for event detection and message formatting (ntfy POST mocked)

### Out of scope

- Configurable rank threshold (hardcoded to 3)
- Trajectory flip events (backlog says top-3 entry/exit only)
- Alert history / deduplication (stateless — each scan compares independently)
- Dashboard UI for alert configuration
- Alternative delivery channels (Slack, email, GitHub Issues)

## Verification

1. `pytest` — new tests for `detect_top_n_events` (entry, exit, no-change,
   first-scan, themes) and `format_alert_body` (grouping, empty).
2. `python3 scan.py --dry-run` — alerts skipped, no error.
3. Manual: set `NTFY_TOPIC` locally, run a scan, confirm notification arrives
   on phone/desktop via ntfy app.
