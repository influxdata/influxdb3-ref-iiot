"""Unit-test the request andon-board plugin with a recording fake."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_plugin():
    plugin_path = Path(__file__).resolve().parents[2] / "plugins" / "request_andon_board.py"
    spec = importlib.util.spec_from_file_location("request_andon_board", plugin_path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["request_andon_board"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


class CannedInflux:
    def __init__(self, query_responses: dict[str, list[dict]]) -> None:
        self._responses = query_responses
        self.queries: list[str] = []

    def query(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        # Longest matching key wins so more-specific keys take precedence.
        best_key = None
        for key in self._responses:
            if key in sql and (best_key is None or len(key) > len(best_key)):
                best_key = key
        if best_key is not None:
            return self._responses[best_key]
        return []

    def info(self, msg: str) -> None:
        pass


def _machine(line_id: str, station_id: str, state: str) -> dict:
    return {
        "site": "acme-main",
        "line_id": line_id,
        "station_id": station_id,
        "machine_id": f"{line_id}-{station_id}",
        "state": state,
        "reason": "",
    }


def test_returns_three_lines_eight_stations_each():
    mod = _load_plugin()
    machines = []
    for li in ("L1", "L2", "L3"):
        for si in (f"S{i}" for i in range(1, 9)):
            machines.append(_machine(li, si, "running"))
    fake = CannedInflux(
        {
            "FROM machine_state": machines,
            "FROM part_events": [
                {
                    "line_id": "L1",
                    "total_count": 100,
                    "good_count": 99,
                    "running_seconds": 3000,
                    "planned_seconds": 3600,
                },
                {
                    "line_id": "L2",
                    "total_count": 100,
                    "good_count": 99,
                    "running_seconds": 3000,
                    "planned_seconds": 3600,
                },
                {
                    "line_id": "L3",
                    "total_count": 100,
                    "good_count": 99,
                    "running_seconds": 3000,
                    "planned_seconds": 3600,
                },
            ],
            "FROM alerts": [],
        }
    )
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    assert resp["status"] == 200
    body = resp["body"]
    assert len(body["lines"]) == 3
    for line in body["lines"]:
        assert len(line["machines"]) == 8
        assert "oee" in line
        assert "availability" in line
        assert "performance" in line
        assert "quality" in line


def test_machine_state_serialized_per_machine():
    mod = _load_plugin()
    machines = [_machine("L2", "S4", "stopped"), _machine("L1", "S1", "running")]
    fake = CannedInflux(
        {
            "FROM machine_state": machines,
            "FROM part_events": [],
            "FROM alerts": [],
        }
    )
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    by_id = {m["machine_id"]: m for line in body["lines"] for m in line["machines"]}
    assert by_id["L2-S4"]["state"] == "stopped"
    assert by_id["L1-S1"]["state"] == "running"


def test_alerts_array_present_even_when_empty():
    mod = _load_plugin()
    fake = CannedInflux(
        {
            "FROM machine_state": [_machine("L1", "S1", "running")],
            "FROM part_events": [],
            "FROM alerts": [],
        }
    )
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    assert isinstance(body["lines"][0]["alerts"], list)
    assert body["lines"][0]["alerts"] == []  # not None


def test_alerts_filtered_per_line():
    mod = _load_plugin()
    fake = CannedInflux(
        {
            "FROM machine_state": [
                _machine("L1", "S1", "running"),
                _machine("L2", "S4", "stopped"),
            ],
            "FROM part_events": [],
            "FROM alerts": [
                {
                    "time": 1,
                    "line_id": "L2",
                    "machine_id": "L2-S4",
                    "severity": "critical",
                    "reason": "tool_change",
                    "source": "wal_downtime_detector",
                    "value": 0.0,
                },
            ],
        }
    )
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    by_line = {l["line_id"]: l for l in body["lines"]}
    assert len(by_line["L2"]["alerts"]) == 1
    assert by_line["L2"]["alerts"][0]["reason"] == "tool_change"
    assert "L1" not in by_line or len(by_line["L1"]["alerts"]) == 0
