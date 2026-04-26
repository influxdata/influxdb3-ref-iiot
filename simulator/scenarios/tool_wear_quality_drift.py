"""Vibration on Line 1 Station 6 trends up over 5 min; cycle time slows, scrap rate rises, quality alert fires.

Writes vibration rows ramping from 2.0 → 4.5 mm/s and part_events rows whose
quality flips to scrap at 15% rate once vibration exceeds 3.5 mm/s. The WAL
trigger wal_quality_excursion (registered by init.sh) fires when the windowed
scrap rate crosses the configured threshold (default 0.10 over 20-event window).

Run via: make scenario name=tool_wear_quality_drift
"""

from __future__ import annotations

import random

from simulator.scenarios._base import announce, now_ns, open_writer, sleep

LINE = "L1"
TARGET_STATION = "S6"
SITE = "acme-main"
DURATION_S = 300  # 5 minutes
START_VIB = 2.0
END_VIB = 4.5


def _vib_line(rms: float) -> str:
    machine_id = f"{LINE}-{TARGET_STATION}"
    return (
        f'vibration,site={SITE},line_id={LINE},'
        f'station_id={TARGET_STATION},machine_id={machine_id} '
        f'rms_mm_s={rms:.3f} {now_ns()}'
    )


def _part_line(seq: int, quality: str, cycle_time: float) -> str:
    machine_id = f"{LINE}-{TARGET_STATION}"
    part_id = f"scen-{machine_id}-{seq:08d}"
    return (
        f'part_events,site={SITE},line_id={LINE},'
        f'station_id={TARGET_STATION},machine_id={machine_id},'
        f'part_id={part_id},quality={quality} '
        f'cycle_time_s={cycle_time:.3f} {now_ns()}'
    )


def main() -> None:
    writer = open_writer()
    rng = random.Random(2026)
    seq = 0
    try:
        announce(1, f"baseline: 10s of nominal vibration on {LINE}-{TARGET_STATION}")
        for _ in range(10):
            writer.write(_vib_line(START_VIB + rng.gauss(0.0, 0.05)))
            writer.flush()
            sleep(1)

        announce(2, f"ramping vibration {START_VIB} → {END_VIB} over {DURATION_S}s")
        steps = DURATION_S
        for i in range(steps):
            t_norm = i / max(1, steps - 1)
            vib = START_VIB + (END_VIB - START_VIB) * t_norm
            writer.write(_vib_line(vib + rng.gauss(0.0, 0.05)))

            # Cycle-time inflation: extra +0.5s/(mm/s) above 3.0
            extra = max(0.0, 0.5 * (vib - 3.0))
            cycle_time = 30.0 + extra

            # Scrap rate goes from 1% to 15% as vibration crosses 3.5
            if vib >= 3.5:
                scrap_rate = 0.15
            else:
                scrap_rate = 0.01
            quality = "scrap" if rng.random() < scrap_rate else "good"

            seq += 1
            writer.write(_part_line(seq, quality, cycle_time))
            if i % 5 == 0:
                writer.flush()
                announce(3, f"  t={i}s  vib={vib:.2f} mm/s  cycle={cycle_time:.2f}s  scrap_rate={scrap_rate:.2f}")
            sleep(1)
        writer.flush()

        announce(4, "DONE — verify: SELECT * FROM alerts WHERE line_id='L1' AND severity='quality' ORDER BY time DESC LIMIT 5")
    finally:
        writer.flush()


if __name__ == "__main__":
    main()
