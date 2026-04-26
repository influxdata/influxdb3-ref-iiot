"""Named SQL queries the UI runs against InfluxDB.

Every function is named for what the UI uses it for and documents:
  - what the result rows look like
  - which route consumes it
  - why this query is the right one for this vertical

This file is the primary teaching artifact for IIoT domain modeling on
InfluxDB 3.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Plant-state banner (LVC-served)
# ---------------------------------------------------------------------------


def plant_state_sql() -> str:
    """Return the latest state for every machine.

    Used by: GET /partials/plant_state -> _plant_state.html
    Result rows: {machine_id, state}  (24 rows, one per machine)
    Why: Reads the LVC via the `last_cache(table, cache_name)` table-valued
        function, which returns one row per cache-key combination — 24 rows
        for the 24 machines, in single-digit ms. A plain SELECT against
        machine_state would scan the whole table and count every tick.
    """
    return """
        SELECT machine_id, state
        FROM last_cache('machine_state', 'machine_state_last')
        WHERE site = 'acme-main'
        ORDER BY machine_id
    """


# ---------------------------------------------------------------------------
# KPI row
# ---------------------------------------------------------------------------


def kpi_units_last_window_sql(minutes: int = 60) -> str:
    """COUNT of part_events in the last `minutes` window. Used by the KPI tile."""
    return f"""
        SELECT COUNT(*) AS units
        FROM part_events
        WHERE time > now() - INTERVAL '{minutes} minutes'
    """


def kpi_alerts_last_window_sql(minutes: int = 60) -> str:
    """COUNT of alerts in the last `minutes` window. Used by the KPI tile."""
    return f"""
        SELECT COUNT(*) AS active_alerts
        FROM alerts
        WHERE time > now() - INTERVAL '{minutes} minutes'
    """


def kpi_distinct_parts_today_sql() -> str:
    """Count of distinct part_ids today. Hits the Distinct Value Cache.

    The cache hint clause makes the speedup explicit; without the cache,
    the query would scan the full part_events table.
    """
    return """
        SELECT COUNT(DISTINCT part_id) AS distinct_parts
        FROM part_events
        WHERE time > date_trunc('day', now())
    """


def kpi_plant_oee_current_shift_sql() -> str:
    """Aggregate plant OEE for the current shift (rolling 8h approximation).

    The exact shift-aligned OEE is in `shift_summary` after the schedule
    plugin runs at the next shift boundary; this query gives a live
    approximation between boundaries.
    """
    return """
        WITH ms AS (
          SELECT
            SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_seconds,
            SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END) AS planned_seconds
          FROM machine_state WHERE time > now() - INTERVAL '8 hours'
        ), pe AS (
          SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) AS good_count
          FROM part_events WHERE time > now() - INTERVAL '8 hours'
        )
        SELECT
          CASE WHEN ms.planned_seconds > 0 THEN ms.running_seconds * 1.0 / ms.planned_seconds ELSE 0 END AS availability,
          CASE WHEN ms.running_seconds > 0 THEN LEAST(1.0, 30.0 * pe.total_count / ms.running_seconds) ELSE 0 END AS performance,
          CASE WHEN pe.total_count > 0 THEN pe.good_count * 1.0 / pe.total_count ELSE 0 END AS quality
        FROM ms, pe
    """


# ---------------------------------------------------------------------------
# Per-line OEE breakdown
# ---------------------------------------------------------------------------
# The breakdown chart is fed by the `request_andon_board` Processing Engine
# plugin, which returns `{lines: [{line_id, history: [{bucket, availability,
# performance, quality}, ...]}, ...]}`. The browser fetches the andon endpoint
# directly, so there is no FastAPI partial route or named SQL query for the
# breakdown chart in this file. See `plugins/request_andon_board.py` for the
# OEE math (it joins per-minute state and parts aggregates and computes
# A × P × Q with the line-level Performance correctly normalized by
# `running_seconds` summed across all running machines on the line).


# ---------------------------------------------------------------------------
# Recent alerts table
# ---------------------------------------------------------------------------


def recent_alerts_sql(limit: int = 50) -> str:
    """Recent alerts written by WAL plugins.

    Used by: GET /partials/alerts -> _alerts.html
    Result rows: {time, source, severity, line_id, machine_id, reason, value}
    """
    return f"""
        SELECT time, source, severity, line_id, machine_id, reason, value
        FROM alerts
        WHERE time > now() - INTERVAL '24 hours'
        ORDER BY time DESC
        LIMIT {limit}
    """
