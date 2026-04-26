"""Tier-3 smoke test: bring up the full compose stack, query a few key metrics, tear down.

Requires:
  - Docker available
  - INFLUXDB3_ENTERPRISE_EMAIL set in env (or .env)
  - License-validated influxdb-data volume (see FOR_MAINTAINERS.md)

Marked with `smoke`; run via `make test-smoke`. Slow (~3 min).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_URL = "http://127.0.0.1:8080"
INFLUX_URL = "http://127.0.0.1:8181"


def _docker_compose() -> list[str]:
    return ["docker", "compose"]


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, cwd=REPO_ROOT, check=True, capture_output=True, text=True, **kw)


@pytest.fixture(scope="module")
def stack_up():
    if shutil.which("docker") is None:
        pytest.skip("docker not available")
    if not os.environ.get("INFLUXDB3_ENTERPRISE_EMAIL"):
        pytest.skip("INFLUXDB3_ENTERPRISE_EMAIL not set")
    _run([*_docker_compose(), "up", "-d"])
    try:
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                if httpx.get(f"{INFLUX_URL}/health", timeout=2).status_code in (200, 401):
                    break
            except Exception:
                pass
            time.sleep(2)
        else:
            pytest.fail("influxdb3 never reached /health (license validation needed?)")
        # Give simulator + plugins ~30s to produce data
        time.sleep(30)
        yield
    finally:
        _run([*_docker_compose(), "down"])


def _query(sql: str) -> list[dict]:
    token_path = REPO_ROOT / ".smoke-token"  # written by setup, optional
    if token_path.exists():
        token = token_path.read_text().strip()
    else:
        token = ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    r = httpx.post(
        f"{INFLUX_URL}/api/v3/query_sql",
        json={"db": "iiot", "q": sql, "format": "json"},
        headers=headers, timeout=10.0,
    )
    r.raise_for_status()
    return r.json() or []


def test_simulator_writing_machine_state(stack_up):
    rows = _query("SELECT COUNT(*) AS n FROM machine_state WHERE time > now() - INTERVAL '1 minute'")
    assert int(rows[0]["n"]) > 0


def test_lvc_returns_24_machines(stack_up):
    rows = _query("SELECT machine_id FROM machine_state WHERE site = 'acme-main' ORDER BY machine_id")
    assert len(rows) == 24


def test_andon_endpoint_reachable(stack_up):
    r = httpx.get(f"{INFLUX_URL}/api/v3/engine/andon_board", timeout=10.0)
    # Without auth it'll 401 — that's still "reachable"
    assert r.status_code in (200, 401)


def test_ui_root_loads(stack_up):
    r = httpx.get(UI_URL, timeout=10.0)
    assert r.status_code == 200
    assert "Andon board" in r.text or "andon" in r.text.lower()


def test_ui_kpi_partial_returns_html(stack_up):
    r = httpx.get(f"{UI_URL}/partials/kpi_row", timeout=10.0)
    assert r.status_code == 200
    assert "Plant OEE" in r.text
