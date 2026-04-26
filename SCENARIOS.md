# Scenarios

Two curated scenarios ship with this repo. Each is a Python script under
`simulator/scenarios/` that connects to the running stack and injects a
specific event pattern. Run any with:

```bash
make scenario name=<scenario>
```

List available scenarios with `make scenario-list`.

## unplanned_downtime_cascade

**One-line:** A station on Line 2 stops; upstream starves, downstream blocks, line OEE plummets.

**Steps:**
1. Wait 10s (baseline).
2. `L2-S4` → `state=stopped`, `reason=tool_change`.
3. After 5s, mark `L2-S1..S3` as `idle/starved` (upstream) and `L2-S5..S8` as `idle/blocked` (downstream).
4. Hold for 60s (re-emitting stopped state every 5s so the WAL trigger fires reliably).
5. Restore all Line-2 machines to `running`.

**Plugin exercised:** `wal_downtime_detector` — fires within ~1s of the L2-S4 transition,
writes one row to `alerts` with `source='wal_downtime_detector'`, `severity='critical'`.

**What to watch in the dashboard:**
- Plant state banner flips RUNNING → DEGRADED.
- Andon board: L2-S4 turns red, neighbours turn yellow.
- Line 2 OEE drops to ~0.4 within 30s.
- Recent alerts table shows the new row at the top.

**Verify with SQL:**

```sql
SELECT time, machine_id, reason
FROM alerts
WHERE source = 'wal_downtime_detector' AND line_id = 'L2'
ORDER BY time DESC LIMIT 5;
```

## tool_wear_quality_drift

**One-line:** Vibration on Line 1 Station 6 climbs over 5 minutes; cycle time slows; scrap rate rises; quality alert fires.

**Steps:**
1. Wait 10s (baseline at 2.0 mm/s).
2. Linearly ramp `L1-S6` vibration RMS from 2.0 → 4.5 mm/s over 5 minutes.
3. As vibration crosses 3.0 mm/s, extend cycle time by `0.5 × (vib − 3.0)` seconds.
4. As vibration crosses 3.5 mm/s, set scrap rate to 15% (vs nominal 1%).
5. The `wal_quality_excursion` plugin fires once the windowed scrap rate crosses 10%.

**Plugin exercised:** `wal_quality_excursion` (windowed/derivative WAL pattern).

**What to watch in the dashboard:**
- Vibration trend visible in Line 1 OEE breakdown chart (Quality component drops).
- One quality alert appears in the Recent alerts table with `severity=quality`.

**Verify with SQL:**

```sql
SELECT time, machine_id, reason, value
FROM alerts
WHERE source = 'wal_quality_excursion'
ORDER BY time DESC LIMIT 5;
```
