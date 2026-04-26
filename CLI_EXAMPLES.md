# CLI Examples

Curated `influxdb3` CLI commands for the IIoT demo. Run any of these via:

```bash
make cli-example name=<example>
```

(or copy the SQL into `make query sql='...'`)

## list-databases

```bash
influxdb3 show database --token "$TOKEN"
```

## list-tables

```bash
influxdb3 query --database iiot --token "$TOKEN" "SHOW TABLES"
```

## machine-count

```bash
influxdb3 query --database iiot --token "$TOKEN" "SELECT COUNT(DISTINCT machine_id) AS n FROM machine_state WHERE site = 'acme-main'"
```

## line-oee

Plant OEE per line over the last hour (Availability × Performance × Quality):

```bash
influxdb3 query --database iiot --token "$TOKEN" "WITH ms AS (SELECT line_id, SUM(CASE WHEN state = 'running' THEN 1 ELSE 0 END) AS running_seconds, SUM(CASE WHEN state NOT IN ('changeover','planned_maintenance') THEN 1 ELSE 0 END) AS planned_seconds FROM machine_state WHERE time > now() - INTERVAL '1 hour' GROUP BY line_id), pe AS (SELECT line_id, COUNT(*) AS total_count, SUM(CASE WHEN quality = 'good' THEN 1 ELSE 0 END) AS good_count FROM part_events WHERE time > now() - INTERVAL '1 hour' GROUP BY line_id) SELECT ms.line_id, ms.running_seconds * 1.0 / NULLIF(ms.planned_seconds,0) AS availability, LEAST(1.0, 30.0 * pe.total_count / NULLIF(ms.running_seconds,0)) AS performance, pe.good_count * 1.0 / NULLIF(pe.total_count,0) AS quality FROM ms JOIN pe ON ms.line_id = pe.line_id ORDER BY ms.line_id"
```

## recent-alerts

```bash
influxdb3 query --database iiot --token "$TOKEN" "SELECT time, source, severity, line_id, machine_id, reason, value FROM alerts ORDER BY time DESC LIMIT 10"
```

## cache-last-compare

Compare LVC vs raw scan for "latest state per machine" — use `EXPLAIN ANALYZE`:

```bash
influxdb3 query --database iiot --token "$TOKEN" "EXPLAIN ANALYZE SELECT machine_id, state FROM machine_state WHERE site = 'acme-main'"
```

## cache-distinct

Use the Distinct Value Cache to enumerate distinct part_ids today:

```bash
influxdb3 query --database iiot --token "$TOKEN" "SELECT COUNT(DISTINCT part_id) AS distinct_parts FROM part_events WHERE time > date_trunc('day', now())"
```

## list-triggers

```bash
influxdb3 show trigger --database iiot --token "$TOKEN"
```

## andon-board-api

Hit the request trigger directly:

```bash
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8181/api/v3/engine/andon_board | python3 -m json.tool | head -40
```
