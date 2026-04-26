"""Schedule trigger: per-line OEE rollup at every shift boundary.

Binding: cron="0 0 6,14,22 * * *", args={"ideal_cycle_s": "30.0"}
Runs: at 06:00, 14:00, 22:00 UTC every day.
Side effects: writes one row per line to `shift_summary` for the shift
   that just ended.

Shifts:
  A = 06:00-14:00 UTC, shift_id = YYYY-MM-DD-A (using shift-start date)
  B = 14:00-22:00 UTC
  C = 22:00 day N to 06:00 day N+1, shift_id uses day N
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# LineBuilder is INJECTED — see ARCHITECTURE.md "Plugin conventions".


def compute_shift_window(call_time: datetime) -> tuple[str, datetime, datetime]:
    """Return (shift_id, start, end) for the shift that ended at call_time."""
    if call_time.hour == 6:
        end = call_time.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=8)
        sid_date = start.date()
        return f"{sid_date.isoformat()}-C", start, end
    if call_time.hour == 14:
        end = call_time.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=8)
        sid_date = start.date()
        return f"{sid_date.isoformat()}-A", start, end
    if call_time.hour == 22:
        end = call_time.replace(minute=0, second=0, microsecond=0)
        start = end - timedelta(hours=8)
        sid_date = start.date()
        return f"{sid_date.isoformat()}-B", start, end
    raise ValueError(f"call_time hour {call_time.hour} is not a shift boundary")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _per_line_running(start: datetime, end: datetime) -> str:
    return f"""
        SELECT
          line_id,
          SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_seconds,
          SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END) AS planned_seconds
        FROM machine_state
        WHERE time >= TIMESTAMP '{_iso(start)}' AND time < TIMESTAMP '{_iso(end)}'
        GROUP BY line_id
    """


def _per_line_parts(start: datetime, end: datetime) -> str:
    return f"""
        SELECT
          line_id,
          COUNT(*) AS total_count,
          SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) AS good_count
        FROM part_events
        WHERE time >= TIMESTAMP '{_iso(start)}' AND time < TIMESTAMP '{_iso(end)}'
        GROUP BY line_id
    """


def _per_line_downtime(start: datetime, end: datetime) -> str:
    return f"""
        SELECT line_id, reason, COUNT(*) AS seconds
        FROM machine_state WHERE state IN ('stopped','error','idle')
          AND time >= TIMESTAMP '{_iso(start)}' AND time < TIMESTAMP '{_iso(end)}'
          AND reason <> ''
        GROUP BY line_id, reason
    """


def process_scheduled_call(influxdb3_local, call_time, args=None):
    a = args or {}
    ideal_cycle_s = float(a.get("ideal_cycle_s", "30.0"))
    sid, start, end = compute_shift_window(call_time)

    running = {r["line_id"]: r for r in influxdb3_local.query(_per_line_running(start, end))}
    parts = {r["line_id"]: r for r in influxdb3_local.query(_per_line_parts(start, end))}
    downtime_rows = influxdb3_local.query(_per_line_downtime(start, end))

    by_line_downtime: dict[str, list[tuple[str, float]]] = {}
    for r in downtime_rows:
        by_line_downtime.setdefault(r["line_id"], []).append(
            (str(r["reason"]), float(r["seconds"]))
        )
    for lid in by_line_downtime:
        by_line_downtime[lid].sort(key=lambda x: x[1], reverse=True)

    line_ids = set(running) | set(parts)
    influxdb3_local.info(
        f"shift_summary: shift={sid} lines={sorted(line_ids)} call_time={call_time}"
    )

    for line_id in sorted(line_ids):
        rs = running.get(line_id, {"running_seconds": 0, "planned_seconds": 0})
        ps = parts.get(line_id, {"total_count": 0, "good_count": 0})
        running_s = float(rs["running_seconds"] or 0)
        planned_s = float(rs["planned_seconds"] or 0)
        total = float(ps["total_count"] or 0)
        good = float(ps["good_count"] or 0)
        availability = (running_s / planned_s) if planned_s > 0 else 0.0
        performance = min(1.0, (ideal_cycle_s * total) / running_s) if running_s > 0 else 0.0
        quality = (good / total) if total > 0 else 0.0
        oee = availability * performance * quality
        top3 = by_line_downtime.get(line_id, [])
        top1 = top3[0] if len(top3) > 0 else ("", 0.0)
        top2 = top3[1] if len(top3) > 1 else ("", 0.0)
        top3v = top3[2] if len(top3) > 2 else ("", 0.0)

        lb = (
            LineBuilder("shift_summary")
            .tag("line_id", line_id)
            .tag("shift_id", sid)
            .float64_field("oee", float(oee))
            .float64_field("availability", float(availability))
            .float64_field("performance", float(performance))
            .float64_field("quality", float(quality))
            .float64_field("units_total", total)
            .float64_field("units_good", good)
            .string_field("downtime_top1_reason", top1[0])
            .float64_field("downtime_top1_seconds", top1[1])
            .string_field("downtime_top2_reason", top2[0])
            .float64_field("downtime_top2_seconds", top2[1])
            .string_field("downtime_top3_reason", top3v[0])
            .float64_field("downtime_top3_seconds", top3v[1])
        )
        influxdb3_local.write(lb)
