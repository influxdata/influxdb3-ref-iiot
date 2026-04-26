"""HTTP line-protocol writer for InfluxDB 3.

Batches writes to reduce request count. The transport is injectable so unit
tests can capture payloads without a real HTTP roundtrip.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class Transport(Protocol):
    def post(self, url: str, content: str, headers: dict[str, str]) -> None: ...


class _HttpxTransport:
    def __init__(self, timeout_s: float = 10.0) -> None:
        self._client = httpx.Client(timeout=timeout_s)

    def post(self, url: str, content: str, headers: dict[str, str]) -> None:
        r = self._client.post(url, content=content, headers=headers)
        r.raise_for_status()


class InfluxDB3Writer:
    def __init__(
        self,
        url: str,
        database: str,
        token: str,
        batch_size: int = 1000,
        transport: Transport | None = None,
    ) -> None:
        self._url = f"{url.rstrip('/')}/api/v3/write_lp?db={database}&precision=nanosecond"
        self._token = token
        self._batch_size = batch_size
        self._buf: list[str] = []
        self._transport: Transport = transport or _HttpxTransport()

    def write(self, line: str) -> None:
        self._buf.append(line)
        if len(self._buf) >= self._batch_size:
            self.flush()

    def flush(self) -> None:
        if not self._buf:
            return
        payload = "\n".join(self._buf)
        self._transport.post(
            self._url,
            content=payload,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "text/plain; charset=utf-8",
            },
        )
        self._buf.clear()
