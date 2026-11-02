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
