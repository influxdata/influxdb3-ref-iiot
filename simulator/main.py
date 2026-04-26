"""Simulator entry point. Runs forever (or for SIM_DURATION_S) writing to InfluxDB."""

from __future__ import annotations

import logging
import time

from simulator.config import load
from simulator.signals import build_plant
from simulator.writer import InfluxDB3Writer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("simulator")


def main() -> None:
    cfg = load()
    log.info("starting simulator: %s", cfg)
    writer = InfluxDB3Writer(url=cfg.influxdb_url, database=cfg.database, token=cfg.token)
    plant = build_plant(
        site=cfg.site, lines=cfg.lines,
        stations_per_line=cfg.stations_per_line, seed=cfg.seed,
        nominal_cycle_s=cfg.nominal_cycle_s,
    )
    period = 1.0 / cfg.rate_hz
    t0 = time.time()
    tick = 0
    try:
        while True:
            now = time.time()
            t_seconds = now - t0
            if cfg.duration_s is not None and t_seconds >= cfg.duration_s:
                break
            t_ns = int(now * 1_000_000_000)
            for line in plant.tick(t_seconds, t_ns):
                writer.write(line)
            tick += 1
            if tick % 30 == 0:
                writer.flush()
                log.info("tick=%d t=%.1fs lines_buffered=ok", tick, t_seconds)
            sleep_for = period - (time.time() - now)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        writer.flush()
        log.info("simulator exiting after %d ticks", tick)


if __name__ == "__main__":
    main()
