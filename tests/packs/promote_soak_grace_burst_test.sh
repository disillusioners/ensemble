#!/usr/bin/env bash
# tests/packs/promote_soak_grace_burst_test.sh
#
# Pack: promote_soak_grace_burst_test
# Scope: A5(i) — a HEALTHY release under a simulated burst is NOT rolled
#   back (requirements.md §5(i) + decisions §3a A1.4).
#
# WHAT THIS PACK PROVES
# ----------------------
# PART A — the fake-probe unit legs (the scripted classification
#   stream, requirements §5(i)'s observable):
#     U1  transient(grace-eligible), transient(grace-eligible), green,
#         green, green  ⇒ consecutive_green ≥ 3 and gate_soak returns 0;
#     U2  green, green, hard-red                     ⇒ returns 1;
#     U3  green, grace-held × 3                      ⇒ returns 1 at
#         cap-reach (the never-green trigger is intact).
#
# PART B — the REAL leg: the REAL gate_soak against a REAL daemon
#   under the CONCURRENT three-stream burst (adjudicated R-1):
#     (1) the MCP warmup against a deliberately DEAD host (the C2 DNS
#         pre-check must refuse it fast — that IS the stream),
#     (2) the job-recovery reconcile,
#     (3) the auto-continue resume burst,
#   all three OVERLAPPING on one event loop, with the checkpoint pool
#   under pressure in the incident's healthy-busy shape (the checked-out
#   count RISES AND FALLS, recovery ticks between the degradations).
#   The observable: the soak exits 0, the SCRATCH journal carries NO
#   rollback/quarantine event and `current` is unchanged.
#
# THE FENCE (binding — see the report's attestation)
# ---------------------------------------------------
#   * the daemon boots against a THROWAWAY scratch PostgreSQL database
#     (a throwaway LOGIN role + an owned database, DROPPED at cleanup);
#   * the five POSTGRES_* variables are pinned PER VARIABLE (the
#     `env -u POSTGRES_*` glob idiom is a SILENT no-op);
#   * HOME is a FAKE_HOME (mktemp) with an escape guard — no live-shaped
#     HOME-derived path is ever resolved;
#   * the PORT is explicit and throwaway;
#   * the daemon child REFUSES to boot against a non-scratch database;
#   * the scratch journal lives under the FAKE_HOME scratch install root.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Internal watchdog: 900s (this pack boots a REAL daemon).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

# shellcheck source=/dev/null
. "$REPO_ROOT/tests/helpers/soak_gate_pack_common.sh"

PACK_NAME="promote_soak_grace_burst_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

soak_pack_fake_home
soak_pack_scrub_env

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-grace-burst.XXXXXX")" || {
    echo "FAIL: cannot create the workdir"; echo "RESULT: FAIL"; exit 1;
}
CHILD_PID=""
cleanup() {
    [ -n "$CHILD_PID" ] && kill "$CHILD_PID" 2>/dev/null
    [ -n "$CHILD_PID" ] && wait "$CHILD_PID" 2>/dev/null
    soak_pack_drop_scratch_db
    # SOAK_PACK_KEEP=1 keeps the workdir (the child's log + the report)
    # for post-mortem inspection — the default is a full cleanup.
    if [ "${SOAK_PACK_KEEP:-0}" = "1" ]; then
        echo "KEPT: $WORKDIR"
        return
    fi
    rm -rf "$WORKDIR" "$SOAK_PACK_FAKE_HOME" 2>/dev/null || true
}
trap cleanup EXIT

# ── the REAL gate_soak (scripts/upgrade/lib.sh) ──────────────────────
export ENSEMBLE_UPGRADE_SCRIPTS_DIR="$REPO_ROOT/scripts/upgrade"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/upgrade/lib.sh" || {
    echo "FAIL: cannot source lib.sh"; echo "RESULT: FAIL"; exit 1;
}

LOG_TAG="soak-grace-burst-pack"
UP_TARGET="pack"

# The journal + the lock heartbeat are stubbed for PART A (the pack
# never writes a real journal there); PART B's journal observations
# come from the SCRATCH install root.
_JOURNAL_LOG="$WORKDIR/soak-journal.log"
journal_history_append() { printf '%s\n' "$*" >> "$_JOURNAL_LOG"; return 0; }
lock_heartbeat() { return 0; }
gate_version() { return 0; }

# ══════════════════════════════════════════════════════════════════════
# PART A — the fake-probe unit legs
# ══════════════════════════════════════════════════════════════════════
_BODY_GRACED='{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"],"component_classes":{"database":"probe_timeout","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":true,"grace_reason":"probe_timeout","grace_expires_at":"2099-01-01T00:00:00+00:00","scope":"load","holds":1},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}'
_BODY_GREEN='{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"inflight_turns":0}}'
_BODY_HARD='{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 failed"],"component_classes":{"database":"hard_component_red","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":false},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}'

_SCRIPT_LIVEZ=()
_SCRIPT_READYZ=()
_SCRIPT_LIVEZ_IDX=0
_SCRIPT_READYZ_IDX=0
_SCRIPT_CALLS=0
_FAKE_NOW=0

# The REAL implementations are captured BEFORE the stubs replace them —
# PART B drives the REAL probe path (the scripted stub would answer
# every probe from the replayed stream and the soak would never see
# the daemon).
_REAL_PROBE_FUNC="$(declare -f _probe_once_classified)"
_REAL_NOW_FUNC="$(declare -f _now_epoch)"

_probe_once_classified() {
    local path="$1" port="$2" spec
    _SCRIPT_CALLS=$(( _SCRIPT_CALLS + 1 ))
    case "$path" in
        */livez)
            if [ "${#_SCRIPT_LIVEZ[@]}" -eq 0 ]; then
                _PROBE_STATUS="green"; _PROBE_BODY=""; _PROBE_CODE="200"; return 0
            fi
            if [ "$_SCRIPT_LIVEZ_IDX" -ge "${#_SCRIPT_LIVEZ[@]}" ]; then
                spec="${_SCRIPT_LIVEZ[$(( ${#_SCRIPT_LIVEZ[@]} - 1 ))]}"
            else
                spec="${_SCRIPT_LIVEZ[$_SCRIPT_LIVEZ_IDX]}"
                _SCRIPT_LIVEZ_IDX=$(( _SCRIPT_LIVEZ_IDX + 1 ))
            fi
            ;;
        */readyz)
            if [ "${#_SCRIPT_READYZ[@]}" -eq 0 ]; then
                _PROBE_STATUS="green"; _PROBE_BODY=""; _PROBE_CODE="200"; return 0
            fi
            if [ "$_SCRIPT_READYZ_IDX" -ge "${#_SCRIPT_READYZ[@]}" ]; then
                spec="${_SCRIPT_READYZ[$(( ${#_SCRIPT_READYZ[@]} - 1 ))]}"
            else
                spec="${_SCRIPT_READYZ[$_SCRIPT_READYZ_IDX]}"
                _SCRIPT_READYZ_IDX=$(( _SCRIPT_READYZ_IDX + 1 ))
            fi
            _FAKE_NOW=$(( _FAKE_NOW + ADAPTIVE_SOAK_CADENCE_S ))
            ;;
        *)
            _PROBE_STATUS="green"; _PROBE_BODY=""; _PROBE_CODE="200"; return 0
            ;;
    esac
    case "$spec" in
        green)     _PROBE_STATUS="green";     _PROBE_CODE="200"; _PROBE_BODY="$_BODY_GREEN" ;;
        transient) _PROBE_STATUS="transient"; _PROBE_CODE="503"; _PROBE_BODY="$_BODY_GRACED" ;;
        held)      _PROBE_STATUS="transient"; _PROBE_CODE="503"; _PROBE_BODY="$_BODY_GRACED" ;;
        red)       _PROBE_STATUS="red";       _PROBE_CODE="500"; _PROBE_BODY="$_BODY_HARD" ;;
        *)         _PROBE_STATUS="green";     _PROBE_CODE="200"; _PROBE_BODY="$_BODY_GREEN" ;;
    esac
    case "$_PROBE_STATUS" in
        green) return 0 ;;
        red)   return 1 ;;
        *)     return 2 ;;
    esac
}

_now_epoch() { printf '%s' "$_FAKE_NOW"; }
# The scripted clock replaces the real sleep ONLY for PART A — the
# function is removed again before PART B (the real soak needs the REAL
# cadence: the sleep stub would make the soak's window meaningless).
sleep() { :; }

# The PORT the scripted probes address (PART A never touches the
# network — the stub answers every probe).
PORT=9999

run_stream() {  # run_stream <name> <expected_rc> <cap> <spec...>
    local name="$1" expected_rc="$2" cap="$3"; shift 3
    _SCRIPT_LIVEZ=(); _SCRIPT_READYZ=()
    local spec
    for spec in "$@"; do
        _SCRIPT_LIVEZ+=("green")
        _SCRIPT_READYZ+=("${spec}")
    done
    _SCRIPT_LIVEZ_IDX=0; _SCRIPT_READYZ_IDX=0; _SCRIPT_CALLS=0; _FAKE_NOW=0
    : > "$_JOURNAL_LOG"
    ADAPTIVE_SOAK_FLOOR_S=0
    ADAPTIVE_SOAK_CAP_S="$cap"
    ADAPTIVE_SOAK_CONSECUTIVE=3
    ADAPTIVE_SOAK_CADENCE_S=1
    GRACE_EXTENSION_CAP_S=2; GRACE_EXTENSION_CAP_S_INT=2
    local rc
    gate_soak 300 "v-test" > "$WORKDIR/${name}.out" 2>&1
    rc=$?
    if [ "$_SCRIPT_CALLS" -lt 2 ]; then
        soak_pack_report "$name" 0 "the scripted stream was NOT consumed (calls=${_SCRIPT_CALLS}) — vacuous"
        return
    fi
    if [ "$rc" != "$expected_rc" ]; then
        soak_pack_report "$name" 0 "expected rc=${expected_rc} got rc=${rc} (calls=${_SCRIPT_CALLS})"
        return
    fi
    soak_pack_report "$name" 1 "rc=${rc} (calls=${_SCRIPT_CALLS})"
}

echo
echo "--- PART A: the fake-probe unit legs (requirements §5(i)) ---"
run_stream "U1 transient,transient,green,green,green reaches 3 greens" 0 8 transient transient green green green
run_stream "U2 green,green,hard-red returns 1" 1 8 green green red
run_stream "U3 green,grace-held x3 returns 1 at cap-reach" 1 8 green held held held
if grep -q "cap-reach" "$WORKDIR/U3 green,grace-held x3 returns 1 at cap-reach.out" 2>/dev/null; then
    soak_pack_report "U3b the never-green stream ended at cap-reach (not an early exit)" 1
else
    soak_pack_report "U3b the never-green stream ended at cap-reach (not an early exit)" 0 \
        "$(tail -3 "$WORKDIR/U3 green,grace-held x3 returns 1 at cap-reach.out" 2>/dev/null | tr '\n' ' ')"
fi

# ══════════════════════════════════════════════════════════════════════
# PART B — the REAL leg: the concurrent three-stream burst
# ══════════════════════════════════════════════════════════════════════
echo
echo "--- PART B: the REAL daemon under the CONCURRENT three-stream burst ---"

# The scripted stubs end here — PART B drives the REAL probe path, the
# REAL clock and the REAL sleep.
eval "$_REAL_PROBE_FUNC"
eval "$_REAL_NOW_FUNC"
unset -f sleep

# The scratch database (a throwaway LOGIN role + an owned database).
soak_pack_scratch_db
echo "FENCE: scratch database = ${SOAK_PG_DB} (role ${SOAK_PG_ROLE})"

# The scratch install root — the journal the promote lane would own.
# It lives UNDER THE FAKE_HOME (never the live install root) and the
# daemon's SELF-ENV resolves it there.
SCRATCH_INSTALL="$SOAK_PACK_FAKE_HOME/agents-ensemble-demo"
mkdir -p "$SCRATCH_INSTALL/releases"
printf '{"current":"v0.0.0-scratch","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}' \
    > "$SCRATCH_INSTALL/releases/state.json"
SCRATCH_JOURNAL="$SCRATCH_INSTALL/releases/state.json"

BURST_PORT="$(soak_pack_free_port)" || exit 1
BURST_DURATION=150
REPORT="$WORKDIR/burst-report.json"
READY="$WORKDIR/burst.ready"
rm -f "$REPORT" "$READY"

# The child boots the REAL daemon: the scratch DB, the explicit PORT,
# the FAKE_HOME, and the scratch install root (SELF-ENV=demo resolves
# it under the FAKE_HOME). The built-in context7/plane warmups are
# disabled AT BOOT so the boot never reaches for the npm registry — the
# burst's dead-host stream owns context7 instead (through the same v3
# schema path).
env | sort > "$WORKDIR/child-env.txt"
env -u POSTGRES_URL -u DAEMON_PORT -u PORT \
    HOME="$SOAK_PACK_FAKE_HOME" \
    POSTGRES_HOST="$POSTGRES_HOST" POSTGRES_PORT="$POSTGRES_PORT" \
    POSTGRES_DB="$POSTGRES_DB" POSTGRES_USER="$POSTGRES_USER" POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    PORT="$BURST_PORT" \
    ENSEMBLE_SELF_ENV=demo \
    MCP_DISABLE_BUILT_IN_CONTEXT7=true \
    MCP_DISABLE_BUILT_IN_PLANE=true \
    PYTHONPATH="$REPO_ROOT" \
    "$SOAK_PACK_PYTHON" "$REPO_ROOT/tests/helpers/soak_gate_daemon_child.py" \
        --port "$BURST_PORT" --mode burst --report "$REPORT" --ready-file "$READY" \
        --duration "$BURST_DURATION" > "$WORKDIR/child.log" 2>&1 &
CHILD_PID=$!

# Wait for the daemon to answer /livez.
BURST_UP=0
for _ in $(seq 1 90); do
    sleep 2
    if [ -f "$READY" ]; then
        code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 "http://127.0.0.1:$BURST_PORT/livez" 2>/dev/null || echo 000)"
        if [ "$code" = "200" ]; then BURST_UP=1; break; fi
    fi
    kill -0 "$CHILD_PID" 2>/dev/null || break
done
if [ "$BURST_UP" = "1" ]; then
    soak_pack_report "B0 the real daemon is up on the throwaway port" 1 "port=${BURST_PORT} db=${SOAK_PG_DB}"
else
    soak_pack_report "B0 the real daemon is up on the throwaway port" 0 \
        "the child never served /livez (see $WORKDIR/child.log)"
    soak_pack_report "B1 the soak exits 0 under the burst" 0 "the daemon never came up"
    soak_pack_report "B2 no rollback/quarantine event in the scratch journal" 0 "the daemon never came up"
    soak_pack_report "B3 the scratch journal current is unchanged" 0 "the daemon never came up"
    soak_pack_report "B4 the grace engaged during the burst" 0 "the daemon never came up"
    soak_pack_report "B5 the healthy-busy shape ROSE AND FELL" 0 "the daemon never came up"
    soak_pack_report "B6 the dead-host MCP warmup was refused fast" 0 "the daemon never came up"
    soak_pack_report "B7 the daemon stayed up through the burst" 0 "the daemon never came up"
    soak_pack_summary 10
fi

# The REAL soak — the SHIPPED constants (floor 60 s, cap 120 s,
# cadence 15 s, 3 consecutive greens). A shrunk window would make the
# measurement meaningless.
ADAPTIVE_SOAK_FLOOR_S=60
ADAPTIVE_SOAK_CAP_S=120
ADAPTIVE_SOAK_CONSECUTIVE=3
ADAPTIVE_SOAK_CADENCE_S=15
PORT="$BURST_PORT"
SOAK_T0="$(date +%s)"
gate_soak 300 "v-test" > "$WORKDIR/soak.out" 2>&1
SOAK_RC=$?
SOAK_T1="$(date +%s)"
SOAK_ELAPSED=$(( SOAK_T1 - SOAK_T0 ))

# Give the child a moment to flush its last samples, then stop it.
sleep 3
kill "$CHILD_PID" 2>/dev/null
wait "$CHILD_PID" 2>/dev/null
CHILD_PID=""

if [ "$SOAK_RC" = "0" ]; then
    soak_pack_report "B1 the soak exits 0 under the burst (the release is NOT rolled back)" 1 \
        "rc=0 elapsed=${SOAK_ELAPSED}s"
else
    soak_pack_report "B1 the soak exits 0 under the burst (the release is NOT rolled back)" 0 \
        "rc=${SOAK_RC} elapsed=${SOAK_ELAPSED}s: $(tail -4 "$WORKDIR/soak.out" | tr '\n' ' ')"
fi

# ── the scratch journal: NO rollback / quarantine, current unchanged ─
if [ -f "$SCRATCH_JOURNAL" ]; then
    if grep -q '"event":"rollback"' "$SCRATCH_JOURNAL" 2>/dev/null; then
        soak_pack_report "B2 no rollback/quarantine event in the scratch journal" 0 \
            "the scratch journal carries a rollback event"
    elif grep -q '"event":"quarantine"' "$SCRATCH_JOURNAL" 2>/dev/null; then
        soak_pack_report "B2 no rollback/quarantine event in the scratch journal" 0 \
            "the scratch journal carries a quarantine event"
    else
        soak_pack_report "B2 no rollback/quarantine event in the scratch journal" 1
    fi
    # NON-VACUITY: the daemon really wrote to the scratch journal.
    if grep -q '"event":"mcp_server_state"' "$SCRATCH_JOURNAL" 2>/dev/null; then
        soak_pack_report "B3a the scratch journal is LIVE (the daemon wrote to it)" 1
    else
        soak_pack_report "B3a the scratch journal is LIVE (the daemon wrote to it)" 0 \
            "events: $(grep -o '"event":"[a-z_]*"' "$SCRATCH_JOURNAL" | tr '\n' ' ')"
    fi
    JOURNAL_CURRENT="$(soak_pack_journal_field "$SCRATCH_INSTALL" current)"
    if [ "$JOURNAL_CURRENT" = "v0.0.0-scratch" ]; then
        soak_pack_report "B3 the scratch journal current is unchanged" 1 "current=${JOURNAL_CURRENT}"
    else
        soak_pack_report "B3 the scratch journal current is unchanged" 0 "current=${JOURNAL_CURRENT}"
    fi
else
    soak_pack_report "B2 no rollback/quarantine event in the scratch journal" 0 "no scratch journal at ${SCRATCH_JOURNAL}"
    soak_pack_report "B3a the scratch journal is LIVE (the daemon wrote to it)" 0 "no scratch journal"
    soak_pack_report "B3 the scratch journal current is unchanged" 0 "no scratch journal"
fi

# ── the child's report: the burst's observables ─────────────────────
if [ -f "$REPORT" ]; then
    # The grace engaged (a grace-eligible degradation was HELD).
    if "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
samples = data.get("readyz_samples") or []
ok = any(
    (s.get("view") or {}).get("grace_active") is True
    and ((s.get("view") or {}).get("status") == "degraded")
    for s in samples
)
sys.exit(0 if ok else 1)
PYEOF
    then
        soak_pack_report "B4 the grace ENGAGED during the burst (a degraded tick was held)" 1
    else
        soak_pack_report "B4 the grace ENGAGED during the burst (a degraded tick was held)" 0 \
            "no degraded+grace_active sample in the report"
    fi
    # The healthy-busy shape: the pool's in-use count ROSE AND FELL.
    if "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
series = [int(p.get("in_use", 0)) for p in (data.get("pool_series") or [])]
if len(series) < 4:
    sys.exit(1)
rose = max(series) > min(series)
fell = any(series[i] < series[i - 1] for i in range(1, len(series)))
sys.exit(0 if (rose and fell) else 1)
PYEOF
    then
        soak_pack_report "B5 the healthy-busy shape ROSE AND FELL (recovery ticks present)" 1
    else
        soak_pack_report "B5 the healthy-busy shape ROSE AND FELL (recovery ticks present)" 0 \
            "the pool series never oscillated"
    fi
    # The V2 numbers (the measured discrimination) — printed for the report.
    "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
series = [int(p.get("in_use", 0)) for p in (data.get("pool_series") or [])]
leaks = [
    s for s in (data.get("readyz_samples") or [])
    if (s.get("view") or {}).get("leak_detected")
]
print(f"V2 healthy-busy in_use series: {series}")
print(f"V2 max={max(series) if series else 0} min={min(series) if series else 0} "
      f"rises={sum(1 for i in range(1, len(series)) if series[i] > series[i-1])} "
      f"falls={sum(1 for i in range(1, len(series)) if series[i] < series[i-1])} "
      f"leak_samples={len(leaks)}")
PYEOF
    # The dead-host MCP warmup: the C2 refusal was FAST and the server
    # was quarantined.
    if "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
mcp = (data.get("streams") or {}).get("mcp_warmup") or {}
elapsed = float(mcp.get("init_elapsed_s", 999))
sys.exit(0 if (mcp.get("quarantined") is True and elapsed < 10.0) else 1)
PYEOF
    then
        soak_pack_report "B6 the dead-host MCP warmup was refused FAST (C2) and quarantined" 1
    else
        soak_pack_report "B6 the dead-host MCP warmup was refused FAST (C2) and quarantined" 0 \
            "$("$SOAK_PACK_PYTHON" -c "
import json,sys
d=json.load(open('$REPORT'))
print(json.dumps((d.get('streams') or {}).get('mcp_warmup') or {}, default=str)[:400])
")"
    fi
    # The daemon stayed up through the burst.
    if "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
samples = data.get("readyz_samples") or []
errors = [s for s in samples if (s.get("view") or {}).get("status") is None]
sys.exit(0 if (len(samples) >= 5 and not errors) else 1)
PYEOF
    then
        soak_pack_report "B7 the daemon stayed up through the burst (no unreadable /readyz)" 1
    else
        soak_pack_report "B7 the daemon stayed up through the burst (no unreadable /readyz)" 0 \
            "the /readyz sampler saw an unreadable body"
    fi
    # The three streams really ran.
    if "$SOAK_PACK_PYTHON" - "$REPORT" <<'PYEOF'
import json, sys
data = json.load(open(sys.argv[1]))
streams = data.get("streams") or {}
mcp = streams.get("mcp_warmup") or {}
jr = streams.get("job_recovery") or {}
ac = streams.get("auto_continue") or {}
pp = streams.get("pool_pressure") or {}
ok = (
    bool(mcp.get("outcome"))
    and int(jr.get("ticks", 0)) >= 1
    and int(ac.get("passes", 0)) >= 1
    and int(pp.get("ticks", 0)) >= 1
)
sys.exit(0 if ok else 1)
PYEOF
    then
        soak_pack_report "B8 all THREE streams ran concurrently (MCP warmup + reconcile + resume)" 1
    else
        soak_pack_report "B8 all THREE streams ran concurrently (MCP warmup + reconcile + resume)" 0 \
            "a stream did not run"
    fi
else
    soak_pack_report "B4 the grace ENGAGED during the burst (a degraded tick was held)" 0 "no report at ${REPORT}"
    soak_pack_report "B5 the healthy-busy shape ROSE AND FELL (recovery ticks present)" 0 "no report"
    soak_pack_report "B6 the dead-host MCP warmup was refused FAST (C2) and quarantined" 0 "no report"
    soak_pack_report "B7 the daemon stayed up through the burst (no unreadable /readyz)" 0 "no report"
    soak_pack_report "B8 all THREE streams ran concurrently (MCP warmup + reconcile + resume)" 0 "no report"
fi

# ── the fence attestation ───────────────────────────────────────────
soak_pack_assert_scratch_db "$POSTGRES_DB"
soak_pack_report "B9 the fence held (the boot used the scratch DB on the throwaway port)" 1 \
    "db=${POSTGRES_DB} port=${BURST_PORT} home=${SOAK_PACK_FAKE_HOME}"

soak_pack_summary 14
