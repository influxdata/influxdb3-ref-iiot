"""Request trigger: GET /api/v3/engine/andon_board returns full plant view as JSON.

Binding: request path="andon_board", args={}
Returns: {
  "lines": [
    {
      "line_id": "L1",
      "oee": 0.873, "availability": 0.92, "performance": 0.97, "quality": 0.99,
      "machines": [{"machine_id": "L1-S1", "state": "running", "reason": ""}, ...],
      "alerts": [{"time": ..., "machine_id": ..., "severity": ..., "reason": ..., "source": ..., "value": ...}, ...]
    },
    ...
  ],
  "generated_at": "2026-04-25T14:01:23Z"
}

The UI fetches this and shows a "served by Processing Engine: <ms>" badge,
demonstrating Processing-Engine-mediated APIs replacing a custom backend.
"""

from __future__ import annotations

from datetime import datetime, timezone

# Window for "current shift" KPIs and recent alerts.
SHIFT_WINDOW_SQL = "INTERVAL '8 hours'"
ALERT_WINDOW_SQL = "INTERVAL '15 minutes'"


def process_request(influxdb3_local, query_parameters, request_headers, request_body, args=None):
    # Latest state per machine via the LVC: a SELECT against machine_state
    # picks up the cache automatically when grouping by the cache-key columns.
    state_rows = influxdb3_local.query(
        "SELECT site, line_id, station_id, machine_id, state, reason "
        "FROM machine_state ORDER BY line_id, station_id"
    )

    # Per-line A x P x Q over the current shift window (approximation: last 8h).
    parts_rows = influxdb3_local.query(
        f"""
        SELECT
          ms.line_id AS line_id,
          (SELECT COUNT(*) FROM machine_state WHERE state = 'running'
             AND line_id = ms.line_id AND time > now() - {SHIFT_WINDOW_SQL}) AS running_seconds,
          (SELECT COUNT(*) FROM machine_state WHERE state NOT IN ('changeover','planned_maintenance')
             AND line_id = ms.line_id AND time > now() - {SHIFT_WINDOW_SQL}) AS planned_seconds,
          (SELECT COUNT(*) FROM part_events
             WHERE line_id = ms.line_id AND time > now() - {SHIFT_WINDOW_SQL}) AS total_count,
          (SELECT COUNT(*) FROM part_events
             WHERE line_id = ms.line_id AND quality = 'good'
             AND time > now() - {SHIFT_WINDOW_SQL}) AS good_count
        FROM machine_state ms
        WHERE ms.time > now() - {SHIFT_WINDOW_SQL}
        GROUP BY ms.line_id
        """
    )

    alert_rows = influxdb3_local.query(
        f"SELECT time, line_id, machine_id, severity, reason, source, value FROM alerts "
        f"WHERE time > now() - {ALERT_WINDOW_SQL} ORDER BY time DESC LIMIT 100"
    )

    # Group machines by line_id
    by_line: dict[str, list[dict]] = {}
    for r in state_rows:
        by_line.setdefault(str(r["line_id"]), []).append({
            "machine_id": str(r["machine_id"]),
            "station_id": str(r.get("station_id", "")),
            "state": str(r["state"]),
            "reason": str(r.get("reason", "")),
        })

    # Group alerts by line_id (a row with no line_id is dropped from per-line view)
    alerts_by_line: dict[str, list[dict]] = {}
    for r in alert_rows:
        line_id = str(r.get("line_id", ""))
        if not line_id:
            continue
        alerts_by_line.setdefault(line_id, []).append({
            "time": r.get("time"),
            "machine_id": str(r.get("machine_id", "")),
            "severity": str(r.get("severity", "")),
            "reason": str(r.get("reason", "")),
            "source": str(r.get("source", "")),
            "value": float(r.get("value", 0.0)),
        })

    # Per-line OEE (use ideal cycle 30s; real value should come from per-machine
    # config but for this reference we use the fleet default)
    ideal_cycle_s = 30.0
    parts_by_line = {str(r["line_id"]): r for r in parts_rows}

    lines_out = []
    for line_id in sorted(by_line):
        machines = by_line[line_id]
        machines.sort(key=lambda m: m["station_id"])
        pl = parts_by_line.get(line_id)
        if pl:
            running_s = float(pl.get("running_seconds") or 0)
            planned_s = float(pl.get("planned_seconds") or 0)
            total = float(pl.get("total_count") or 0)
            good = float(pl.get("good_count") or 0)
        else:
            running_s = planned_s = total = good = 0.0
        availability = (running_s / planned_s) if planned_s > 0 else 0.0
        performance = min(1.0, (ideal_cycle_s * total) / running_s) if running_s > 0 else 0.0
        quality = (good / total) if total > 0 else 0.0
        oee = availability * performance * quality
        lines_out.append({
            "line_id": line_id,
            "oee": round(oee, 4),
            "availability": round(availability, 4),
            "performance": round(performance, 4),
            "quality": round(quality, 4),
            "machines": machines,
            "alerts": alerts_by_line.get(line_id, []),
        })

    return {
        "status": 200,
        "body": {
            "lines": lines_out,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    }
