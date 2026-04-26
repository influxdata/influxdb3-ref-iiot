"""IIoT domain signal generators.

Each class takes (site, line_id, station_id, machine_id) plus class-specific
config and exposes a `tick(t_seconds, t_ns)` method returning a list of
line-protocol strings written for that tick. The Plant aggregate composes
24 machines (3 lines × 8 stations) and ticks them all in lockstep.

Signals support runtime overrides (e.g., `MachineState.set_state(...)`,
`VibrationSensor.set_target(...)`) so scenarios can inject events without
modifying the simulator main loop.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from simulator.signals_base import jitter, random_walk

State = Literal["running", "idle", "stopped", "error", "changeover", "planned_maintenance"]


def _esc(s: str) -> str:
    """Escape a tag/field-key string for line protocol (commas, spaces, equals)."""
    return s.replace(",", r"\,").replace(" ", r"\ ").replace("=", r"\=")


def _tag_block(tags: dict[str, str]) -> str:
    return ",".join(f"{_esc(k)}={_esc(v)}" for k, v in tags.items())


@dataclass
class MachineState:
    site: str
    line_id: str
    station_id: str
    machine_id: str
    _state: State = "running"
    _reason: str = ""

    def set_state(self, state: State, reason: str = "") -> None:
        self._state = state
        self._reason = reason

    def state(self) -> State:
        return self._state

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        tags = _tag_block(
            {
                "site": self.site,
                "line_id": self.line_id,
                "station_id": self.station_id,
                "machine_id": self.machine_id,
            }
        )
        fields = f'state="{self._state}",reason="{self._reason}"'
        return [f"machine_state,{tags} {fields} {t_ns}"]


@dataclass
class TemperatureSensor:
    site: str
    line_id: str
    station_id: str
    machine_id: str
    nominal_c: float
    seed: int
    _walk: object = field(init=False)

    def __post_init__(self) -> None:
        self._walk = random_walk(
            seed=self.seed,
            step_std=0.2,
            start=self.nominal_c,
            min_val=self.nominal_c - 10.0,
            max_val=self.nominal_c + 20.0,
        )

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        v = self._walk()
        tags = _tag_block(
            {
                "site": self.site,
                "line_id": self.line_id,
                "station_id": self.station_id,
                "machine_id": self.machine_id,
            }
        )
        return [f"temperature,{tags} temp_c={v:.3f} {t_ns}"]


@dataclass
class VibrationSensor:
    site: str
    line_id: str
    station_id: str
    machine_id: str
    nominal_mm_s: float
    seed: int
    _target: float = field(init=False)
    _noise: object = field(init=False)

    def __post_init__(self) -> None:
        self._target = self.nominal_mm_s
        self._noise = jitter(seed=self.seed, std=0.1)

    def set_target(self, target_mm_s: float) -> None:
        self._target = target_mm_s

    def target(self) -> float:
        return self._target

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        # 10 Hz: emit 10 samples spread over the second
        out = []
        for i in range(10):
            ts = t_ns + int(i * 100_000_000)
            v = max(0.0, self._target + self._noise(t_seconds + i * 0.1))
            tags = _tag_block(
                {
                    "site": self.site,
                    "line_id": self.line_id,
                    "station_id": self.station_id,
                    "machine_id": self.machine_id,
                }
            )
            out.append(f"vibration,{tags} rms_mm_s={v:.3f} {ts}")
        return out


@dataclass
class PartCount:
    site: str
    line_id: str
    station_id: str
    machine_id: str
    ideal_cycle_time_s: float
    seed: int
    _running: bool = True
    _scrap_rate: float = 0.01
    _cycle_extension: float = 0.0  # added seconds per cycle (for tool-wear scenario)
    _cycle_start_t: float | None = None
    _seq: int = 0
    _rng: random.Random = field(init=False)

    def __post_init__(self) -> None:
        self._rng = random.Random(self.seed)

    def set_running(self, running: bool) -> None:
        self._running = running
        if not running:
            self._cycle_start_t = None

    def set_scrap_rate(self, rate: float) -> None:
        self._scrap_rate = max(0.0, min(1.0, rate))

    def set_cycle_extension(self, seconds: float) -> None:
        self._cycle_extension = max(0.0, seconds)

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        if not self._running:
            return []
        if self._cycle_start_t is None:
            self._cycle_start_t = t_seconds
            return []
        elapsed = t_seconds - self._cycle_start_t
        target = self.ideal_cycle_time_s + self._cycle_extension
        if elapsed < target:
            return []
        # cycle complete
        self._seq += 1
        part_id = f"{self.machine_id}-{self._seq:08d}"
        quality = "scrap" if self._rng.random() < self._scrap_rate else "good"
        tags = _tag_block(
            {
                "site": self.site,
                "line_id": self.line_id,
                "station_id": self.station_id,
                "machine_id": self.machine_id,
                "part_id": part_id,
                "quality": quality,
            }
        )
        cycle_time = elapsed
        line = f"part_events,{tags} cycle_time_s={cycle_time:.3f} {t_ns}"
        self._cycle_start_t = t_seconds  # next cycle starts now
        return [line]


@dataclass
class Machine:
    site: str
    line_id: str
    station_id: str
    machine_id: str
    state: MachineState
    temperature: TemperatureSensor
    vibration: VibrationSensor
    parts: PartCount

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        out: list[str] = []
        out.extend(self.state.tick(t_seconds, t_ns))
        out.extend(self.temperature.tick(t_seconds, t_ns))
        out.extend(self.vibration.tick(t_seconds, t_ns))
        # Part-count gates on running state
        if self.state.state() == "running":
            self.parts.set_running(True)
        else:
            self.parts.set_running(False)
        out.extend(self.parts.tick(t_seconds, t_ns))
        return out


@dataclass
class Plant:
    site: str
    machines: list[Machine]

    def tick(self, t_seconds: float, t_ns: int) -> list[str]:
        lines: list[str] = []
        for m in self.machines:
            lines.extend(m.tick(t_seconds, t_ns))
        return lines

    def find(self, machine_id: str) -> Machine:
        for m in self.machines:
            if m.machine_id == machine_id:
                return m
        raise KeyError(machine_id)


def build_plant(
    site: str,
    lines: int,
    stations_per_line: int,
    seed: int,
    nominal_cycle_s: float = 30.0,
) -> Plant:
    machines: list[Machine] = []
    for li in range(1, lines + 1):
        line_id = f"L{li}"
        for si in range(1, stations_per_line + 1):
            station_id = f"S{si}"
            mid = f"{line_id}-{station_id}"
            machine_seed = seed + li * 1000 + si
            machines.append(
                Machine(
                    site=site,
                    line_id=line_id,
                    station_id=station_id,
                    machine_id=mid,
                    state=MachineState(site, line_id, station_id, mid),
                    temperature=TemperatureSensor(
                        site,
                        line_id,
                        station_id,
                        mid,
                        nominal_c=65.0,
                        seed=machine_seed + 1,
                    ),
                    vibration=VibrationSensor(
                        site,
                        line_id,
                        station_id,
                        mid,
                        nominal_mm_s=2.0,
                        seed=machine_seed + 2,
                    ),
                    parts=PartCount(
                        site,
                        line_id,
                        station_id,
                        mid,
                        ideal_cycle_time_s=nominal_cycle_s,
                        seed=machine_seed + 3,
                    ),
                )
            )
    return Plant(site=site, machines=machines)
