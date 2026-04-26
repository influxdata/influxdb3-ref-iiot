# Architecture

Deep-dive companion to `README.md`. Read the README first for the quickstart and
headline story; read this for schema rationale, OEE math, gotchas, and how to
scale this pattern to production.

## Table of contents

1. Domain model
2. Schema
3. Why these tables and tags
4. OEE math (worked example)
5. Shift conventions
6. Processing Engine triggers
7. Enterprise features used
8. Token bootstrap (admin-token flow)
9. UI data flow
10. Plugin conventions and gotchas
11. Security notes
12. Scaling to production
13. Extending the plugins

## 1. Domain model

A **plant** contains 3 **lines**. Each line has 8 **stations**, one **machine** per station.
Default demo scale: 1 plant × 3 lines × 8 stations = 24 machines.

Each machine emits:

- State (1 Hz) — running / idle / stopped / error / changeover / planned_maintenance.
- Temperature (1 Hz) — °C.
- Vibration (10 Hz) — RMS mm/s.
- Part completion event — fires when an in-progress cycle completes; tagged with `part_id` and `quality` ∈ {good, scrap}.

Aggregate write rate at default config: ~300 points/sec.

## 2. Schema

See `influxdb/schema.md` for the table reference. Tags are `site`, `line_id`, `station_id`,
`machine_id`. `part_events` adds `part_id` and `quality`. `alerts` adds `source`, `severity`.
`shift_summary` is keyed by `line_id` and `shift_id`.

## 3. Why these tables and tags

- **`machine_state` written every tick** (not just on transition): lets you compute
  time-in-state with simple `COUNT(CASE WHEN state = 'X' …)` SQL — no complex temporal
  windowing or LAG functions needed. The 24 machines × 1 Hz rate is trivial.
- **`vibration` separate from `temperature`** because vibration is 10× the rate. Mixing
  them would produce NULL-heavy rows and waste columnar storage.
- **`part_events` uses `part_id` as a high-cardinality tag**, demonstrating the Distinct
  Value Cache. ~700K parts/day at default config; the DVC keeps "distinct parts today"
  in the single-digit-ms range regardless of total table size (exact latency depends on
  the query).
- **Pack-level rollups in `shift_summary`** rather than re-aggregating raw rows on every
  query: the schedule plugin writes once per shift; downstream consumers (BI, reporting)
  query the rollup directly. Materialized-downsample pattern.

## 4. OEE math (worked example)

OEE = Availability × Performance × Quality.

Worked example for Line 1 over an 8-hour shift (28,800 seconds):

| Metric | Value | Source |
|--------|-------|--------|
| `running_seconds` | 24,000 | COUNT of `machine_state` rows where `state='running'` |
| `planned_seconds` | 28,800 | COUNT of rows where `state NOT IN ('changeover','planned_maintenance')` |
| `total_count` | 800 | COUNT of `part_events` rows in window |
| `good_count` | 790 | COUNT of `part_events` rows where `quality='good'` |
| `ideal_cycle_time_s` | 30 | per-machine config |

- Availability = 24,000 / 28,800 = 0.833
- Performance = min(1.0, (30 × 800) / 24,000) = min(1.0, 1.000) = 1.000
- Quality = 790 / 800 = 0.988
- **OEE = 0.833 × 1.000 × 0.988 = 0.823 (82.3%)**

For per-line OEE in the demo we average across machines on the line by summing the per-machine
counts (mathematically equivalent for this convention).

## 5. Shift conventions

Three 8-hour shifts UTC: A = 06:00–14:00, B = 14:00–22:00, C = 22:00–06:00 next day.

`shift_id` format: `YYYY-MM-DD-{A|B|C}`. The date is the **shift-start** date — so the
C shift that begins at 22:00 on 2026-04-25 has `shift_id=2026-04-25-C`, even though
its data lands across two calendar dates.

The `schedule_shift_summary` plugin runs at each shift boundary (`cron:0 0 6,14,22 * * *`)
and writes one `shift_summary` row per line for the shift that just ended.

## 6. Processing Engine triggers

| Name | Type | Plugin | Binding | What it does |
|------|------|--------|---------|--------------|
| `downtime_detector` | WAL | `wal_downtime_detector.py` | `table:machine_state` | Writes an `alerts` row on transitions to `stopped`/`error`. |
| `quality_excursion` | WAL | `wal_quality_excursion.py` | `table:part_events`, args=`window=20,scrap_threshold=0.10` | Writes a quality `alerts` row when the per-machine windowed scrap rate crosses the threshold. |
| `shift_summary` | Schedule | `schedule_shift_summary.py` | `cron:0 0 6,14,22 * * *` | Writes per-line OEE rollup at each shift boundary. |
| `andon_board` | Request | `request_andon_board.py` | `request:andon_board` | GET `/api/v3/engine/andon_board` returns the full plant view as JSON. |

Two WAL plugins on purpose: one demonstrates **transition-detect** (emit on the row where
state changes), the other demonstrates **windowed/derivative** (maintain per-key state
across batches). Both patterns recur throughout IIoT.

## 7. Enterprise features used

| Feature | How this demo uses it |
|---------|------------------------|
| Ingest | Simulator writes ~300 pts/s across 4 tables |
| Last Value Cache | Powers plant-state banner directly; the `request_andon_board` plugin reads it to assemble the andon JSON |
| Distinct Value Cache | "Distinct parts today" KPI tile; explicitly demonstrated in `cache-distinct` CLI example |
| WAL trigger × 2 | `wal_downtime_detector`, `wal_quality_excursion` |
| Schedule trigger | `schedule_shift_summary` at shift boundaries |
| Request trigger | `request_andon_board` powers the UI's andon panel via direct fetch (with latency badge) |
| Custom UI | FastAPI + HTMX + Jinja2 + uPlot dashboard — no backend service for the andon view |

### Table creation: explicit, not implicit

InfluxDB 3 auto-creates tables on first write, but caches and named queries need
the table to **exist at create / plan time**. `init.sh` therefore POSTs each user
table to `/api/v3/configure/table` (passing tags + typed fields as JSON) before
creating the LVC/DVC and before the simulator starts writing. A 409 on re-run is
treated as success, so init is idempotent. Without this, the LVC/DVC `create`
calls — and any UI query that fires before the simulator's first batch lands —
race against implicit creation and intermittently fail with "table not found".

This replaces an earlier pattern that wrote a `__init` sentinel row at `t=1ns`
to each table to materialize it; that approach leaked a throwaway row that every
downstream query had to filter out.

## 8. Token bootstrap

The `token-bootstrap` compose service generates an offline admin token on first boot,
writing it to `/var/lib/influxdb3/.iiot-operator-token` inside the shared data volume.
The `influxdb3` server starts with `--admin-token-file <that path>`. Other services
(simulator, UI) mount the volume read-only at `/tokens` and read the JSON file.

This separates token lifecycle from server lifecycle: tokens persist across server
restarts, and a single bootstrap step doesn't have to embed token-creation in the
server's healthcheck.

## 9. UI data flow

Jinja2 renders the shell. HTMX polls partial endpoints on intervals (KPIs every 2s,
charts every 5s, alerts every 3s). Each partial route in `ui/app.py` invokes a named
function in `ui/queries.py`, runs SQL against InfluxDB 3, renders a Jinja2 fragment.

The **andon board panel is different**: its DATA is fetched directly from the
Processing Engine request endpoint (`/api/v3/engine/andon_board`) by `app.js`, with
a "served by Processing Engine: N ms" badge measuring the round-trip. This makes
the Processing-Engine-as-backend pattern visible side-by-side with the SQL-through-FastAPI
pattern. Pick the plugin pattern when the response shape is stable and reused; pick
the SQL pattern when the shape is UI-specific.

## 10. Plugin conventions and gotchas

- **`LineBuilder` is INJECTED, NOT IMPORTED.** The engine sets it in module globals
  before exec'ing the file. Do NOT do `from influxdb3_local import LineBuilder` or any
  try/except fallback. Tests use `monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)`.
- **Cron strings are 6-FIELD**: `<sec> <min> <hour> <dom> <mon> <dow>`. Not the 5-field
  Unix cron format.
- **Plugin file naming auto-installs**: `wal_<name>.py` → WAL trigger; `schedule_<name>.py` → schedule;
  `request_<name>.py` → request. Filename without prefix is the trigger name; for request
  triggers it also becomes the URL path.
- **In-process plugin state survives across batches but resets on engine restart.**
  `wal_downtime_detector._prev_state` and `wal_quality_excursion._windows` exemplify this.
  Production deployments should externalize state (e.g., a fast KV store) if cross-restart
  continuity matters.

## 11. Security notes

This is a reference architecture, not production. Notable simplifications:

- The admin token is auto-generated and shared between simulator/UI. Production should
  issue per-service tokens with scoped permissions.
- The UI calls the Processing Engine endpoint with the admin token in the browser
  (visible to `View Source`). Production should proxy through the UI backend, or use
  a token-exchange flow for the browser.
- No TLS in the compose stack. Production needs TLS.

## 12. Scaling to production

- **Multi-node**: split into ingest / query / compact services (see other portfolio
  repos for examples). The IIoT demo is single-node by design — most factories run
  fewer machines than this demo and a single node is sufficient.
- **Object store**: swap `file` for S3/GCS/Azure Blob. No code changes; one env var.
- **Retention**: add retention on raw `machine_state`/`vibration` (e.g., 30 days);
  keep `shift_summary` indefinitely.
- **K8s**: the compose pattern translates 1:1 to Helm. Not shipped here per portfolio
  policy.

## 13. Extending the plugins

To add a new alert: copy `wal_downtime_detector.py` to `wal_<your_name>.py`, change
`table_name` filter and detection logic, add a unit test, and re-run `make down && make up`
(init.sh registers it on next start). Same for schedule and request triggers — just
follow the file-naming convention.
