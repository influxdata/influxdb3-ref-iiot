"""Request trigger: GET /api/v3/engine/andon_board returns full plant view as JSON.

Binding: request path="andon_board", args={}
Returns: {
  "lines": [
    {
      "line_id": "L1",
      "oee": 0.873, "availability": 0.92, "performance": 0.97, "quality": 0.99,
      "machines": [{"machine_id": "L1-S1", "state": "running", "reason": ""}, ...],
      "alerts": [{"time": ..., "machine_id": ..., "severity": ..., "reason": ..., "source": ..., "value": ...}, ...],
      "history": [
        {"bucket": "2026-04-26T13:00:00Z", "availability": 1.0, "performance": 1.0, "quality": 0.99},
        ...
      ]
    },
    ...
  ],
  "generated_at": "2026-04-25T14:01:23Z"
}

The UI fetches this and shows a "served by Processing Engine: <ms>" badge,
demonstrating Processing-Engine-mediated APIs replacing a custom backend.
The browser's per-line OEE breakdown chart consumes `history` directly —
single fetch drives both the cell grid and the time-series chart.
"""

from __future__ import annotations

from datetime import UTC, datetime

# Window for "current shift" current OEE values and recent alerts.
SHIFT_WINDOW_SQL = "INTERVAL '8 hours'"
ALERT_WINDOW_SQL = "INTERVAL '15 minutes'"
# Historical window for the chart (per-minute buckets).
HISTORY_WINDOW_SQL = "INTERVAL '60 minutes'"
HISTORY_BUCKET_SQL = "INTERVAL '1 minute'"
# Ideal cycle time per machine (seconds). For per-line Performance, the
# ideal output across N parallel machines for the bucket is N * bucket_seconds
# / ideal_cycle_s, which equals running_seconds / ideal_cycle_s — so
# Performance = ideal_cycle_s * total_count / running_seconds.
IDEAL_CYCLE_S = 30.0


def process_request(influxdb3_local, query_parameters, request_headers, request_body, args=None):
    # Latest state per machine via the LVC. last_cache() is a table-valued
    # function returning one row per cache-key combination — 24 rows for
    # the 24 machines.
    state_rows = influxdb3_local.query(
        "SELECT site, line_id, station_id, machine_id, state, reason "
        "FROM last_cache('machine_state', 'machine_state_last') "
        "ORDER BY line_id, station_id"
    )

    # Per-line state aggregates over the current shift window. We split the
    # OEE inputs across two queries (machine_state for time-in-state,
    # part_events for counts) instead of using correlated COUNT(*) scalar
    # subqueries — DataFusion's optimizer can't disambiguate multiple
    # unaliased count(*) projections in the same SELECT.
    state_agg_rows = influxdb3_local.query(
        f"""
        SELECT
          line_id,
          SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_seconds,
          SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END) AS planned_seconds
        FROM machine_state
        WHERE time > now() - {SHIFT_WINDOW_SQL}
        GROUP BY line_id
        """
    )

    parts_agg_rows = influxdb3_local.query(
        f"""
        SELECT
          line_id,
          COUNT(*) AS total_count,
          SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) AS good_count
        FROM part_events
        WHERE time > now() - {SHIFT_WINDOW_SQL}
        GROUP BY line_id
        """
    )

    alert_rows = influxdb3_local.query(
        f"SELECT time, line_id, machine_id, severity, reason, source, value FROM alerts "
        f"WHERE time > now() - {ALERT_WINDOW_SQL} ORDER BY time DESC LIMIT 100"
    )

    # Per-line, per-minute history for the OEE breakdown chart. Two
    # bucketed aggregates joined in Python on (line_id, bucket).
    state_hist_rows = influxdb3_local.query(
        f"""
        SELECT
          line_id,
          date_bin({HISTORY_BUCKET_SQL}, time) AS bucket,
          SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_seconds,
          SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END) AS planned_seconds
        FROM machine_state
        WHERE time > now() - {HISTORY_WINDOW_SQL}
        GROUP BY line_id, bucket
        ORDER BY bucket
        """
    )
    parts_hist_rows = influxdb3_local.query(
        f"""
        SELECT
          line_id,
          date_bin({HISTORY_BUCKET_SQL}, time) AS bucket,
          COUNT(*) AS total_count,
          SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) AS good_count
        FROM part_events
        WHERE time > now() - {HISTORY_WINDOW_SQL}
        GROUP BY line_id, bucket
        ORDER BY bucket
        """
    )

    # Group machines by line_id
    by_line: dict[str, list[dict]] = {}
    for r in state_rows:
        by_line.setdefault(str(r["line_id"]), []).append(
            {
                "machine_id": str(r["machine_id"]),
                "station_id": str(r.get("station_id", "")),
                "state": str(r["state"]),
                "reason": str(r.get("reason", "")),
            }
        )

    # Group alerts by line_id (a row with no line_id is dropped from per-line view)
    alerts_by_line: dict[str, list[dict]] = {}
    for r in alert_rows:
        line_id = str(r.get("line_id", ""))
        if not line_id:
            continue
        alerts_by_line.setdefault(line_id, []).append(
            {
                "time": r.get("time"),
                "machine_id": str(r.get("machine_id", "")),
                "severity": str(r.get("severity", "")),
                "reason": str(r.get("reason", "")),
                "source": str(r.get("source", "")),
                "value": float(r.get("value", 0.0)),
            }
        )

    # Index aggregates and history by line_id (and (line_id, bucket) for history).
    state_by_line = {str(r["line_id"]): r for r in state_agg_rows}
    parts_by_line = {str(r["line_id"]): r for r in parts_agg_rows}
    state_hist = {(str(r["line_id"]), str(r["bucket"])): r for r in state_hist_rows}
    parts_hist = {(str(r["line_id"]), str(r["bucket"])): r for r in parts_hist_rows}

    def _compute_oee(running_s: float, planned_s: float, total: float, good: float) -> dict:
        availability = (running_s / planned_s) if planned_s > 0 else 0.0
        performance = (
            min(1.0, (IDEAL_CYCLE_S * total) / running_s) if running_s > 0 else 0.0
        )
        quality = (good / total) if total > 0 else 0.0
        return {
            "availability": round(availability, 4),
            "performance": round(performance, 4),
            "quality": round(quality, 4),
            "oee": round(availability * performance * quality, 4),
        }

    def _history_for(line_id: str) -> list[dict]:
        # Union of buckets from both queries — a bucket present in one but not
        # the other is still rendered (with the missing side defaulting to 0).
        buckets = sorted({b for (lid, b) in state_hist if lid == line_id} |
                         {b for (lid, b) in parts_hist if lid == line_id})
        out = []
        for b in buckets:
            sh = state_hist.get((line_id, b)) or {}
            ph = parts_hist.get((line_id, b)) or {}
            comp = _compute_oee(
                float(sh.get("running_seconds") or 0),
                float(sh.get("planned_seconds") or 0),
                float(ph.get("total_count") or 0),
                float(ph.get("good_count") or 0),
            )
            out.append({"bucket": b, **comp})
        return out

    lines_out = []
    for line_id in sorted(by_line):
        machines = by_line[line_id]
        machines.sort(key=lambda m: m["station_id"])
        sl = state_by_line.get(line_id) or {}
        pl = parts_by_line.get(line_id) or {}
        comp = _compute_oee(
            float(sl.get("running_seconds") or 0),
            float(sl.get("planned_seconds") or 0),
            float(pl.get("total_count") or 0),
            float(pl.get("good_count") or 0),
        )
        lines_out.append(
            {
                "line_id": line_id,
                **comp,
                "machines": machines,
                "alerts": alerts_by_line.get(line_id, []),
                "history": _history_for(line_id),
            }
        )

    # Return the body directly. Some Processing Engine versions auto-unwrap
    # a {"status", "body"} shape; this version returns the dict as-is to the
    # client. Returning the body dict directly works in either case.
    return {
        "lines": lines_out,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
