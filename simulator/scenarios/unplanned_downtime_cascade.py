"""A station on Line 2 stops; upstream starves, downstream blocks, line OEE plummets.

Writes machine_state rows that flip L2-S4 to stopped, then propagate idle states
to other Line 2 stations. The WAL trigger wal_downtime_detector (registered by
init.sh) fires within ~1s of the L2-S4 state change and writes a row to alerts.

Run via: make scenario name=unplanned_downtime_cascade
"""

from __future__ import annotations

from simulator.scenarios._base import announce, now_ns, open_writer, sleep

LINE = "L2"
DOWNED_STATION = "S4"
SITE = "acme-main"


def _state_line(station: str, state: str, reason: str = "") -> str:
    machine_id = f"{LINE}-{station}"
    return (
        f'machine_state,site={SITE},line_id={LINE},'
        f'station_id={station},machine_id={machine_id} '
        f'state="{state}",reason="{reason}" {now_ns()}'
    )


def main() -> None:
    writer = open_writer()
    try:
        announce(1, "baseline: 10s of nominal operation (no scenario writes)")
        sleep(10)

        announce(2, f"{LINE}-{DOWNED_STATION} → stopped (reason=tool_change)")
        writer.write(_state_line(DOWNED_STATION, "stopped", "tool_change"))
        writer.flush()

        announce(3, "propagating idle to upstream/downstream over 5s")
        sleep(5)
        for s in ("S1", "S2", "S3"):  # upstream starves
            writer.write(_state_line(s, "idle", "starved"))
        for s in ("S5", "S6", "S7", "S8"):  # downstream blocked
            writer.write(_state_line(s, "idle", "blocked"))
        writer.flush()

        announce(4, "holding stopped state for 60s (alert should fire by now)")
        # Re-emit the stopped state every 5s so the WAL trigger sees fresh writes.
        for _ in range(12):
            writer.write(_state_line(DOWNED_STATION, "stopped", "tool_change"))
            writer.flush()
            sleep(5)

        announce(5, "recovery: all Line 2 stations back to running")
        for s in ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8"):
            writer.write(_state_line(s, "running"))
        writer.flush()

        announce(6, "DONE — verify: SELECT * FROM alerts WHERE line_id='L2' ORDER BY time DESC LIMIT 5")
    finally:
        writer.flush()


if __name__ == "__main__":
    main()
