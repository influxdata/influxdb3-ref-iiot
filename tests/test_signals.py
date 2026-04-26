"""Tests for IIoT domain signals."""

from __future__ import annotations

from simulator.signals import (
    MachineState,
    PartCount,
    TemperatureSensor,
    VibrationSensor,
    build_plant,
)


def _parse_line(lp: str) -> tuple[str, dict[str, str], dict[str, str], int]:
    """Minimal line-protocol parser for test assertions.

    Returns (measurement, tags, fields, timestamp_ns).
    """
    measurement_and_tags, rest = lp.split(" ", 1)
    fields_and_ts = rest.rsplit(" ", 1)
    fields_str, ts_str = fields_and_ts[0], fields_and_ts[1]
    parts = measurement_and_tags.split(",")
    measurement = parts[0]
    tags = dict(p.split("=", 1) for p in parts[1:])
    fields = dict(p.split("=", 1) for p in fields_str.split(","))
    return measurement, tags, fields, int(ts_str)


def test_machine_state_emits_one_line_per_tick():
    m = MachineState(site="acme-main", line_id="L1", station_id="S1", machine_id="L1-S1")
    lines = m.tick(t_seconds=0.0, t_ns=1_000_000_000)
    assert len(lines) == 1
    measurement, tags, fields, ts = _parse_line(lines[0])
    assert measurement == "machine_state"
    assert tags["machine_id"] == "L1-S1"
    assert fields["state"] in (
        '"running"',
        '"idle"',
        '"stopped"',
        '"error"',
        '"changeover"',
        '"planned_maintenance"',
    )
    assert ts == 1_000_000_000


def test_machine_state_default_is_running():
    m = MachineState(site="acme-main", line_id="L1", station_id="S1", machine_id="L1-S1")
    lines = m.tick(t_seconds=0.0, t_ns=1_000_000_000)
    _, _, fields, _ = _parse_line(lines[0])
    assert fields["state"] == '"running"'


def test_machine_state_can_be_overridden():
    m = MachineState(site="acme-main", line_id="L1", station_id="S1", machine_id="L1-S1")
    m.set_state("stopped", reason="tool_change")
    lines = m.tick(t_seconds=0.0, t_ns=1_000_000_000)
    _, _, fields, _ = _parse_line(lines[0])
    assert fields["state"] == '"stopped"'
    assert fields["reason"] == '"tool_change"'


def test_temperature_sensor_emits_value_near_nominal():
    t = TemperatureSensor(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        nominal_c=65.0,
        seed=1,
    )
    samples = [float(_parse_line(t.tick(i, i * 1_000_000_000)[0])[2]["temp_c"]) for i in range(200)]
    avg = sum(samples) / len(samples)
    assert 60.0 <= avg <= 70.0  # drift bounded


def test_vibration_sensor_emits_10_lines_per_tick():
    v = VibrationSensor(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        nominal_mm_s=2.0,
        seed=1,
    )
    lines = v.tick(t_seconds=0.0, t_ns=1_000_000_000)
    assert len(lines) == 10  # 10 Hz
    for lp in lines:
        m, tags, fields, _ = _parse_line(lp)
        assert m == "vibration"
        assert "rms_mm_s" in fields


def test_vibration_sensor_responds_to_override():
    v = VibrationSensor(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        nominal_mm_s=2.0,
        seed=1,
    )
    v.set_target(4.5)  # tool wear scenario
    lines = v.tick(t_seconds=0.0, t_ns=1_000_000_000)
    avg = sum(float(_parse_line(lp)[2]["rms_mm_s"]) for lp in lines) / len(lines)
    # noise std is small; average should be close to target
    assert 4.0 <= avg <= 5.0


def test_part_count_emits_when_cycle_completes():
    pc = PartCount(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        ideal_cycle_time_s=2.0,
        seed=42,
    )
    # Tick once at t=0: starts a part, no line emitted
    assert pc.tick(t_seconds=0.0, t_ns=0) == []
    # Tick at t=2.0: cycle completes
    lines = pc.tick(t_seconds=2.0, t_ns=2_000_000_000)
    assert len(lines) == 1
    m, tags, fields, _ = _parse_line(lines[0])
    assert m == "part_events"
    assert "part_id" in tags
    assert tags["quality"] in ("good", "scrap")
    assert "cycle_time_s" in fields


def test_part_count_emits_no_parts_when_machine_stopped():
    pc = PartCount(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        ideal_cycle_time_s=2.0,
        seed=42,
    )
    pc.set_running(False)
    assert pc.tick(t_seconds=0.0, t_ns=0) == []
    assert pc.tick(t_seconds=10.0, t_ns=10_000_000_000) == []


def test_part_count_scrap_rate_default_is_low():
    pc = PartCount(
        site="acme-main",
        line_id="L1",
        station_id="S1",
        machine_id="L1-S1",
        ideal_cycle_time_s=1.0,
        seed=42,
    )
    qualities = []
    for i in range(2000):
        lines = pc.tick(t_seconds=i, t_ns=i * 1_000_000_000)
        for lp in lines:
            _, tags, _, _ = _parse_line(lp)
            qualities.append(tags["quality"])
    scrap_rate = qualities.count("scrap") / len(qualities)
    assert 0.0 < scrap_rate < 0.05  # nominal ~1%


def test_build_plant_creates_24_machines():
    plant = build_plant(site="acme-main", lines=3, stations_per_line=8, seed=42)
    assert len(plant.machines) == 24
    line_ids = {m.line_id for m in plant.machines}
    assert line_ids == {"L1", "L2", "L3"}
    assert all(len([m for m in plant.machines if m.line_id == lid]) == 8 for lid in line_ids)


def test_plant_tick_returns_lines_from_all_signals():
    plant = build_plant(site="acme-main", lines=3, stations_per_line=8, seed=42)
    lines = plant.tick(t_seconds=0.0, t_ns=0)
    # 24 machines × (1 state + 1 temp + 10 vibration) = 288 lines minimum
    # Part events are 0 at t=0 (cycles haven't completed yet)
    assert len(lines) == 24 * 12
