# IIoT schema

This file documents the IIoT demo's InfluxDB 3 schema. The `init.sh` script creates
the database, registers each user table explicitly via `POST /api/v3/configure/table`,
creates the Last/Distinct caches, and registers Processing Engine triggers.

## Why explicit table creation

InfluxDB 3 will auto-create a table on the first line-protocol write — but the LVC
and DVC are bound to a *named* table that must already exist when `create last_cache`
or `create distinct_cache` runs, and the UI's named queries plan against tables by
name at request time. If the simulator hadn't started yet (or hadn't yet written to
that table) when a UI partial fired or `init.sh` tried to create a cache, the
operation would fail with "table not found".

`init.sh` therefore registers every user table up front via:

```http
POST /api/v3/configure/table
Content-Type: application/json
Authorization: Bearer <token>

{
  "db": "iiot",
  "table": "machine_state",
  "tags": ["site", "line_id", "station_id", "machine_id"],
  "fields": [
    {"name": "state",  "type": "utf8"},
    {"name": "reason", "type": "utf8"}
  ]
}
```

A 201 means the table was created; a 409 means it already exists (idempotent re-run).
Field types: `float64`, `int64`, `uint64`, `utf8`, `bool`. This avoids the older
"write a sentinel row at t=1ns with `__init` tag values to materialize the table"
hack — that pattern leaves a permanent throwaway row in every table that all queries
must filter out.

## Tables

| Table | Tags | Fields | Source |
|-------|------|--------|--------|
| `machine_state` | site, line_id, station_id, machine_id | state (string), reason (string) | simulator (1 Hz per machine) |
| `temperature` | site, line_id, station_id, machine_id | temp_c (f64) | simulator (1 Hz per machine) |
| `vibration` | site, line_id, station_id, machine_id | rms_mm_s (f64) | simulator (10 Hz per machine) |
| `part_events` | site, line_id, station_id, machine_id, part_id, quality | cycle_time_s (f64) | simulator (event-driven) |
| `alerts` | source, severity, line_id, machine_id | reason (string), value (f64) | plugins (`wal_downtime_detector`, `wal_quality_excursion`) |
| `shift_summary` | line_id, shift_id | oee, availability, performance, quality, units_total, units_good (f64), downtime_top1_reason, downtime_top2_reason, downtime_top3_reason (string), downtime_top1_seconds, downtime_top2_seconds, downtime_top3_seconds (f64) | plugin (`schedule_shift_summary`) |

## Caches

Both caches are created in `init.sh` *after* the explicit table creation above —
`create last_cache` and `create distinct_cache` resolve their `--table` argument
against the catalog, so the table must already exist.

- **Last Value Cache** `machine_state_last` on `machine_state` keyed by (site, line_id, station_id, machine_id) — 24-row in-memory cache that powers the plant-state banner and is read by `request_andon_board` to assemble the andon JSON. Single-digit-ms per-machine lookup (exact latency depends on the query).
- **Distinct Value Cache** `part_id_distinct` on `part_events.part_id` — accelerates the "distinct parts today" KPI. Demonstrated explicitly in `cache-distinct` CLI example.

## Retention

No retention policy in the demo. The simulator writes at ~300 pts/s and we want
readers to see accumulated history for the per-line OEE charts. For production,
see `ARCHITECTURE.md` § "Scaling to production".
