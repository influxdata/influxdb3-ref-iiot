"""Tier-2 scenario test: windowed scrap rate triggers wal_quality_excursion."""

from __future__ import annotations

import time

import httpx
import pytest

pytestmark = pytest.mark.scenario


def _write_lp(client: httpx.Client, db: str, lines: str) -> None:
    r = client.post(
        f"/api/v3/write_lp?db={db}&precision=nanosecond",
        content=lines, headers={"Content-Type": "text/plain"},
    )
    r.raise_for_status()


def _query(client: httpx.Client, db: str, sql: str) -> list[dict]:
    r = client.post("/api/v3/query_sql", json={"db": db, "q": sql, "format": "json"})
    r.raise_for_status()
    return r.json() or []


def test_quality_excursion_alert_fires(influx_client, influx_container):
    db = influx_container["database"]
    base_t = int(time.time() * 1_000_000_000)
    lines = []
    # 17 good + 3 scrap → 15% scrap rate, >= 10% threshold
    for i in range(17):
        lines.append(
            f'part_events,site=acme-main,line_id=L1,station_id=S6,machine_id=L1-S6,'
            f'part_id=p{i},quality=good cycle_time_s=30.0 {base_t + i * 1_000_000}'
        )
    for i in range(3):
        lines.append(
            f'part_events,site=acme-main,line_id=L1,station_id=S6,machine_id=L1-S6,'
            f'part_id=p{17 + i},quality=scrap cycle_time_s=30.0 {base_t + (17 + i) * 1_000_000}'
        )
    _write_lp(influx_client, db, "\n".join(lines))
    time.sleep(2)
    rows = _query(influx_client, db,
        "SELECT * FROM alerts WHERE source = 'wal_quality_excursion' ORDER BY time DESC LIMIT 5"
    )
    assert any(r.get("machine_id") == "L1-S6" for r in rows)
