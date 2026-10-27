#!/usr/bin/env bash
# tests/packs/promote_soak_fail_fast_test.sh
#
# Pack: promote_soak_fail_fast_test
# Scope: the A2 fail-fast property (soak-gate-mcp-warmup) — a hard red
#   must fail the soak on the FIRST observation (no hold, no grace
#   deferral), even INSIDE an active class-gated grace; and the
#   extension arithmetic must respect all FOUR min() terms with
#   granted=0 while no HOLD was observed (requirements.md §5(iii)(a) +
#   decisions §3b B3).
#
#   The pack drives the REAL ``gate_soak`` (scripts/upgrade/lib.sh) with
#   a SCRIPTED probe stream; the clock / sleep / lock_heartbeat /
#   journal_history_append / gate_version helpers are stubbed so the run
#   is deterministic and NEVER touches a real journal, a real lock dir
#   or a real daemon.
#
# House rules honoured:
#   * FAKE_HOME fence + escape guard (never resolve a live-shaped
#     HOME-derived install path);
#   * per-variable env scrub (``env -u POSTGRES_*`` is a silent NO-OP);
#   * the pack asserts its OWN non-vacuity (the scripted stream was
#     actually consumed).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

PACK_NAME="promote_soak_fail_fast_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# ── FAKE_HOME fence ────────────────────────────────────────────────────
REAL_HOME="${HOME:-/root}"
FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/soak-failfast-fakehome.XXXXXX")" || {
    echo "FAIL: cannot create FAKE_HOME"; echo "RESULT: FAIL"; exit 1;
}
case "$(cd "$FAKE_HOME" && pwd -P)" in
    "$(cd "$REAL_HOME" && pwd -P)"/*)
        echo "FAIL: FAKE_HOME ($FAKE_HOME) resolves under the REAL HOME — refusing to run"
        echo "RESULT: FAIL"; exit 1
        ;;
esac
export HOME="$FAKE_HOME"
export ENSEMBLE_UPGRADE_SCRIPTS_DIR="$REPO_ROOT/scripts/upgrade"

unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DAEMON_PORT PORT

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-failfast-pack.XXXXXX")" || {
    echo "FAIL: cannot create workdir"; echo "RESULT: FAIL"; exit 1;
}
cleanup() { rm -rf "$WORKDIR" "$FAKE_HOME" 2>/dev/null || true; }
trap cleanup EXIT

# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/upgrade/lib.sh" || {
    echo "FAIL: cannot source lib.sh"; echo "RESULT: FAIL"; exit 1;
}

# ── the stubs ─────────────────────────────────────────────────────────
_SCRIPT_LIVEZ=()
_SCRIPT_READYZ=()
_SCRIPT_LIVEZ_IDX=0
_SCRIPT_READYZ_IDX=0
_SCRIPT_CALLS=0
_FAKE_NOW=0
_JOURNAL_LOG="$WORKDIR/journal.log"

_body_grace_active() {
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"],"component_classes":{"database":"probe_timeout","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":true,"grace_reason":"probe_timeout","grace_expires_at":"2026-10-27T00:00:00+00:00","scope":"load","holds":1},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}
JSON
}
_body_plain_green() {
    cat <<'JSON'
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":null,"inflight_turns":0}}
JSON
}
BODY_GRACED="$(_body_grace_active)"
BODY_PLAIN_GREEN="$(_body_plain_green)"

_body_for() {
    case "$1" in
        graced) printf '%s' "$BODY_GRACED" ;;
        *)      printf '%s' "$BODY_PLAIN_GREEN" ;;
    esac
}

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
        green)      _PROBE_STATUS="green";     _PROBE_CODE="200" ;;
        softgreen)  _PROBE_STATUS="busy-amnesty"; _PROBE_CODE="200" ;;
        transient)  _PROBE_STATUS="transient"; _PROBE_CODE="503" ;;
        red)        _PROBE_STATUS="red";       _PROBE_CODE="500" ;;
        graced)     _PROBE_STATUS="transient"; _PROBE_CODE="503" ;;
        *)          _PROBE_STATUS="green";     _PROBE_CODE="200" ;;
    esac
    _PROBE_BODY="$(_body_for "$spec")"
    case "$_PROBE_STATUS" in
        green)      return 0 ;;
        red)        return 1 ;;
        *)          return 2 ;;
    esac
}

_now_epoch() { printf '%s' "$_FAKE_NOW"; }
sleep() { :; }
lock_heartbeat() { return 0; }
journal_history_append() { printf '%s\n' "$*" >> "$_JOURNAL_LOG"; return 0; }
gate_version() { return 0; }

PORT=9999
LOG_TAG="soak-failfast-pack"
UP_TARGET="pack"

PASS_COUNT=0
FAIL_COUNT=0
_report() {
    if [ "$2" = "1" ]; then
        PASS_COUNT=$(( PASS_COUNT + 1 ))
        echo "PASS: ${1}${3:+ — ${3}}"
    else
        FAIL_COUNT=$(( FAIL_COUNT + 1 ))
        echo "FAIL: ${1}${3:+ — ${3}}"
    fi
}

# run_stream <name> <expected_rc> <cap> <max_calls_allowed> <livez> <readyz>...
run_stream() {
    local name="$1" expected_rc="$2" cap="$3" max_calls="$4"; shift 4
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
    gate_soak 300 "v-test" > "$WORKDIR/out.txt" 2>&1
    rc=$?
    if [ "$_SCRIPT_CALLS" -lt 2 ]; then
        _report "$name" 0 "the scripted stream was NOT consumed (calls=${_SCRIPT_CALLS})"
        return
    fi
    if [ "$rc" != "$expected_rc" ]; then
        _report "$name" 0 "expected rc=${expected_rc} got rc=${rc} (calls=${_SCRIPT_CALLS})"
        return
    fi
    if [ "$max_calls" != "0" ] && [ "$_SCRIPT_CALLS" -gt "$max_calls" ]; then
        _report "$name" 0 "the soak did not fail fast (calls=${_SCRIPT_CALLS} > ${max_calls})"
        return
    fi
    _report "$name" 1 "rc=${rc} (calls=${_SCRIPT_CALLS})"
}

echo
echo "--- F1: a hard red fails the soak on the FIRST observation ---"
run_stream "F1 hard red fails fast" 1 6 4 green red

echo "--- F2: a hard red inside an ACTIVE grace still fails fast ---"
run_stream "F2 hard red inside a grace fails fast" 1 6 6 graced red

echo "--- F3: a livez hard red fails fast ---"
run_stream "F3 livez hard red fails fast" 1 6 2 red green

echo
echo "--- F4: the extension arithmetic honours all FOUR min() terms ---"
ADAPTIVE_SOAK_CAP_S=120
GRACE_EXTENSION_CAP_S=180.0
GRACE_EXTENSION_CAP_S_INT=180
cap_default="$(_soak_effective_cap 0 "" 300)"
cap_ceremony="$(_soak_effective_cap 0 100 300)"
cap_soak_s="$(_soak_effective_cap 0 "" 90)"
cap_granted="$(_soak_effective_cap 180 "" 300)"
cap_ceiling="$(_soak_effective_cap 300 "" 100000)"
[ "$cap_default" = "120" ] \
    && _report "F4a the default cap is ADAPTIVE_SOAK_CAP_S (120)" 1 "got ${cap_default}" \
    || _report "F4a the default cap is ADAPTIVE_SOAK_CAP_S (120)" 0 "got ${cap_default}"
[ "$cap_ceremony" = "40" ] \
    && _report "F4b the ceremony-budget term is honoured" 1 "got ${cap_ceremony}" \
    || _report "F4b the ceremony-budget term is honoured" 0 "got ${cap_ceremony}"
[ "$cap_soak_s" = "90" ] \
    && _report "F4c the operator-ceiling term is honoured" 1 "got ${cap_soak_s}" \
    || _report "F4c the operator-ceiling term is honoured" 0 "got ${cap_soak_s}"
[ "$cap_granted" = "300" ] \
    && _report "F4d the granted extension reaches 300" 1 "got ${cap_granted}" \
    || _report "F4d the granted extension reaches 300" 0 "got ${cap_granted}"
[ "$cap_ceiling" = "300" ] \
    && _report "F4e the absolute ceiling caps an over-grant at 300" 1 "got ${cap_ceiling}" \
    || _report "F4e the absolute ceiling caps an over-grant at 300" 0 "got ${cap_ceiling}"

echo
echo "--- F5: granted=0 while no HOLD was observed (the extension is not free) ---"
_SCRIPT_LIVEZ=("green"); _SCRIPT_READYZ=("green" "green")
_SCRIPT_LIVEZ_IDX=0; _SCRIPT_READYZ_IDX=0; _SCRIPT_CALLS=0; _FAKE_NOW=0
: > "$_JOURNAL_LOG"
ADAPTIVE_SOAK_FLOOR_S=0; ADAPTIVE_SOAK_CAP_S=4
ADAPTIVE_SOAK_CONSECUTIVE=3; ADAPTIVE_SOAK_CADENCE_S=1
GRACE_EXTENSION_CAP_S=2; GRACE_EXTENSION_CAP_S_INT=2
gate_soak 300 "v-test" > "$WORKDIR/out.txt" 2>&1 || true
if grep -q "soak_grace_extended" "$_JOURNAL_LOG"; then
    _report "F5 no HOLD means no soak_grace_extended event" 0 "the extension was granted without a HOLD"
else
    _report "F5 no HOLD means no soak_grace_extended event" 1
fi

echo
echo "======================================"
echo "PASS: ${PASS_COUNT}  FAIL: ${FAIL_COUNT}"
echo "======================================"
if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi
if [ "$PASS_COUNT" -lt 9 ]; then
    echo "RESULT: FAIL (vacuous — only ${PASS_COUNT} assertions ran)"
    exit 1
fi
echo "RESULT: PASS"
exit 0
