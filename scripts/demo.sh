#!/usr/bin/env bash
# End-to-end scripted demo: stack up → wait for healthy → open browser → run scenarios.
# Usage: scripts/demo.sh [--fresh]

set -euo pipefail
cd "$(dirname "$0")/.."

if [[ "${1:-}" == "--fresh" ]]; then
    echo "[demo] wiping data volume (will require license re-validation)"
    docker compose down -v
fi

./scripts/setup.sh

echo "[demo] starting stack"
docker compose up -d

echo "[demo] waiting for influxdb3 to become healthy (this includes the license-validation pause)"
deadline=$(( $(date +%s) + 300 ))
while [[ $(date +%s) -lt $deadline ]]; do
    if docker compose ps --format json influxdb3 2>/dev/null | grep -q '"Health":"healthy"'; then
        break
    fi
    sleep 3
done

if ! docker compose ps --format json influxdb3 | grep -q '"Health":"healthy"'; then
    echo "[demo] influxdb3 never became healthy — check logs with 'make logs'" >&2
    exit 1
fi

echo "[demo] opening browser to http://localhost:8080"
case "$(uname)" in
    Darwin) open http://localhost:8080 ;;
    Linux)  xdg-open http://localhost:8080 || true ;;
esac

sleep 5
echo "[demo] running unplanned_downtime_cascade scenario"
SCENARIO=unplanned_downtime_cascade docker compose --profile scenarios run --rm scenarios

echo "[demo] running tool_wear_quality_drift scenario (5 min)"
SCENARIO=tool_wear_quality_drift docker compose --profile scenarios run --rm scenarios

echo "[demo] DONE — leave the stack up to explore, or 'make down' when finished"
