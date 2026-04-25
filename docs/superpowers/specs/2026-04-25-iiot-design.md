# `influxdb3-ref-iiot` — IIoT / Factory Floor Monitoring Design

**Date:** 2026-04-25
**Status:** Approved design. Implementation plan to follow in `influxdb3-reference-architectures/docs/superpowers/plans/`.
**Inherits from:** [`influxdb3-reference-architectures` portfolio design (2026-04-23)](https://github.com/influxdata/influxdb3-reference-architectures/blob/main/docs/superpowers/specs/2026-04-23-reference-architectures-portfolio-design.md).
**Pattern reference:** [`influxdb3-ref-bess`](https://github.com/influxdata/influxdb3-ref-bess).

## 1. Goals

Ship a complete, runnable reference architecture that:

1. Boots end-to-end with `git clone && make up` in under two minutes (after one-time license validation).
2. Demonstrates how InfluxDB 3 Enterprise — ingest, Last Value Cache, Distinct Value Cache, Processing Engine (WAL/Schedule/Request triggers), and the InfluxDB 3 query engine — solve real factory-floor problems without a separate application backend.
3. Tells a coherent IIoT story (assembly-plant monitoring, OEE tracking, downtime alerting, shift reporting) that a developer or AI coding agent can adapt to their own factory data.

The repo serves both human developers evaluating InfluxDB 3 Enterprise for IIoT and AI coding agents grounding their work in a concrete vertical example.

## 2. Domain model

**Automotive-style discrete assembly plant.**

| Level | Count | Tag |
|-------|-------|-----|
| Plant (site) | 1 (`acme-main`) | `site` |
| Lines | 3 (`L1`, `L2`, `L3`) | `line_id` |
| Stations per line | 8 (`S1`–`S8`) | `station_id` |
| Machine per station | 1 (`<line>-<station>`, e.g. `L2-S4`) | `machine_id` |
| Total machines | **24** | — |

Vocabulary used throughout the demo, docs, and UI: **plant, line, station, machine, cycle time, downtime reason, shift, OEE, andon, scrap rate, ideal cycle time**.

### 2.1 Per-machine signals

| Signal | Rate | Source class |
|--------|------|--------------|
| State (running / idle / stopped / error / changeover / planned_maintenance) | 1 Hz (every tick, even when unchanged) | `MachineState` |
| Temperature (°C) | 1 Hz | `TemperatureSensor` |
| Vibration RMS (mm/s) | 10 Hz | `VibrationSensor` |
| Part completion event (with `quality` ∈ {good, scrap}) | event-driven, ~1 part/s nominal | `PartCount` |

Aggregate write rate at default config: ~300 points/sec.

### 2.2 Per-machine config (in simulator)

Each machine has a small per-machine config block:

| Field | Example | Purpose |
|-------|---------|---------|
| `ideal_cycle_time_s` | 30.0 | OEE Performance denominator. |
| `nominal_temp_c` | 65.0 | Steady-state for `TemperatureSensor`. |
| `nominal_vibration_mm_s` | 2.0 | Steady-state for `VibrationSensor`. |
| `shift_pattern` | `06-14-22 UTC` | Drives `planned_maintenance` windows in non-scenario operation. |

This is hard-coded for the demo; production deployments would pull this from a master-data system.

### 2.3 OEE definition

The textbook formula: **OEE = Availability × Performance × Quality.**

- **Availability** = `running_seconds / planned_seconds`
  - `planned_seconds` excludes time in `changeover` and `planned_maintenance` states.
  - `running_seconds` is time in `running` state only. Time in `stopped`, `error`, `idle` counts against availability.
- **Performance** = `(ideal_cycle_time_s × total_count) / running_seconds`, capped at 1.0.
  - `total_count` = all part events in the window.
- **Quality** = `good_count / total_count`
  - `good_count` = part events with `quality=good`.

OEE is computed **per-line** for KPIs and the andon board (the line is the smallest unit a plant manager cares about) and **per-machine** for the per-line OEE breakdown chart and shift summaries.

### 2.4 Shift definition

Three 8-hour shifts per day, UTC: **06:00–14:00 (A), 14:00–22:00 (B), 22:00–06:00 (C)**. Each shift has a `shift_id` of the form `YYYY-MM-DD-A`. Shift A's `shift_id` uses the date the shift started (so the C shift that begins at 22:00 on 2026-04-25 is `2026-04-25-C`, even though it ends after midnight).

This is documented prominently in `ARCHITECTURE.md` because shift conventions vary by industry.

## 3. Schema

Four ingest tables, one alert table, one rollup table.

| Table | Tags | Fields | Source |
|-------|------|--------|--------|
| `machine_state` | site, line_id, station_id, machine_id | state (string), reason (string) | simulator (1 Hz per machine) |
| `temperature` | site, line_id, station_id, machine_id | temp_c (f64) | simulator (1 Hz per machine) |
| `vibration` | site, line_id, station_id, machine_id | rms_mm_s (f64) | simulator (10 Hz per machine) |
| `part_events` | site, line_id, station_id, machine_id, part_id, quality | cycle_time_s (f64) | simulator (event-driven, ~1/s/machine) |
| `alerts` | source, severity, line_id, machine_id | reason (string), value (f64) | plugins (`wal_downtime_detector`, `wal_quality_excursion`) |
| `shift_summary` | line_id, shift_id | oee, availability, performance, quality, units_total, units_good (f64), downtime_top1_reason, downtime_top2_reason, downtime_top3_reason (string), downtime_top1_seconds, downtime_top2_seconds, downtime_top3_seconds (f64) | plugin (`schedule_shift_summary`) |

### 3.1 Caches

- **Last Value Cache** on `machine_state` keyed by `(site, line_id, station_id, machine_id)`. 24-row lookup serves the andon board grid and the plant-state banner in sub-millisecond.
- **Distinct Value Cache** on `part_events.part_id`. Powers the "distinct parts today" KPI tile and tag-completion in queries; demonstrates DVC value at high event cardinality (~700K parts/day at default config).

### 3.2 Retention

No retention policy in the demo (same as bess) — the simulator writes at a modest rate and we want readers to see accumulated history for the per-line OEE charts. `ARCHITECTURE.md` includes a "scaling to production" subsection with typical IIoT retention (raw signals 30 days, `shift_summary` indefinite).

## 4. Processing Engine plugins

Four plugins, each in its own file under `plugins/`. Each file opens with the required header comment (purpose, binding, side effects).

| File | Trigger | Binding | Effect |
|------|---------|---------|--------|
| `wal_downtime_detector.py` | WAL | `table:machine_state` | On state→`stopped` or `error`, write a row to `alerts` with the downtime reason. State transitions to/from `changeover` and `planned_maintenance` are ignored. |
| `wal_quality_excursion.py` | WAL | `table:part_events`, args=`window=20,scrap_threshold=0.10` | Maintains a per-machine rolling window of the last `window` part events. When `(scrap_count / window) >= scrap_threshold`, writes a quality alert to `alerts`. |
| `schedule_shift_summary.py` | Schedule | `cron:0 0 6,14,22 * * *` (6-field, UTC shift boundaries) | At each shift boundary, computes per-line OEE for the shift that just ended and writes one row per line to `shift_summary`. |
| `request_andon_board.py` | Request | `request:andon_board` | Serves `GET /api/v3/engine/andon_board`. Returns full plant view as JSON: every line, every machine, current state, current-shift OEE, active alerts. |

### 4.1 Pattern decisions

- **Two WAL plugins, intentionally.** They demonstrate two different WAL-trigger patterns: instant transition-detect (`wal_downtime_detector` reads only the rows in the current write batch) vs windowed/derivative (`wal_quality_excursion` keeps per-machine state across batches). A repo with only one WAL plugin can't teach this contrast.
- **Six-field cron strings** are required by the Processing Engine (`<sec> <min> <hour> <dom> <mon> <dow>`). The shift-boundary cron is therefore `0 0 6,14,22 * * *`, not `0 6,14,22 * * *`. This gotcha lives in `ARCHITECTURE.md` § "Plugin conventions".
- **`LineBuilder` is injected, not imported.** Same gotcha as bess. Documented in plugin file headers and in `ARCHITECTURE.md`.
- **Plugin state.** `wal_quality_excursion` needs cross-batch memory. It uses an in-process dict keyed by `machine_id`. This is fine for the demo but `ARCHITECTURE.md` calls out the caveat for production (state lost on restart; no cross-replica coordination).
- **Auto-installation.** `influxdb/init.sh` registers all four triggers on first boot; binding details are read from each plugin's header block per the portfolio convention.

## 5. Scenarios

Two scenarios, mapping 1:1 to the two WAL plugins.

### 5.1 `unplanned_downtime_cascade`

**One-line description (for `make scenario list`):** "A station on Line 2 stops; upstream starves, downstream blocks, line OEE plummets."

**Steps:**
1. Wait 10 s for baseline.
2. Set `L2-S4` state → `stopped`, reason → `tool_change`.
3. Hold for 60 s. The simulator ripples the state-change effect through Line 2: stations S1–S3 progress to `idle` (starved of upstream parts within ~5 s); stations S5–S8 progress to `idle` (blocked downstream within ~5 s as their input buffers drain). Part events cease across Line 2.
4. Restore `L2-S4` to `running`.
5. Print observable assertions: at least one `alerts` row written by `wal_downtime_detector` within 1 s of step 2; line-2 OEE in the andon board drops below 0.5 within 30 s.

**Plugin exercised:** `wal_downtime_detector` (transition-detect WAL pattern).

### 5.2 `tool_wear_quality_drift`

**One-line description:** "Vibration on Line 1 Station 6 trends up over 5 min; cycle time slows, scrap rate rises, quality alert fires."

**Steps:**
1. Wait 10 s for baseline.
2. Linearly ramp `L1-S6` vibration RMS from 2.0 → 4.5 mm/s over 5 minutes.
3. As vibration crosses 3.0 mm/s, simulator extends `L1-S6` cycle time by `1 + 0.5 × (vib − 3.0)`.
4. As vibration crosses 3.5 mm/s, simulator marks 15% of `L1-S6` parts as `quality=scrap` (instead of the nominal ~1%).
5. Print observable assertions: at least one quality alert in `alerts` within 60 s of step 4 starting; Line 1 Quality component of OEE visibly drops in the per-line breakdown chart.

**Plugin exercised:** `wal_quality_excursion` (windowed/derivative WAL pattern).

### 5.3 Scenarios deliberately excluded from v1

- **Planned changeover + recovery.** Adds value but requires the WAL plugins to differentiate planned vs unplanned states, which is already the design — but the scenario doesn't fire any plugin (it's the absence-of-alert that's interesting), and verifying "no alert was written" is a brittle test pattern. `ARCHITECTURE.md` discusses planned vs unplanned in prose; a future v2 scenario can be added once the negative-assertion test pattern is solved at the portfolio level.
- **Predictive maintenance ML model.** Out of scope for a reference architecture. The vibration trend in `tool_wear_quality_drift` lays the groundwork; readers can plug in their model.

## 6. UI

Same stack as bess: FastAPI + HTMX + Jinja2 + uPlot (vendored, no frontend toolchain). Single-page dashboard.

### 6.1 Layout

```
┌──────────────────────────────────────────────────────────────────────────┐
│ PLANT STATE BANNER:  RUNNING / DEGRADED / DOWN  · plant: ACME-MAIN       │
├──────────────────────────────────────────────────────────────────────────┤
│ KPI ROW (4 tiles):  Plant OEE %  ·  Units last 1h  ·  Active alerts  ·  │
│                     Distinct parts today                                 │
├──────────────────────────────────────────────────────────────────────────┤
│ ANDON BOARD  ⚡ served by Processing Engine: 11 ms                       │
│   Line 1  [■ ■ ■ ■ ■ ■ ■ ■]  OEE 87.3%   (8 stations as colored cells)  │
│   Line 2  [■ ■ ■ ■ ✕ ■ ■ ■]  OEE 41.2%                                  │
│   Line 3  [■ ■ ■ ■ ■ ■ ■ ■]  OEE 91.5%                                  │
├──────────────────────────────────────────────────────────────────────────┤
│ PER-LINE OEE BREAKDOWN (uPlot, 3 stacked charts, last 1h)                │
│   Line 1: Availability ─── Performance ─── Quality ─── OEE              │
│   Line 2: …                                                              │
│   Line 3: …                                                              │
├──────────────────────────────────────────────────────────────────────────┤
│ RECENT ALERTS (last 50)                                                  │
│   12:14:03  L2/S4   downtime         reason: tool change                │
│   12:09:47  L1/S6   quality_excursion  scrap_rate=0.18                  │
└──────────────────────────────────────────────────────────────────────────┘
```

### 6.2 Panel-to-source mapping

| Panel | Source | Endpoint / query |
|-------|--------|------------------|
| Plant state banner | direct SQL via LVC | reads `machine_state` LVC: any machine `state = 'error'` → DOWN; ≥1 machine `state = 'stopped'` → DEGRADED; else RUNNING |
| KPI row | direct SQL | Plant OEE: aggregate over current-shift window joining `part_events` and time-in-`running` derived from `machine_state`. Units last 1h: `SELECT count(*) FROM part_events WHERE time > now() - interval '1h'`. Active alerts: `SELECT count(*) FROM alerts WHERE time > now() - interval '1h'`. Distinct parts today: `SELECT count(distinct part_id) FROM part_events WHERE time > today_start_utc()` (DVC accelerates the distinct enumeration). |
| **Andon board** | **`request_andon_board` plugin** | **`GET /api/v3/engine/andon_board`** — single payload, every line + station + OEE + active alerts |
| Per-line OEE breakdown | direct SQL | three queries in `ui/queries.py`: `line_availability_1h`, `line_performance_1h`, `line_quality_1h` |
| Recent alerts | direct SQL | `SELECT … FROM alerts ORDER BY time DESC LIMIT 50` |

### 6.3 The Processing Engine teaching moment

The andon board panel is the primary teaching artifact for "Processing Engine as application backend." It:

1. Calls the request trigger via `fetch` from the panel's HTMX-loaded JS, not the FastAPI backend.
2. Records the round-trip time on the client and renders `served by Processing Engine: N ms` next to the panel header.
3. The README has a callout box: *"Panels with the ⚡ badge call a Processing Engine plugin via the InfluxDB HTTP API directly. Other panels query InfluxDB through the FastAPI backend. The Processing Engine pattern lets you ship pre-shaped JSON without a custom backend service."*
4. `ARCHITECTURE.md` discusses when to pick which: plugin trigger when shape is stable and reused (mobile, partner APIs); SQL through backend when shape is UI-specific.

### 6.4 Polling cadence

| Panel | Cadence |
|-------|---------|
| Plant state banner | 2 s |
| KPI row | 2 s |
| Andon board | 2 s |
| Per-line OEE breakdown | 5 s |
| Recent alerts | 3 s |

All cadences configurable via env vars on the `ui` service.

### 6.5 Styling

One `app.css`, dark-friendly, no CSS framework. Same approach as bess.

## 7. Repository layout

Identical skeleton to bess (per portfolio spec § 4.2). Concrete files:

```
influxdb3-ref-iiot/
├── README.md                   # quickstart, architecture diagram, plugins table, ⚡-badge callout
├── ARCHITECTURE.md             # OEE math, shift definitions, schema deep-dive, gotchas, scaling notes
├── SCENARIOS.md                # both scenarios with step-by-step walkthroughs
├── CLI_EXAMPLES.md             # curated influxdb3 CLI commands
├── FOR_MAINTAINERS.md          # CI license-volume refresh process
├── LICENSE                     # Apache 2.0
├── .env.example                # INFLUXDB3_ENTERPRISE_EMAIL, simulator and UI tunables
├── .gitignore
├── docker-compose.yml          # influxdb3, simulator, ui, scenarios services
├── Makefile                    # up, down, clean, scenario, cli, test targets
├── pyproject.toml              # single project: simulator + ui + tests + plugin tests
├── diagrams/
│   ├── architecture.mmd
│   └── architecture.png
├── influxdb/
│   ├── init.sh                 # creates DB, token, caches, registers triggers
│   └── schema.md               # table-by-table reference
├── plugins/
│   ├── wal_downtime_detector.py
│   ├── wal_quality_excursion.py
│   ├── schedule_shift_summary.py
│   └── request_andon_board.py
├── simulator/
│   ├── Dockerfile
│   ├── __init__.py
│   ├── main.py
│   ├── config.py
│   ├── writer.py
│   ├── signals_base.py         # COPIED from bess (sinusoid, random_walk, step, burst, jitter, correlation)
│   ├── signals.py              # IIoT domain: MachineState, VibrationSensor, TemperatureSensor, PartCount
│   └── scenarios/
│       ├── __init__.py
│       ├── unplanned_downtime_cascade.py
│       └── tool_wear_quality_drift.py
├── ui/
│   ├── Dockerfile
│   ├── __init__.py
│   ├── app.py                  # FastAPI routes + HTMX partials
│   ├── queries.py              # all SQL, named and documented
│   ├── templates/
│   │   ├── base.html
│   │   ├── overview.html
│   │   └── partials/
│   │       ├── _plant_state.html
│   │       ├── _kpi_row.html
│   │       ├── _andon_board.html
│   │       ├── _oee_breakdown.html
│   │       └── _alerts.html
│   └── static/
│       ├── app.css
│       ├── app.js              # uPlot wiring + andon-board fetch + latency badge
│       ├── htmx.min.js
│       ├── uplot.min.js
│       └── uplot.min.css
├── scripts/
│   ├── setup.sh                # email prompt, .env scaffolding
│   └── demo.sh                 # one-command demo orchestrator
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── test_signals.py
│   ├── test_writer.py
│   ├── test_queries.py
│   ├── test_smoke.py
│   ├── test_plugins/
│   │   ├── __init__.py
│   │   ├── test_wal_downtime_detector.py
│   │   ├── test_wal_quality_excursion.py
│   │   ├── test_schedule_shift_summary.py
│   │   └── test_request_andon_board.py
│   └── test_scenarios/
│       ├── __init__.py
│       ├── test_unplanned_downtime_cascade.py
│       └── test_tool_wear_quality_drift.py
├── .github/
│   └── workflows/
│       ├── unit.yml
│       ├── scenarios.yml
│       ├── smoke.yml
│       └── lint.yml
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-25-iiot-design.md   # this document
```

## 8. Compose, license, Makefile

Inherits the portfolio shared conventions verbatim (§ 4.3, § 4.4, § 4.5):

- **Compose stack:** `influxdb3` (image `influxdb:3-enterprise`, mounts `./plugins` → `/plugins`, healthcheck on `/health`), `simulator`, `ui`, `scenarios` (one-shot, profile `scenarios`).
- **Ports:** `8181` (InfluxDB), `8080` (UI).
- **Object store:** local `file` backend; named volume `influxdb-data` preserves license validation across `make down`/`make up`; `make clean` drops it.
- **License flow:** `make up` prompts for `INFLUXDB3_ENTERPRISE_EMAIL` if absent in `.env`, writes it, runs `docker compose up`. Compose uses `depends_on: condition: service_healthy` so `simulator` and `ui` wait for the influxdb3 healthcheck (which only passes after validation completes).
- **Makefile targets:** `up`, `down`, `clean`, `scenario name=<X>`, `scenario list`, `cli`, `cli-example name=<X>`, `test`, `test-unit`, `test-scenarios`, `test-smoke`.

## 9. Testing

Three tiers, identical structure to bess (per portfolio § 9):

### 9.1 Tier 1 — Plugin unit tests (`tests/test_plugins/`)

One file per plugin (4 files). Plugins tested as pure functions with fake payloads and a recording `influxdb3_local` fake. No Docker. Fast (<1 s per file).

Per-plugin test focus:

| Plugin | Key assertions |
|--------|----------------|
| `wal_downtime_detector` | Writes alert when state transitions to stopped/error; ignores transitions to changeover/planned_maintenance; emits one alert per transition (not per tick while stopped). |
| `wal_quality_excursion` | Maintains per-machine rolling window; fires alert exactly once when scrap_rate crosses threshold; does not fire while above threshold for subsequent batches until rate drops and re-crosses. |
| `schedule_shift_summary` | Reads correct shift window given trigger time; computes A × P × Q correctly across boundary cases (zero-running, zero-parts, all-scrap); emits exactly one row per line. |
| `request_andon_board` | Returns expected JSON shape; respects `since` query param; returns empty `alerts` array when no recent alerts (not null). |

Plus tier-1 tests for the simulator (`test_signals.py`, `test_writer.py`) and UI queries (`test_queries.py`).

### 9.2 Tier 2 — Scenario tests (`tests/test_scenarios/`)

One file per scenario (2 files). Uses `testcontainers-python` to run the real `influxdb:3-enterprise` image with real plugins mounted. Each test:

1. Boots the container, runs `init.sh`-equivalent setup.
2. Writes synthetic IIoT data directly via the InfluxDB HTTP API (does not run the simulator).
3. Asserts observable outcomes (alert rows present, OEE values in expected ranges).
4. Skipped when Docker unavailable.

Moderate (30–60 s per scenario). Runs on PR.

### 9.3 Tier 3 — Smoke test (`tests/test_smoke.py`)

`make up` → wait for health → let simulator run ~30 s → query 5 key metrics (plant OEE, distinct parts, latest machine_state for one machine, recent alerts count, andon_board endpoint round-trip) → `make down`. Slow (~3 min). Runs on `main` push and nightly.

### 9.4 CI

Four workflows (per portfolio § 9.4):

- `unit.yml` — every push.
- `scenarios.yml` — every PR.
- `smoke.yml` — push to `main` + nightly. Uses license-validated `influxdb-data` volume restored from GitHub Actions artifact.
- `lint.yml` — `ruff check`, `ruff format --check`, `markdownlint` on every push.

`FOR_MAINTAINERS.md` documents how to refresh the license-validated volume artifact (same process as bess).

## 10. Documentation set

| File | Audience | Length target |
|------|----------|---------------|
| `README.md` | First-time reader, evaluator, AI agent | 2–3 screens. Quickstart, what's in the repo, architecture diagram (PNG), plugins table, ⚡-badge callout, "scaling to production" pointer. |
| `ARCHITECTURE.md` | Reader who's run the demo and wants depth | 4–6 screens. Domain model, schema rationale, OEE math worked example, shift conventions, plugin patterns, gotchas (LineBuilder injection, 6-field cron, plugin state caveats), security notes, scaling-to-production. |
| `SCENARIOS.md` | Reader running the demo | 1–2 screens. Per-scenario step-by-step walkthroughs, expected dashboard observations, expected SQL output. |
| `CLI_EXAMPLES.md` | Reader exploring via `make cli` | 1–2 screens. Curated `influxdb3` CLI commands: `list-databases`, `list-tables`, `machine-count`, `line-oee`, `recent-alerts`, `cache-last-compare`, `cache-distinct`, `list-triggers`, `andon-board-api`. |
| `FOR_MAINTAINERS.md` | Repo maintainers | 1 screen. License-volume refresh process. |
| `influxdb/schema.md` | Schema reference | 1 screen. Table-by-table reference. |
| `diagrams/architecture.png` | Quick visual | One image. Rendered from `architecture.mmd`. |

## 11. Enterprise feature coverage

Per portfolio § 8 (IIoT row): common core + Distinct Cache + Request trigger.

| Feature | Where it shows up |
|---------|-------------------|
| Ingest | Simulator writes ~300 pts/s across 4 tables. |
| **Last Value Cache** | Powers the plant-state banner directly and the andon board indirectly (the `request_andon_board` plugin reads the LVC to assemble its JSON response); sub-ms reads on the 24-row machine-state set. |
| **Distinct Value Cache** | Powers "distinct parts today" KPI; demonstrated explicitly in `cache-distinct` CLI example with timing comparison. |
| **WAL trigger** (×2) | `wal_downtime_detector` (transition-detect), `wal_quality_excursion` (windowed). |
| **Schedule trigger** | `schedule_shift_summary` rolls up per-line OEE every 8 hours. |
| **Request trigger** | `request_andon_board` serves the andon board panel directly. |
| Custom UI | FastAPI + HTMX + Jinja2 + uPlot dashboard with live polling and direct-fetch panels. |

## 12. Non-goals (explicit)

- **No multi-node compose.** Single-node only. Multi-node IIoT is a viable variant but adds complexity that doesn't earn its keep for this reference repo. README's "scaling to production" section sketches the multi-node shape.
- **No K8s / Helm / Terraform manifests.** Same portfolio-wide deferral.
- **No real ML model.** The vibration-trend scenario lays the groundwork but a real predictive-maintenance model is out of scope.
- **No edge-to-core replication.** That pattern is the O&G repo's job.
- **No retention enforcement.** Same as bess.
- **No security hardening beyond the operator-token bootstrap pattern.** README's "security notes" section is descriptive, not prescriptive.

## 13. Implementation sequencing

The implementation plan (in `influxdb3-reference-architectures/docs/superpowers/plans/`) will follow the same phase pattern as bess: scaffolding → simulator → InfluxDB+compose+Makefile → manual checkpoint #1 → scenarios → plugins → manual checkpoint #2 → UI → tests tier 2 & 3 + CI → docs.

The plan's **final task** is to: create `influxdata/influxdb3-ref-iiot` on GitHub as a public repo with an Apache 2.0 LICENSE-validated description, push the local repo, and update `influxdb3-reference-architectures/README.md` to mark IIoT as ✅ Available with a link.

## 14. Open questions

None at design time. Anything that surfaces during implementation (e.g., concrete Distinct Cache TTL choice, exact plant-state-banner thresholds) is captured as a plan amendment per the bess-pilot precedent.
