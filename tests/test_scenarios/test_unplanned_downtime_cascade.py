"""Tier-2 scenario test: unplanned downtime triggers wal_downtime_detector."""

from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.scenario


def _write_lp(client: httpx.Client, db: str, lines: str) -> None:
    r = client.post(
        f"/api/v3/write_lp?db={db}&precision=nanosecond",
        content=lines,
        headers={"Content-Type": "text/plain"},
    )
    r.raise_for_status()


def _query(client: httpx.Client, db: str, sql: str) -> list[dict]:
    r = client.post("/api/v3/query_sql", json={"db": db, "q": sql, "format": "json"})
    r.raise_for_status()
    return r.json() or []


def test_downtime_alert_fires(influx_client, influx_container):
    db = influx_container["database"]
    base_t = int(time.time() * 1_000_000_000)
    # First, write a "running" baseline for L2-S4
    _write_lp(
        influx_client,
        db,
        f"machine_state,site=acme-main,line_id=L2,station_id=S4,machine_id=L2-S4 "
        f'state="running",reason="" {base_t}',
    )
    # Then, write a "stopped" transition
    _write_lp(
        influx_client,
        db,
        f"machine_state,site=acme-main,line_id=L2,station_id=S4,machine_id=L2-S4 "
        f'state="stopped",reason="tool_change" {base_t + 1_000_000_000}',
    )
    # Allow the WAL trigger to run
    time.sleep(2)
    rows = _query(
        influx_client,
        db,
        "SELECT * FROM alerts WHERE source = 'wal_downtime_detector' ORDER BY time DESC LIMIT 5",
    )
    assert any(r.get("machine_id") == "L2-S4" for r in rows)
