"""Unit-test the schedule shift-summary plugin with a recording fake.

The plugin runs SQL via influxdb3_local.query(); the fake returns canned rows.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime
from pathlib import Path


def _load_plugin():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "schedule_shift_summary.py"
    spec = importlib.util.spec_from_file_location("schedule_shift_summary", plugin_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["schedule_shift_summary"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class FakeLineBuilder:
    def __init__(self, measurement: str) -> None:
        self.measurement = measurement
        self.tags: dict[str, str] = {}
        self.fields: dict[str, object] = {}

    def tag(self, k, v):
        self.tags[k] = v
        return self

    def string_field(self, k, v):
        self.fields[k] = v
        return self

    def float64_field(self, k, v):
        self.fields[k] = v
        return self


class CannedInflux:
    def __init__(self, query_responses: dict[str, list[dict]]) -> None:
        self._responses = query_responses
        self.queries: list[str] = []
        self.writes: list[FakeLineBuilder] = []
        self.logs: list[tuple[str, str]] = []

    def query(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        # Match by substring of caller's intent. Longest matching key wins so
        # that more-specific keys (e.g. "FROM machine_state WHERE state") take
        # precedence over their prefixes (e.g. "FROM machine_state").
        best_key = None
        for key in self._responses:
            if key in sql and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key is not None:
            return self._responses[best_key]
        return []

    def write(self, line: FakeLineBuilder) -> None:
        self.writes.append(line)

    def info(self, msg: str) -> None:
        self.logs.append(("info", msg))


def test_shift_id_for_call_at_06_is_yesterday_C():
    mod = _load_plugin()
    call = datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC)
    sid, start, end = mod.compute_shift_window(call)
    assert sid == "2026-04-24-C"
    assert start == datetime(2026, 4, 24, 22, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC)


def test_shift_id_for_call_at_14_is_today_A():
    mod = _load_plugin()
    call = datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC)
    sid, start, end = mod.compute_shift_window(call)
    assert sid == "2026-04-25-A"
    assert start == datetime(2026, 4, 25, 6, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC)


def test_shift_id_for_call_at_22_is_today_B():
    mod = _load_plugin()
    call = datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)
    sid, start, end = mod.compute_shift_window(call)
    assert sid == "2026-04-25-B"
    assert start == datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC)
    assert end == datetime(2026, 4, 25, 22, 0, 0, tzinfo=UTC)


def test_writes_one_row_per_line(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    fake = CannedInflux(
        {
            "FROM machine_state": [
                {"line_id": "L1", "running_seconds": 24000, "planned_seconds": 28800},
                {"line_id": "L2", "running_seconds": 14400, "planned_seconds": 28800},
                {"line_id": "L3", "running_seconds": 26000, "planned_seconds": 28800},
            ],
            "FROM part_events": [
                {"line_id": "L1", "total_count": 800, "good_count": 790},
                {"line_id": "L2", "total_count": 480, "good_count": 470},
                {"line_id": "L3", "total_count": 860, "good_count": 850},
            ],
            "FROM machine_state WHERE state": [  # downtime breakdown
                {"line_id": "L1", "reason": "tool_change", "seconds": 600},
                {"line_id": "L1", "reason": "starved", "seconds": 200},
                {"line_id": "L2", "reason": "tool_change", "seconds": 1800},
                {"line_id": "L2", "reason": "starved", "seconds": 1200},
                {"line_id": "L2", "reason": "blocked", "seconds": 800},
                {"line_id": "L3", "reason": "starved", "seconds": 100},
            ],
        }
    )
    call = datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC)
    mod.process_scheduled_call(fake, call, args={"ideal_cycle_s": "30.0"})
    assert len(fake.writes) == 3
    by_line = {w.tags["line_id"]: w for w in fake.writes}
    assert set(by_line.keys()) == {"L1", "L2", "L3"}
    for w in fake.writes:
        assert w.tags["shift_id"] == "2026-04-25-A"
        assert 0.0 <= float(w.fields["oee"]) <= 1.0
        assert 0.0 <= float(w.fields["availability"]) <= 1.0
        assert 0.0 <= float(w.fields["performance"]) <= 1.0
        assert 0.0 <= float(w.fields["quality"]) <= 1.0


def test_oee_is_a_x_p_x_q(monkeypatch):
    mod = _load_plugin()
    monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)
    fake = CannedInflux(
        {
            "FROM machine_state": [
                {"line_id": "L1", "running_seconds": 24000, "planned_seconds": 28800},
            ],
            "FROM part_events": [
                {"line_id": "L1", "total_count": 800, "good_count": 790},
            ],
            "FROM machine_state WHERE state": [],
        }
    )
    call = datetime(2026, 4, 25, 14, 0, 0, tzinfo=UTC)
    mod.process_scheduled_call(fake, call, args={"ideal_cycle_s": "30.0"})
    w = fake.writes[0]
    a = float(w.fields["availability"])
    p = float(w.fields["performance"])
    q = float(w.fields["quality"])
    assert abs(a - 24000 / 28800) < 1e-9
    assert abs(p - min(1.0, (30.0 * 800) / 24000)) < 1e-9
    assert abs(q - 790 / 800) < 1e-9
    assert abs(float(w.fields["oee"]) - (a * p * q)) < 1e-9
