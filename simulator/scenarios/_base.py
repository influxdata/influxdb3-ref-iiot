"""Shared helpers for scenario scripts.

Each scenario:
  1. Opens an InfluxDB3Writer connected to the running stack.
  2. Writes line protocol directly (no signals.py instances) to inject
     a specific event pattern.
  3. Prints step-by-step so readers can follow along.
"""

from __future__ import annotations

import sys
import time

from simulator.config import load
from simulator.writer import InfluxDB3Writer


def open_writer() -> InfluxDB3Writer:
    cfg = load()
    return InfluxDB3Writer(
        url=cfg.influxdb_url, database=cfg.database, token=cfg.token, batch_size=200,
    )


def announce(step_no: int, message: str) -> None:
    print(f"[scenario step {step_no}] {message}", flush=True)


def fail(message: str) -> None:
    print(f"[scenario] FAIL: {message}", file=sys.stderr, flush=True)
    sys.exit(1)


def now_ns() -> int:
    return int(time.time() * 1_000_000_000)


def sleep(seconds: float) -> None:
    time.sleep(seconds)
