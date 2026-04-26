# IIoT schema

This file documents the IIoT demo's InfluxDB 3 schema. It is descriptive — the actual
tables are created implicitly by the first write. The `init.sh` script creates the
database, an operator token, the Last/Distinct caches, and registers Processing Engine
triggers.

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

- **Last Value Cache** `machine_state_last` on `machine_state` keyed by (site, line_id, station_id, machine_id) — 24-row in-memory cache that powers the plant-state banner and is read by `request_andon_board` to assemble the andon JSON. Single-digit-ms per-machine lookup (exact latency depends on the query).
- **Distinct Value Cache** `part_id_distinct` on `part_events.part_id` — accelerates the "distinct parts today" KPI. Demonstrated explicitly in `cache-distinct` CLI example.

## Retention

No retention policy in the demo. The simulator writes at ~300 pts/s and we want
readers to see accumulated history for the per-line OEE charts. For production,
see `ARCHITECTURE.md` § "Scaling to production".
