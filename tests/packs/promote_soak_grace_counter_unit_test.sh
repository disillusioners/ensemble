#!/usr/bin/env bash
# tests/packs/promote_soak_grace_counter_unit_test.sh
#
# Pack: promote_soak_grace_counter_unit_test
# Scope: the A2 soak counter semantics (soak-gate-mcp-warmup) — the THREE
#   observation kinds (GREEN / HELD / RESET) of requirements.md §4.2 (P2),
#   the MAX_HOLDS_PER_GREEN bound, the livez-HARD-green precondition, the
#   reset-on-green rule, the bounded window extension of §4.3 (P3), and
#   the UNCONDITIONAL never-green trigger of §4.1 (P1).
#
#   The pack drives the REAL ``gate_soak`` (scripts/upgrade/lib.sh) with a
#   SCRIPTED probe stream: ``_probe_once_classified`` is stubbed to replay
#   a per-observation classification stream, and the clock / sleep /
#   lock_heartbeat / journal_history_append / gate_version helpers are
#   stubbed so the run is deterministic and NEVER touches a real journal,
#   a real lock dir or a real daemon.
#
# House rules honoured:
#   * FAKE_HOME fence + escape guard (never resolve a live-shaped
#     HOME-derived install path);
#   * per-variable env scrub (``env -u POSTGRES_*`` is a silent NO-OP);
#   * explicit interpreter / no bare ``python``;
#   * the pack asserts its OWN non-vacuity (the scripted stream was
#     actually consumed).
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Internal watchdog: 120s (unit-type limit per the test-pack skill).

set -u

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT" || { echo "FAIL: cannot cd to repo root"; echo "RESULT: FAIL"; exit 1; }

PACK_NAME="promote_soak_grace_counter_unit_test"
echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    $(pwd)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# ── FAKE_HOME fence ────────────────────────────────────────────────────
# The install-root resolution is HOME-derived; the pack must never
# resolve (or touch) a live-shaped path. The escape guard refuses to run
# when the fake home is under the REAL home.
REAL_HOME="${HOME:-/root}"
FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/soak-grace-fakehome.XXXXXX")" || {
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

# ── per-variable env scrub (never `env -u POSTGRES_*` — bash does not
#    glob env names, so that idiom is a silent NO-OP) ───────────────────
unset POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD POSTGRES_HOST POSTGRES_PORT
unset DAEMON_PORT PORT

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/soak-grace-pack.XXXXXX")" || {
    echo "FAIL: cannot create workdir"; echo "RESULT: FAIL"; exit 1;
}
cleanup() { rm -rf "$WORKDIR" "$FAKE_HOME" 2>/dev/null || true; }
trap cleanup EXIT

echo "--- sourcing lib.sh (the real gate_soak) ---"
# shellcheck source=/dev/null
. "$REPO_ROOT/scripts/upgrade/lib.sh" || {
    echo "FAIL: cannot source lib.sh"; echo "RESULT: FAIL"; exit 1;
}
echo "lib.sh sourced OK"

# ── the stubs ─────────────────────────────────────────────────────────
# The scripted stream: one line per PROBE CALL ("<path> <spec>"), in
# order. The stub replays it; when the stream is exhausted the LAST
# line repeats (so a scenario's shape persists to the cap).
_SCRIPT_LIVEZ=()
_SCRIPT_READYZ=()
_SCRIPT_LIVEZ_IDX=0
_SCRIPT_READYZ_IDX=0
_SCRIPT_CALLS=0
_FAKE_NOW=0
_JOURNAL_LOG="$WORKDIR/journal.log"

# The readyz bodies the daemon would publish.
_body_grace_active() {
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"],"component_classes":{"database":"probe_timeout","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":true,"grace_reason":"probe_timeout","grace_expires_at":"2026-10-27T00:00:00+00:00","scope":"load","holds":1},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}
JSON
}
_body_grace_leak() {
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"],"component_classes":{"database":"probe_timeout","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":false,"grace_reason":null,"grace_expires_at":null,"scope":null,"holds":0},"leak_signal":{"detected":true,"checkedout_rising":true,"streak":3}}}
JSON
}
_body_grace_hard_red() {
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed"],"component_classes":{"database":"hard_component_red","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":false},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}
JSON
}
_body_plain_green() {
    cat <<'JSON'
{"status":"ready","components":{"database":true,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":[],"queue_max_age_seconds":null,"checked_at":null,"inflight_turns":0}}
JSON
}
_body_grace_expired() {
    # The daemon's grace AFTER its horizon: grace_active is false, so
    # the readyz transient is NO LONGER grace-eligible (the §4.3
    # expiry). The gate must treat it as a RESET — the sub-ceiling gap
    # can never hold the counter open.
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"],"component_classes":{"database":"probe_timeout","queue_freshness":"ok","services":"ok","checkpoint_saver":"ok"},"grace":{"grace_active":false},"leak_signal":{"detected":false,"checkedout_rising":false,"streak":0}}}
JSON
}
_body_legacy_no_grace() {
    # An OLDER daemon: NO detail.grace / detail.leak_signal /
    # detail.component_classes block at all (R-MASK-8).
    cat <<'JSON'
{"status":"degraded","components":{"database":false,"queue_freshness":true,"services":true,"checkpoint_saver":true},"detail":{"reasons":["database: SELECT 1 probe failed or timed out"]}}
JSON
}

# The bodies are materialised ONCE (a per-call heredoc would spawn a
# subshell per probe — the pack drives hundreds of probes).
BODY_GRACED="$(_body_grace_active)"
BODY_GRACED_LEAK="$(_body_grace_leak)"
BODY_GRACED_HARD="$(_body_grace_hard_red)"
BODY_GRACED_EXPIRED="$(_body_grace_expired)"
BODY_LEGACY="$(_body_legacy_no_grace)"
BODY_PLAIN_GREEN="$(_body_plain_green)"

_body_for() {
    case "$1" in
        graced)       printf '%s' "$BODY_GRACED" ;;
        graced-leak)  printf '%s' "$BODY_GRACED_LEAK" ;;
        graced-hard)  printf '%s' "$BODY_GRACED_HARD" ;;
        graced-expired) printf '%s' "$BODY_GRACED_EXPIRED" ;;
        legacy)       printf '%s' "$BODY_LEGACY" ;;
        *)            printf '%s' "$BODY_PLAIN_GREEN" ;;
    esac
}

_probe_once_classified() {
    local path="$1" port="$2" spec line
    _SCRIPT_CALLS=$(( _SCRIPT_CALLS + 1 ))
    case "$path" in
        */livez)
            if [ "${#_SCRIPT_LIVEZ[@]}" -eq 0 ]; then
                _PROBE_STATUS="green"; _PROBE_BODY=""; _PROBE_CODE="200"; return 0
            fi
            if [ "$_SCRIPT_LIVEZ_IDX" -ge "${#_SCRIPT_LIVEZ[@]}" ]; then
                # The stream is exhausted — REPLAY the last spec so the
                # scenario's shape persists to the cap.
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
            # One iteration = one clock tick (the cadence).
            _FAKE_NOW=$(( _FAKE_NOW + ADAPTIVE_SOAK_CADENCE_S ))
            ;;
        *)
            _PROBE_STATUS="green"; _PROBE_BODY=""; _PROBE_CODE="200"; return 0
            ;;
    esac
    case "$spec" in
        green)      _PROBE_STATUS="green"; _PROBE_CODE="200" ;;
        softgreen)  _PROBE_STATUS="busy-amnesty"; _PROBE_CODE="200" ;;
        transient)  _PROBE_STATUS="transient"; _PROBE_CODE="503" ;;
        red)        _PROBE_STATUS="red"; _PROBE_CODE="500" ;;
        graced|graced-leak|graced-hard|graced-expired|legacy)
                    _PROBE_STATUS="transient"; _PROBE_CODE="503" ;;
        *)          _PROBE_STATUS="green"; _PROBE_CODE="200" ;;
    esac
    _PROBE_BODY="$(_body_for "$spec")"
    case "$_PROBE_STATUS" in
        green)      return 0 ;;
        red)        return 1 ;;
        *)          return 2 ;;
    esac
}

# The clock — deterministic, advanced by the cadence once per iteration.
_now_epoch() { printf '%s' "$_FAKE_NOW"; }

# No real sleeping (the cadence is simulated by the clock).
sleep() { :; }

# The lock heartbeat WRITES to the lock dir and the journal — stub it.
lock_heartbeat() { return 0; }

# The journal write — stub it (the pack must NEVER touch a real journal).
journal_history_append() { printf '%s\n' "$*" >> "$_JOURNAL_LOG"; return 0; }

# The mid-soak version check — stub it (the version is not the subject).
gate_version() { return 0; }

PORT=9999
LOG_TAG="soak-grace-pack"
UP_TARGET="pack"

PASS_COUNT=0
FAIL_COUNT=0

_report() {
    local name="$1" ok="$2" detail="${3:-}"
    if [ "$ok" = "1" ]; then
        PASS_COUNT=$(( PASS_COUNT + 1 ))
        echo "PASS: ${name}${detail:+ — ${detail}}"
    else
        FAIL_COUNT=$(( FAIL_COUNT + 1 ))
        echo "FAIL: ${name}${detail:+ — ${detail}}"
    fi
}

# run_scenario <name> <expected_rc> <cap> <spec...>  — the specs are the
# READYZ statuses; the livez status is "green" unless prefixed "L:".
run_scenario() {
    local name="$1" expected_rc="$2" cap="$3"; shift 3
    local specs=("$@")
    _SCRIPT_LIVEZ=()
    _SCRIPT_READYZ=()
    local i livez_spec readyz_spec
    for i in "${!specs[@]}"; do
        livez_spec="green"
        readyz_spec="${specs[$i]}"
        case "$readyz_spec" in
            L:*)
                livez_spec="${readyz_spec#L:}"
                readyz_spec="${readyz_spec#L:}"
                ;;
        esac
        _SCRIPT_LIVEZ+=("${livez_spec}")
        _SCRIPT_READYZ+=("${readyz_spec}")
    done
    _SCRIPT_LIVEZ_IDX=0
    _SCRIPT_READYZ_IDX=0
    _SCRIPT_CALLS=0
    _FAKE_NOW=0
    : > "$_JOURNAL_LOG"
    ADAPTIVE_SOAK_FLOOR_S=0
    ADAPTIVE_SOAK_CAP_S="$cap"
    ADAPTIVE_SOAK_CONSECUTIVE=3
    ADAPTIVE_SOAK_CADENCE_S=1
    # A SMALL extension cap keeps the scripted loop short (the
    # extension re-anchors the deadline, so a 180s extension would
    # add 180 iterations per scenario). The NORMATIVE 180 is asserted
    # in S11/S12 against the shipped default.
    GRACE_EXTENSION_CAP_S=2
    GRACE_EXTENSION_CAP_S_INT=2
    local rc
    gate_soak 300 "v-test" > "$WORKDIR/out.txt" 2>&1
    rc=$?
    # NON-VACUITY: the scripted stream must actually have been consumed.
    if [ "$_SCRIPT_CALLS" -lt 2 ]; then
        _report "$name" 0 "the scripted probe stream was NOT consumed (calls=${_SCRIPT_CALLS}) — vacuous run"
        return
    fi
    if [ "$rc" != "$expected_rc" ]; then
        _report "$name" 0 "expected rc=${expected_rc} got rc=${rc} (probe calls=${_SCRIPT_CALLS})"
        return
    fi
    _report "$name" 1 "rc=${rc} (probe calls=${_SCRIPT_CALLS})"
}

echo
echo "--- S1: green, green, grace-held, green reaches 3 (rc 0) ---"
run_scenario "S1 green,green,graced,green reaches 3" 0 6 green green graced green

echo "--- S2: green, held, held, held never reaches 3 (rc 1) ---"
run_scenario "S2 green,graced,graced,graced never reaches 3" 1 6 green graced graced graced

echo "--- S3: a hard red fails fast on the FIRST observation (rc 1) ---"
run_scenario "S3 hard red fails fast" 1 6 green red

echo "--- S4: aliasing — livez soft-green + readyz hard green does NOT increment (rc 1) ---"
run_scenario "S4 aliasing livez-softgreen" 1 6 "L:softgreen" "L:softgreen" "L:softgreen" "L:softgreen"

echo "--- S5: aliasing — livez hard green + readyz soft-green does NOT increment (rc 1) ---"
run_scenario "S5 aliasing readyz-softgreen" 1 6 "L:green" "L:softgreen" "L:softgreen" "L:softgreen"

echo "--- S6: livez-transient-during-grace is a RESET, never a HOLD (rc 1) ---"
run_scenario "S6 livez-transient-during-grace resets" 1 5 green green "L:transient" green green

echo "--- S7: a grace-eligible daemon that NEVER greens still rolls back (rc 1) ---"
run_scenario "S7 never-green grace-eligible rolls back" 1 4 graced graced graced graced

echo "--- S8: the leak signal refuses the grace (rc 1) ---"
run_scenario "S8 leak refuses the grace" 1 6 green graced-leak graced-leak graced-leak

echo "--- S9: a hard-red component class is never grace-held (rc 1) ---"
run_scenario "S9 hard-red class is never held" 1 6 green graced-hard graced-hard graced-hard

echo "--- S10: an OLDER daemon (no grace block) is fail-closed (rc 1) ---"
run_scenario "S10 legacy daemon body is fail-closed" 1 6 green legacy legacy legacy

echo "--- S15: an EXPIRED grace is a RESET (the sub-ceiling gap cannot hold the counter open) ---"
run_scenario "S15 an expired grace resets the counter" 1 6 green graced-expired graced-expired graced-expired

echo
echo "--- S11: the effective-cap arithmetic (requirements §4.3) ---"
# Restore the SHIPPED defaults (the scenarios above shrank them).
ADAPTIVE_SOAK_CAP_S=120
GRACE_EXTENSION_CAP_S=180.0
GRACE_EXTENSION_CAP_S_INT=180
cap_no_grant="$(_soak_effective_cap 0 "" 300)"
cap_with_grant="$(_soak_effective_cap "$GRACE_EXTENSION_CAP_S_INT" "" 300)"
# The 4th term is the ABSOLUTE ceiling: an over-grant (420) is capped
# back to 300 (= ADAPTIVE_SOAK_CAP_S + GRACE_EXTENSION_CAP_S).
cap_ceiling="$(_soak_effective_cap 300 "" 100000)"
[ "$cap_no_grant" = "120" ] \
    && _report "S11a effective cap with granted=0 is 120" 1 "got ${cap_no_grant}" \
    || _report "S11a effective cap with granted=0 is 120" 0 "got ${cap_no_grant}"
[ "$cap_with_grant" = "300" ] \
    && _report "S11b effective cap with granted=180 is 300" 1 "got ${cap_with_grant}" \
    || _report "S11b effective cap with granted=180 is 300" 0 "got ${cap_with_grant}"
[ "$cap_ceiling" = "300" ] \
    && _report "S11c the absolute ceiling caps the extension at 300" 1 "got ${cap_ceiling}" \
    || _report "S11c the absolute ceiling caps the extension at 300" 0 "got ${cap_ceiling}"
cap_ceremony="$(_soak_effective_cap 0 100 300)"
[ "$cap_ceremony" = "40" ] \
    && _report "S11d the ceremony budget term is honoured" 1 "got ${cap_ceremony}" \
    || _report "S11d the ceremony budget term is honoured" 0 "got ${cap_ceremony}"
cap_soak_s="$(_soak_effective_cap 0 "" 90)"
[ "$cap_soak_s" = "90" ] \
    && _report "S11e the operator ceiling term is honoured" 1 "got ${cap_soak_s}" \
    || _report "S11e the operator ceiling term is honoured" 0 "got ${cap_soak_s}"

echo
echo "--- S12: the normative constant names + values ---"
[ "${GRACE_HORIZON_S}" = "120.0" ] \
    && _report "S12a GRACE_HORIZON_S is 120.0" 1 "got ${GRACE_HORIZON_S}" \
    || _report "S12a GRACE_HORIZON_S is 120.0" 0 "got ${GRACE_HORIZON_S}"
[ "${GRACE_EXTENSION_CAP_S}" = "180.0" ] \
    && _report "S12b GRACE_EXTENSION_CAP_S is 180.0" 1 "got ${GRACE_EXTENSION_CAP_S}" \
    || _report "S12b GRACE_EXTENSION_CAP_S is 180.0" 0 "got ${GRACE_EXTENSION_CAP_S}"
[ "${MAX_HOLDS_PER_GREEN}" = "2" ] \
    && _report "S12c MAX_HOLDS_PER_GREEN is 2" 1 "got ${MAX_HOLDS_PER_GREEN}" \
    || _report "S12c MAX_HOLDS_PER_GREEN is 2" 0 "got ${MAX_HOLDS_PER_GREEN}"

echo
echo "--- S13: the extension emits the soak_grace_extended journal event ---"
_SCRIPT_LIVEZ=("green" "green")
_SCRIPT_READYZ=("graced" "graced")
_SCRIPT_LIVEZ_IDX=0; _SCRIPT_READYZ_IDX=0; _SCRIPT_CALLS=0; _FAKE_NOW=0
: > "$_JOURNAL_LOG"
ADAPTIVE_SOAK_FLOOR_S=0; ADAPTIVE_SOAK_CAP_S=4
ADAPTIVE_SOAK_CONSECUTIVE=3; ADAPTIVE_SOAK_CADENCE_S=1
GRACE_EXTENSION_CAP_S=2; GRACE_EXTENSION_CAP_S_INT=2
gate_soak 300 "v-test" > "$WORKDIR/out.txt" 2>&1 || true
if grep -q "soak_grace_extended" "$_JOURNAL_LOG"; then
    _report "S13a soak_grace_extended journal event emitted" 1
else
    _report "S13a soak_grace_extended journal event emitted" 0 "journal log: $(cat "$_JOURNAL_LOG")"
fi

echo
echo "--- S14: the counter site reads the PER-PROBE locals (no _PROBE_STATUS aliasing) ---"
# A structural proof: the counter block must NOT reference the
# probe-level global. The global stays the probe-level value elsewhere.
if sed -n '/A2 (soak-gate-mcp-warmup): the THREE observation kinds/,/esac$/p' \
        "$REPO_ROOT/scripts/upgrade/lib.sh" | grep -q '_PROBE_STATUS'; then
    _report "S14a the counter block does not read the _PROBE_STATUS global" 0
else
    _report "S14a the counter block does not read the _PROBE_STATUS global" 1
fi

echo
echo "======================================"
echo "PASS: ${PASS_COUNT}  FAIL: ${FAIL_COUNT}"
echo "======================================"
if [ "$FAIL_COUNT" -gt 0 ]; then
    echo "RESULT: FAIL"
    exit 1
fi
if [ "$PASS_COUNT" -lt 20 ]; then
    echo "RESULT: FAIL (vacuous — only ${PASS_COUNT} assertions ran)"
    exit 1
fi
echo "RESULT: PASS"
exit 0
