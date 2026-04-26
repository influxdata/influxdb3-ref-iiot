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
    def __init__(self, query_responses) -> None:
        # Keys may be a single substring (str) or a tuple of substrings
        # that must ALL be present in the SQL. The total matched-character
        # count is the score; highest score wins, so multi-fragment keys
        # naturally beat broader single-substring keys.
        self._responses = query_responses
        self.queries: list[str] = []

    def query(self, sql: str) -> list[dict]:
        self.queries.append(sql)
        best_key, best_score = None, -1
        for key in self._responses:
            fragments = key if isinstance(key, tuple) else (key,)
            if all(f in sql for f in fragments):
                score = sum(len(f) for f in fragments)
                if score > best_score:
                    best_key, best_score = key, score
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


def _state_agg(line_ids: tuple[str, ...]) -> list[dict]:
    return [
        {"line_id": lid, "running_seconds": 3000, "planned_seconds": 3600} for lid in line_ids
    ]


def _parts_agg(line_ids: tuple[str, ...]) -> list[dict]:
    return [{"line_id": lid, "total_count": 100, "good_count": 99} for lid in line_ids]


def _state_hist(line_ids: tuple[str, ...]) -> list[dict]:
    # Two synthetic minute-buckets per line.
    return [
        {"line_id": lid, "bucket": b, "running_seconds": 480, "planned_seconds": 480}
        for lid in line_ids
        for b in ("2026-04-26T13:00:00Z", "2026-04-26T13:01:00Z")
    ]


def _parts_hist(line_ids: tuple[str, ...]) -> list[dict]:
    return [
        {"line_id": lid, "bucket": b, "total_count": 16, "good_count": 16}
        for lid in line_ids
        for b in ("2026-04-26T13:00:00Z", "2026-04-26T13:01:00Z")
    ]


def _routes(line_ids: tuple[str, ...], machines: list[dict], alerts: list[dict] | None = None):
    return {
        "FROM last_cache": machines,
        ("FROM machine_state", "INTERVAL '8 hours'"): _state_agg(line_ids),
        ("FROM part_events", "INTERVAL '8 hours'"): _parts_agg(line_ids),
        ("FROM machine_state", "INTERVAL '60 minutes'"): _state_hist(line_ids),
        ("FROM part_events", "INTERVAL '60 minutes'"): _parts_hist(line_ids),
        "FROM alerts": alerts or [],
    }


def test_returns_three_lines_eight_stations_each():
    mod = _load_plugin()
    machines = []
    for li in ("L1", "L2", "L3"):
        for si in (f"S{i}" for i in range(1, 9)):
            machines.append(_machine(li, si, "running"))
    fake = CannedInflux(_routes(("L1", "L2", "L3"), machines))
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
        assert "history" in line
        assert isinstance(line["history"], list)


def test_machine_state_serialized_per_machine():
    mod = _load_plugin()
    machines = [_machine("L2", "S4", "stopped"), _machine("L1", "S1", "running")]
    fake = CannedInflux(_routes(("L1", "L2"), machines))
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    by_id = {m["machine_id"]: m for line in body["lines"] for m in line["machines"]}
    assert by_id["L2-S4"]["state"] == "stopped"
    assert by_id["L1-S1"]["state"] == "running"


def test_alerts_array_present_even_when_empty():
    mod = _load_plugin()
    fake = CannedInflux(_routes(("L1",), [_machine("L1", "S1", "running")]))
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    assert isinstance(body["lines"][0]["alerts"], list)
    assert body["lines"][0]["alerts"] == []  # not None


def test_alerts_filtered_per_line():
    mod = _load_plugin()
    machines = [_machine("L1", "S1", "running"), _machine("L2", "S4", "stopped")]
    alerts = [
        {
            "time": 1,
            "line_id": "L2",
            "machine_id": "L2-S4",
            "severity": "critical",
            "reason": "tool_change",
            "source": "wal_downtime_detector",
            "value": 0.0,
        },
    ]
    fake = CannedInflux(_routes(("L1", "L2"), machines, alerts))
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    body = resp["body"]
    by_line = {l["line_id"]: l for l in body["lines"]}
    assert len(by_line["L2"]["alerts"]) == 1
    assert by_line["L2"]["alerts"][0]["reason"] == "tool_change"
    assert "L1" not in by_line or len(by_line["L1"]["alerts"]) == 0


def test_history_includes_per_minute_buckets():
    mod = _load_plugin()
    machines = [_machine("L1", "S1", "running")]
    fake = CannedInflux(_routes(("L1",), machines))
    resp = mod.process_request(fake, query_parameters={}, request_headers={}, request_body=b"")
    line = resp["body"]["lines"][0]
    assert len(line["history"]) == 2  # two synthetic buckets in fake
    for h in line["history"]:
        assert "bucket" in h
        assert "availability" in h
        assert "performance" in h
        assert "quality" in h
        # _state_hist provides running=480 planned=480 → A=1.0
        assert h["availability"] == 1.0
        # _parts_hist provides total=16 good=16 (16 * 30s ideal = 480 = running) → P=1.0, Q=1.0
        assert h["performance"] == 1.0
        assert h["quality"] == 1.0
