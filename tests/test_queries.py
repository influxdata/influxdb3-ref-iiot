"""Tests for the named SQL queries.

Each test verifies the SQL string is shaped correctly (correct table,
correct WHERE clause, correct grouping). Actual data is exercised by
the smoke test (Tier 3).
"""

from __future__ import annotations

import re

from ui import queries as q


def _normalize(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def test_plant_state_query_uses_lvc():
    sql = _normalize(q.plant_state_sql())
    # Reads the LVC via last_cache(...) so we get one row per machine
    # rather than one row per tick.
    assert "last_cache('machine_state', 'machine_state_last')" in sql
    assert "machine_id" in sql
    assert "site = 'acme-main'" in sql


def test_kpi_units_last_window_query_filters_part_events():
    sql = _normalize(q.kpi_units_last_window_sql(minutes=60))
    assert "FROM part_events" in sql
    assert "INTERVAL '60 minutes'" in sql or "INTERVAL '60 minute'" in sql
    assert "COUNT(" in sql.upper()


def test_kpi_alerts_last_window_query_filters_alerts():
    sql = _normalize(q.kpi_alerts_last_window_sql(minutes=60))
    assert "FROM alerts" in sql
    assert "INTERVAL '60 minutes'" in sql or "INTERVAL '60 minute'" in sql


def test_kpi_distinct_parts_today_uses_distinct_part_id():
    sql = _normalize(q.kpi_distinct_parts_today_sql())
    assert "FROM part_events" in sql
    assert "DISTINCT" in sql.upper() and "PART_ID" in sql.upper()


def test_recent_alerts_query_orders_desc_with_limit():
    sql = _normalize(q.recent_alerts_sql(limit=50))
    assert "FROM alerts" in sql
    assert "ORDER BY TIME DESC" in sql.upper()
    assert "LIMIT 50" in sql.upper()
