"""Unit-test the WAL quality-excursion plugin with a recording fake."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_plugin():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "wal_quality_excursion.py"
    spec = importlib.util.spec_from_file_location("wal_quality_excursion", plugin_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["wal_quality_excursion"] = mod
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


def _row(machine_id: str, quality: str) -> dict:
    line_id, station_id = machine_id.split("-")
    return {
        "site": "acme-main",
        "line_id": line_id,
        "station_id": station_id,
        "machine_id": machine_id,
        "quality": quality,
        "cycle_time_s": 30.0,
        "time": 1,
    }


def _batch(rows: list[dict]) -> dict:
    return {"table_name": "part_events", "rows": rows}


def _reset(mod):
    mod._windows.clear()
    mod._above.clear()


def test_no_alert_below_threshold(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    rows = [_row("L1-S1", "good")] * 19 + [_row("L1-S1", "scrap")] * 1
    mod.process_writes(fake, [_batch(rows)], args={"window": "20", "scrap_threshold": "0.10"})
    assert fake.writes == []  # 1/20 = 0.05 < 0.10


def test_alert_when_threshold_crossed(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    rows = [_row("L1-S6", "good")] * 17 + [_row("L1-S6", "scrap")] * 3
    mod.process_writes(fake, [_batch(rows)], args={"window": "20", "scrap_threshold": "0.10"})
    assert len(fake.writes) == 1  # 3/20 = 0.15 >= 0.10
    w = fake.writes[0]
    assert w.measurement == "alerts"
    assert w.tags["source"] == "wal_quality_excursion"
    assert w.tags["severity"] == "quality"
    assert w.tags["line_id"] == "L1"
    assert w.tags["machine_id"] == "L1-S6"
    assert w.fields["reason"] == "quality_excursion"
    assert w.fields["value"] == 0.15


def test_no_repeat_alert_while_above_threshold(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    bad = [_row("L1-S6", "scrap")] * 5 + [_row("L1-S6", "good")] * 15
    mod.process_writes(fake, [_batch(bad)], args={"window": "20", "scrap_threshold": "0.10"})
    assert len(fake.writes) == 1
    # Another batch keeps scrap rate above threshold
    mod.process_writes(
        fake, [_batch([_row("L1-S6", "scrap")])], args={"window": "20", "scrap_threshold": "0.10"}
    )
    # Still only one alert
    assert len(fake.writes) == 1


def test_re_alerts_after_dropping_below_then_crossing_again(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    bad = [_row("L1-S6", "scrap")] * 5 + [_row("L1-S6", "good")] * 15
    mod.process_writes(fake, [_batch(bad)], args={"window": "20", "scrap_threshold": "0.10"})
    assert len(fake.writes) == 1
    # Push 20 goods through to drop the rate to 0
    mod.process_writes(
        fake,
        [_batch([_row("L1-S6", "good")] * 25)],
        args={"window": "20", "scrap_threshold": "0.10"},
    )
    assert len(fake.writes) == 1  # no new alert below threshold
    # Now cross again
    mod.process_writes(
        fake,
        [_batch([_row("L1-S6", "scrap")] * 5)],
        args={"window": "20", "scrap_threshold": "0.10"},
    )
    assert len(fake.writes) == 2  # new alert fired


def test_per_machine_isolation(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    # L1-S6 crosses; L2-S2 stays clean
    rows = (
        [_row("L1-S6", "scrap")] * 5 + [_row("L1-S6", "good")] * 15 + [_row("L2-S2", "good")] * 20
    )
    mod.process_writes(fake, [_batch(rows)], args={"window": "20", "scrap_threshold": "0.10"})
    assert len(fake.writes) == 1
    assert fake.writes[0].tags["machine_id"] == "L1-S6"


def test_ignores_other_tables(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    _reset(mod)
    fake = RecordingInflux()
    mod.process_writes(fake, [{"table_name": "vibration", "rows": []}], args={})
    assert fake.writes == []
