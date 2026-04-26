"""WAL trigger: emit a quality alert when per-machine windowed scrap rate exceeds a threshold.

Binding: table=part_events, args={"window": "20", "scrap_threshold": "0.10"}
Fires on: every write batch to part_events.
Side effects: writes one row to `alerts` each time a machine's rolling
   window first crosses the threshold (transitions below->above). Does NOT
   re-emit while the machine remains above; emits again only after dropping
   back below and re-crossing.

Per-machine windows kept in a module-level dict; survives across batches,
resets on engine restart (acceptable for reference; see ARCHITECTURE.md).
"""

from collections import deque

# LineBuilder is INJECTED — see ARCHITECTURE.md "Plugin conventions".

# machine_id -> deque of last N qualities ("good"/"scrap")
_windows: dict[str, deque[str]] = {}
# machine_id -> bool: are we currently above the threshold?
_above: dict[str, bool] = {}


def _scrap_rate(window: deque[str]) -> float:
    if not window:
        return 0.0
    return sum(1 for q in window if q == "scrap") / len(window)


def process_writes(influxdb3_local, table_batches, args=None):
    a = args or {}
    win_size = int(a.get("window", "20"))
    threshold = float(a.get("scrap_threshold", "0.10"))

    for batch in table_batches:
        if batch["table_name"] != "part_events":
            continue
        for row in batch["rows"]:
            machine_id = str(row.get("machine_id", ""))
            quality = str(row.get("quality", ""))
            if not machine_id or quality not in ("good", "scrap"):
                continue
            if machine_id not in _windows:
                _windows[machine_id] = deque(maxlen=win_size)
            window = _windows[machine_id]
            window.append(quality)
            # Only assess threshold once the rolling window is at full capacity.
            # This avoids spurious early alerts on a partially-filled window
            # (e.g. 1 scrap out of 5 = 0.20 would be a false positive against
            # a 0.10 threshold meant for a 20-sample window).
            if len(window) < win_size:
                continue
            rate = _scrap_rate(window)
            currently_above = rate >= threshold
            was_above = _above.get(machine_id, False)
            _above[machine_id] = currently_above
            if currently_above and not was_above:
                line_id = str(row.get("line_id", ""))
                influxdb3_local.info(
                    f"quality_excursion: {machine_id} scrap_rate={rate:.3f} "
                    f"window={len(window)} threshold={threshold:.3f}"
                )
                lb = (
                    LineBuilder("alerts")
                    .tag("source", "wal_quality_excursion")
                    .tag("severity", "quality")
                    .tag("line_id", line_id)
                    .tag("machine_id", machine_id)
                    .string_field("reason", "quality_excursion")
                    .float64_field("value", float(rate))
                )
                influxdb3_local.write(lb)
