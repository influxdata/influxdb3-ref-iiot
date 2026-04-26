"""Simulator configuration loaded from environment variables.

The InfluxDB admin token is generated inside the container by the
token-bootstrap compose service and persisted to a JSON file inside the
shared influxdb-data volume (mounted read-only at /tokens). We read that
file here. This mirrors the bess pattern.

All other defaults are appropriate for the demo; CI and scenarios override
what they need via env vars (notably SIM_SEED for reproducibility).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    influxdb_url: str
    database: str
    token: str
    site: str
    rate_hz: float
    seed: int
    lines: int
    stations_per_line: int
    nominal_cycle_s: float
    duration_s: float | None  # None = run forever


def _load_token() -> str:
    # Direct env var takes precedence (used by tests and ad-hoc local runs).
    if "INFLUXDB3_TOKEN" in os.environ:
        return os.environ["INFLUXDB3_TOKEN"]
    path = os.environ.get("INFLUX_TOKEN_FILE", "/tokens/.iiot-operator-token")
    with open(path) as f:
        data = json.load(f)
    return data["token"]


def load() -> Config:
    return Config(
        influxdb_url=os.environ.get("INFLUX_URL", "http://influxdb3:8181"),
        database=os.environ.get("INFLUX_DB", "iiot"),
        token=_load_token(),
        site=os.environ.get("SIM_SITE", "acme-main"),
        rate_hz=float(os.environ.get("SIM_RATE_HZ", "1.0")),
        seed=int(os.environ.get("SIM_SEED", "42")),
        lines=int(os.environ.get("SIM_LINES", "3")),
        stations_per_line=int(os.environ.get("SIM_STATIONS_PER_LINE", "8")),
        nominal_cycle_s=float(os.environ.get("SIM_NOMINAL_CYCLE_S", "30.0")),
        duration_s=float(os.environ["SIM_DURATION_S"]) if "SIM_DURATION_S" in os.environ else None,
    )
