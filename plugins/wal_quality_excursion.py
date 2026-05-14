"""WAL trigger: emit a quality alert when per-machine windowed scrap rate exceeds a threshold.

Binding: table=part_events, args={"window": "20", "scrap_threshold": "0.10"}
Fires on: every write batch to part_events.
Side effects: writes one row to `alerts` each time a machine's rolling
   window first crosses the threshold (transitions below->above). Does NOT
   re-emit while the machine remains above; emits again only after dropping
   back below and re-crossing.

Per-machine state is stored in the trigger-local cache
(influxdb3_local.cache) so it persists across WAL invocations.
"""

from collections import deque

# LineBuilder is INJECTED — see ARCHITECTURE.md "Plugin conventions".


def _scrap_rate(window: deque[str]) -> float:
    if not window:
        return 0.0
    return sum(1 for q in window if q == "scrap") / len(window)


def process_writes(influxdb3_local, table_batches, args=None):
    a = args or {}
    win_size = int(a.get("window", "20"))
    threshold = float(a.get("scrap_threshold", "0.10"))
    cache = influxdb3_local.cache

    # Load from cache on first access per machine, work in memory, flush at end.
    windows: dict[str, deque[str]] = {}
    above: dict[str, bool] = {}

    def _ensure_loaded(mid: str) -> None:
        if mid not in windows:
            state = cache.get(f"qe:{mid}")
            if state is not None:
                windows[mid] = deque(state["window"], maxlen=win_size)
                above[mid] = state["above"]
            else:
                windows[mid] = deque(maxlen=win_size)
                above[mid] = False

    for batch in table_batches:
        if batch["table_name"] != "part_events":
            continue
        for row in batch["rows"]:
            machine_id = str(row.get("machine_id", ""))
            quality = str(row.get("quality", ""))
            if not machine_id or quality not in ("good", "scrap"):
                continue
            _ensure_loaded(machine_id)
            window = windows[machine_id]
            window.append(quality)
            if len(window) < win_size:
                continue
            rate = _scrap_rate(window)
            currently_above = rate >= threshold
            was_above = above[machine_id]
            above[machine_id] = currently_above
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

    for mid in windows:
        cache.put(f"qe:{mid}", {"window": list(windows[mid]), "above": above[mid]})
