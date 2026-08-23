#!/bin/bash
# ============================================================================
# tests/test_launcher.sh — unit tests for launcher.sh (Auto-Restart Phase 1)
# ============================================================================
# Portable plain-bash tests: no bats, no new dev-dependency. Self-asserting;
# exits nonzero on any failure.
#
#   bash tests/test_launcher.sh
#
# Strategy: source launcher.sh (the source-guard in main must hold — sourcing
# must NOT run anything), then unit-test the pure decision functions
# (classify_exit / next_backoff / budget_tick), the .env parser, the
# state-file round-trip + corrupt tolerance, and the ADR-012 journal sweep
# (P2.1 T7: decision table against fixture state.json + release dirs — pure
# filesystem sandboxes, no daemon, no network). NO binaries are ever spawned;
# the run-loop itself is not executed here.
# ============================================================================

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LAUNCHER="$REPO_ROOT/launcher.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() {
    PASS=$((PASS + 1))
}

_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}

# assert_eq <name> <expected> <actual>
assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        _pass
    else
        _fail "$name" "$expected" "$actual"
    fi
}

# assert_contains <name> <needle> <haystack>
assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

section() {
    printf '\n== %s ==\n' "$1"
}

# ─── 1. bash -n syntax gate ─────────────────────────────────────────────────
section "bash -n syntax gate"
if bash -n "$LAUNCHER" 2>/dev/null; then
    _pass
else
    _fail "launcher.sh passes bash -n"
fi

# ─── 2. Source guard: sourcing must not run anything ────────────────────────
section "source guard"
# Source IN THE CURRENT SHELL so functions persist for sections 3–5.
# If the source-guard were broken, sourcing would run main() → it would try
# to resolve INSTALL_DIR/binaries and we would likely not reach here at all.
# shellcheck disable=SC1090
if . "$LAUNCHER" 2>/dev/null; then
    _pass
else
    _fail "sourcing launcher.sh succeeds"
fi
if declare -F main >/dev/null 2>&1; then
    _pass
else
    _fail "main() defined after sourcing"
fi
if declare -F classify_exit >/dev/null 2>&1 \
    && declare -F next_backoff >/dev/null 2>&1 \
    && declare -F effective_prev_backoff >/dev/null 2>&1 \
    && declare -F budget_tick >/dev/null 2>&1 \
    && declare -F load_env_file >/dev/null 2>&1 \
    && declare -F read_state >/dev/null 2>&1 \
    && declare -F write_state >/dev/null 2>&1 \
    && declare -F _journal_sweep >/dev/null 2>&1 \
    && declare -F _notify_once >/dev/null 2>&1 \
    && declare -F resolve_binary >/dev/null 2>&1; then
    _pass
else
    _fail "pure-logic functions defined after sourcing"
fi

# ─── 3. classify_exit mapping ───────────────────────────────────────────────
section "classify_exit"
assert_eq "classify_exit(0) = clean"      "clean"    "$(classify_exit 0)"
assert_eq "classify_exit(75) = tempfail"  "tempfail" "$(classify_exit 75)"
assert_eq "classify_exit(78) = refuse"    "refuse"   "$(classify_exit 78)"
assert_eq "classify_exit(1) = crash"      "crash"    "$(classify_exit 1)"
assert_eq "classify_exit(2) = crash"      "crash"    "$(classify_exit 2)"
assert_eq "classify_exit(127) = crash"    "crash"    "$(classify_exit 127)"
assert_eq "classify_exit(255) = crash"    "crash"    "$(classify_exit 255)"
assert_eq "classify_exit(139) = crash"    "crash"    "$(classify_exit 139)"

# ─── 4. next_backoff — crash track (10 → 20 → 40 → … → 300) ────────────────
section "next_backoff (crash track)"
assert_eq "crash backoff from 0"     "10"  "$(next_backoff 0 1)"
assert_eq "crash backoff 10→20"      "20"  "$(next_backoff 10 1)"
assert_eq "crash backoff 20→40"      "40"  "$(next_backoff 20 1)"
assert_eq "crash backoff 40→80"      "80"  "$(next_backoff 40 1)"
assert_eq "crash backoff 80→160"     "160" "$(next_backoff 80 1)"
assert_eq "crash backoff 160→300(cap)" "300" "$(next_backoff 160 1)"
assert_eq "crash backoff 300→300 (capped)" "300" "$(next_backoff 300 1)"
assert_eq "crash backoff 600→300 (over cap clamps)" "300" "$(next_backoff 600 1)"

section "next_backoff (75 tempfail track)"
assert_eq "75 backoff from 0"        "5"  "$(next_backoff 0 75)"
assert_eq "75 backoff 5→10"          "10" "$(next_backoff 5 75)"
assert_eq "75 backoff 10→20"         "20" "$(next_backoff 10 75)"
assert_eq "75 backoff 20→40"         "40" "$(next_backoff 20 75)"
assert_eq "75 backoff 40→60(cap)"    "60" "$(next_backoff 40 75)"
assert_eq "75 backoff 60→60 (capped)" "60" "$(next_backoff 60 75)"
assert_eq "75 backoff 300→60 (cross-track cap applies)" "60" "$(next_backoff 300 75)"

# ─── 4b. track-switch backoff reset (review m3) ────────────────────────────
section "next_backoff track-switch reset (effective_prev_backoff)"
# STATE_LAST_BACKOFF persists across verdict families, but the crash and
# tempfail-75 ladders are independent: switching family must reset the
# persisted backoff to 0 BEFORE next_backoff computes, else a 75-track prev
# of 60 yields a 120s first crash backoff (12× the 10s base).

# direct unit: family comparison gates the persisted value
assert_eq "prev 75 → crash discards persisted 60"   "0" "$(effective_prev_backoff 75 1 60)"
assert_eq "prev crash → 75 discards persisted 300"  "0" "$(effective_prev_backoff 1 75 300)"
assert_eq "prev 75 → 75 keeps persisted 40"         "40" "$(effective_prev_backoff 75 75 40)"
assert_eq "any crash code = same family (1→143)"    "20" "$(effective_prev_backoff 1 143 20)"
assert_eq "any crash code = same family (143→2)"    "80" "$(effective_prev_backoff 143 2 80)"
assert_eq "no previous cycle (empty) → reset"       "0" "$(effective_prev_backoff "" 75 40)"
assert_eq "previous clean stop (0) → stale backoff discarded" "0" "$(effective_prev_backoff 0 1 40)"
assert_eq "previous refuse (78) → stale backoff discarded"   "0" "$(effective_prev_backoff 78 1 40)"

# sequence simulation: evolve (STATE_LAST_EXIT, STATE_LAST_BACKOFF) exactly
# as run_loop does — prev captured BEFORE STATE_LAST_EXIT is overwritten.
sim_backoff_seq() {
    STATE_LAST_EXIT=""
    STATE_LAST_BACKOFF=0
    local code prev
    for code in "$@"; do
        prev="$(effective_prev_backoff "${STATE_LAST_EXIT:-}" "$code" "${STATE_LAST_BACKOFF:-0}")"
        STATE_LAST_BACKOFF="$(next_backoff "$prev" "$code")"
        STATE_LAST_EXIT="$code"
    done
    printf '%s\n' "$STATE_LAST_BACKOFF"
}
assert_eq "75×3 then crash → first crash backoff 10 (not 60/120)" \
    "10" "$(sim_backoff_seq 75 75 75 1)"
assert_eq "crash×3 then 75 → first 75 backoff 5 (not 40/60)" \
    "5" "$(sim_backoff_seq 1 1 1 75)"
assert_eq "75×5 same-family growth unchanged (→60 cap)" \
    "60" "$(sim_backoff_seq 75 75 75 75 75)"
assert_eq "crash×4 same-family growth unchanged (10→20→40→80)" \
    "80" "$(sim_backoff_seq 1 1 1 1)"
assert_eq "75 then crash×2 → 10 then 20 (growth resumes at base)" \
    "20" "$(sim_backoff_seq 75 1 1)"
assert_eq "crash then 75×2 → 5 then 10 (growth resumes at base)" \
    "10" "$(sim_backoff_seq 1 75 75)"

# Structural pin (incident follow-up): sim_backoff_seq models the INTENDED
# order (prev captured before STATE_LAST_EXIT is overwritten). The run_loop
# itself must follow that order in BOTH branches — the crash branch
# originally passed ${STATE_LAST_EXIT:-} AFTER it had been overwritten with
# the current exit, making the family comparison always-equal (tautology)
# and the reset a no-op, while the sequence simulation above kept passing.
# Grep the run_loop body so a regression back to the tautology fails here.
run_loop_body="$(sed -n '/^run_loop()/,/^}/p' "$LAUNCHER")"
crash_calls="$(printf '%s\n' "$run_loop_body" | grep -c 'effective_prev_backoff "\$prev_exit"')"
assert_eq "run_loop: both branches gate on prev_exit (not overwritten STATE_LAST_EXIT)" \
    "2" "$crash_calls"
assert_eq "run_loop: no branch passes overwritten STATE_LAST_EXIT to the gate" \
    "0" "$(printf '%s\n' "$run_loop_body" | grep -c 'effective_prev_backoff "\${STATE_LAST_EXIT:-}"')"
assert_eq "run_loop: prev_exit captured before STATE_LAST_EXIT overwrite" \
    "1" "$(printf '%s\n' "$run_loop_body" | grep -c 'prev_exit="\${STATE_LAST_EXIT:-}"')"

# ─── 5. budget_tick ─────────────────────────────────────────────────────────
section "budget_tick"
# budget_tick <count> <window_start> <uptime_s> <code> [now]
NOW=1000000
IN_WINDOW=$((NOW - 60))       # window opened 60s ago → live
AT_EDGE=$((NOW - 600))        # exactly 600s old → still inside (boundary: >600 ages out)

# 75 is exempt: budget untouched
assert_eq "75 exempt (count stays, action=exempt)" \
    "0 $IN_WINDOW exempt" "$(budget_tick 0 $IN_WINDOW 10 75 $NOW)"
assert_eq "75 exempt even with high count" \
    "5 $IN_WINDOW exempt" "$(budget_tick 5 $IN_WINDOW 10 75 $NOW)"

# Fresh window (window_start ≤ 0) → reset
assert_eq "no window → reset, count=1" \
    "1 $NOW reset" "$(budget_tick 0 0 5 1 $NOW)"

# Crash inside window → count increments
assert_eq "crash #1 counted" \
    "1 $IN_WINDOW count" "$(budget_tick 0 $IN_WINDOW 5 1 $NOW)"
assert_eq "crash #2 counted" \
    "2 $IN_WINDOW count" "$(budget_tick 1 $IN_WINDOW 5 1 $NOW)"
assert_eq "crash #5 counted (at budget, not over)" \
    "5 $IN_WINDOW count" "$(budget_tick 4 $IN_WINDOW 5 1 $NOW)"
# >5 crashes within 10 min → abort
assert_eq "crash #6 in-window → abort" \
    "6 $IN_WINDOW abort" "$(budget_tick 5 $IN_WINDOW 5 1 $NOW)"

# Window aged out (now - window_start > 600) → reset
assert_eq "aged window → reset" \
    "1 $NOW reset" "$(budget_tick 5 $((NOW - 601)) 5 1 $NOW)"
assert_eq "window exactly 600s old → still in-window" \
    "6 $AT_EDGE abort" "$(budget_tick 5 $AT_EDGE 5 1 $NOW)"

# Uptime ≥ 600s continuous → reset (ADR-011 #2)
assert_eq "uptime 600s → reset" \
    "1 $NOW reset" "$(budget_tick 5 $IN_WINDOW 600 1 $NOW)"
assert_eq "uptime >600s → reset" \
    "1 $NOW reset" "$(budget_tick 5 $IN_WINDOW 3600 1 $NOW)"
assert_eq "uptime 599s → NO reset (still counts)" \
    "6 $IN_WINDOW abort" "$(budget_tick 5 $IN_WINDOW 599 1 $NOW)"

# ─── 6. .env parser ─────────────────────────────────────────────────────────
section "load_env_file"
ENV_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-test.XXXXXX")"

# 6a. comments / quotes / spaces / export prefix / CRLF
cat > "$ENV_TEST_DIR/env1" <<'EOF'
# full-line comment
PLAIN=value123

   # indented comment
export EXPORTED=yes
QUOTED_D="double quoted value"
QUOTED_S='single quoted value'
SPACES=a value with spaces
EMPTY=
NOQUOTE_MIX="inner 'quotes' kept"
	LEADING_TAB=tabvalue

garbage-line-without-equals
1BADKEY=x
EOF
printf 'CRLF_LINE=crlfvalue\r\n' >> "$ENV_TEST_DIR/env1"

(
    . "$LAUNCHER"
    load_env_file "$ENV_TEST_DIR/env1"
    [ "${PLAIN:-}" = "value123" ]                     || exit 10
    [ "${EXPORTED:-}" = "yes" ]                       || exit 11
    [ "${QUOTED_D:-}" = "double quoted value" ]       || exit 12
    [ "${QUOTED_S:-}" = "single quoted value" ]       || exit 13
    [ "${SPACES:-}" = "a value with spaces" ]         || exit 14
    [ "${EMPTY-UNSET}" = "" ]                          || exit 15  # set-but-empty (bash: `${E:-x}` treats empty as unset → use `${E-x}`)
    [ "${NOQUOTE_MIX:-}" = "inner 'quotes' kept" ]    || exit 16
    [ "${LEADING_TAB:-}" = "tabvalue" ]               || exit 17
    [ "${CRLF_LINE:-}" = "crlfvalue" ]                || exit 18
    exit 0
)
ENV1_RC=$?
assert_eq "env1: all lines parsed correctly" "0" "$ENV1_RC"

# 6b. precedence: loader export wins over pre-set value
(
    . "$LAUNCHER"
    PRECEDENCE=original
    printf 'PRECEDENCE=fromfile\n' > "$ENV_TEST_DIR/env2"
    load_env_file "$ENV_TEST_DIR/env2"
    [ "${PRECEDENCE:-}" = "fromfile" ] && exit 0
    exit 20
)
assert_eq "env loader overrides pre-set var" "0" "$?"

# 6c. missing file tolerated (returns 0, logs WARN)
(
    . "$LAUNCHER"
    if load_env_file "$ENV_TEST_DIR/does-not-exist"; then
        exit 0
    fi
    exit 21
)
assert_eq "missing env file tolerated (exit 0)" "0" "$?"

# 6d. value with '=' inside is preserved
(
    . "$LAUNCHER"
    printf 'EQ_VALUE=key=val=ue\nURL=https://x.example/a?b=c\n' > "$ENV_TEST_DIR/env3"
    load_env_file "$ENV_TEST_DIR/env3"
    [ "${EQ_VALUE:-}" = "key=val=ue" ]    || exit 30
    [ "${URL:-}" = "https://x.example/a?b=c" ] || exit 31
    exit 0
)
assert_eq "values containing '=' parsed whole" "0" "$?"

# ─── 7. State file round-trip + corrupt tolerance ───────────────────────────
section "state file"
STATE_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-state.XXXXXX")"

# 7a. write → read round-trip
(
    . "$LAUNCHER"
    STATE_FILE="$STATE_TEST_DIR/.launcher-state"
    STATE_LAST_EXIT=75
    STATE_CRASH_COUNT=3
    STATE_WINDOW_START=1234567890
    STATE_LAST_BACKOFF=40
    STATE_NOTIFIED_75=1
    STATE_LAST_UPTIME=42
    write_state

    # reset globals to defaults, then read back
    STATE_LAST_EXIT=""
    STATE_CRASH_COUNT=0
    STATE_WINDOW_START=0
    STATE_LAST_BACKOFF=0
    STATE_NOTIFIED_75=0
    STATE_LAST_UPTIME=0
    read_state
    [ "${STATE_LAST_EXIT:-}" = "75" ]          || exit 40
    [ "${STATE_CRASH_COUNT:-}" = "3" ]         || exit 41
    [ "${STATE_WINDOW_START:-}" = "1234567890" ] || exit 42
    [ "${STATE_LAST_BACKOFF:-}" = "40" ]       || exit 43
    [ "${STATE_LAST_UPTIME:-}" = "42" ]        || exit 45
    [ "${STATE_NOTIFIED_75:-}" = "1" ]         || exit 46
    exit 0
)
assert_eq "state write→read round-trip" "0" "$?"

# 7b. atomic-write side effect: no .tmp droppings
TMP_LEFT="$(ls "$STATE_TEST_DIR" | grep -c '\.tmp' || true)"
assert_eq "no .tmp droppings after write_state" "0" "$TMP_LEFT"

# 7c. corrupt file tolerated (defaults restored, no crash)
(
    . "$LAUNCHER"
    STATE_FILE="$STATE_TEST_DIR/.launcher-state"
    printf 'this is not key=value at all\nrandom garbage\n' > "$STATE_FILE"
    read_state
    [ "${STATE_CRASH_COUNT:-}" = "0" ]         || exit 50
    [ "${STATE_WINDOW_START:-}" = "0" ]        || exit 51
    [ "${STATE_LAST_BACKOFF:-}" = "0" ]        || exit 52
    exit 0
)
assert_eq "corrupt state file → defaults, no crash" "0" "$?"

# 7d. partially corrupt: valid lines survive, invalid ignored
(
    . "$LAUNCHER"
    STATE_FILE="$STATE_TEST_DIR/.launcher-state"
    printf 'crash_count=7\ngarbage line\nlast_backoff=80\n' > "$STATE_FILE"
    read_state
    [ "${STATE_CRASH_COUNT:-}" = "7" ]         || exit 60
    [ "${STATE_LAST_BACKOFF:-}" = "80" ]       || exit 61
    [ "${STATE_WINDOW_START:-}" = "0" ]        || exit 62
    exit 0
)
assert_eq "partially corrupt state: valid keys survive" "0" "$?"

# 7e. missing state file tolerated
(
    . "$LAUNCHER"
    STATE_FILE="$STATE_TEST_DIR/no-such-state"
    read_state
    [ "${STATE_CRASH_COUNT:-}" = "0" ] || exit 70
    exit 0
)
assert_eq "missing state file → defaults" "0" "$?"

# ─── 8. Journal sweep (ADR-012 / P2.1 T7) ──────────────────────────────────
# Decision table (test-strategy.md §P2.1): stale (>600s) + flipped →
# sweep-rollback executed (current repointed to previous, sweep_rollback
# history event, ADR-024 counter+cooldown, quarantine); stale + not flipped
# → txn cleared; fresh (≤600s) → untouched; no journal → no-op. The sweep is
# NEVER refused by cap/cooldown (D-FA4.2 entry-side only). kind=restart is
# never launcher-swept (D-FA4.3). Fixtures are pure filesystem sandboxes.
section "journal sweep"
JS_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-js.XXXXXX")"

# _js_fixture <dir> — two release dirs (with rollback_safe:true manifests —
# the sweep's D-FA4.5 gate reads previous's manifest) + `current` -> v0.10.6
# (the flipped/orphaned state; pre-flip tests re-point the symlink)
_js_fixture() {
    mkdir -p "$1/releases/v0.10.5" "$1/releases/v0.10.6"
    printf 'x' > "$1/releases/v0.10.5/ensemble-prod"
    printf 'x' > "$1/releases/v0.10.6/ensemble-prod"
    printf '{"version":"v0.10.5","binary_version":"v0.10.5","rollback_safe":true}\n' \
        > "$1/releases/v0.10.5/manifest.json"
    printf '{"version":"v0.10.6","binary_version":"v0.10.6","rollback_safe":true}\n' \
        > "$1/releases/v0.10.6/manifest.json"
    ln -sfn "releases/v0.10.6" "$1/current"
}

# _js_seed <dir> <started_iso> <flipped> [count] [window_start_iso] [cooldown_raw] [kind]
_js_seed() {
    local d="$1" started="$2" flipped="$3" cnt="${4:-1}" wstart="${5:-$2}" cd_raw="${6:-null}" kind="${7:-promote}"
    printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"%s","target":"v0.10.6","started_at":"%s","flipped":%s,"owner_pid":999999},"rollback_window_count":{"24h":%s,"window_start":"%s"},"cooldown_until":%s,"quarantined":[],"history":[]}\n' \
        "$kind" "$started" "$flipped" "$cnt" "$wstart" "$cd_raw" > "$d/releases/state.json"
}

_js_run() {  # run the sweep against <dir> in a fresh launcher shell
    ( . "$LAUNCHER"; INSTALL_DIR="$1" _journal_sweep >/dev/null 2>&1 )
}

_js_journal() {  # raw journal bytes
    cat "$1/releases/state.json" 2>/dev/null
}

_js_field() {   # _js_field <dir> <key> — top-level field of the journal
    ( . "$LAUNCHER"; _js_json_field "$(_js_journal "$1")" "$2" 2>/dev/null )
}

_js_count() {   # rollback_window_count "24h" value
    ( . "$LAUNCHER"; _js_json_field "$(_js_json_sub "$(_js_journal "$1")" rollback_window_count 2>/dev/null)" 24h 2>/dev/null )
}

STALE_TS="$(date -ju -v-700S +%Y-%m-%dT%H:%M:%SZ)"
FRESH_TS="$(date -ju -v-30S +%Y-%m-%dT%H:%M:%SZ)"
OLD25H_TS="$(date -ju -v-90000S +%Y-%m-%dT%H:%M:%SZ)"

# 8a. stale + flipped:true → sweep-rollback: current repointed to previous,
#     sweep_rollback history event, counter incremented, cooldown armed,
#     target quarantined, in_flight cleared (ADR-012/024).
JS_A="$JS_TEST_DIR/a"; _js_fixture "$JS_A"; _js_seed "$JS_A" "$STALE_TS" true
_js_run "$JS_A"
assert_eq "8a sweep-rollback: rc 0 (boot proceeds)" "0" "$?"
assert_eq "8a current repointed to previous" "releases/v0.10.5" "$(readlink "$JS_A/current")"
assert_eq "8a journal current updated" "v0.10.5" "$(_js_field "$JS_A" current)"
assert_eq "8a in_flight cleared" "null" "$(_js_field "$JS_A" in_flight)"
assert_eq "8a rollback counter incremented (1→2)" "2" "$(_js_count "$JS_A")"
assert_contains "8a sweep_rollback history event recorded" '"event":"sweep_rollback"' "$(_js_journal "$JS_A")"
assert_contains "8a failed target quarantined" '"v0.10.6"' \
    "$( . "$LAUNCHER"; _js_json_sub "$(_js_journal "$JS_A")" quarantined 2>/dev/null)"
CD_A="$(_js_field "$JS_A" cooldown_until)"
case "$CD_A" in null|"") _fail "8a cooldown armed after sweep-rollback" "ISO ts" "$CD_A" ;; *) _pass ;; esac
[ -d "$JS_A/releases/rollback.lock.d" ] && _fail "8a lock dir left behind" "absent" "present" || _pass
ls "$JS_A" | grep -q 'current\.new' && _fail "8a current.new.\$\$ droppings" "absent" "present" || _pass

# 8b. stale + flipped:false + current NOT at the txn target → in_flight
#     cleared, sweep history event, current UNTOUCHED (never flipped), no
#     counter/cooldown mutation. B3 regression pin: a TRUE pre-flip txn
#     leaves current on the serving release (the flip never happened);
#     flipped:false + current→target is the kill-window anomaly (8r).
JS_B="$JS_TEST_DIR/b"; _js_fixture "$JS_B"; _js_seed "$JS_B" "$STALE_TS" false
ln -sfn "releases/v0.10.5" "$JS_B/current"   # pre-flip: still on the serving release
_js_run "$JS_B"
assert_eq "8b stale pre-flip txn cleared: rc 0" "0" "$?"
assert_eq "8b in_flight cleared" "null" "$(_js_field "$JS_B" in_flight)"
assert_eq "8b current symlink untouched (never flipped)" "releases/v0.10.5" "$(readlink "$JS_B/current")"
assert_contains "8b sweep history event recorded" '"event":"sweep"' "$(_js_journal "$JS_B")"
if printf '%s' "$(_js_journal "$JS_B")" | grep -q 'sweep_rollback'; then
    _fail "8b no sweep_rollback event (nothing rolled back)" "absent" "present"
else
    _pass
fi
assert_eq "8b rollback counter NOT incremented" "1" "$(_js_count "$JS_B")"
assert_eq "8b cooldown NOT armed" "null" "$(_js_field "$JS_B" cooldown_until)"

# 8c. fresh (≤600s) in_flight → left completely alone (owner may be alive).
JS_C="$JS_TEST_DIR/c"; _js_fixture "$JS_C"; _js_seed "$JS_C" "$FRESH_TS" true
JS_C_BEFORE="$(_js_journal "$JS_C")"
_js_run "$JS_C"
assert_eq "8c fresh txn: rc 0" "0" "$?"
assert_eq "8c journal byte-identical (untouched)" "$JS_C_BEFORE" "$(_js_journal "$JS_C")"
assert_eq "8c current symlink untouched" "releases/v0.10.6" "$(readlink "$JS_C/current")"

# 8d. no journal → silent no-op (pre-existing behavior preserved).
JS_D="$JS_TEST_DIR/d"; mkdir -p "$JS_D/releases"
( . "$LAUNCHER"; INSTALL_DIR="$JS_D" _journal_sweep >/dev/null 2>&1 )
assert_eq "8d no journal → silent success" "0" "$?"

# 8e. D-FA4.2: the sweep NEVER refuses on cap — count already AT cap (3)
#     + stale flipped → rollback STILL executes, count → 4, halt event armed.
JS_E="$JS_TEST_DIR/e"; _js_fixture "$JS_E"; _js_seed "$JS_E" "$STALE_TS" true 3 "$STALE_TS"
_js_run "$JS_E"
assert_eq "8e at-cap sweep-rollback still executes: rc 0" "0" "$?"
assert_eq "8e current repointed past cap" "releases/v0.10.5" "$(readlink "$JS_E/current")"
assert_eq "8e count increments past cap (entry-side ≥3 check)" "4" "$(_js_count "$JS_E")"
assert_contains "8e halt event armed at cap" '"event":"halt"' "$(_js_journal "$JS_E")"
assert_eq "8e in_flight cleared (recovery completed)" "null" "$(_js_field "$JS_E" in_flight)"

# 8f. D-FA4.2: active cooldown NEVER refuses the sweep — cooldown_until in
#     the FUTURE + stale flipped → rollback still executes + cooldown re-armed.
JS_F="$JS_TEST_DIR/f"; _js_fixture "$JS_F"
_js_seed "$JS_F" "$STALE_TS" true 1 "$STALE_TS" "\"$(date -ju -v+300S +%Y-%m-%dT%H:%M:%SZ)\""
_js_run "$JS_F"
assert_eq "8f cooldown-active sweep-rollback still executes: rc 0" "0" "$?"
assert_eq "8f current repointed despite cooldown" "releases/v0.10.5" "$(readlink "$JS_F/current")"
assert_eq "8f in_flight cleared" "null" "$(_js_field "$JS_F" in_flight)"

# 8g. ADR-005 window rollover (R1.4): window_start >24h old → count resets,
#     this sweep-rollback re-opens the window at 1.
JS_G="$JS_TEST_DIR/g"; _js_fixture "$JS_G"; _js_seed "$JS_G" "$STALE_TS" true 2 "$OLD25H_TS"
_js_run "$JS_G"
assert_eq "8g stale window rollover → count re-opened at 1" "1" "$(_js_count "$JS_G")"

# 8h. D-FA4.3 / R-SR13: kind=restart is NEVER launcher-swept (daemon boot
#     sweep owns it) — stale flipped restart txn left untouched.
JS_H="$JS_TEST_DIR/h"; _js_fixture "$JS_H"; _js_seed "$JS_H" "$STALE_TS" true 1 "$STALE_TS" null restart
JS_H_BEFORE="$(_js_journal "$JS_H")"
_js_run "$JS_H"
assert_eq "8h restart-kind: rc 0" "0" "$?"
assert_eq "8h restart-kind journal untouched" "$JS_H_BEFORE" "$(_js_journal "$JS_H")"
assert_eq "8h restart-kind current untouched" "releases/v0.10.6" "$(readlink "$JS_H/current")"

# 8i. fail-closed edges: unparseable started_at + torn journal → untouched,
#     rc 0 (the sweep never fires on a txn it cannot age / cannot trust).
JS_I="$JS_TEST_DIR/i"; _js_fixture "$JS_I"
printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.10.6","started_at":"yesterday-ish","flipped":true,"owner_pid":1},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}\n' > "$JS_I/releases/state.json"
_js_run "$JS_I"; JS_I_RC=$?
assert_eq "8i unparseable started_at → rc 0, untouched" "0" "$JS_I_RC"
assert_contains "8i unparseable txn left in place" '"flipped":true' "$(_js_journal "$JS_I")"
JS_I2="$JS_TEST_DIR/i2"; _js_fixture "$JS_I2"
printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote' > "$JS_I2/releases/state.json"
JS_I2_RAW='{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote'
_js_run "$JS_I2"; JS_I2_RC=$?
assert_eq "8i torn (unbalanced) journal → rc 0" "0" "$JS_I2_RC"
assert_eq "8i torn journal byte-identical" "$JS_I2_RAW" "$(_js_journal "$JS_I2")"

# 8j. halt-for-human: stale flipped but previous release DIR missing → NO
#     flip, halt history event, txn left in place (T8 explicit check).
JS_J="$JS_TEST_DIR/j"; _js_fixture "$JS_J"; _js_seed "$JS_J" "$STALE_TS" true
rm -rf "$JS_J/releases/v0.10.5"
_js_run "$JS_J"
assert_eq "8j missing previous: rc 0 (boot proceeds)" "0" "$?"
assert_eq "8j current NOT repointed" "releases/v0.10.6" "$(readlink "$JS_J/current")"
assert_contains "8j halt event recorded" '"event":"halt"' "$(_js_journal "$JS_J")"
assert_contains "8j txn left in place for diagnosis" '"kind":"promote"' "$(_js_journal "$JS_J")"

# 8k. D5 lock defer: pipeline lock held with a FRESH heartbeat + live owner
#     → sweep defers (journal untouched, rc 0) — never blocks boot.
JS_K="$JS_TEST_DIR/k"; _js_fixture "$JS_K"; _js_seed "$JS_K" "$STALE_TS" true
mkdir -p "$JS_K/releases/rollback.lock.d"
printf '%s\n' "$$" > "$JS_K/releases/rollback.lock.d/owner"
printf '%s\n' "$(date +%s)" > "$JS_K/releases/rollback.lock.d/heartbeat"
JS_K_BEFORE="$(_js_journal "$JS_K")"
_js_run "$JS_K"
assert_eq "8k busy live lock → rc 0 (defer)" "0" "$?"
assert_eq "8k journal untouched while lock busy" "$JS_K_BEFORE" "$(_js_journal "$JS_K")"
assert_eq "8k current untouched while lock busy" "releases/v0.10.6" "$(readlink "$JS_K/current")"
assert_eq "8k foreign lock left in place" "present" "$([ -d "$JS_K/releases/rollback.lock.d" ] && echo present)"

# 8l. D5 stale-break: lock dir with DEAD owner + heartbeat >300s old →
#     sweep breaks it (mv to rollback.lock.stale.*) and proceeds.
JS_L="$JS_TEST_DIR/l"; _js_fixture "$JS_L"; _js_seed "$JS_L" "$STALE_TS" true
mkdir -p "$JS_L/releases/rollback.lock.d"
printf '%s\n' "999999" > "$JS_L/releases/rollback.lock.d/owner"
printf '%s\n' "$(( $(date +%s) - 400 ))" > "$JS_L/releases/rollback.lock.d/heartbeat"
_js_run "$JS_L"
assert_eq "8l stale lock broken + sweep proceeds: rc 0" "0" "$?"
assert_eq "8l current repointed after stale-break" "releases/v0.10.5" "$(readlink "$JS_L/current")"
ls "$JS_L/releases" | grep -q 'rollback\.lock\.d\.stale\.' && _pass \
    || _fail "8l stale lock moved to .stale.*" "present" "$(ls "$JS_L/releases" | tr '\n' ' ')"

# 8m. structural pin: the sweep runs BEFORE binary resolution in main() —
# the orphaned-flip recovery must not boot the broken release first.
MAIN_BODY="$(sed -n '/^main()/,/^}/p' "$LAUNCHER")"
SWEEP_LINE="$(printf '%s\n' "$MAIN_BODY" | grep -n '_journal_sweep' | head -1 | cut -d: -f1)"
BIN_LINE="$(printf '%s\n' "$MAIN_BODY" | grep -n 'resolve_binary' | head -1 | cut -d: -f1)"
if [ -n "$SWEEP_LINE" ] && [ -n "$BIN_LINE" ] && [ "$SWEEP_LINE" -lt "$BIN_LINE" ]; then
    _pass
else
    _fail "main(): _journal_sweep precedes resolve_binary" "sweep<binary" "sweep=$SWEEP_LINE binary=$BIN_LINE"
fi

# 8n. D5 protocol completeness (review m1): the launcher's lock acquire
#     writes the run_id file lib.sh writes — owner/run_id/heartbeat are
#     the protocol triple; a missing run_id degrades every lib.sh
#     pipeline-busy diagnostic to run_id=? and blanks status.sh's display.
JS_N="$JS_TEST_DIR/n"
mkdir -p "$JS_N/releases"
( . "$LAUNCHER"; INSTALL_DIR="$JS_N" _js_lock_acquire "$JS_N" >/dev/null 2>&1 )
assert_eq "8n _js_lock_acquire: rc 0" "0" "$?"
RUN_ID_FILE="$JS_N/releases/rollback.lock.d/run_id"
if [ -f "$RUN_ID_FILE" ]; then
    _pass
else
    _fail "8n lock dir has run_id file" "present" "absent"
fi
RUN_ID_VAL="$(cat "$RUN_ID_FILE" 2>/dev/null)"
case "$RUN_ID_VAL" in
    run-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9]-*) _pass ;;
    *) _fail "8n run_id matches run-YYYYmmdd-HHMMSS-pid shape" "run-…-…" "$RUN_ID_VAL" ;;
esac
( . "$LAUNCHER"; INSTALL_DIR="$JS_N" _js_lock_release "$JS_N" >/dev/null 2>&1 )
[ -d "$JS_N/releases/rollback.lock.d" ] && _fail "8n lock released after _js_lock_release" "absent" "present" || _pass

# 8o. B1 manifest gate: stale flipped + previous rollback_safe:FALSE →
#     halt-for-human — NO repoint (never boot the old binary against a
#     possibly migrated DB, R1.2), halt event citing the guard, txn left
#     in place, sweep-halt notify, boot proceeds (rc 0). Mirrors the halt
#     promote auto-rollback / rollback.sh / adopt_stale_txn enforce.
JS_O="$JS_TEST_DIR/o"; _js_fixture "$JS_O"; _js_seed "$JS_O" "$STALE_TS" true
printf '{"version":"v0.10.5","binary_version":"v0.10.5","rollback_safe":false}\n' \
    > "$JS_O/releases/v0.10.5/manifest.json"
JS_O_LOG="$( . "$LAUNCHER"; INSTALL_DIR="$JS_O" _journal_sweep 2>&1 >/dev/null )"; JS_O_RC=$?
assert_eq "8o unsafe previous: rc 0 (boot proceeds)" "0" "$JS_O_RC"
assert_eq "8o current NOT repointed onto unsafe previous" "releases/v0.10.6" "$(readlink "$JS_O/current")"
assert_contains "8o halt event recorded" '"event":"halt"' "$(_js_journal "$JS_O")"
assert_contains "8o halt cites the D-FA4.5 guard" 'rollback_safe=false' "$(_js_journal "$JS_O")"
assert_contains "8o txn left in place for diagnosis" '"kind":"promote"' "$(_js_journal "$JS_O")"
assert_contains "8o sweep-halt notified" 'NOTIFY[sweep-halt]' "$JS_O_LOG"
if printf '%s' "$(_js_journal "$JS_O")" | grep -q 'sweep_rollback'; then
    _fail "8o no sweep_rollback event (nothing rolled back)" "absent" "present"
else
    _pass
fi
assert_eq "8o rollback counter NOT incremented" "1" "$(_js_count "$JS_O")"

# 8p. B1 fail-closed: previous manifest MISSING → same halt shape.
JS_P="$JS_TEST_DIR/p"; _js_fixture "$JS_P"; _js_seed "$JS_P" "$STALE_TS" true
rm "$JS_P/releases/v0.10.5/manifest.json"
JS_P_LOG="$( . "$LAUNCHER"; INSTALL_DIR="$JS_P" _journal_sweep 2>&1 >/dev/null )"; JS_P_RC=$?
assert_eq "8p missing manifest: rc 0" "0" "$JS_P_RC"
assert_eq "8p current NOT repointed" "releases/v0.10.6" "$(readlink "$JS_P/current")"
assert_contains "8p halt event recorded" '"event":"halt"' "$(_js_journal "$JS_P")"
assert_contains "8p halt cites missing rollback_safe" 'rollback_safe=missing' "$(_js_journal "$JS_P")"
assert_contains "8p txn left in place" '"kind":"promote"' "$(_js_journal "$JS_P")"
assert_contains "8p sweep-halt notified" 'NOTIFY[sweep-halt]' "$JS_P_LOG"

# 8q. B1 fail-closed: previous manifest GARBAGE (unparseable) → same halt.
JS_Q="$JS_TEST_DIR/q"; _js_fixture "$JS_Q"; _js_seed "$JS_Q" "$STALE_TS" true
printf 'not json at all {{{ torn\n' > "$JS_Q/releases/v0.10.5/manifest.json"
JS_Q_LOG="$( . "$LAUNCHER"; INSTALL_DIR="$JS_Q" _journal_sweep 2>&1 >/dev/null )"; JS_Q_RC=$?
assert_eq "8q garbage manifest: rc 0" "0" "$JS_Q_RC"
assert_eq "8q current NOT repointed" "releases/v0.10.6" "$(readlink "$JS_Q/current")"
assert_contains "8q halt event recorded" '"event":"halt"' "$(_js_journal "$JS_Q")"
assert_contains "8q txn left in place" '"kind":"promote"' "$(_js_journal "$JS_Q")"
assert_contains "8q sweep-halt notified" 'NOTIFY[sweep-halt]' "$JS_Q_LOG"

# 8r. B3 kill-window: journal flipped:FALSE but current ALREADY -> the txn
#     target (promote died between atomic_flip and journal_mark_flipped,
#     promote.sh:180-181) → the sweep trusts the symlink over the marker,
#     treats the txn as flipped and runs the FULL rollback branch: repoint
#     to previous, sweep_rollback event, counter+cooldown, quarantine,
#     txn cleared. Never boots the ungated release.
JS_R="$JS_TEST_DIR/r"; _js_fixture "$JS_R"; _js_seed "$JS_R" "$STALE_TS" false
# fixture already leaves current -> releases/v0.10.6 (the orphaned flip)
_js_run "$JS_R"
assert_eq "8r kill-window heal: rc 0" "0" "$?"
assert_eq "8r current repointed to previous" "releases/v0.10.5" "$(readlink "$JS_R/current")"
assert_eq "8r journal current updated" "v0.10.5" "$(_js_field "$JS_R" current)"
assert_eq "8r in_flight cleared" "null" "$(_js_field "$JS_R" in_flight)"
assert_eq "8r rollback counter incremented (1→2)" "2" "$(_js_count "$JS_R")"
assert_contains "8r sweep_rollback history event recorded" '"event":"sweep_rollback"' "$(_js_journal "$JS_R")"
assert_contains "8r failed target quarantined" '"v0.10.6"' \
    "$( . "$LAUNCHER"; _js_json_sub "$(_js_journal "$JS_R")" quarantined 2>/dev/null)"
CD_R="$(_js_field "$JS_R" cooldown_until)"
case "$CD_R" in null|"") _fail "8r cooldown armed after kill-window heal" "ISO ts" "$CD_R" ;; *) _pass ;; esac
[ -d "$JS_R/releases/rollback.lock.d" ] && _fail "8r lock dir left behind" "absent" "present" || _pass

# 8s. B2(a) stale-snapshot guard — owner COMPLETES under the lock: the
#     sweep read the journal (stale flipped txn) BEFORE acquiring the lock;
#     if the live owner commits + clears its txn in that window, the sweep
#     must NOT act on the stale snapshot — no flip-back of the good current,
#     no phantom quarantine/counter/cooldown, lock released. Simulated by
#     overriding _js_lock_acquire in the test shell (post-source, subshell-
#     local): it performs the owner's completing mutations, then takes the
#     lock exactly as the real acquire would.
JS_S="$JS_TEST_DIR/s"; _js_fixture "$JS_S"; _js_seed "$JS_S" "$STALE_TS" true
(
    . "$LAUNCHER"
    _js_lock_acquire() {
        # the live owner finishes between the sweep's first read and this
        # acquire: commit v0.10.6 (symlink flip + journal) + clear txn
        ln -sfn "releases/v0.10.6" "$1/current"
        printf '{"current":"v0.10.6","previous":"v0.10.5","in_flight":null,"rollback_window_count":{"24h":1,"window_start":"%s"},"cooldown_until":null,"quarantined":[],"history":[]}\n' \
            "$STALE_TS" > "$1/releases/state.json"
        # then the acquire itself succeeds (mkdir IS the acquire, D5)
        mkdir "$1/releases/rollback.lock.d" 2>/dev/null
        printf '%s\n' "$$" > "$1/releases/rollback.lock.d/owner"
        printf '%s\n' "run-test-$$" > "$1/releases/rollback.lock.d/run_id"
        printf '%s\n' "$(date +%s)" > "$1/releases/rollback.lock.d/heartbeat"
        return 0
    }
    INSTALL_DIR="$JS_S" _journal_sweep >/dev/null 2>&1
)
assert_eq "8s owner-completed-under-lock: rc 0 (boot proceeds)" "0" "$?"
assert_eq "8s current NOT flipped back (the good commit stands)" "releases/v0.10.6" "$(readlink "$JS_S/current")"
assert_eq "8s journal current untouched by sweep" "v0.10.6" "$(_js_field "$JS_S" current)"
assert_eq "8s txn stays cleared (owner's commit intact)" "null" "$(_js_field "$JS_S" in_flight)"
if printf '%s' "$(_js_journal "$JS_S")" | grep -q '"event":"sweep'; then
    _fail "8s no sweep event on the stale snapshot" "absent" "present"
else
    _pass
fi
assert_eq "8s counter NOT incremented (no phantom count)" "1" "$(_js_count "$JS_S")"
assert_eq "8s cooldown NOT armed (no phantom cooldown)" "null" "$(_js_field "$JS_S" cooldown_until)"
assert_eq "8s committed version NOT quarantined" "[]" \
    "$( . "$LAUNCHER"; _js_json_sub "$(_js_journal "$JS_S")" quarantined 2>/dev/null)"
[ -d "$JS_S/releases/rollback.lock.d" ] && _fail "8s lock released after revalidation skip" "absent" "present" || _pass

# 8t. B2(a) variant — txn REPLACED under the lock: the snapshot's owner died
#     pre-flip, but before the sweep acts a NEW promote has already cleared
#     the corpse and opened its OWN fresh txn (different target/started_at).
#     Acting on the stale snapshot would clear the LIVE txn (killing the new
#     promote mid-flight). The sweep must compare and do nothing.
JS_T="$JS_TEST_DIR/t"; _js_fixture "$JS_T"; _js_seed "$JS_T" "$STALE_TS" false
ln -sfn "releases/v0.10.5" "$JS_T/current"   # true pre-flip: current on the serving release
(
    . "$LAUNCHER"
    _js_lock_acquire() {
        printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.11.0","started_at":"%s","flipped":false,"owner_pid":%s},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}\n' \
            "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$$" > "$1/releases/state.json"
        mkdir "$1/releases/rollback.lock.d" 2>/dev/null
        printf '%s\n' "$$" > "$1/releases/rollback.lock.d/owner"
        printf '%s\n' "run-test-$$" > "$1/releases/rollback.lock.d/run_id"
        printf '%s\n' "$(date +%s)" > "$1/releases/rollback.lock.d/heartbeat"
        return 0
    }
    INSTALL_DIR="$JS_T" _journal_sweep >/dev/null 2>&1
)
assert_eq "8t txn-replaced-under-lock: rc 0 (boot proceeds)" "0" "$?"
assert_contains "8t the LIVE replacement txn is NOT cleared" '"target":"v0.11.0"' "$(_js_journal "$JS_T")"
assert_eq "8t in_flight still an open txn (not null)" "promote" \
    "$( . "$LAUNCHER"; _js_json_field "$(_js_json_sub "$(_js_journal "$JS_T")" in_flight 2>/dev/null)" kind 2>/dev/null)"
if printf '%s' "$(_js_journal "$JS_T")" | grep -q '"event":"sweep'; then
    _fail "8t no sweep event on the stale snapshot" "absent" "present"
else
    _pass
fi
assert_eq "8t current untouched" "releases/v0.10.5" "$(readlink "$JS_T/current")"
[ -d "$JS_T/releases/rollback.lock.d" ] && _fail "8t lock released after revalidation skip" "absent" "present" || _pass

# 8u. W2 (B4×B3): same-version re-promote abort — journal current == txn
#     target, symlink == target, flipped:false, aged. The kill-window heal
#     must NOT fire: the flip never moved the symlink off the journaled
#     baseline (journal current == target proves no flip). The sweep clears
#     the pre-flip txn with NO rollback / quarantine / count / cooldown;
#     current stays on the healthy serving version. Pre-fix the heal fired
#     on cur_link == target alone and swept the healthy version back.
JS_U="$JS_TEST_DIR/u"; _js_fixture "$JS_U"
printf '{"current":"v0.10.6","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.10.6","started_at":"%s","flipped":false,"owner_pid":999999},"rollback_window_count":{"24h":1,"window_start":"%s"},"cooldown_until":null,"quarantined":[],"history":[]}\n' \
    "$STALE_TS" "$STALE_TS" > "$JS_U/releases/state.json"
_js_run "$JS_U"
assert_eq "8u same-version abort: rc 0 (boot proceeds)" "0" "$?"
assert_eq "8u txn cleared via the pre-flip branch (no heal)" "null" "$(_js_field "$JS_U" in_flight)"
assert_eq "8u current stays on the healthy serving version" "releases/v0.10.6" "$(readlink "$JS_U/current")"
assert_eq "8u journal current untouched" "v0.10.6" "$(_js_field "$JS_U" current)"
assert_contains "8u sweep (clear) event recorded" '"event":"sweep"' "$(_js_journal "$JS_U")"
if printf '%s' "$(_js_journal "$JS_U")" | grep -q 'sweep_rollback'; then
    _fail "8u no sweep_rollback (healthy version not rolled back)" "absent" "present"
else
    _pass
fi
assert_eq "8u rollback counter NOT incremented" "1" "$(_js_count "$JS_U")"
assert_eq "8u cooldown NOT armed" "null" "$(_js_field "$JS_U" cooldown_until)"
assert_eq "8u healthy version NOT quarantined" "[]" \
    "$( . "$LAUNCHER"; _js_json_sub "$(_js_journal "$JS_U")" quarantined 2>/dev/null)"

# 8u2. W2 control: the SAME shape but journal current ≠ target (the flip
#      DID move the symlink off the baseline) → the heal still fires (the
#      genuine kill-window case, pinned against the 8u over-correction).
JS_U2="$JS_TEST_DIR/u2"; _js_fixture "$JS_U2"
printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.10.6","started_at":"%s","flipped":false,"owner_pid":999999},"rollback_window_count":{"24h":1,"window_start":"%s"},"cooldown_until":null,"quarantined":[],"history":[]}\n' \
    "$STALE_TS" "$STALE_TS" > "$JS_U2/releases/state.json"
_js_run "$JS_U2"
assert_eq "8u2 kill-window control: rc 0" "0" "$?"
assert_eq "8u2 heal still fires when journal current ≠ target" "releases/v0.10.5" "$(readlink "$JS_U2/current")"
assert_contains "8u2 sweep_rollback event (heal ran the rollback branch)" '"event":"sweep_rollback"' "$(_js_journal "$JS_U2")"

# 8v. W1: stale lock heartbeat + LIVE owner (the un-heartbeated stop span)
#      → the sweep's lock acquire must NOT break the lock — defer instead
#      (journal untouched, rc 0, boot proceeds). Owner = a real live child.
JS_V="$JS_TEST_DIR/v"; _js_fixture "$JS_V"; _js_seed "$JS_V" "$STALE_TS" true
sleep 30 & JS_V_OWNER=$!
mkdir -p "$JS_V/releases/rollback.lock.d"
printf '%s\n' "$JS_V_OWNER" > "$JS_V/releases/rollback.lock.d/owner"
printf '%s\n' "run-w1-$JS_V_OWNER" > "$JS_V/releases/rollback.lock.d/run_id"
printf '%s\n' "$(( $(date +%s) - 400 ))" > "$JS_V/releases/rollback.lock.d/heartbeat"  # >300s stale, owner LIVE
JS_V_BEFORE="$(_js_journal "$JS_V")"
_js_run "$JS_V"
assert_eq "8v stale-hb + live owner: rc 0 (defer, boot proceeds)" "0" "$?"
assert_eq "8v journal untouched (sweep deferred)" "$JS_V_BEFORE" "$(_js_journal "$JS_V")"
assert_eq "8v current untouched (no rollback under a live owner)" "releases/v0.10.6" "$(readlink "$JS_V/current")"
assert_eq "8v lock NOT broken (live owner protected)" "present" "$([ -d "$JS_V/releases/rollback.lock.d" ] && echo present)"
if ls "$JS_V/releases" | grep -q 'rollback\.lock\.d\.stale\.'; then
    _fail "8v no stale-break artifacts while the owner lives" "absent" "$(ls "$JS_V/releases" | tr '\n' ' ')"
else
    _pass
fi
# liveness is what protected it: same lock, owner now DEAD → break + sweep proceeds
kill "$JS_V_OWNER" 2>/dev/null; wait "$JS_V_OWNER" 2>/dev/null
_js_run "$JS_V"
assert_eq "8v dead owner: stale lock broken, sweep proceeds" "0" "$?"
assert_eq "8v sweep rolled back after the owner died" "releases/v0.10.5" "$(readlink "$JS_V/current")"
ls "$JS_V/releases" | grep -q 'rollback\.lock\.d\.stale\.' && _pass \
    || _fail "8v dead-owner break moved the lock aside" "present" "$(ls "$JS_V/releases" | tr '\n' ' ')"

# 8w. W1 unit seam: _js_lock_acquire itself — stale heartbeat + LIVE owner
#     → rc 1 (busy at wait=0, never delays boot), lock intact; the lib.sh
#     mirror (lock_acquire) is proven in the release-journal pack (7c).
JS_W="$JS_TEST_DIR/w"; mkdir -p "$JS_W/releases"
sleep 30 & JS_W_OWNER=$!
mkdir -p "$JS_W/releases/rollback.lock.d"
printf '%s\n' "$JS_W_OWNER" > "$JS_W/releases/rollback.lock.d/owner"
printf '%s\n' "run-w1-$JS_W_OWNER" > "$JS_W/releases/rollback.lock.d/run_id"
printf '%s\n' "$(( $(date +%s) - 400 ))" > "$JS_W/releases/rollback.lock.d/heartbeat"
( . "$LAUNCHER"; _js_lock_acquire "$JS_W" 0 >/dev/null 2>&1 )
assert_eq "8w _js_lock_acquire: stale-hb + live owner → busy (1)" "1" "$?"
assert_eq "8w lock dir intact" "present" "$([ -d "$JS_W/releases/rollback.lock.d" ] && echo present)"
kill "$JS_W_OWNER" 2>/dev/null; wait "$JS_W_OWNER" 2>/dev/null
( . "$LAUNCHER"; _js_lock_acquire "$JS_W" 0 >/dev/null 2>&1 )
assert_eq "8w dead owner → acquire succeeds via stale-break (0)" "0" "$?"
( . "$LAUNCHER"; _js_lock_release "$JS_W" >/dev/null 2>&1 )


# ─── 9. resolve_binary preference order ─────────────────────────────────────
section "resolve_binary"
RB_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-rb.XXXXXX")"
mkdir -p "$RB_TEST_DIR/current"
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR/current/ensemble-prod"
chmod +x "$RB_TEST_DIR/current/ensemble-prod"
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR/ensemble-prod"
chmod +x "$RB_TEST_DIR/ensemble-prod"
RB_OUT="$( . "$LAUNCHER"; INSTALL_DIR="$RB_TEST_DIR"; resolve_binary )"
assert_eq "current/ beats flat layout" "$RB_TEST_DIR/current/ensemble-prod" "$RB_OUT"

RB_TEST_DIR2="$(mktemp -d "${TMPDIR:-/tmp}/launcher-rb2.XXXXXX")"
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR2/ensemble-prod"
chmod +x "$RB_TEST_DIR2/ensemble-prod"
RB_OUT2="$( . "$LAUNCHER"; INSTALL_DIR="$RB_TEST_DIR2"; resolve_binary )"
assert_eq "flat layout fallback" "$RB_TEST_DIR2/ensemble-prod" "$RB_OUT2"

RB_TEST_DIR3="$(mktemp -d "${TMPDIR:-/tmp}/launcher-rb3.XXXXXX")"
( . "$LAUNCHER"; INSTALL_DIR="$RB_TEST_DIR3"; resolve_binary >/dev/null 2>&1 )
assert_eq "no binary anywhere → nonzero (78 path)" "1" "$?"

# 9d. P5b dedupe regression lock (a24bf643): both candidates exist, both
# non-executable → exit 1 with exactly ONE WARN — the via_current one
# ("trying flat layout"); the guard [ ! -e via_current ] must suppress the
# second WARN naming the flat path (pre-fix this path warned twice).
RB_TEST_DIR4="$(mktemp -d "${TMPDIR:-/tmp}/launcher-rb4.XXXXXX")"
mkdir -p "$RB_TEST_DIR4/current"
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR4/current/ensemble-prod"   # non-exec
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR4/ensemble-prod"           # non-exec
(
    OUT="$( . "$LAUNCHER"; INSTALL_DIR="$RB_TEST_DIR4"; resolve_binary 2>&1 )"
    RC=$?
    [ "$RC" -eq 1 ] || exit 80
    [ "$(printf '%s\n' "$OUT" | grep -c 'WARN:')" -eq 1 ] || exit 81
    printf '%s\n' "$OUT" | grep -q 'trying flat layout' || exit 82
    exit 0
)
assert_eq "both non-exec → exit 1 + exactly one WARN (P5b dedupe)" "0" "$?"

# 9e. guarded WARN still fires when via_current is absent: flat-only,
# non-executable → exit 1 with exactly one WARN naming the flat path.
RB_TEST_DIR5="$(mktemp -d "${TMPDIR:-/tmp}/launcher-rb5.XXXXXX")"
printf '#!/bin/bash\nexit 0\n' > "$RB_TEST_DIR5/ensemble-prod"           # non-exec
(
    OUT="$( . "$LAUNCHER"; INSTALL_DIR="$RB_TEST_DIR5"; resolve_binary 2>&1 )"
    RC=$?
    [ "$RC" -eq 1 ] || exit 83
    [ "$(printf '%s\n' "$OUT" | grep -c 'WARN:')" -eq 1 ] || exit 84
    printf '%s\n' "$OUT" | grep -qF "$RB_TEST_DIR5/ensemble-prod exists but is not executable" || exit 85
    exit 0
)
assert_eq "flat-only non-exec → exit 1 + one WARN naming flat path" "0" "$?"

# ─── 10. notify stub: callable, returns 0 ───────────────────────────────────
section "notify stub"
N_OUT="$( . "$LAUNCHER"; _notify_once test-kind "message body" 2>&1 )"
assert_contains "_notify_once logs NOTIFY[...]" "NOTIFY[test-kind]" "$N_OUT"

# ─── 11. kill-0 EPERM twins: _pid_alive (P2.3 B4 leg 3 — B3.5 mirror) ───────
# launcher.sh:522/:536 (W1 lock-owner liveness) must treat kill -0 EPERM as
# ALIVE, matching B3.5's lib.sh _pid_alive. pid 1 (launchd, root-owned) is
# the clean EPERM source for an unprivileged runner: raw kill -0 fails with
# "Operation not permitted" while the process is plainly alive.
section "kill-0 EPERM semantics (_pid_alive launcher twin)"

# 11a. pid 1 is alive under BOTH outcomes: non-root → EPERM branch → ALIVE;
#      root → plain success → ALIVE. uid-independent contract.
( . "$LAUNCHER"; _pid_alive 1 ) >/dev/null 2>&1
assert_eq "11a pid 1 (root-owned): EPERM→ALIVE (non-root) / success→ALIVE (root)" "0" "$?"

# 11b. prove the EPERM string-branch fired when unprivileged: raw kill -0 1
#      fails "not permitted" AND the helper still returns 0. Under root the
#      raw call succeeds (EPERM not cleanly simulable on pid 1) — the
#      string-branch is then covered by the 11d twin-identity assertion.
RAW_K0_ERR="$(kill -0 1 2>&1)"; RAW_K0_RC=$?
case "$RAW_K0_ERR" in
    *"not permitted"*)
        assert_eq "11b raw kill -0 1 EPERM under unprivileged runner" "1" "$RAW_K0_RC"
        ( . "$LAUNCHER"; _pid_alive 1 ) >/dev/null 2>&1
        assert_eq "11b EPERM string-branch → ALIVE (rc 0)" "0" "$?"
        ;;
    *)
        if [ "$RAW_K0_RC" -eq 0 ] && [ "$(id -u)" -eq 0 ]; then
            _pass   # root: branch covered via 11d (identical body to lib.sh)
        else
            _fail "11b unexpected kill -0 1 state" "EPERM or root-success" "rc=$RAW_K0_RC err=$RAW_K0_ERR"
        fi
        ;;
esac

# 11c. dead pid: a reaped child reads dead (helper returns nonzero).
bash -c 'exit 0' & DEAD_PID=$!
wait "$DEAD_PID" 2>/dev/null
( . "$LAUNCHER"; _pid_alive "$DEAD_PID" ) >/dev/null 2>&1
if [ "$?" -ne 0 ]; then _pass; else _fail "11c reaped pid $DEAD_PID must read dead"; fi

# 11d. twin identity: the launcher-local _pid_alive body is byte-identical
#      to B3.5's scripts/upgrade/lib.sh _pid_alive body (launcher.sh never
#      sources lib.sh — the mirror IS the contract; shared-semantics proof
#      for environments where EPERM cannot be simulated).
TWIN_LAUNCHER="$(sed -n '/^_pid_alive() {/,/^}/p' "$LAUNCHER")"
TWIN_LIB="$(sed -n '/^_pid_alive() {/,/^}/p' "$REPO_ROOT/scripts/upgrade/lib.sh")"
if [ -n "$TWIN_LAUNCHER" ] && [ "$TWIN_LAUNCHER" = "$TWIN_LIB" ]; then
    _pass
else
    _fail "11d _pid_alive twin bodies must be byte-identical (launcher vs lib.sh)" \
        "identical bodies" "launcher=[${TWIN_LAUNCHER}] lib=[${TWIN_LIB}]"
fi

# ─── cleanup ────────────────────────────────────────────────────────────────
rm -rf "$ENV_TEST_DIR" "$STATE_TEST_DIR" "$JS_TEST_DIR" \
       "$RB_TEST_DIR" "$RB_TEST_DIR2" "$RB_TEST_DIR3" "$RB_TEST_DIR4" \
       "$RB_TEST_DIR5" 2>/dev/null

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'launcher tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
