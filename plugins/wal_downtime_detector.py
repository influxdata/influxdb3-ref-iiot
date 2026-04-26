"""WAL trigger: emit an alert when a machine transitions to stopped or error.

Binding: table=machine_state, args={}
Fires on: every write batch to machine_state.
Side effects: writes one row to `alerts` per detected transition.

Detects transitions only — repeated writes of the same stopped/error state
do NOT re-emit alerts. State `changeover` and `planned_maintenance` are
considered planned and never alert. Per-machine previous state is kept in
a module-level dict that survives across batches but resets on engine
restart (acceptable for the reference; see ARCHITECTURE.md).
"""

# LineBuilder is INJECTED into module globals by the Processing Engine runtime;
# do NOT import it. Tests attach a fake via
# `monkeypatch.setattr(mod, "LineBuilder", FakeLineBuilder, raising=False)`.

UNPLANNED_DOWN_STATES = {"stopped", "error"}
PLANNED_STATES = {"changeover", "planned_maintenance"}

# machine_id -> last observed state. Module-level on purpose (cross-batch).
_prev_state: dict[str, str] = {}


def process_writes(influxdb3_local, table_batches, args=None):
    for batch in table_batches:
        if batch["table_name"] != "machine_state":
            continue
        for row in batch["rows"]:
            machine_id = str(row.get("machine_id", ""))
            state = str(row.get("state", ""))
            if not machine_id or not state:
                continue
            prev = _prev_state.get(machine_id)
            _prev_state[machine_id] = state
            if state in PLANNED_STATES:
                continue
            if state not in UNPLANNED_DOWN_STATES:
                continue
            # Only alert on transition INTO an unplanned-down state from a
            # different state (or first observation).
            if prev == state:
                continue
            reason = str(row.get("reason", ""))
            line_id = str(row.get("line_id", ""))
            influxdb3_local.info(f"downtime: {machine_id} {prev} -> {state} (reason={reason})")
            lb = (
                LineBuilder("alerts")
                .tag("source", "wal_downtime_detector")
                .tag("severity", "critical")
                .tag("line_id", line_id)
                .tag("machine_id", machine_id)
                .string_field("reason", reason or "unspecified")
                .float64_field("value", 0.0)
            )
            influxdb3_local.write(lb)
