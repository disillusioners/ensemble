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
# (classify_exit / next_backoff / budget_tick), the .env parser, and the
# state-file round-trip + corrupt tolerance. NO binaries are ever spawned;
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

# ─── 8. journal sweep stub: present journal → logs, returns 0 ──────────────
section "journal sweep stub"
JS_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-js.XXXXXX")"
mkdir -p "$JS_TEST_DIR/releases"
printf '{"txn": {"status": "in-flight"}}\n' > "$JS_TEST_DIR/releases/state.json"
JS_OUT="$( . "$LAUNCHER"; INSTALL_DIR="$JS_TEST_DIR"; _journal_sweep 2>&1 )"
assert_eq "journal sweep returns 0" "0" "$?"
assert_contains "journal sweep logs (stub)" "ADR-012 stub" "$JS_OUT"

JS_TEST_DIR2="$(mktemp -d "${TMPDIR:-/tmp}/launcher-js2.XXXXXX")"
( . "$LAUNCHER"; INSTALL_DIR="$JS_TEST_DIR2"; _journal_sweep >/dev/null 2>&1 )
assert_eq "journal sweep: no journal → silent success" "0" "$?"

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

# ─── 10. notify stub: callable, returns 0 ───────────────────────────────────
section "notify stub"
N_OUT="$( . "$LAUNCHER"; _notify_once test-kind "message body" 2>&1 )"
assert_contains "_notify_once logs NOTIFY[...]" "NOTIFY[test-kind]" "$N_OUT"

# ─── cleanup ────────────────────────────────────────────────────────────────
rm -rf "$ENV_TEST_DIR" "$STATE_TEST_DIR" "$JS_TEST_DIR" "$JS_TEST_DIR2" \
       "$RB_TEST_DIR" "$RB_TEST_DIR2" "$RB_TEST_DIR3" 2>/dev/null

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'launcher tests: %d passed, %d failed\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
