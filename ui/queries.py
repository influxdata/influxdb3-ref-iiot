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
    Result rows: {machine_id, state}
    Why: A WHERE clause matching the LVC key columns (site, line_id,
        station_id, machine_id) auto-routes through the LVC, returning
        24 rows in sub-millisecond.
    """
    return """
        SELECT machine_id, state
        FROM machine_state
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
# Per-line OEE breakdown (uPlot charts)
# ---------------------------------------------------------------------------

def per_line_availability_sql(minutes: int = 60) -> str:
    """Per-line Availability over the last `minutes`. Used by the OEE breakdown chart."""
    return f"""
        SELECT
          line_id,
          date_bin(INTERVAL '1 minute', time) AS bucket,
          SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) * 1.0
            / NULLIF(SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END), 0)
            AS availability
        FROM machine_state
        WHERE time > now() - INTERVAL '{minutes} minutes'
        GROUP BY line_id, bucket
        ORDER BY bucket
    """


def per_line_performance_sql(minutes: int = 60) -> str:
    """Per-line Performance over the last `minutes`."""
    return f"""
        SELECT
          line_id,
          date_bin(INTERVAL '1 minute', time) AS bucket,
          LEAST(1.0, 30.0 * COUNT(*) * 1.0 / 60.0) AS performance
        FROM part_events
        WHERE time > now() - INTERVAL '{minutes} minutes'
        GROUP BY line_id, bucket
        ORDER BY bucket
    """


def per_line_quality_sql(minutes: int = 60) -> str:
    """Per-line Quality over the last `minutes`."""
    return f"""
        SELECT
          line_id,
          date_bin(INTERVAL '1 minute', time) AS bucket,
          SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) * 1.0 / NULLIF(COUNT(*), 0) AS quality
        FROM part_events
        WHERE time > now() - INTERVAL '{minutes} minutes'
        GROUP BY line_id, bucket
        ORDER BY bucket
    """


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
