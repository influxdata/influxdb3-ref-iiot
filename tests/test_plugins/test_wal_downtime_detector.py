"""Unit-test the WAL downtime-detector plugin with a recording fake."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_plugin():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "wal_downtime_detector.py"
    spec = importlib.util.spec_from_file_location("wal_downtime_detector", plugin_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["wal_downtime_detector"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class FakeLineBuilder:
    def __init__(self, measurement: str) -> None:
        self.measurement = measurement
        self.tags: dict[str, str] = {}
        self.fields: dict[str, object] = {}

    def tag(self, k: str, v: str):
        self.tags[k] = v
        return self

    def string_field(self, k: str, v: str):
        self.fields[k] = v
        return self

    def float64_field(self, k: str, v: float):
        self.fields[k] = v
        return self


class RecordingInflux:
    def __init__(self) -> None:
        self.writes: list[FakeLineBuilder] = []
        self.logs: list[tuple[str, str]] = []

    def write(self, line: FakeLineBuilder) -> None:
        self.writes.append(line)

    def info(self, msg: str) -> None:
        self.logs.append(("info", msg))


def _row(machine_id: str, state: str, reason: str = "") -> dict:
    line_id, station_id = machine_id.split("-")
    return {
        "site": "acme-main",
        "line_id": line_id,
        "station_id": station_id,
        "machine_id": machine_id,
        "state": state,
        "reason": reason,
        "time": 1,
    }


def _batch(rows: list[dict]) -> dict:
    return {"table_name": "machine_state", "rows": rows}


def test_no_alert_when_state_stays_running(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    assert fake.writes == []


def test_alert_on_running_to_stopped(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    # First batch: machine is running (no alert)
    mod.process_writes(fake, [_batch([_row("L2-S4", "running")])])
    # Second batch: machine transitions to stopped
    mod.process_writes(fake, [_batch([_row("L2-S4", "stopped", "tool_change")])])
    assert len(fake.writes) == 1
    w = fake.writes[0]
    assert w.measurement == "alerts"
    assert w.tags["source"] == "wal_downtime_detector"
    assert w.tags["severity"] == "critical"
    assert w.tags["line_id"] == "L2"
    assert w.tags["machine_id"] == "L2-S4"
    assert w.fields["reason"] == "tool_change"


def test_alert_on_running_to_error(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "error", "spindle_fault")])])
    assert len(fake.writes) == 1
    assert fake.writes[0].tags["severity"] == "critical"


def test_no_alert_when_already_stopped(monkeypatch):
    """Repeated stopped writes should not re-emit alerts."""
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "stopped", "x")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "stopped", "x")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "stopped", "x")])])
    assert len(fake.writes) == 1


def test_no_alert_for_changeover(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "changeover", "sku_change")])])
    assert fake.writes == []


def test_no_alert_for_planned_maintenance(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [_batch([_row("L1-S1", "running")])])
    mod.process_writes(fake, [_batch([_row("L1-S1", "planned_maintenance", "lube")])])
    assert fake.writes == []


def test_ignores_other_tables(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    monkeypatch.setattr(mod, "_prev_state", {}, raising=False)
    fake = RecordingInflux()
    mod.process_writes(fake, [{"table_name": "vibration", "rows": [{"machine_id": "L1-S1"}]}])
    assert fake.writes == []
