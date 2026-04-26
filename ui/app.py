"""FastAPI app serving the IIoT dashboard.

Routes:
  GET /              -> overview shell (Jinja2)
  GET /partials/...  -> HTMX-loaded partials (KPI row, plant state, OEE
                        breakdown, recent alerts)
  /static/*          -> bundled assets

The andon-board panel is loaded as a partial too, but its DATA fetch goes
directly to the InfluxDB Processing-Engine endpoint via JS (see app.js)
so that the "served by Processing Engine" timing badge measures only
the round-trip to the database, not a backend hop.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ui import queries

ROOT = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))

INFLUX_URL = os.environ.get("INFLUX_URL", "http://influxdb3:8181")
# Browser-facing URL for direct fetches (the andon board panel calls the Processing
# Engine endpoint from the browser). Defaults to localhost so the dev `make up` flow
# works with the bundled docker-compose port mapping (8181).
INFLUX_PUBLIC_URL = os.environ.get("INFLUX_PUBLIC_URL", "http://localhost:8181")
INFLUX_DB = os.environ.get("INFLUX_DB", "iiot")
TOKEN_FILE = os.environ.get("INFLUX_TOKEN_FILE", "/tokens/.iiot-operator-token")


def _load_token() -> str:
    if "INFLUXDB3_TOKEN" in os.environ:
        return os.environ["INFLUXDB3_TOKEN"]
    with open(TOKEN_FILE) as f:
        return json.load(f)["token"]


app = FastAPI()
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=INFLUX_URL,
        headers={"Authorization": f"Bearer {_load_token()}"},
        timeout=10.0,
    )


def _query(sql: str) -> list[dict]:
    with _client() as c:
        r = c.post(
            "/api/v3/query_sql",
            json={"db": INFLUX_DB, "q": sql, "format": "json"},
        )
        r.raise_for_status()
        return r.json() or []


def _poll_intervals() -> dict[str, int]:
    return {
        "kpi": int(os.environ.get("UI_KPI_POLL_MS", "2000")),
        "andon": int(os.environ.get("UI_ANDON_POLL_MS", "2000")),
        "oee": int(os.environ.get("UI_OEE_POLL_MS", "5000")),
        "alerts": int(os.environ.get("UI_ALERTS_POLL_MS", "3000")),
    }


@app.get("/", response_class=HTMLResponse)
def overview(request: Request) -> HTMLResponse:
    return TEMPLATES.TemplateResponse(
        "overview.html",
        {
            "request": request,
            "poll": _poll_intervals(),
            "andon_url": f"{INFLUX_PUBLIC_URL}/api/v3/engine/andon_board",
            "andon_token": _load_token(),
        },
    )


@app.get("/partials/plant_state", response_class=HTMLResponse)
def plant_state(request: Request) -> HTMLResponse:
    rows = _query(queries.plant_state_sql())
    state_counts = {
        "running": 0,
        "stopped": 0,
        "error": 0,
        "idle": 0,
        "changeover": 0,
        "planned_maintenance": 0,
    }
    for r in rows:
        s = r.get("state")
        if s in state_counts:
            state_counts[s] += 1
    if state_counts["error"] > 0:
        plant_state = "DOWN"
    elif state_counts["stopped"] > 0:
        plant_state = "DEGRADED"
    else:
        plant_state = "RUNNING"
    return TEMPLATES.TemplateResponse(
        "partials/_plant_state.html",
        {"request": request, "plant_state": plant_state, "counts": state_counts},
    )


@app.get("/partials/kpi_row", response_class=HTMLResponse)
def kpi_row(request: Request) -> HTMLResponse:
    units = _query(queries.kpi_units_last_window_sql(60))
    alerts = _query(queries.kpi_alerts_last_window_sql(60))
    distinct = _query(queries.kpi_distinct_parts_today_sql())
    oee = _query(queries.kpi_plant_oee_current_shift_sql())
    units_n = int((units[0] or {}).get("units", 0)) if units else 0
    alerts_n = int((alerts[0] or {}).get("active_alerts", 0)) if alerts else 0
    distinct_n = int((distinct[0] or {}).get("distinct_parts", 0)) if distinct else 0
    if oee:
        a = float(oee[0].get("availability") or 0.0)
        p = float(oee[0].get("performance") or 0.0)
        q = float(oee[0].get("quality") or 0.0)
        plant_oee = a * p * q
    else:
        plant_oee = 0.0
    return TEMPLATES.TemplateResponse(
        "partials/_kpi_row.html",
        {
            "request": request,
            "plant_oee_pct": round(plant_oee * 100, 1),
            "units_last_hour": units_n,
            "active_alerts": alerts_n,
            "distinct_parts_today": distinct_n,
        },
    )


@app.get("/partials/andon_board", response_class=HTMLResponse)
def andon_board(request: Request) -> HTMLResponse:
    """Server-rendered shell with the JS hook; the data fetch happens in app.js."""
    return TEMPLATES.TemplateResponse("partials/_andon_board.html", {"request": request})


@app.get("/partials/oee_breakdown", response_class=JSONResponse)
def oee_breakdown() -> JSONResponse:
    a = _query(queries.per_line_availability_sql(60))
    p = _query(queries.per_line_performance_sql(60))
    q = _query(queries.per_line_quality_sql(60))
    return JSONResponse({"availability": a, "performance": p, "quality": q})


@app.get("/partials/alerts", response_class=HTMLResponse)
def alerts(request: Request) -> HTMLResponse:
    rows = _query(queries.recent_alerts_sql(50))
    return TEMPLATES.TemplateResponse("partials/_alerts.html", {"request": request, "alerts": rows})
