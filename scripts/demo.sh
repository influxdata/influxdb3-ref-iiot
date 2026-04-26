#!/usr/bin/env bash
# One-shot end-to-end demo of the IIoT reference architecture.
# Brings the stack up, opens a browser, fires a scenario, shows results.
#
# Usage:
#   ./scripts/demo.sh             # reuse existing license volume if present
#   ./scripts/demo.sh --fresh     # wipe first; forces re-validation email click
#   ./scripts/demo.sh --no-browser
#   ./scripts/demo.sh --no-pause  # skip the intro keypress (CI / scripted runs)
#   ./scripts/demo.sh --help

set -euo pipefail

cd "$(dirname "$0")/.."

# ── args ──────────────────────────────────────────────────────────────────
FRESH=0
OPEN_BROWSER=1
PAUSE=1
# auto-skip the pause if not on a TTY (e.g. piped/captured runs)
[[ -t 0 ]] || PAUSE=0
for arg in "$@"; do
    case "$arg" in
        --fresh) FRESH=1 ;;
        --no-browser) OPEN_BROWSER=0 ;;
        --no-pause) PAUSE=0 ;;
        -h|--help)
            sed -n '2,11p' "$0" | sed 's/^# //; s/^#//'
            exit 0
            ;;
        *) echo "unknown arg: $arg" >&2; exit 2 ;;
    esac
done

# ── colors ────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
    BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
    FG_BLUE=$'\033[38;5;75m'; FG_GREEN=$'\033[38;5;42m'; FG_YELLOW=$'\033[38;5;221m'
    FG_RED=$'\033[38;5;203m'; FG_CYAN=$'\033[38;5;80m'; FG_MAGENTA=$'\033[38;5;177m'
    FG_GREY=$'\033[38;5;244m'   # borders, structure
    FG_TEXT=$'\033[38;5;252m'   # narrative/prose, lighter for readability
else
    BOLD= DIM= RESET= FG_BLUE= FG_GREEN= FG_YELLOW= FG_RED= FG_CYAN= FG_MAGENTA= FG_GREY= FG_TEXT=
fi

STEP=0
step() {
    STEP=$((STEP + 1))
    echo
    echo "${BOLD}${FG_BLUE}┌─ Step ${STEP}: $1${RESET}"
}
info()  { echo "${FG_GREY}│${RESET}  $*"; }
ok()    { echo "${FG_GREEN}│  ✓${RESET} $*"; }
warn()  { echo "${FG_YELLOW}│  ⚠${RESET}  $*"; }
fail()  { echo "${FG_RED}│  ✗${RESET}  $*"; }
note()  { echo "${FG_CYAN}│  ◆${RESET}  $*"; }
cmd()   { echo "${FG_MAGENTA}│  \$${RESET} ${DIM}$*${RESET}"; }
close_step() { echo "${FG_BLUE}└───────${RESET}"; }

banner() {
    # printf "%-64s" pads by BYTES, not characters — so any multi-byte glyph
    # in the title (em-dash, accented letters, …) shifts the right border
    # left by the byte-vs-char delta. Count chars with `wc -m` instead.
    local title="$1"
    local cw
    cw=$(printf '%s' "$title" | wc -m | tr -d ' ')
    local pad=$((64 - cw))
    [[ $pad -lt 0 ]] && pad=0
    echo
    echo "${BOLD}${FG_CYAN}╔══════════════════════════════════════════════════════════════════╗${RESET}"
    printf "${BOLD}${FG_CYAN}║${RESET}  ${BOLD}%s${RESET}%*s${BOLD}${FG_CYAN}║${RESET}\n" "$title" "$pad" ""
    echo "${BOLD}${FG_CYAN}╚══════════════════════════════════════════════════════════════════╝${RESET}"
}

# ── spinner ───────────────────────────────────────────────────────────────
spin_until() {
    # spin_until "label" "check-cmd" timeout_s
    #
    # The check-cmd may include pipes (e.g. `docker logs … | grep -q …`).
    # `grep -q` exits as soon as it finds a match, which sends SIGPIPE to
    # the upstream command. Under `set -o pipefail` (set globally above)
    # the pipeline's exit code becomes 141 (SIGPIPE) — and the if below
    # treats that as "no match", so the spinner loops forever even though
    # the grep DID find what we were looking for. Run the check in a
    # subshell with pipefail disabled so SIGPIPE on the upstream command
    # doesn't fail the pipeline.
    local label="$1" check="$2" timeout="${3:-60}"
    local spin=("⠋" "⠙" "⠹" "⠸" "⠼" "⠴" "⠦" "⠧" "⠇" "⠏")
    local start; start=$(date +%s); local i=0
    while true; do
        if (set +o pipefail; eval "$check") >/dev/null 2>&1; then
            printf "\r${FG_GREY}│${RESET}  ${FG_GREEN}✓${RESET} %s %s\n" "$label" "${DIM}($(( $(date +%s) - start ))s)${RESET}"
            return 0
        fi
        local elapsed=$(( $(date +%s) - start ))
        if (( elapsed >= timeout )); then
            printf "\r${FG_GREY}│${RESET}  ${FG_RED}✗${RESET} %s ${FG_RED}(timeout after %ds)${RESET}\n" "$label" "$timeout"
            return 1
        fi
        printf "\r${FG_GREY}│${RESET}  ${FG_YELLOW}%s${RESET} %s ${DIM}(%ds)${RESET}" "${spin[i % 10]}" "$label" "$elapsed"
        i=$((i + 1))
        sleep 0.2
    done
}

# ── helpers ───────────────────────────────────────────────────────────────
open_browser() {
    local url="$1"
    if [[ $OPEN_BROWSER -eq 0 ]]; then return; fi
    if command -v open >/dev/null 2>&1; then open "$url"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$url"
    else info "open this URL manually: $url"; fi
}

token() {
    docker compose exec -T influxdb3 cat /var/lib/influxdb3/.iiot-token-plain 2>/dev/null | tr -d '\r\n'
}

query() {
    # query "<sql>"
    local sql="$1"
    docker compose exec -T -e "SQL=$sql" influxdb3 bash -c \
        'TOKEN=$(cat /var/lib/influxdb3/.iiot-token-plain); influxdb3 query --database iiot --token "$TOKEN" "$SQL"' 2>&1 \
        | grep -v "deprecated" || true
}

# Hit /api/v3/query_sql directly via curl so we can capture honest end-to-end
# wall-clock latency (curl's %{time_total}). Pretty-print JSON rows as a
# table, then print "responded in X.XXX ms" at the bottom. Same network path
# an external app would use — not the docker-exec path of `query`.
timed_query_table() {
    local sql="$1"
    local tok body_file
    tok=$(token)
    body_file=$(mktemp)
    local time_total
    time_total=$(curl -s -w '%{time_total}' \
        -o "$body_file" \
        -H "Authorization: Bearer ${tok}" \
        -H "Content-Type: application/json" \
        -X POST "http://localhost:8181/api/v3/query_sql" \
        -d "$(python3 -c 'import json,sys; print(json.dumps({"db":"iiot","q":sys.argv[1],"format":"json"}))' "$sql")")
    local ms
    ms=$(awk -v t="$time_total" 'BEGIN{printf "%.3f", t*1000}')
    python3 - "$body_file" <<'PY'
import json, sys
rows = json.load(open(sys.argv[1]))
if not rows:
    print("(0 rows)")
    raise SystemExit
cols = list(rows[0].keys())
def cell(v):
    return "" if v is None else str(v)
widths = [max(len(c), max(len(cell(r.get(c))) for r in rows)) for c in cols]
sep = "+".join("-" * (w + 2) for w in widths)
print("+" + sep + "+")
print("|" + "|".join(f" {c:<{w}} " for c, w in zip(cols, widths)) + "|")
print("+" + sep + "+")
for r in rows:
    print("|" + "|".join(f" {cell(r.get(c)):<{w}} " for c, w in zip(cols, widths)) + "|")
print("+" + sep + "+")
PY
    rm -f "$body_file"
    printf "${FG_TEXT}responded in ${BOLD}${FG_GREEN}%s ms${RESET}${FG_TEXT} via /api/v3/query_sql${RESET}\n" "$ms"
}

# Curl an arbitrary endpoint, time it, pretty-print JSON, report latency.
# Use this for the Request-trigger demo so we get true end-to-end timing.
timed_get_json() {
    local url="$1"
    local tok body_file
    tok=$(token)
    body_file=$(mktemp)
    local time_total
    time_total=$(curl -s -w '%{time_total}' \
        -o "$body_file" \
        -H "Authorization: Bearer ${tok}" \
        "$url")
    local ms
    ms=$(awk -v t="$time_total" 'BEGIN{printf "%.3f", t*1000}')
    python3 -m json.tool < "$body_file" 2>/dev/null || cat "$body_file"
    rm -f "$body_file"
    printf "${FG_TEXT}responded in ${BOLD}${FG_GREEN}%s ms${RESET}${FG_TEXT} ← same endpoint the browser's andon panel hits${RESET}\n" "$ms"
}

# ══════════════════════════════════════════════════════════════════════════
banner "IIoT Reference Architecture — One-Shot Demo"
echo "${DIM}   InfluxDB 3 Enterprise · Python · FastAPI + HTMX · docker-compose${RESET}"
echo

# ── intro / talk-track ────────────────────────────────────────────────────
echo "${BOLD}What this demo shows${RESET}"
echo
echo "  ${FG_TEXT}A complete reference architecture for using InfluxDB 3 Enterprise to"
echo "  monitor an automotive-style assembly plant: 1 plant × 3 lines × 8 stations"
echo "  = 24 machines, ~300 telemetry points/sec (machine state, vibration at 10 Hz,"
echo "  temperature, part-completion events). Real-time downtime + quality alerts,"
echo "  shift-boundary OEE rollups, and a live andon board — all served by Python"
echo "  plugins running INSIDE the database.${RESET}"
echo
echo "${BOLD}${FG_YELLOW}>>${RESET}  ${BOLD}Headline Enterprise features on the stage${RESET}  ${BOLD}${FG_YELLOW}<<${RESET}"
echo
echo "    ${BOLD}${FG_YELLOW}◆${RESET}  ${BOLD}Processing Engine — four Python plugins${RESET}"
echo "       ${FG_TEXT}All three trigger types in use, plus a second WAL plugin so you${RESET}"
echo "       ${FG_TEXT}can see two distinct WAL patterns side-by-side:${RESET}"
echo "       ${FG_TEXT}  · ${BOLD}wal_downtime_detector${RESET}${FG_TEXT}  — transition-detect (instant alerts)${RESET}"
echo "       ${FG_TEXT}  · ${BOLD}wal_quality_excursion${RESET}${FG_TEXT}  — windowed/derivative (rolling scrap rate)${RESET}"
echo "       ${FG_TEXT}  · ${BOLD}schedule_shift_summary${RESET}${FG_TEXT} — per-line OEE rollup at every shift boundary${RESET}"
echo "       ${FG_TEXT}  · ${BOLD}request_andon_board${RESET}${FG_TEXT}    — full plant view as JSON via HTTP GET${RESET}"
echo
echo "    ${BOLD}${FG_YELLOW}◆${RESET}  ${BOLD}Last Value Cache  +  Distinct Value Cache${RESET}"
echo "       ${FG_TEXT}LVC on machine_state powers the plant banner and the andon plugin's${RESET}"
echo "       ${FG_TEXT}24-row lookup. DVC on part_events.part_id makes 'distinct parts today'${RESET}"
echo "       ${FG_TEXT}return in single-digit ms against ~700K events/day.${RESET}"
echo
echo "    ${BOLD}${FG_YELLOW}◆${RESET}  ${BOLD}OEE = Availability × Performance × Quality${RESET}"
echo "       ${FG_TEXT}Computed live in SQL from machine_state + part_events. The schedule${RESET}"
echo "       ${FG_TEXT}plugin freezes per-line OEE into ${BOLD}shift_summary${RESET}${FG_TEXT} every shift boundary${RESET}"
echo "       ${FG_TEXT}(06:00 / 14:00 / 22:00 UTC).${RESET}"
echo
echo "${BOLD}Who's doing what${RESET}"
echo
echo "  ${FG_GREEN}[script]${RESET}    ${FG_TEXT}this terminal — orchestrates docker compose, runs CLI/curl${RESET}"
echo "  ${FG_BLUE}[ui]${RESET}        ${FG_TEXT}browser at localhost:8080 — HTMX polls FastAPI in iiot-ui${RESET}"
echo "  ${FG_MAGENTA}[sim]${RESET}       ${FG_TEXT}iiot-simulator container — writes line protocol continuously${RESET}"
echo "  ${FG_YELLOW}[db]${RESET}        ${FG_TEXT}iiot-influxdb3 container — DB + Python plugins (Processing Engine)${RESET}"
echo
echo "${BOLD}What's about to happen${RESET}"
echo
echo "  ${FG_CYAN}1.${RESET} ${FG_GREEN}[script]${RESET} ${BOLD}Bring up the 6-service stack:${RESET}"
echo "       ${FG_TEXT}token-bootstrap -> influxdb3 -> init (db/caches/triggers) ->"
echo "       simulator -> ui -> scenarios (on-demand). Demo script issues"
echo "       'docker compose up -d' and waits for each service to be ready.${RESET}"
echo
echo "  ${FG_CYAN}2.${RESET} ${FG_GREEN}[script]${RESET} -> ${FG_BLUE}[ui]${RESET} ${BOLD}Open the control-room UI${RESET} in your browser."
echo "       ${FG_TEXT}Once open, the ${BOLD}browser polls FastAPI in iiot-ui${RESET}${FG_TEXT} every 2-5s for the${RESET}"
echo "       ${FG_TEXT}plant-state banner, KPIs, and OEE charts. The ${BOLD}andon panel is different${RESET}${FG_TEXT}:${RESET}"
echo "       ${FG_TEXT}it fetches /api/v3/engine/andon_board ${BOLD}directly from the browser${RESET}${FG_TEXT}, with a${RESET}"
echo "       ${FG_TEXT}\"served by Processing Engine: N ms\" badge showing the actual round-trip.${RESET}"
echo "       ${FG_TEXT}Two patterns side-by-side: SQL through a backend vs plugin via direct fetch.${RESET}"
echo
echo "  ${FG_CYAN}3.${RESET} ${FG_GREEN}[script]${RESET} -> ${FG_MAGENTA}[sim]${RESET} -> ${FG_YELLOW}[db]${RESET} ${BOLD}Inject the unplanned_downtime_cascade scenario${RESET}"
echo "       ${FG_TEXT}Script runs a one-shot scenario container that flips Line 2 / Station 4${RESET}"
echo "       ${FG_TEXT}to state=stopped (reason=tool_change), then propagates idle states to${RESET}"
echo "       ${FG_TEXT}upstream (starved) and downstream (blocked) machines. The DB's WAL${RESET}"
echo "       ${FG_TEXT}trigger — wal_downtime_detector.py running INSIDE the influxdb3 process${RESET}"
echo "       ${FG_TEXT}— sees the state transition and writes alert rows. The UI's plant banner${RESET}"
echo "       ${FG_TEXT}flips to DEGRADED and the andon cell turns red on its next poll.${RESET}"
echo
echo "  ${FG_CYAN}4.${RESET} ${FG_GREEN}[script]${RESET} -> ${FG_YELLOW}[db]${RESET} ${BOLD}Query the alerts table${RESET} (script-side, via /api/v3/query_sql)."
echo "       ${FG_TEXT}Pulls the most recent downtime alerts. These rows did NOT come from the${RESET}"
echo "       ${FG_TEXT}simulator — the WAL plugin wrote them. Proof the plugin ran.${RESET}"
echo
echo "  ${FG_CYAN}5.${RESET} ${FG_GREEN}[script]${RESET} -> ${FG_YELLOW}[db]${RESET} ${BOLD}Call the Processing Engine HTTP endpoint${RESET} (curl from script)."
echo "       ${FG_TEXT}'GET /api/v3/engine/andon_board' is served by request_andon_board.py${RESET}"
echo "       ${FG_TEXT}running inside influxdb3 — Python in the database, no app server. The${RESET}"
echo "       ${FG_TEXT}response payload is the full plant view: every line, every machine, the${RESET}"
echo "       ${FG_TEXT}live A×P×Q breakdown, and the active alerts. ${BOLD}This is the same call${RESET}"
echo "       ${FG_TEXT}${BOLD}the browser's andon panel makes${RESET}${FG_TEXT} — measured here so you can${RESET}"
echo "       ${FG_TEXT}see the latency yourself.${RESET}"
echo
echo "  ${FG_CYAN}6.${RESET} ${FG_GREEN}[script]${RESET} -> ${FG_YELLOW}[db]${RESET} ${BOLD}Read the Last Value Cache${RESET} (script SQL)."
echo "       ${FG_TEXT}'SELECT COUNT(*) FROM last_cache(...)' returns instantly from memory.${RESET}"
echo "       ${FG_TEXT}24 rows (one per machine), single-digit ms — same cache the andon plugin${RESET}"
echo "       ${FG_TEXT}reads on every request and the plant banner reads every 2s.${RESET}"
echo
if [[ $PAUSE -eq 1 ]]; then
    echo "${BOLD}${FG_YELLOW}Press [Enter] to begin${RESET}  ${FG_TEXT}(Ctrl-C to abort)${RESET}"
    read -r _
else
    echo "${FG_TEXT}--no-pause set; starting immediately.${RESET}"
fi
echo

# ── prereqs ───────────────────────────────────────────────────────────────
step "Prereqs"
if ! command -v docker >/dev/null; then fail "docker not on PATH"; exit 1; fi
if ! docker info >/dev/null 2>&1; then fail "docker daemon not running"; exit 1; fi
ok "Docker is running"
close_step

# ── fresh wipe (optional) ─────────────────────────────────────────────────
if [[ $FRESH -eq 1 ]]; then
    step "Wiping previous state (--fresh)"
    info "dropping .env and all volumes — you'll need to click a new validation email"
    cmd "make clean && rm -f .env"
    make clean >/dev/null 2>&1 || true
    rm -f .env
    ok "Clean slate"
    close_step
fi

# ── email / .env ──────────────────────────────────────────────────────────
step "Enterprise trial license — email"
if [[ -f .env ]] && grep -q '^INFLUXDB3_ENTERPRISE_EMAIL=.\+' .env; then
    EMAIL=$(grep '^INFLUXDB3_ENTERPRISE_EMAIL=' .env | cut -d= -f2-)
    ok "Reusing .env: trial registered to ${BOLD}$EMAIL${RESET}"
    NEEDS_VALIDATION=0
    if ! docker volume inspect influxdb3-ref-iiot_influxdb-data >/dev/null 2>&1; then
        warn "No license-validated volume found — a new validation email will be sent"
        NEEDS_VALIDATION=1
    else
        info "License-validated volume present — no email click needed this run"
    fi
else
    info "${BOLD}InfluxDB 3 Enterprise requires a trial license tied to an email address.${RESET}"
    info "A validation link will be sent to the address you provide. You'll need to"
    info "click it on first run (~1 minute). The resulting license is cached in a Docker"
    info "volume; future runs skip this step unless you --fresh."
    echo "${FG_GREY}│${RESET}"
    printf "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}Enter email for the Enterprise trial license: ${RESET}"
    read -r EMAIL
    if [[ -z "$EMAIL" ]]; then fail "empty email, aborting"; exit 1; fi
    if [[ "$EMAIL" != *@* ]]; then fail "that doesn't look like an email address"; exit 1; fi
    [[ -f .env ]] || cp .env.example .env
    if grep -q '^INFLUXDB3_ENTERPRISE_EMAIL=' .env; then
        python3 - "$EMAIL" <<'PY'
import pathlib, re, sys
email = sys.argv[1]
p = pathlib.Path(".env")
p.write_text(re.sub(r'^INFLUXDB3_ENTERPRISE_EMAIL=.*$',
                    f'INFLUXDB3_ENTERPRISE_EMAIL={email}',
                    p.read_text(), flags=re.M))
PY
    else
        echo "INFLUXDB3_ENTERPRISE_EMAIL=${EMAIL}" >> .env
    fi
    ok "Registered ${BOLD}$EMAIL${RESET} — written to .env"
    NEEDS_VALIDATION=1
fi
close_step

# ── bring up ──────────────────────────────────────────────────────────────
step "Bringing up the stack"
cmd "docker compose up -d"
docker compose up -d 2>&1 | sed "s|^|${FG_GREY}│${RESET}  ${DIM}|" | sed "s|$|${RESET}|"
ok "Compose up issued"
close_step

# ── license validation (if fresh) ─────────────────────────────────────────
if [[ $NEEDS_VALIDATION -eq 1 ]]; then
    step "License validation — action required"
    echo "${FG_GREY}│${RESET}"
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}┌──────────────────────────────────────────────────────────┐${RESET}"
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}│${RESET}  ${BOLD}CHECK YOUR EMAIL: ${EMAIL}${RESET}"
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}│${RESET}  ${BOLD}Click the validation link.${RESET}"
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}│${RESET}  The demo continues automatically once the license"
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}│${RESET}  is verified (up to 10 minutes)."
    echo "${FG_GREY}│${RESET}  ${BOLD}${FG_YELLOW}└──────────────────────────────────────────────────────────┘${RESET}"
    echo "${FG_GREY}│${RESET}"
    # `docker compose logs` writes to STDERR for non-TTY containers — `2>&1`
    # is required or the grep sees nothing. Read the FULL log here (not
    # --tail N): once the server is licensed it stays licensed, but the
    # success message gets buried under hundreds of /health 401 lines as
    # time passes, so a small tail can scroll past the line we're looking
    # for. This step only runs on first boot anyway.
    if ! spin_until "Waiting for license verification" \
        "docker logs iiot-influxdb3 2>&1 | grep -q 'Trial Enterprise license verified\\|valid license found'" 600; then
        fail "License verification timed out after 10 minutes."
        fail "Check your inbox (including spam) and re-run: ./scripts/demo.sh"
        exit 1
    fi
    ok "License verified — continuing"
    close_step
fi

# ── wait for init to finish ───────────────────────────────────────────────
step "Stack initialization"
spin_until "influxdb3 accepting HTTP (any response)" \
    "curl -s --max-time 2 -o /dev/null http://localhost:8181/health" 90 \
    || { fail "influxdb3 HTTP not responding"; docker logs --tail 30 iiot-influxdb3 2>&1; exit 1; }
# NOTE: docker logs writes to stderr for non-TTY containers. Use 2>&1 (not
# 2>/dev/null) or the downstream grep sees nothing and waits forever.
spin_until "influxdb3-init complete (DB, caches, triggers)" \
    "docker logs --tail 50 iiot-influxdb3-init 2>&1 | grep -q 'initialization complete'" 60 \
    || { fail "init did not complete"; docker logs iiot-influxdb3-init 2>&1; exit 1; }
spin_until "simulator writing line protocol" \
    "docker logs --tail 20 iiot-simulator 2>&1 | grep -qE 'starting simulator|tick=[0-9]+'" 60 \
    || { fail "simulator not writing"; docker logs --tail 30 iiot-simulator 2>&1; exit 1; }
spin_until "UI serving on :8080" \
    "curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/ | grep -q 200" 60 \
    || { fail "UI not responding"; docker logs --tail 30 iiot-ui 2>&1; exit 1; }
close_step

# ── open browser ──────────────────────────────────────────────────────────
step "Opening UI"
info "UI at: ${BOLD}${FG_CYAN}http://localhost:8080${RESET}"
open_browser "http://localhost:8080"
ok "Browser opened (or URL printed above)"
info "Give the dashboard 5-10 seconds to populate KPIs, the andon board, and OEE charts."
info "Look for the ${BOLD}⚡ served by Processing Engine: N ms${RESET} badge on the andon panel —"
info "that's the live latency of the request_andon_board plugin call."
close_step

# ── scenario: unplanned downtime ─────────────────────────────────────────
step "Firing scenario: unplanned_downtime_cascade"
note "Flips Line 2 / Station 4 to state=stopped (reason=tool_change)."
note "Cascades idle states to upstream (starved) and downstream (blocked) machines."
note "The WAL trigger (wal_downtime_detector.py) sees the transition and writes alerts."
note "Watch the UI — banner flips to ${BOLD}DEGRADED${RESET}, L2-S4 cell turns red, alerts fill."
info "Running the scenario (~90s — 10s baseline + 60s hold + 5s recovery + 15s buffer)."
cmd "make scenario name=unplanned_downtime_cascade"
echo "${FG_GREY}│${RESET}"
docker compose --profile scenarios run --rm \
    -e SCENARIO=unplanned_downtime_cascade scenarios 2>&1 \
    | grep --line-buffered -E "scenario step|DONE" \
    | sed -u "s|^|${FG_GREY}│${RESET}  ${DIM}|; s|\$|${RESET}|" \
    || true
ok "Scenario complete"
info "Giving the Processing Engine a moment to flush alert rows..."
sleep 3
close_step

# ── show alerts ───────────────────────────────────────────────────────────
step "Alerts written by the WAL plugin"
note "These rows were written by wal_downtime_detector.py, NOT by the simulator."
note "The plugin only fires on state TRANSITIONS, so repeated stopped writes don't spam."
note "Latency below is the wall-clock /api/v3/query_sql round-trip from this host."
cmd "SELECT time, line_id, machine_id, severity, reason, value FROM alerts WHERE source='wal_downtime_detector' ORDER BY time DESC LIMIT 5"
echo "${FG_GREY}│${RESET}"
timed_query_table "SELECT time, line_id, machine_id, severity, reason, value FROM alerts WHERE source='wal_downtime_detector' ORDER BY time DESC LIMIT 5" \
    | sed "s|^|${FG_GREY}│${RESET}  |"
close_step

# ── request trigger ──────────────────────────────────────────────────────
step "Calling the Request trigger (HTTP API backed by a Python plugin)"
note "GET /api/v3/engine/andon_board"
note "The server runs request_andon_board.py inline and returns the full plant view."
note "Same call the browser's andon panel makes — the latency below should match the badge."
cmd "curl -H 'Authorization: Bearer \$TOKEN' http://localhost:8181/api/v3/engine/andon_board"
echo "${FG_GREY}│${RESET}"
timed_get_json "http://localhost:8181/api/v3/engine/andon_board" \
    | head -60 \
    | sed "s|^|${FG_GREY}│${RESET}  |"
info "(JSON truncated to first 60 lines; full payload contains all 3 lines × 8 stations.)"
close_step

# ── LVC ──────────────────────────────────────────────────────────────────
step "Last Value Cache — 24 machines, single-digit ms"
note "Read via the table-valued function last_cache('machine_state','machine_state_last')."
note "Same cache request_andon_board reads on every request (and the plant banner every 2s)."
cmd "SELECT COUNT(*) AS machines_in_cache FROM last_cache('machine_state','machine_state_last')"
echo "${FG_GREY}│${RESET}"
timed_query_table "SELECT COUNT(*) AS machines_in_cache FROM last_cache('machine_state','machine_state_last')" \
    | sed "s|^|${FG_GREY}│${RESET}  |"
close_step

# ── summary ───────────────────────────────────────────────────────────────
echo
echo "${BOLD}${FG_GREEN}╔══════════════════════════════════════════════════════════════════╗${RESET}"
echo "${BOLD}${FG_GREEN}║${RESET}  ${BOLD}Demo complete.${RESET}                                                  ${BOLD}${FG_GREEN}║${RESET}"
echo "${BOLD}${FG_GREEN}╚══════════════════════════════════════════════════════════════════╝${RESET}"
echo
echo "  Next things to try:"
echo "    ${FG_MAGENTA}make scenario name=tool_wear_quality_drift${RESET}   # 5-min drift; fires wal_quality_excursion"
echo "    ${FG_MAGENTA}make cli${RESET}                                      # shell into the DB with TOKEN + iql helper"
echo "    ${FG_MAGENTA}make cli-example name=line-oee${RESET}                # curated examples in CLI_EXAMPLES.md"
echo "    ${FG_MAGENTA}make cli-example name=cache-distinct${RESET}          # see the Distinct Value Cache in action"
echo "    ${FG_MAGENTA}./.venv/bin/pytest tests/test_plugins -v${RESET}      # all 22 plugin unit tests"
echo
echo "  ${FG_GREY}UI: ${FG_CYAN}http://localhost:8080${RESET}    ${FG_GREY}API: ${FG_CYAN}http://localhost:8181${RESET}"
echo "  ${FG_GREY}Stop with:${RESET}  ${FG_MAGENTA}make down${RESET}  (preserves data)  ${FG_GREY}or${RESET}  ${FG_MAGENTA}make clean${RESET}  (wipes everything)"
echo
