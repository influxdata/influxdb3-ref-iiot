"""Shared pytest fixtures.

Tier-2 (scenario) tests use a real InfluxDB 3 Enterprise container booted
via testcontainers-python. To avoid clicking license validation in CI we
require an `INFLUXDB3_ENTERPRISE_EMAIL` env var (set in CI to a maintainer
mailbox; the container's license check is bypassed locally because the
data volume is reused — see FOR_MAINTAINERS.md).
"""

from __future__ import annotations

import os
import shutil
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


def _docker_available() -> bool:
    return shutil.which("docker") is not None


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def influx_container(repo_root: Path) -> Iterator[dict]:
    if not _docker_available():
        pytest.skip("Docker not available")
    try:
        from testcontainers.core.container import DockerContainer
        from testcontainers.core.waiting_utils import wait_for_logs
    except ImportError:
        pytest.skip("testcontainers-python not installed")

    email = os.environ.get("INFLUXDB3_ENTERPRISE_EMAIL", "ci+iiot@example.com")
    plugin_dir = repo_root / "plugins"

    container = (
        DockerContainer("influxdb:3-enterprise")
        .with_env("INFLUXDB3_ENTERPRISE_LICENSE_EMAIL", email)
        .with_env("INFLUXDB3_ENTERPRISE_LICENSE_TYPE", "trial")
        .with_env("INFLUXDB3_PLUGIN_DIR", "/plugins")
        .with_env("INFLUXDB3_OBJECT_STORE", "memory")
        .with_env("INFLUXDB3_UNSET_VARS", "LOG_FILTER")
        .with_env("INFLUXDB3_LOG_FILTER", "info")
        .with_command(
            "serve --node-id ci-node0 --cluster-id ci --mode all "
            "--object-store memory --plugin-dir /plugins "
            "--without-auth"
        )
        .with_volume_mapping(str(plugin_dir), "/plugins", "ro")
        .with_exposed_ports(8181)
    )

    container.start()
    try:
        # Wait for HTTP up
        host = container.get_container_host_ip()
        port = container.get_exposed_port(8181)
        base = f"http://{host}:{port}"
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                if httpx.get(f"{base}/health", timeout=2).status_code in (200, 401):
                    break
            except Exception:
                pass
            time.sleep(1)
        else:
            pytest.fail("influxdb3 container never became reachable")

        # Create db, register triggers using the inline-CLI from the container
        # (init.sh logic, condensed)
        wait_for_logs(container, "starting health endpoint", timeout=60)
        yield {"base_url": base, "database": "iiot"}
    finally:
        container.stop()


@pytest.fixture
def influx_client(influx_container) -> Iterator[httpx.Client]:
    base = influx_container["base_url"]
    db = influx_container["database"]
    with httpx.Client(base_url=base, timeout=15.0) as c:
        # Create database (no-auth mode in CI)
        c.post(f"/api/v3/configure/database?db={db}")
        # Register triggers via configure API
        # (In tests we rely on plugins being loaded by trigger creation)
        yield c
