#!/bin/bash
# ============================================================================
# tests/packs/promote_handback_halt_serving_green_test.sh — the launcher +
# shell halves of the Phase-2 serving-green SPLIT closer.
#
# Commission: promote-handback-halt Phase 2 (incident r-20261101-212542-8279,
# target v0.28.5). The launcher's boot sweep is structurally BLIND to serving
# health (it runs PRE-daemon — "port down" is ambiguous: a broken release, or
# THIS boot starting the daemon) ⇒ it rolled back + quarantined a
# SERVING-GREEN release. This pack pins the new disposition table:
#
#   BROKEN ⇒ immediate · rollback_requested ⇒ immediate · AMBIGUOUS ⇒ bounded
#   (the absent-ceiling) · everything else ⇒ DEFER
#   GREEN ∧ intent_flip ⇒ commit-close (stay; no punishment)
#
# Pins owned here (phase2-plan.md's phase-2 rows):
#   P-1/P-10  the serving-green commit-close (+ the version asymmetry)
#   P-4/P-20  the deferral + the bounded ceiling
#   P-5       the probe primitive legs
#   P-7       tonight's 4-stage sequence
#   P-8/P-36  the quarantine clearance (both sides; the reader's negatives)
#   P-9       the adopt-path polarity
#   P-13..P-17 the T14 request path
#   P-19      the history-before-close fault injection
#   P-23/P-24/P-27 the absent-ceiling / the sticky green / the mismatch split
#   P-25      the cross-entry negative
#   P-26      the R3 hinge
#   P-29      the counter-file lifecycle
#   P-30      the kill-switch (two states)
#   P-31      the launcher purity scan
#   P-32      the T15 hook's scope + idempotence
#   P-33      the owner-liveness conjunct on the whole stale path
#
# FENCES (house): every fixture is under mktemp; the listener binds 127.0.0.1
# on a FREE port; the journal is SYNTHETIC (never copied from a live
# install); the live install is never touched.
#
# Run: bash tests/packs/promote_handback_halt_serving_green_test.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
. "$REPO_ROOT/tests/upgrade_test_lib.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$(( PASS + 1 )); }
_fail() {
    FAIL=$(( FAIL + 1 ))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass; else _fail "$name" "$expected" "$actual"; fi
}
section() { printf '\n== %s ==\n' "$1"; }
# assert_re <name> <eregex> <haystack> — the journal's field formatting
# differs between the writers (the launcher's `_js_journal_update` writes
# `"field": value`, the seeded fixtures are compact) ⇒ the structural
# assertions use a regex, not a literal.
assert_re() {
    local name="$1" ere="$2" hay="$3"
    if printf '%s' "$hay" | grep -qE "$ere"; then _pass; else _fail "$name" "matches /$ere/" "$(printf '%s' "$hay" | head -c 300)"; fi
}

TARGET="v9.9.9-test-target"
PREV="v9.9.9-test-prev"
LISTENER_PIDS=""

cleanup_listeners() {
    local pid
    for pid in $LISTENER_PIDS; do
        kill "$pid" 2>/dev/null || true
    done
    LISTENER_PIDS=""
}
trap 'cleanup_listeners; _cleanup_fixtures' EXIT

# ── fixture helpers ────────────────────────────────────────────────────────

make_install_fixture() {
    local fix="$1" target="$2" prev="$3" prev_safe="${4:-true}"
    rm -rf "$fix"; mkdir -p "$fix/releases/$target" "$fix/releases/$prev" "$fix/data"
    cat > "$fix/releases/$target/manifest.json" <<EOF
{"manifest_version":"1.0.0","binary_version":"$target","rollback_safe":true}
EOF
    cat > "$fix/releases/$prev/manifest.json" <<EOF
{"manifest_version":"1.0.0","binary_version":"$prev","rollback_safe":$prev_safe}
EOF
}

# seed_journal <fix> <target> <prev> <flipped> <intent> [request_detail] [quarantined]
seed_journal() {
    local fix="$1" target="$2" prev="$3" flipped="$4" intent="$5" request="${6:-}" quar="${7:-[]}"
    local hist="" intent_row="" request_row=""
    if [ "$intent" = "1" ]; then
        intent_row=',{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target '"$target"' — integrity verified (CURRENT drift-check + TARGET manifest match); operator-initiated"}'
    fi
    if [ -n "$request" ]; then
        request_row=',{"ts":"2026-10-07T11:00:00Z","event":"rollback_requested","detail":"'"$request"'"}'
    fi
    cat > "$fix/releases/state.json" <<EOF
{"current":"$prev","previous":"$prev","in_flight":{"kind":"promote","target":"$target","started_at":"2026-10-07T09:00:00Z","flipped":$flipped,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$prev"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":$quar,"history":[{"ts":"2026-10-07T09:00:00Z","event":"open_txn","detail":"test seeded"}$intent_row$request_row]}
EOF
}

# run_sweep <fix> [port] — run the launcher's sweep in isolation.
run_sweep() {
    local fix="$1" port="${2:-19999}"
    INSTALL_DIR="$fix" PORT="$port" \
    SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
    SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
    bash -c '
        . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
        _journal_sweep "$INSTALL_DIR"
        echo "sweep-rc=$?"
    ' 2>&1
}

# run_hook <fix> [port] [child_exit] — drive the T15 run-loop HOOK's own
# path: the flipped-branch helper called with lock_held=0, i.e. it TAKES
# THE PIPELINE LOCK ITSELF. Deliberately does NOT mimic the call site's
# belt-and-braces release — the pin proves the HELPER releases what it
# acquired.
run_hook() {
    local fix="$1" port="${2:-19999}" child_exit="${3:-0}"
    INSTALL_DIR="$fix" PORT="$port" \
    SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
    SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
    bash -c '
        . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
        _JS_SWEEP_HOOK_EXIT="'"$child_exit"'"
        _js_sweep_flipped_disposition "$INSTALL_DIR" 0
        echo "hook-rc=$?"
    ' 2>&1
}

# run_sweep_unset_port <fix> [daemon_port] — drive the sweep with PORT
# ASSIGNED NOWHERE (the F3 shape: the .env carries no PORT). DAEMON_PORT
# is optionally set (the F9 divergence shape).
run_sweep_unset_port() {
    local fix="$1" dport="${2:-}"
    if [ -n "$dport" ]; then
        env -u PORT DAEMON_PORT="$dport" INSTALL_DIR="$fix" \
            SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
            SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
            bash -c '
                . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
                _journal_sweep "$INSTALL_DIR"
                echo "sweep-rc=$?"
            ' 2>&1
    else
        env -u PORT -u DAEMON_PORT INSTALL_DIR="$fix" \
            SWEEP_STALE_S=0 HEARTBEAT_STALE_S=0 LOCK_HEARTBEAT_S=30 LOCK_STALE_S=300 \
            SWEEP_DEFER_CEILING="${SWEEP_DEFER_CEILING:-3}" \
            bash -c '
                . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
                _journal_sweep "$INSTALL_DIR"
                echo "sweep-rc=$?"
            ' 2>&1
    fi
}

# free_port — a free TCP port on the loopback (never a fixed/privileged one).
free_port() {
    python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

# start_listener <mode> <version> — start the fake serving listener; echoes
# the port. The listener is REGISTERED for cleanup.
start_listener() {
    local mode="$1" version="$2" port
    port="$(free_port)"
    python3 "$REPO_ROOT/tests/helpers/serving_probe_fake_listener.py" \
        --port "$port" --mode "$mode" --version "$version" \
        --log "$TMP_LISTENER_LOG" >/dev/null 2>&1 &
    LISTENER_PIDS="$LISTENER_PIDS $!"
    TMP_LISTENER_LOG="/tmp/ens-serving-probe-$$.jsonl"
    : > "$TMP_LISTENER_LOG"
    local waited=0
    while [ "$waited" -lt 50 ]; do
        if python3 - "$port" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(0.2)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
    sys.exit(0)
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
        then
            printf '%s' "$port"
            return 0
        fi
        sleep 0.1
        waited=$(( waited + 1 ))
    done
    printf '%s' "$port"
    return 1
}

# the journal fields a pin reads
jfield() {
    printf '%s' "$(_js_field "$1" "$2")"
}

# ===========================================================================
section "P-5 — the probe primitive legs (the canonical GREEN predicate)"

# The probe is driven directly (unit level) against the fake listener.
probe_once() {
    local port="$1" target="$2"
    bash -c '
        . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
        _js_serving_probe "'"$port"'" "'"$target"'"
    ' 2>/dev/null
}

TMP_LISTENER_LOG="/tmp/ens-serving-probe-$$.jsonl"
: > "$TMP_LISTENER_LOG"

P_PORT="$(start_listener green 0.28.5)"
P_VERDICT="$(probe_once "$P_PORT" "v0.28.5")"
assert_eq "P-5 green listener ⇒ GREEN" "green" "${P_VERDICT%% *}"
# P-10 — the version-string asymmetry: a v-prefixed target matches the
# non-v __version__ (a naive compare would read every healthy target as
# unhealthy ⇒ the wrongful rollback returns).
assert_contains "P-10 the v-prefixed target matched the non-v version" "version-0.28.5" "$P_VERDICT"

P_PORT2="$(start_listener wrong-version 0.0.0-wrong)"
P_VERDICT2="$(probe_once "$P_PORT2" "v0.28.5")"
assert_eq "P-5 wrong-version listener ⇒ MISMATCH (ambiguous, not green)" "mismatch" "${P_VERDICT2%% *}"

P_PORT3="$(start_listener livez-non200 0.28.5)"
P_VERDICT3="$(probe_once "$P_PORT3" "v0.28.5")"
assert_eq "P-5 livez non-200 ⇒ BROKEN (the immediate path)" "broken" "${P_VERDICT3%% *}"

P_PORT4="$(start_listener readyz-red 0.28.5)"
P_VERDICT4="$(probe_once "$P_PORT4" "v0.28.5")"
assert_eq "P-5 readyz 500 ⇒ UNREADY (ambiguous — never BROKEN)" "unready" "${P_VERDICT4%% *}"

P_VERDICT5="$(probe_once 1 "v0.28.5")"
assert_eq "P-5 no listener ⇒ ABSENT (ambiguous)" "absent" "${P_VERDICT5%% *}"

# the never-answer leg must NOT hang: the probe is bounded
P_PORT6="$(start_listener never-answer 0.28.5)"
P_START="$(date +%s)"
P_VERDICT6="$(probe_once "$P_PORT6" "v0.28.5")"
P_ELAPSED=$(( $(date +%s) - P_START ))
assert_eq "P-5 never-answer ⇒ ABSENT" "absent" "${P_VERDICT6%% *}"
if [ "$P_ELAPSED" -le 12 ]; then
    _pass
else
    _fail "P-5 the never-answer leg stayed inside the probe budget" "<=12s" "${P_ELAPSED}s"
fi
cleanup_listeners

# ===========================================================================
section "P-5u / P-5v / P-5w — the probe-configuration class (F3), the port source (F9), the fd isolation (F11)"

# ─ P-5u (F3, code-council W1) ─────────────────────────────────────────────
# An unusable PORT is a PROBE-CONFIGURATION failure, NOT an observation:
# class `unprobeable`, never counted, never rollback-eligible. Pre-fix it
# collapsed into `absent` (detail `no-probe-target`) and WAS counted ⇒
# three launcher boots on a host whose .env lacks PORT rolled back a
# healthy release.
PV_EMPTY="$(probe_once "" "v0.28.5")"
assert_eq "P-5u an EMPTY port ⇒ UNPROBEABLE (never an observation)" "unprobeable" "${PV_EMPTY%% *}"
PV_ALPHA="$(probe_once "abc" "v0.28.5")"
assert_eq "P-5u a NON-NUMERIC port ⇒ UNPROBEABLE" "unprobeable" "${PV_ALPHA%% *}"
PV_RANGE="$(probe_once "70000" "v0.28.5")"
assert_eq "P-5u an OUT-OF-RANGE port ⇒ UNPROBEABLE" "unprobeable" "${PV_RANGE%% *}"

# the SWEEP-level leg: 4 observations (one past the default ceiling of 3)
# with PORT assigned nowhere ⇒ NO rollback, NO count, a journaled defer.
P5U_FIX="$(mktemp -d -t p5u.XXXXXX)"; _register_fixture "$P5U_FIX"
make_install_fixture "$P5U_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P5U_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P5U_FIX/current"
i=0
while [ "$i" -lt 4 ]; do
    run_sweep_unset_port "$P5U_FIX" >/dev/null
    i=$(( i + 1 ))
done
P5U_J="$(cat "$P5U_FIX/releases/state.json")"
assert_not_contains "P-5u PORT-unset observations NEVER reach the absent-ceiling" '"event":"sweep_rollback"' "$P5U_J"
assert_not_contains "P-5u PORT-unset observations never quarantine" "\"quarantined\":\[\"$TARGET\"" "$P5U_J"
assert_contains "P-5u the probe-unprobeable defer is journaled (greppable marker)" 'probe-unprobeable-no-port' "$P5U_J"
if ls "$P5U_FIX"/.sweep-defer-* >/dev/null 2>&1; then
    _fail "P-5u a probe-configuration failure is NOT counted (no counter file)" "absent" "present"
else
    _pass
fi
rm -rf "$P5U_FIX"

# ─ P-5v (F9, code-council A2) ─────────────────────────────────────────────
# The probe targets the daemon's EFFECTIVE port. A PORT/DAEMON_PORT
# divergence must be logged LOUDLY (a wrong target silently makes the
# serving-green arm inert); a PORT-less .env with DAEMON_PORT set must say
# so (the daemon binds the config.yaml default, not DAEMON_PORT).
P5V_FIX="$(mktemp -d -t p5v.XXXXXX)"; _register_fixture "$P5V_FIX"
make_install_fixture "$P5V_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P5V_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P5V_FIX/current"
P5V_DIV="$(PORT=19001 DAEMON_PORT=19002 run_sweep "$P5V_FIX" 19001 2>&1)"
assert_contains "P-5v a PORT/DAEMON_PORT divergence is logged loudly" "PORT=19001 but DAEMON_PORT=19002" "$P5V_DIV"
P5V_NP="$(run_sweep_unset_port "$P5V_FIX" 19003)"
assert_contains "P-5v PORT unset + DAEMON_PORT set is surfaced (the daemon binds the yaml default)" "PORT is unset while DAEMON_PORT=19003" "$P5V_NP"
rm -rf "$P5V_FIX"

# ─ P-5w (F11, portability) ────────────────────────────────────────────────
# The fd-open and the /dev/tcp probe are ISOLATED in a subshell: a failing
# `exec 3<>` can never kill (bash 3.2 + execfail) or clobber the caller.
# Behavioural discriminator: the probe is called IN THE CURRENT SHELL (not
# through `$( )`, which would hide the difference). Pre-fix it re-pointed
# and then CLOSED the caller's fd 3; post-fix the caller's fd survives.
P5W_PORT="$(start_listener green 0.28.5)"
P5W_OUT="$(bash -c '
    . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
    exec 3<>/dev/null
    _js_serving_probe "'"$P5W_PORT"'" v0.28.5 >/dev/null 2>&1 || true
    if : >&3 2>/dev/null; then echo ALIVE; else echo CLOBBERED; fi
' 2>/dev/null)"
assert_eq "P-5w the probe does not clobber the caller's fd 3 (subshell isolation)" "ALIVE" "$P5W_OUT"
# execfail safety: the probe stays non-fatal with `set -e` + `execfail`
# ON against a DOWN port (a probe failure must never take the shell down).
P5W_EXECFAIL="$(bash -c '
    shopt -s execfail
    . "'"$REPO_ROOT"'/launcher.sh" >/dev/null 2>&1
    out="$(_js_serving_probe 1 v0.28.5 2>/dev/null)"
    printf "%s SURVIVED" "${out%% *}"
' 2>/dev/null)"
assert_eq "P-5w a failed fd-open under execfail leaves the launcher alive" "absent SURVIVED" "$P5W_EXECFAIL"
# structural: the exec 3<> inside the probe sits INSIDE a subshell
P5W_SRC="$(sed -n '/^_js_serving_probe() {/,/^}/p' "$REPO_ROOT/launcher.sh")"
case "$P5W_SRC" in
    *'( '*"exec 3<>"*) _pass ;;
    *) _fail "P-5w the fd-open + probe body are wrapped in a subshell" "a subshell opener before exec 3<>" "flat" ;;
esac
cleanup_listeners

# ===========================================================================
section "P-1 — the serving-green commit-close (the owner's ruling)"

P1_FIX="$(mktemp -d -t p1.XXXXXX)"; _register_fixture "$P1_FIX"
make_install_fixture "$P1_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P1_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P1_FIX/current"
printf 'PORT=19999\n' > "$P1_FIX/.env"
P1_PORT="$(start_listener green 9.9.9-test-target)"
P1_OUT="$(run_sweep "$P1_FIX" "$P1_PORT")"
P1_JOURNAL="$(cat "$P1_FIX/releases/state.json")"
assert_contains "P-1 the serving_green_commit event is present" '"event":"serving_green_commit"' "$P1_JOURNAL"
assert_not_contains "P-1 NO sweep_rollback" '"event":"sweep_rollback"' "$P1_JOURNAL"
assert_not_contains "P-1 NO halt event" '"event":"halt"' "$P1_JOURNAL"
case "$P1_JOURNAL" in
    *'"in_flight":null'*|*"\"in_flight\": null"*) _pass "P-1 the txn is CLOSED" ;;
    *) _fail "P-1 the txn is CLOSED" '"in_flight":null' "$(printf '%s' "$P1_JOURNAL" | grep -oE '"in_flight":[^,}]*')" ;;
esac
case "$P1_JOURNAL" in
    *'"current":"'"$TARGET"'"'*|*"\"current\": \"$TARGET\""*) _pass "P-1 current → target" ;;
    *) _fail "P-1 current → target" "\"current\":\"$TARGET\"" "$(printf '%s' "$P1_JOURNAL" | grep -oE '"current":[^,}]*')" ;;
esac
# NOT done on GREEN: quarantine / counter / cooldown
P1_QUAR="$(printf '%s' "$P1_JOURNAL" | grep -oE '"quarantined":\[[^]]*\]')"
case "$P1_QUAR" in
    *"$TARGET"*) _fail "P-1 NO quarantine append" "absent" "present: $P1_QUAR" ;;
    *) _pass "P-1 NO quarantine append" ;;
esac
assert_re "P-1 the rollback counter is UNCHANGED" '"24h": ?0' "$P1_JOURNAL"
assert_re "P-1 the cooldown is UNCHANGED" '"cooldown_until": ?null' "$P1_JOURNAL"
# AM-P2-1 — the history row exists BEFORE the txn closed (the row is present
# and the txn is closed; the crash-prefix shape is covered by P-19)
assert_contains "P-1 the commit detail carries the target" "target $TARGET" "$P1_JOURNAL"
rm -rf "$P1_FIX"; cleanup_listeners

# ===========================================================================
section "P-4 / P-20 / P-23 — the deferral and the BOUNDED absent-ceiling"

# The canonical never-green shape: every observation is AMBIGUOUS (no
# listener). Across SEPARATE run_sweep invocations the tally persists (the
# launcher-local counter file) and the CEILING-th encounter fires the
# EXISTING chain with all FOUR effects.
P4_FIX="$(mktemp -d -t p4.XXXXXX)"; _register_fixture "$P4_FIX"
make_install_fixture "$P4_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P4_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P4_FIX/current"
printf 'PORT=19999\n' > "$P4_FIX/.env"

P4_OUT1="$(run_sweep "$P4_FIX" 19999)"
P4_JOURNAL1="$(cat "$P4_FIX/releases/state.json")"
assert_contains "P-4 an ambiguous observation DEFERS (sweep_deferred row)" '"event":"sweep_deferred"' "$P4_JOURNAL1"
assert_not_contains "P-4 the deferred txn is NOT rolled back" '"event":"sweep_rollback"' "$P4_JOURNAL1"
case "$P4_JOURNAL1" in
    *'"in_flight":null'*|*"\"in_flight\": null"*) _fail "P-4 the txn stays OPEN" "in_flight present" "in_flight cleared" ;;
    *) _pass "P-4 the txn stays OPEN" ;;
esac
assert_re "P-4 the rollback counter is untouched" '"24h": ?0' "$P4_JOURNAL1"

# the counter file is the launcher's LOCAL state (not a journal field)
P4_COUNTER="$(ls "$P4_FIX"/.sweep-defer-* 2>/dev/null | head -1)"
if [ -n "$P4_COUNTER" ]; then
    _pass "P-4 the counter file is launcher-local"
    assert_contains "P-4 the counter file records the absent class" "absent=1" "$(cat "$P4_COUNTER")"
else
    _fail "P-4 the counter file is launcher-local" "a .sweep-defer-* file" "none"
fi

# second observation ⇒ still below the ceiling (3)
run_sweep "$P4_FIX" 19999 >/dev/null
P4_JOURNAL2="$(cat "$P4_FIX/releases/state.json")"
assert_contains "P-4 the second observation still defers" '"event":"sweep_deferred"' "$P4_JOURNAL2"
assert_not_contains "P-4 the second observation does NOT roll back" '"event":"sweep_rollback"' "$P4_JOURNAL2"
assert_contains "P-20 the tally advanced to 2" "absent=2" "$(cat "$P4_COUNTER" 2>/dev/null)"

# third observation ⇒ the CEILING fires the EXISTING chain (all four effects)
run_sweep "$P4_FIX" 19999 >/dev/null
P4_JOURNAL3="$(cat "$P4_FIX/releases/state.json")"
assert_contains "P-23 the ceiling fires the existing chain (sweep_rollback)" '"event":"sweep_rollback"' "$P4_JOURNAL3"
P4_QUAR3="$(printf '%s' "$P4_JOURNAL3" | grep -oE '"quarantined": ?\[[^]]*\]')"
case "$P4_QUAR3" in
    *"$TARGET"*) _pass "P-23 the target is quarantined" ;;
    *) _fail "P-23 the target is quarantined" "target present" "$P4_QUAR3" ;;
esac
assert_re "P-23 the rollback counter was incremented" '"24h": ?1' "$P4_JOURNAL3"
case "$P4_JOURNAL3" in
    *'"cooldown_until":null'*) _fail "P-23 the cooldown was armed" "a timestamp" "null" ;;
    *) _pass "P-23 the cooldown was armed" ;;
esac
case "$P4_JOURNAL3" in
    *'"current":"'"$PREV"'"'*|*"\"current\": \"$PREV\""*) _pass "P-23 current → prev" ;;
    *) _fail "P-23 current → prev" "\"current\":\"$PREV\"" "$(printf '%s' "$P4_JOURNAL3" | grep -oE '"current":[^,}]*')" ;;
esac
# P-29 — the counter file is DELETED when the txn rolls back
if ls "$P4_FIX"/.sweep-defer-* >/dev/null 2>&1; then
    _fail "P-29 the counter file is deleted on rollback" "deleted" "present"
else
    _pass "P-29 the counter file is deleted on rollback"
fi
rm -rf "$P4_FIX"

# ===========================================================================
section "P-24 — never-green-observed is STICKY (a green observation freezes the ceiling)"

P24_FIX="$(mktemp -d -t p24.XXXXXX)"; _register_fixture "$P24_FIX"
make_install_fixture "$P24_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P24_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P24_FIX/current"
printf 'PORT=19999\n' > "$P24_FIX/.env"
P24_PORT="$(start_listener green 9.9.9-test-target)"
# observation 1 ⇒ GREEN (the sticky flag is set, the txn is committed) —
# the counter file is deleted on commit (P-29) and the txn is closed.
run_sweep "$P24_FIX" "$P24_PORT" >/dev/null
P24_JOURNAL="$(cat "$P24_FIX/releases/state.json")"
assert_contains "P-24 the green observation committed" '"event":"serving_green_commit"' "$P24_JOURNAL"
if ls "$P24_FIX"/.sweep-defer-* >/dev/null 2>&1; then
    _fail "P-29 the counter file is deleted on commit" "deleted" "present"
else
    _pass "P-29 the counter file is deleted on commit"
fi
rm -rf "$P24_FIX"; cleanup_listeners

# P-24's other half: a serving_green_commit row in the history (the
# AM-P2-1 crash-prefix shape) makes the absent-ceiling ineligible.
P24B_FIX="$(mktemp -d -t p24b.XXXXXX)"; _register_fixture "$P24B_FIX"
make_install_fixture "$P24B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P24B_FIX" "$TARGET" "$PREV" "true" "1"
# inject a serving_green_commit row (the crash prefix: the event landed,
# the txn is still open)
python3 - "$P24B_FIX/releases/state.json" "$TARGET" <<'PY'
import json, sys
path, target = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["history"].append({
    "ts": "2026-10-07T12:00:00Z",
    "event": "serving_green_commit",
    "detail": f"target {target} run_id x serving_green_commit prev p",
})
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
ln -sfn "releases/$TARGET" "$P24B_FIX/current"
printf 'PORT=19999\n' > "$P24B_FIX/.env"
run_sweep "$P24B_FIX" 19999 >/dev/null
run_sweep "$P24B_FIX" 19999 >/dev/null
run_sweep "$P24B_FIX" 19999 >/dev/null
P24B_JOURNAL="$(cat "$P24B_FIX/releases/state.json")"
assert_not_contains "P-24 a serving_green_commit row makes the ceiling ineligible" '"event":"sweep_rollback"' "$P24B_JOURNAL"
assert_contains "P-24 the observation still defers" '"event":"sweep_deferred"' "$P24B_JOURNAL"
rm -rf "$P24B_FIX"

# ===========================================================================
section "P-27 — the version-mismatch split (the symlink decides)"

# (a) the symlink is ON the target + the listener answers a wrong version ⇒
#     AMBIGUOUS ⇒ counted and bounded (never an immediate rollback)
P27_FIX="$(mktemp -d -t p27.XXXXXX)"; _register_fixture "$P27_FIX"
make_install_fixture "$P27_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P27_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P27_FIX/current"
printf 'PORT=19999\n' > "$P27_FIX/.env"
P27_PORT="$(start_listener wrong-version 0.0.0-wrong)"
run_sweep "$P27_FIX" "$P27_PORT" >/dev/null
P27_JOURNAL="$(cat "$P27_FIX/releases/state.json")"
assert_not_contains "P-27 (a) a wrong version is NOT an immediate rollback" '"event":"sweep_rollback"' "$P27_JOURNAL"
assert_contains "P-27 (a) a wrong version defers" '"event":"sweep_deferred"' "$P27_JOURNAL"
rm -rf "$P27_FIX"; cleanup_listeners

# (b) the symlink has MOVED AWAY from the target ⇒ NO ACTION at all
P27B_FIX="$(mktemp -d -t p27b.XXXXXX)"; _register_fixture "$P27B_FIX"
make_install_fixture "$P27B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P27B_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$PREV" "$P27B_FIX/current"
printf 'PORT=19999\n' > "$P27B_FIX/.env"
run_sweep "$P27B_FIX" 19999 >/dev/null
P27B_JOURNAL="$(cat "$P27B_FIX/releases/state.json")"
assert_not_contains "P-27 (b) a symlink moved away ⇒ NO rollback" '"event":"sweep_rollback"' "$P27B_JOURNAL"
assert_not_contains "P-27 (b) a symlink moved away ⇒ NO quarantine" '"quarantined":["'"$TARGET"'"]' "$P27B_JOURNAL"
assert_re "P-27 (b) a symlink moved away ⇒ the counter is untouched" '"24h": ?0' "$P27B_JOURNAL"
rm -rf "$P27B_FIX"

# ===========================================================================
section "P-13..P-17 — the T14 request path (the daemon→launcher handoff)"

# P-13: a request + NOT-GREEN ⇒ the EXISTING chain executes
P13_FIX="$(mktemp -d -t p13.XXXXXX)"; _register_fixture "$P13_FIX"
make_install_fixture "$P13_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P13_FIX" "$TARGET" "$PREV" "true" "1" \
    "target $TARGET run_id $TARGET@2026-10-07T09:00:00Z reason not-ready"
ln -sfn "releases/$TARGET" "$P13_FIX/current"
printf 'PORT=19999\n' > "$P13_FIX/.env"
P13_OUT="$(run_sweep "$P13_FIX" 19999)"
P13_JOURNAL="$(cat "$P13_FIX/releases/state.json")"
assert_contains "P-13 a request + not-green ⇒ the chain executes" '"event":"sweep_rollback"' "$P13_JOURNAL"
assert_contains "P-13 the executed rollback names its disposition" "disposition=rollback_requested" "$P13_JOURNAL"
rm -rf "$P13_FIX"

# P-15: a request + GREEN ⇒ commit-close (the request is SUPERSEDED)
P15_FIX="$(mktemp -d -t p15.XXXXXX)"; _register_fixture "$P15_FIX"
make_install_fixture "$P15_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P15_FIX" "$TARGET" "$PREV" "true" "1" \
    "target $TARGET run_id $TARGET@2026-10-07T09:00:00Z reason not-ready"
ln -sfn "releases/$TARGET" "$P15_FIX/current"
printf 'PORT=19999\n' > "$P15_FIX/.env"
P15_PORT="$(start_listener green 9.9.9-test-target)"
run_sweep "$P15_FIX" "$P15_PORT" >/dev/null
P15_JOURNAL="$(cat "$P15_FIX/releases/state.json")"
assert_contains "P-15 a request + green ⇒ commit-close" '"event":"serving_green_commit"' "$P15_JOURNAL"
assert_not_contains "P-15 the request is NOT executed" '"event":"sweep_rollback"' "$P15_JOURNAL"
rm -rf "$P15_FIX"; cleanup_listeners

# P-15's NEGATIVE axis (the R3 hinge): a request + GREEN + NO intent_flip ⇒
# DEFER — never a commit-close, never a rollback
P15B_FIX="$(mktemp -d -t p15b.XXXXXX)"; _register_fixture "$P15B_FIX"
make_install_fixture "$P15B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P15B_FIX" "$TARGET" "$PREV" "true" "0" \
    "target $TARGET run_id $TARGET@2026-10-07T09:00:00Z reason not-ready"
ln -sfn "releases/$TARGET" "$P15B_FIX/current"
printf 'PORT=19999\n' > "$P15_FIX/.env"
printf 'PORT=19999\n' > "$P15B_FIX/.env"
P15B_PORT="$(start_listener green 9.9.9-test-target)"
run_sweep "$P15B_FIX" "$P15B_PORT" >/dev/null
P15B_JOURNAL="$(cat "$P15B_FIX/releases/state.json")"
assert_not_contains "P-15/P-26 the forged request + green ⇒ NO commit-close" '"event":"serving_green_commit"' "$P15B_JOURNAL"
assert_not_contains "P-15/P-26 the forged request + green ⇒ NO rollback" '"event":"sweep_rollback"' "$P15B_JOURNAL"
assert_contains "P-15/P-26 the forged request + green ⇒ DEFER" '"event":"sweep_deferred"' "$P15B_JOURNAL"
case "$P15B_JOURNAL" in
    *'"in_flight":null'*) _fail "P-15/P-26 the txn stays OPEN" "in_flight present" "in_flight cleared" ;;
    *) _pass "P-15/P-26 the txn stays OPEN" ;;
esac
rm -rf "$P15B_FIX"; cleanup_listeners

# P-17: a request naming ANOTHER run_id is ignored (isolation)
P17_FIX="$(mktemp -d -t p17.XXXXXX)"; _register_fixture "$P17_FIX"
make_install_fixture "$P17_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P17_FIX" "$TARGET" "$PREV" "true" "1" \
    "target $TARGET run_id some-other-run reason not-ready"
ln -sfn "releases/$TARGET" "$P17_FIX/current"
printf 'PORT=19999\n' > "$P17_FIX/.env"
run_sweep "$P17_FIX" 19999 >/dev/null
P17_JOURNAL="$(cat "$P17_FIX/releases/state.json")"
assert_not_contains "P-17 a request naming another run_id is ignored" '"event":"sweep_rollback"' "$P17_JOURNAL"
assert_contains "P-17 a request naming another run_id ⇒ defer" '"event":"sweep_deferred"' "$P17_JOURNAL"
rm -rf "$P17_FIX"

# P-25: the cross-entry negative — the event kind in one entry, the target
# string in ANOTHER ⇒ the reader must NOT see a request
P25_FIX="$(mktemp -d -t p25.XXXXXX)"; _register_fixture "$P25_FIX"
make_install_fixture "$P25_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P25_FIX" "$TARGET" "$PREV" "true" "1"
python3 - "$P25_FIX/releases/state.json" "$TARGET" <<'PY'
import json, sys
path, target = sys.argv[1], sys.argv[2]
data = json.load(open(path))
# entry A: the event kind; entry B: the target + run_id
data["history"].append({"ts": "2026-10-07T11:00:00Z", "event": "rollback_requested", "detail": "reason not-ready"})
data["history"].append({"ts": "2026-10-07T11:00:01Z", "event": "sweep", "detail": f"target {target} run_id {target}@2026-10-07T09:00:00Z"})
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
ln -sfn "releases/$TARGET" "$P25_FIX/current"
printf 'PORT=19999\n' > "$P25_FIX/.env"
run_sweep "$P25_FIX" 19999 >/dev/null
P25_JOURNAL="$(cat "$P25_FIX/releases/state.json")"
assert_not_contains "P-25 the cross-entry split is NOT a request" '"event":"sweep_rollback"' "$P25_JOURNAL"
assert_contains "P-25 the cross-entry split ⇒ defer" '"event":"sweep_deferred"' "$P25_JOURNAL"
rm -rf "$P25_FIX"

# ===========================================================================
section "P-30 — the launcher kill-switch (two states)"

# =0 ⇒ today's unconditional auto-rollback disposition (the escape hatch)
P30_FIX="$(mktemp -d -t p30.XXXXXX)"; _register_fixture "$P30_FIX"
make_install_fixture "$P30_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P30_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P30_FIX/current"
printf 'PORT=19999\n' > "$P30_FIX/.env"
SWEEP_SERVING_GREEN_ENABLED=0 run_sweep "$P30_FIX" 19999 >/dev/null
P30_JOURNAL="$(cat "$P30_FIX/releases/state.json")"
assert_contains "P-30 =0 restores the unconditional rollback" '"event":"sweep_rollback"' "$P30_JOURNAL"
rm -rf "$P30_FIX"

# =1 (the default) ⇒ the new ladder (an ambiguous observation defers)
P30B_FIX="$(mktemp -d -t p30b.XXXXXX)"; _register_fixture "$P30B_FIX"
make_install_fixture "$P30B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P30B_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P30B_FIX/current"
printf 'PORT=19999\n' > "$P30B_FIX/.env"
SWEEP_SERVING_GREEN_ENABLED=1 run_sweep "$P30B_FIX" 19999 >/dev/null
P30B_JOURNAL="$(cat "$P30B_FIX/releases/state.json")"
assert_contains "P-30 =1 installs the new ladder (defer)" '"event":"sweep_deferred"' "$P30B_JOURNAL"
assert_not_contains "P-30 =1 does NOT roll back on the first ambiguous observation" '"event":"sweep_rollback"' "$P30B_JOURNAL"
rm -rf "$P30B_FIX"

# F5 (code-council W4) — =0 must restore the BASE disposition, and the base
# disposition was NEVER unconditional: at base the OPERATOR ESCAPE sat
# BEFORE the chain (base 941cb6321: :984-1122 escape vs :1129 chain).
# Pre-fix the switch short-circuited STRAIGHT to the chain ⇒ this shape
# HALTED FOR HUMAN (the chain's manifest gate) instead of committing.
P30C_FIX="$(mktemp -d -t p30c.XXXXXX)"; _register_fixture "$P30C_FIX"
make_install_fixture "$P30C_FIX" "$TARGET" "$PREV" "false"   # prev NOT rollback-safe
seed_journal "$P30C_FIX" "$TARGET" "$PREV" "true" "1"        # + intent_flip
ln -sfn "releases/$TARGET" "$P30C_FIX/current"
printf 'PORT=19999\n' > "$P30C_FIX/.env"
SWEEP_SERVING_GREEN_ENABLED=0 run_sweep "$P30C_FIX" 19999 >/dev/null
P30C_JOURNAL="$(cat "$P30C_FIX/releases/state.json")"
assert_contains "P-30 (=0) the OPERATOR ESCAPE runs BEFORE the chain (the BASE order)" \
    '"event":"boot_sweep_commit_and_continue"' "$P30C_JOURNAL"
assert_not_contains "P-30 (=0) the chain's manifest gate no longer halts this shape" \
    '"event":"halt"' "$P30C_JOURNAL"
assert_not_contains "P-30 (=0) no sweep_rollback on the escape shape" \
    '"event":"sweep_rollback"' "$P30C_JOURNAL"
rm -rf "$P30C_FIX"

# and =0 on a shape the escape does NOT cover still takes the chain
# (today's never-green protection is intact under the legacy switch).
P30D_FIX="$(mktemp -d -t p30d.XXXXXX)"; _register_fixture "$P30D_FIX"
make_install_fixture "$P30D_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P30D_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P30D_FIX/current"
printf 'PORT=19999\n' > "$P30D_FIX/.env"
SWEEP_SERVING_GREEN_ENABLED=0 run_sweep "$P30D_FIX" 19999 >/dev/null
P30D_JOURNAL="$(cat "$P30D_FIX/releases/state.json")"
assert_contains "P-30 (=0) the chain still fires when the escape does not apply" \
    '"event":"sweep_rollback"' "$P30D_JOURNAL"
rm -rf "$P30D_FIX"

# ===========================================================================
section "F8 — the rung-2 authorization reader is PER-ENTRY (prefix collision + cross-entry)"

# The rung-2 operator escape is gated on `intent_flip` evidence. The
# pre-fix whole-array conjunction matched the event kind and the target
# string ANYWHERE in the array ⇒ a false positive COMMIT-AND-CONTINUES a
# flip whose authorization evidence names a DIFFERENT target.
# Both legs below hold every OTHER escape gate (prev rollback_safe:false,
# displaced_version present) so the reader is the only discriminator; a
# positive control proves the escape CAN fire.
f8_escape_fired() {
    local name="$1" fix="$2" mode="$3"
    local journal
    journal="$(cat "$fix/releases/state.json")"
    case "$mode" in
        yes) assert_contains "$name" '"event":"boot_sweep_commit_and_continue"' "$journal" ;;
        no)  assert_not_contains "$name" '"event":"boot_sweep_commit_and_continue"' "$journal" ;;
    esac
}

# (a) POSITIVE CONTROL — the intent_flip names THIS target ⇒ the escape fires
F8A_FIX="$(mktemp -d -t f8a.XXXXXX)"; _register_fixture "$F8A_FIX"
make_install_fixture "$F8A_FIX" "$TARGET" "$PREV" "false"
seed_journal "$F8A_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$F8A_FIX/current"
printf 'PORT=19999\n' > "$F8A_FIX/.env"
run_sweep "$F8A_FIX" 19999 >/dev/null
f8_escape_fired "F8 control: intent_flip naming the target ⇒ the escape fires" "$F8A_FIX" yes
rm -rf "$F8A_FIX"

# (b) PREFIX COLLISION — the evidence names ${TARGET}55, never ${TARGET}
F8B_FIX="$(mktemp -d -t f8b.XXXXXX)"; _register_fixture "$F8B_FIX"
make_install_fixture "$F8B_FIX" "$TARGET" "$PREV" "false"
cat > "$F8B_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":"$PREV","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$PREV"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target ${TARGET}55 — integrity verified; operator-initiated"}]}
EOF
ln -sfn "releases/$TARGET" "$F8B_FIX/current"
printf 'PORT=19999\n' > "$F8B_FIX/.env"
run_sweep "$F8B_FIX" 19999 >/dev/null
f8_escape_fired "F8 (a) a prefix collision (${TARGET}55) does NOT authorize the escape" "$F8B_FIX" no
rm -rf "$F8B_FIX"

# (c) CROSS-ENTRY — the event kind in one entry, the target string in another
F8C_FIX="$(mktemp -d -t f8c.XXXXXX)"; _register_fixture "$F8C_FIX"
make_install_fixture "$F8C_FIX" "$TARGET" "$PREV" "false"
cat > "$F8C_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":"$PREV","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$PREV"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target v0.0.0-other — integrity verified; operator-initiated"},{"ts":"2026-10-07T10:01:00Z","event":"sweep","detail":"sweep: unrelated row mentioning target $TARGET run_id x"}]}
EOF
ln -sfn "releases/$TARGET" "$F8C_FIX/current"
printf 'PORT=19999\n' > "$F8C_FIX/.env"
run_sweep "$F8C_FIX" 19999 >/dev/null
f8_escape_fired "F8 (b) a cross-entry split does NOT authorize the escape" "$F8C_FIX" no
rm -rf "$F8C_FIX"

# (d) the LEGACY arm (=0) shares the SAME reader (one implementation)
F8D_FIX="$(mktemp -d -t f8d.XXXXXX)"; _register_fixture "$F8D_FIX"
make_install_fixture "$F8D_FIX" "$TARGET" "$PREV" "false"
cat > "$F8D_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":"$PREV","in_flight":{"kind":"promote","target":"$TARGET","started_at":"2026-10-07T09:00:00Z","flipped":true,"owner_pid":99999,"last_heartbeat":1728000000,"displaced_version":"$PREV"},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[{"ts":"2026-10-07T10:00:00Z","event":"intent_flip","detail":"preflight: intentional promote target ${TARGET}55 — integrity verified; operator-initiated"}]}
EOF
ln -sfn "releases/$TARGET" "$F8D_FIX/current"
printf 'PORT=19999\n' > "$F8D_FIX/.env"
SWEEP_SERVING_GREEN_ENABLED=0 run_sweep "$F8D_FIX" 19999 >/dev/null
f8_escape_fired "F8 (c) the LEGACY (=0) arm uses the same per-entry reader" "$F8D_FIX" no
rm -rf "$F8D_FIX"

# ===========================================================================
section "P-31 — the launcher purity scan (R-08)"

# Comment-stripping first, then WHOLE-TOKEN matching (a bare `grep nc`
# matches inside `sync`; a bare `grep python3` matches the file's own
# purity comment).
P31_BASELINE_CURL=0
P31_BASELINE_WGET=0
P31_BASELINE_NC=0
P31_BASELINE_LSOF=0
P31_BASELINE_PY=0
P31_STRIPPED="$(sed 's/#.*$//' "$REPO_ROOT/launcher.sh")"
for tok in curl wget nc lsof python3; do
    case "$tok" in
        curl) base=$P31_BASELINE_CURL ;;
        wget) base=$P31_BASELINE_WGET ;;
        nc) base=$P31_BASELINE_NC ;;
        lsof) base=$P31_BASELINE_LSOF ;;
        python3) base=$P31_BASELINE_PY ;;
    esac
    n="$(printf '%s\n' "$P31_STRIPPED" | grep -cE "(^|[^A-Za-z0-9_./-])${tok}([^A-Za-z0-9_.-]|$)" || true)"
    if [ "$n" -gt "$base" ]; then
        _fail "P-31 launcher.sh has no NEW executable '$tok' token" "<= $base" "$n"
    else
        _pass
    fi
done
# the probe must be the /dev/tcp form (no external binary)
assert_contains "P-31 the probe uses the /dev/tcp builtin form" '/dev/tcp/127.0.0.1/$port' "$(cat "$REPO_ROOT/launcher.sh")"

# ===========================================================================
section "P-33 — the owner-liveness conjunct on the WHOLE stale path"

# A stale PRE-FLIP txn whose owner is ALIVE is NOT cleared (it survives)
P33_FIX="$(mktemp -d -t p33.XXXXXX)"; _register_fixture "$P33_FIX"
make_install_fixture "$P33_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P33_FIX" "$TARGET" "$PREV" "false" "1"
# a LIVE owner pid (this shell's pid) + a FRESH heartbeat ⇒ the owner is alive
python3 - "$P33_FIX/releases/state.json" "$$" <<'PY'
import json, sys, time
path, pid = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["in_flight"]["owner_pid"] = int(pid)
data["in_flight"]["last_heartbeat"] = int(time.time())
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
ln -sfn "releases/$PREV" "$P33_FIX/current"
printf 'PORT=19999\n' > "$P33_FIX/.env"
run_sweep "$P33_FIX" 19999 >/dev/null
P33_JOURNAL="$(cat "$P33_FIX/releases/state.json")"
case "$P33_JOURNAL" in
    *'"in_flight":null'*|*'"in_flight": null'*) _fail "P-33 a live owner's pre-flip txn is NOT cleared" "in_flight present" "in_flight cleared" ;;
    *) _pass "P-33 a live owner's pre-flip txn is NOT cleared" ;;
esac
assert_not_contains "P-33 no sweep row for a live owner's txn" '"event":"sweep"' "$P33_JOURNAL"
rm -rf "$P33_FIX"

# the same txn with a DEAD owner IS cleared as today
P33B_FIX="$(mktemp -d -t p33b.XXXXXX)"; _register_fixture "$P33B_FIX"
make_install_fixture "$P33B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P33B_FIX" "$TARGET" "$PREV" "false" "1"
ln -sfn "releases/$PREV" "$P33B_FIX/current"
printf 'PORT=19999\n' > "$P33B_FIX/.env"
run_sweep "$P33B_FIX" 19999 >/dev/null
P33B_JOURNAL="$(cat "$P33B_FIX/releases/state.json")"
case "$P33B_JOURNAL" in
    *'"in_flight":null'*|*'"in_flight": null'*) _pass "P-33 a dead owner's pre-flip txn IS cleared" ;;
    *) _fail "P-33 a dead owner's pre-flip txn IS cleared" '"in_flight":null' "$(printf '%s' "$P33B_JOURNAL" | grep -oE '"in_flight":[^,}]*')" ;;
esac
rm -rf "$P33B_FIX"

# ===========================================================================
section "P-7 — tonight's 4-stage sequence (the acceptance pin)"

# stage 1: halt at hand-back ⇒ the txn stays OPEN, flipped=true (seeded)
# stage 2: the target is serving all-green (the listener)
# stage 3: the wrongful rollback ⇒ NOW a commit-close (no quarantine, no
#          counter, no cooldown)
# stage 4: the quarantine refusal ⇒ the shell gate ACCEPTS the next
#          promote (the clearance event)
P7_FIX="$(mktemp -d -t p7.XXXXXX)"; _register_fixture "$P7_FIX"
make_install_fixture "$P7_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P7_FIX" "$TARGET" "$PREV" "true" "1" "" '["'"$TARGET"'"]'
ln -sfn "releases/$TARGET" "$P7_FIX/current"
printf 'PORT=19999\n' > "$P7_FIX/.env"
P7_PORT="$(start_listener green 9.9.9-test-target)"
run_sweep "$P7_FIX" "$P7_PORT" >/dev/null
P7_JOURNAL="$(cat "$P7_FIX/releases/state.json")"
assert_contains "P-7 stage 3: the commit-close landed" '"event":"serving_green_commit"' "$P7_JOURNAL"
assert_contains "P-7 stage 3: the wrongful quarantine was CLEARED" '"event":"quarantine_cleared_serving_green"' "$P7_JOURNAL"
P7_QUAR="$(printf '%s' "$P7_JOURNAL" | grep -oE '"quarantined":\[[^]]*\]')"
case "$P7_QUAR" in
    *"$TARGET"*) _fail "P-7 stage 3: the target is un-quarantined" "absent" "$P7_QUAR" ;;
    *) _pass "P-7 stage 3: the target is un-quarantined" ;;
esac
assert_re "P-7 stage 3: the counter is untouched" '"24h": ?0' "$P7_JOURNAL"
# stage 4: the shell gate accepts a quarantined version that carries the
# clearance event
P7_GATE="$(INSTALL_DIR="$P7_FIX" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    if journal_quarantine_cleared_serving_green "'"$TARGET"'"; then echo ACCEPT; else echo REFUSE; fi
' 2>/dev/null)"
assert_eq "P-7/P-8 stage 4: the shell gate ACCEPTS the cleared version" "ACCEPT" "$P7_GATE"
rm -rf "$P7_FIX"; cleanup_listeners

# ===========================================================================
section "P-8 / P-36 — the quarantine clearance reader (the positives + the negatives)"

# The frozen rule is EXTENDED, never bypassed: without the event the
# refusal stands (byte-identical message).
P8_FIX="$(mktemp -d -t p8.XXXXXX)"; _register_fixture "$P8_FIX"
mkdir -p "$P8_FIX/releases"
cat > "$P8_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":["$TARGET"],"history":[]}
EOF
P8_GATE="$(INSTALL_DIR="$P8_FIX" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    if journal_quarantine_cleared_serving_green "'"$TARGET"'"; then echo ACCEPT; else echo REFUSE; fi
' 2>/dev/null)"
assert_eq "P-8 without the clearance event ⇒ NOT cleared (the frozen rule stands)" "REFUSE" "$P8_GATE"

# the POSITIVE half: the pinned house detail shape ⇒ cleared
python3 - "$P8_FIX/releases/state.json" "$TARGET" <<'PY'
import json, sys
path, target = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["history"].append({
    "ts": "2026-11-01T21:31:00Z",
    "event": "quarantine_cleared_serving_green",
    "detail": f"target {target} run_id r-1 serving_green_commit prev v0.28.4",
})
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
P8_GATE2="$(INSTALL_DIR="$P8_FIX" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    if journal_quarantine_cleared_serving_green "'"$TARGET"'"; then echo ACCEPT; else echo REFUSE; fi
' 2>/dev/null)"
assert_eq "P-8 the positive half: the pinned detail shape ⇒ cleared" "ACCEPT" "$P8_GATE2"

# P-36 (a) the PREFIX COLLISION: a clearance naming v0.28.55 must not clear
# v0.28.5
P36_FIX="$(mktemp -d -t p36.XXXXXX)"; _register_fixture "$P36_FIX"
mkdir -p "$P36_FIX/releases"
cat > "$P36_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":["$TARGET"],"history":[{"ts":"2026-11-01T21:31:00Z","event":"quarantine_cleared_serving_green","detail":"target ${TARGET}55 run_id r-1 serving_green_commit prev v0.28.4"}]}
EOF
P36_GATE="$(INSTALL_DIR="$P36_FIX" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    if journal_quarantine_cleared_serving_green "'"$TARGET"'"; then echo ACCEPT; else echo REFUSE; fi
' 2>/dev/null)"
assert_eq "P-36 (a) the prefix collision is NOT a clearance" "REFUSE" "$P36_GATE"

# P-36 (b) the CROSS-ENTRY split: the event kind in one entry, the target
# string in another ⇒ NOT a clearance
P36B_FIX="$(mktemp -d -t p36b.XXXXXX)"; _register_fixture "$P36B_FIX"
mkdir -p "$P36B_FIX/releases"
cat > "$P36B_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":["$TARGET"],"history":[{"ts":"2026-11-01T21:31:00Z","event":"quarantine_cleared_serving_green","detail":"run_id r-1 serving_green_commit"},{"ts":"2026-11-01T21:31:01Z","event":"sweep","detail":"target $TARGET run_id r-1"}]}
EOF
P36B_GATE="$(INSTALL_DIR="$P36B_FIX" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    if journal_quarantine_cleared_serving_green "'"$TARGET"'"; then echo ACCEPT; else echo REFUSE; fi
' 2>/dev/null)"
assert_eq "P-36 (b) the cross-entry split is NOT a clearance" "REFUSE" "$P36B_GATE"
rm -rf "$P8_FIX" "$P36_FIX" "$P36B_FIX"

# ===========================================================================
section "P-19 — the history-before-close fault injection (AM-P2-1)"

# The crash prefixes: whatever the interruption point, the NEXT actor's
# disposition is never a wrongful rollback and the sequence converges.
# The prefixes are simulated by seeding the journal in each intermediate
# state and asserting the next sweep's disposition.
#
# prefix A: current written, previous NOT written, no history, txn open
P19_FIX="$(mktemp -d -t p19.XXXXXX)"; _register_fixture "$P19_FIX"
make_install_fixture "$P19_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P19_FIX" "$TARGET" "$PREV" "true" "1"
python3 - "$P19_FIX/releases/state.json" "$TARGET" "$PREV" <<'PY'
import json, sys
path, target, prev = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.load(open(path))
data["current"] = target          # prefix A: current written, nothing else
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
ln -sfn "releases/$TARGET" "$P19_FIX/current"
printf 'PORT=19999\n' > "$P19_FIX/.env"
P19_PORT="$(start_listener green 9.9.9-test-target)"
run_sweep "$P19_FIX" "$P19_PORT" >/dev/null
P19_JOURNAL="$(cat "$P19_FIX/releases/state.json")"
assert_contains "P-19 prefix A converges to the commit-close" '"event":"serving_green_commit"' "$P19_JOURNAL"
assert_not_contains "P-19 prefix A never rolls back" '"event":"sweep_rollback"' "$P19_JOURNAL"
rm -rf "$P19_FIX"; cleanup_listeners

# prefix B: the history row landed but the txn is still open (the crash
# window AM-P2-1 closes) ⇒ the next actor must NOT roll back
P19B_FIX="$(mktemp -d -t p19b.XXXXXX)"; _register_fixture "$P19B_FIX"
make_install_fixture "$P19B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P19B_FIX" "$TARGET" "$PREV" "true" "1"
python3 - "$P19B_FIX/releases/state.json" "$TARGET" <<'PY'
import json, sys
path, target = sys.argv[1], sys.argv[2]
data = json.load(open(path))
data["current"] = target
data["previous"] = "v9.9.9-test-prev"
data["history"].append({
    "ts": "2026-10-07T12:00:00Z",
    "event": "serving_green_commit",
    "detail": f"target {target} run_id x serving_green_commit prev v9.9.9-test-prev",
})
# in_flight stays OPEN (the crash prefix)
json.dump(data, open(path, "w"), separators=(",", ":"))
PY
ln -sfn "releases/$TARGET" "$P19B_FIX/current"
printf 'PORT=19999\n' > "$P19B_FIX/.env"
run_sweep "$P19B_FIX" 19999 >/dev/null
P19B_JOURNAL="$(cat "$P19B_FIX/releases/state.json")"
assert_not_contains "P-19 prefix B never rolls back" '"event":"sweep_rollback"' "$P19B_JOURNAL"
rm -rf "$P19B_FIX"

# ===========================================================================
section "P-9 — the adopt-path polarity (the promote-entry mirror, BEHAVIOURAL)"

# The GREEN arm: a stale flipped txn + a serving-green listener + intent_flip
# ⇒ the adopt path COMMITS (history-before-close) and the promote CONTINUES
# (rc 0 — no exit 78).
P9G_FIX="$(mktemp -d -t p9g.XXXXXX)"; _register_fixture "$P9G_FIX"
make_install_fixture "$P9G_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P9G_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P9G_FIX/current"
P9G_PORT="$(start_listener green 9.9.9-test-target)"
P9G_RC=0
INSTALL_DIR="$P9G_FIX" PORT="$P9G_PORT" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    adopt_stale_txn
' >/dev/null 2>&1 || P9G_RC=$?
assert_eq "P-9 GREEN ⇒ the adopt path returns 0 (the promote continues)" "0" "$P9G_RC"
P9G_JOURNAL="$(cat "$P9G_FIX/releases/state.json")"
assert_contains "P-9 GREEN ⇒ serving_green_commit at promote entry" '"event":"serving_green_commit"' "$P9G_JOURNAL"
assert_not_contains "P-9 GREEN ⇒ NO sweep_rollback" '"event":"sweep_rollback"' "$P9G_JOURNAL"
P9G_QUAR="$(printf '%s' "$P9G_JOURNAL" | grep -oE '"quarantined": ?\[[^]]*\]')"
case "$P9G_QUAR" in
    *"$TARGET"*) _fail "P-9 GREEN ⇒ NO quarantine" "absent" "$P9G_QUAR" ;;
    *) _pass "P-9 GREEN ⇒ NO quarantine" ;;
esac
assert_re "P-9 GREEN ⇒ the counter is untouched" '"24h": ?0' "$P9G_JOURNAL"
case "$P9G_JOURNAL" in
    *'"in_flight":null'*|*'"in_flight": null'*) _pass "P-9 GREEN ⇒ the txn is closed" ;;
    *) _fail "P-9 GREEN ⇒ the txn is closed" '"in_flight":null' "$(printf '%s' "$P9G_JOURNAL" | grep -oE '"in_flight":[^,}]*')" ;;
esac
rm -rf "$P9G_FIX"; cleanup_listeners

# The GREEN-without-intent_flip arm: the EXISTING chain (§D.4 (a-i) — the
# documented divergence; this lane does not rewrite the adopt path's
# pre-ruling behaviour).
P9X_FIX="$(mktemp -d -t p9x.XXXXXX)"; _register_fixture "$P9X_FIX"
make_install_fixture "$P9X_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P9X_FIX" "$TARGET" "$PREV" "true" "0"
ln -sfn "releases/$TARGET" "$P9X_FIX/current"
P9X_PORT="$(start_listener green 9.9.9-test-target)"
P9X_RC=0
INSTALL_DIR="$P9X_FIX" PORT="$P9X_PORT" bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    adopt_stale_txn
' >/dev/null 2>&1 || P9X_RC=$?
assert_eq "P-9 the existing chain completes (the promote continues past it)" "0" "$P9X_RC"
P9X_JOURNAL="$(cat "$P9X_FIX/releases/state.json")"
assert_contains "P-9 GREEN-without-intent_flip ⇒ the EXISTING chain (sweep_rollback)" '"event":"sweep_rollback"' "$P9X_JOURNAL"
assert_not_contains "P-9 GREEN-without-intent_flip ⇒ no serving_green_commit" '"event":"serving_green_commit"' "$P9X_JOURNAL"
rm -rf "$P9X_FIX"; cleanup_listeners

# The NOT-GREEN arm: no listener ⇒ the existing chain UNCHANGED (§D.4 (a-ii)
# — no defer, no grace on the adopt path).
P9N_FIX="$(mktemp -d -t p9n.XXXXXX)"; _register_fixture "$P9N_FIX"
make_install_fixture "$P9N_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P9N_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P9N_FIX/current"
P9N_RC=0
INSTALL_DIR="$P9N_FIX" PORT=19999 bash -c '
    . "'"$REPO_ROOT"'/scripts/upgrade/lib.sh" >/dev/null 2>&1
    adopt_stale_txn
' >/dev/null 2>&1 || P9N_RC=$?
assert_eq "P-9 NOT-GREEN ⇒ the existing chain completes" "0" "$P9N_RC"
P9N_JOURNAL="$(cat "$P9N_FIX/releases/state.json")"
assert_contains "P-9 NOT-GREEN ⇒ sweep_rollback (the existing chain unchanged)" '"event":"sweep_rollback"' "$P9N_JOURNAL"
rm -rf "$P9N_FIX"

# ===========================================================================
section "P-32 — the T15 hook (narrow, idempotent, never clears a pre-flip txn)"

# The hook is the flipped-branch helper called after a child exit. Its
# narrowness is asserted structurally: the helper's source contains NO
# pre-flip clear (the "sweep" event append lives in _journal_sweep).
P32_SRC="$(sed -n '/^_js_sweep_flipped_disposition() {/,/^}/p' "$REPO_ROOT/launcher.sh")"
case "$P32_SRC" in
    *'"sweep"'*) _fail "P-32 the hook does NOT clear a stale pre-flip txn" "no sweep row" "a sweep row" ;;
    *) _pass "P-32 the hook does NOT clear a stale pre-flip txn" ;;
esac
assert_contains "P-32 the run loop calls the flipped-branch helper" '_js_sweep_flipped_disposition "$INSTALL_DIR" 0' "$(cat "$REPO_ROOT/launcher.sh")"

# F1 (code-council CRITICAL) — THE LOCK-ABSENCE PIN. The P-32 legs above
# are INSUFFICIENT for the lock: the idempotence leg drives
# `_journal_sweep` (lock_held=1 — the caller owns the lock), never the
# hook's lock_held=0 path where the HELPER takes the lock itself. Pre-fix
# that path leaked releases/rollback.lock.d with owner=$$ (the launcher —
# ALIVE) ⇒ no stale-break could ever fire ⇒ every later promote refused
# pipeline-busy ⇒ the daemon closer lock_busy on every tick ⇒ the ladder
# accumulated to the ceiling ⇒ a SERVING-GREEN release rolled back +
# quarantined. Drive the hook's OWN path.
f1_lock_absent() {
    local name="$1" fix="$2"
    if [ -e "$fix/releases/rollback.lock.d" ]; then
        _fail "$name" "lock ABSENT after the hook" \
            "present: $(ls -d "$fix"/releases/rollback.lock.d 2>/dev/null)"
    else
        _pass
    fi
}

# F1a — the DEFER exit path (an ambiguous observation, no listener)
F1A_FIX="$(mktemp -d -t f1a.XXXXXX)"; _register_fixture "$F1A_FIX"
make_install_fixture "$F1A_FIX" "$TARGET" "$PREV" "true"
seed_journal "$F1A_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$F1A_FIX/current"
printf 'PORT=19999\n' > "$F1A_FIX/.env"
F1A_OUT="$(run_hook "$F1A_FIX" 19999 0)"
assert_contains "F1a the hook actually ran the disposition (a defer row landed)" '"event":"sweep_deferred"' "$(cat "$F1A_FIX/releases/state.json")"
f1_lock_absent "F1a the T15 hook path (defer) releases the pipeline lock" "$F1A_FIX"
rm -rf "$F1A_FIX"

# F1b — the COMMIT-CLOSE exit path (serving green + intent_flip): the
# green arms returned WITHOUT a release pre-fix, a second leak site.
F1B_FIX="$(mktemp -d -t f1b.XXXXXX)"; _register_fixture "$F1B_FIX"
make_install_fixture "$F1B_FIX" "$TARGET" "$PREV" "true"
seed_journal "$F1B_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$F1B_FIX/current"
printf 'PORT=19999\n' > "$F1B_FIX/.env"
F1B_PORT="$(start_listener green 9.9.9-test-target)"
run_hook "$F1B_FIX" "$F1B_PORT" 0 >/dev/null
assert_contains "F1b the hook committed serving-green on this leg" '"event":"serving_green_commit"' "$(cat "$F1B_FIX/releases/state.json")"
f1_lock_absent "F1b the T15 hook path (commit-close) releases the pipeline lock" "$F1B_FIX"
rm -rf "$F1B_FIX"; cleanup_listeners

# F1c — the pre-flip / already-closed fast path must not create a lock at
# all (no txn ⇒ nothing to dispose of)
F1C_FIX="$(mktemp -d -t f1c.XXXXXX)"; _register_fixture "$F1C_FIX"
make_install_fixture "$F1C_FIX" "$TARGET" "$PREV" "true"
cat > "$F1C_FIX/releases/state.json" <<EOF
{"current":"$PREV","previous":"$PREV","in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
EOF
printf 'PORT=19999\n' > "$F1C_FIX/.env"
run_hook "$F1C_FIX" 19999 0 >/dev/null
f1_lock_absent "F1c the hook on a closed txn takes no lock" "$F1C_FIX"
rm -rf "$F1C_FIX"

# F1d — STRUCTURAL: the belt-and-braces release at the run-loop call site
# exists (the net that degrades a future missed helper exit path from
# "the lock leaks forever" to "the call site cleans up").
assert_contains "F1d the run-loop hook carries the belt-and-braces release" \
    '_js_sweep_release "$INSTALL_DIR" 0' "$(cat "$REPO_ROOT/launcher.sh")"

# idempotence: a second helper call on a closed txn is a no-op
P32_FIX="$(mktemp -d -t p32.XXXXXX)"; _register_fixture "$P32_FIX"
make_install_fixture "$P32_FIX" "$TARGET" "$PREV" "true"
seed_journal "$P32_FIX" "$TARGET" "$PREV" "true" "1"
ln -sfn "releases/$TARGET" "$P32_FIX/current"
printf 'PORT=19999\n' > "$P32_FIX/.env"
P32_PORT="$(start_listener green 9.9.9-test-target)"
run_sweep "$P32_FIX" "$P32_PORT" >/dev/null
P32_J1="$(cat "$P32_FIX/releases/state.json")"
run_sweep "$P32_FIX" "$P32_PORT" >/dev/null
P32_J2="$(cat "$P32_FIX/releases/state.json")"
assert_eq "P-32 the second call is a no-op (idempotent)" "$P32_J1" "$P32_J2"
rm -rf "$P32_FIX"; cleanup_listeners

# ===========================================================================
section "P-9 — the adopt-path polarity (the promote-entry mirror)"

# The adopt path is shell-side (lib.sh); its serving-green branch needs a
# probe answer, so the leg is driven through the lib.sh helpers with a
# stubbed probe. The STRUCTURAL assertion (the branch exists and is
# gated on intent_flip) is the pack's scope; the behavioural leg is the
# phase-3 consolidated map's.
assert_contains "P-9 the adopt path has the serving-green branch" "_sp_green" "$(cat "$REPO_ROOT/scripts/upgrade/lib.sh")"
assert_contains "P-9 the adopt path's GREEN arm is gated on intent_flip" 'journal_history_has_intent_flip "$target"' "$(cat "$REPO_ROOT/scripts/upgrade/lib.sh")"
assert_contains "P-9 the adopt path commits the serving-green txn" 'journal_history_append serving_green_commit' "$(cat "$REPO_ROOT/scripts/upgrade/lib.sh")"

# ===========================================================================
section "summary"

printf '\n== summary ==\n'
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -ne 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
exit 0
