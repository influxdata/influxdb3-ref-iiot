"""Tests for the line-protocol HTTP writer."""

from __future__ import annotations

from simulator.writer import InfluxDB3Writer


class _RecordingTransport:
    def __init__(self) -> None:
        self.payloads: list[str] = []

    def post(self, url: str, content: str, headers: dict[str, str]) -> None:
        self.payloads.append(content)


def test_writer_batches_lines_until_flush():
    t = _RecordingTransport()
    w = InfluxDB3Writer(
        url="http://x", database="iiot", token="t",
        batch_size=3, transport=t,
    )
    w.write("a,t=1 v=1 1")
    w.write("a,t=1 v=2 2")
    assert t.payloads == []  # not yet
    w.write("a,t=1 v=3 3")
    assert len(t.payloads) == 1
    assert "v=1" in t.payloads[0] and "v=3" in t.payloads[0]


def test_writer_flush_emits_remaining():
    t = _RecordingTransport()
    w = InfluxDB3Writer(
        url="http://x", database="iiot", token="t",
        batch_size=10, transport=t,
    )
    w.write("a,t=1 v=1 1")
    w.flush()
    assert len(t.payloads) == 1
    assert "v=1" in t.payloads[0]


def test_writer_flush_is_noop_when_empty():
    t = _RecordingTransport()
    w = InfluxDB3Writer(
        url="http://x", database="iiot", token="t",
        batch_size=10, transport=t,
    )
    w.flush()
    assert t.payloads == []
