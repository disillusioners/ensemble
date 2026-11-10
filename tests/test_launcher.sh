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

# M4: shared helpers (assertions, M1 fixture registry, F1 fake-HOME fence)
. "$REPO_ROOT/tests/upgrade_test_lib.sh"
LAUNCHER="$REPO_ROOT/launcher.sh"

PASS=0
FAIL=0
SKIP=0
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

# _skip <name> — informational skip: counted, never fails the suite.
_skip() {
    SKIP=$((SKIP + 1))
    printf 'SKIP: %s\n' "$1" >&2
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

# 6e. F-06: a value with TRAILING WHITESPACE is trimmed (the launcher and
# the watchdog must resolve the SAME trimmed path).
(
    . "$LAUNCHER"
    printf 'TRAILING=/indented   \n' > "$ENV_TEST_DIR/env4"
    load_env_file "$ENV_TEST_DIR/env4"
    [ "${TRAILING:-}" = "/indented" ] || exit 33
    exit 0
)
assert_eq "6e env value: trailing whitespace trimmed" "0" "$?"

# 6f. F-06: an UNQUOTED inline comment suffix is stripped.
(
    . "$LAUNCHER"
    printf 'CMT=/path/to/state   # a trailing comment\n' > "$ENV_TEST_DIR/env5"
    load_env_file "$ENV_TEST_DIR/env5"
    [ "${CMT:-}" = "/path/to/state" ] || exit 34
    exit 0
)
assert_eq "6f env value: unquoted inline comment stripped" "0" "$?"

# 6g. F-06: a '#' NOT preceded by whitespace is PART of the value.
(
    . "$LAUNCHER"
    printf 'HASH=pa#ss\n' > "$ENV_TEST_DIR/env6"
    load_env_file "$ENV_TEST_DIR/env6"
    [ "${HASH:-}" = "pa#ss" ] || exit 35
    exit 0
)
assert_eq "6g env value: '#' without preceding whitespace kept" "0" "$?"

# 6h. F-06: a '#' INSIDE a quoted value is NOT a comment.
(
    . "$LAUNCHER"
    printf 'QUOTEDHASH="a # b"\n' > "$ENV_TEST_DIR/env7"
    load_env_file "$ENV_TEST_DIR/env7"
    [ "${QUOTEDHASH:-}" = "a # b" ] || exit 36
    exit 0
)
assert_eq "6h env value: '#' inside quotes kept" "0" "$?"

# 6i. FIX 7(b): a PATH= line in .env is IGNORED (PATH is supervisor-owned —
# the launchd anchor + the Layer-2 synthesis decide it) and the refusal is
# logged loudly, naming the key AND the file.
(
    . "$LAUNCHER"
    printf 'PATH=/bogus\nKEEPME=ok\n' > "$ENV_TEST_DIR/env8"
    load_env_file "$ENV_TEST_DIR/env8" 2>"$ENV_TEST_DIR/env8.err"
    [ "${PATH:-}" != "/bogus" ] || exit 37
    [ "${KEEPME:-}" = "ok" ] || exit 38
    grep -q 'PATH key in env file IGNORED' "$ENV_TEST_DIR/env8.err" || exit 39
    grep -q 'env8' "$ENV_TEST_DIR/env8.err" || exit 40
    exit 0
)
assert_eq "6i env PATH key: refused (not exported) + loud WARN" "0" "$?"

# 6j. F-07 / residual (e): a DUPLICATE key in .env resolves LAST-wins (the
# loader exports sequentially — the last export wins; the watchdog agrees).
(
    . "$LAUNCHER"
    printf 'DUPKEY=first\nDUPKEY=second\n' > "$ENV_TEST_DIR/env9"
    load_env_file "$ENV_TEST_DIR/env9"
    [ "${DUPKEY:-}" = "second" ] || exit 41
    exit 0
)
assert_eq "6j env duplicate key: LAST occurrence wins" "0" "$?"

# 6k. the VALUE-level trim: whitespace around the VALUE (e.g. a space
# right after '=') is trimmed — the line-level trim alone would not do it.
(
    . "$LAUNCHER"
    printf 'SPACED=   value-with-spaces   \n' > "$ENV_TEST_DIR/env10"
    load_env_file "$ENV_TEST_DIR/env10"
    [ "${SPACED:-}" = "value-with-spaces" ] || exit 42
    exit 0
)
assert_eq "6k env value: whitespace around the value trimmed" "0" "$?"

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

# _js_seed <dir> <started_iso> <flipped> [count] [window_start_iso] [cooldown_raw] [kind] [request]
#
# Phase 2 (promote-handback-halt, P-3's re-shape discipline): the optional
# `request` flag seeds the DAEMON'S rollback REQUEST (the T14 handoff
# event). Under the new disposition table the existing sweep-rollback
# chain is reached in three ways — a BROKEN observation, a
# `rollback_requested`, or the bounded absent-ceiling — so a pin that
# asserts the CHAIN's effects (the repoint, the quarantine, the counter,
# the cooldown, the halt gates) must drive one of them. The request is
# the deterministic one (a single sweep; the daemon's own verdict is the
# affirmative evidence). The pins that assert the DEFER shape drive the
# AMBIGUOUS class instead (no request ⇒ no chain).
#
# The request's run_id MUST match the txn identity the launcher derives
# ("<target>@<started_at>", or the bare target when started_at is empty —
# the SAME rule the launcher and the daemon use).
_js_seed() {
    local d="$1" started="$2" flipped="$3" cnt="${4:-1}" wstart="${5:-$2}" cd_raw="${6:-null}" kind="${7:-promote}" request="${8:-0}" owner_pid="${9:-999999}"
    local hist="[]" req_ident="v0.10.6"
    if [ -n "$started" ]; then
        req_ident="v0.10.6@$started"
    fi
    if [ "$request" = "1" ]; then
        hist="[{\"ts\":\"$started\",\"event\":\"rollback_requested\",\"detail\":\"target v0.10.6 run_id $req_ident reason not-ready\"}]"
    fi
    printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"%s","target":"v0.10.6","started_at":"%s","flipped":%s,"owner_pid":%s},"rollback_window_count":{"24h":%s,"window_start":"%s"},"cooldown_until":%s,"quarantined":[],"history":%s}\n' \
        "$kind" "$started" "$flipped" "$owner_pid" "$cnt" "$wstart" "$cd_raw" "$hist" > "$d/releases/state.json"
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

# Portable ISO stamp helper (GNU + BSD). The BSD-only `date -ju -v-700S`
# form silently produced an EMPTY string on GNU hosts (coreutils rejects
# `-j`/`-v`), so every fixture below was seeded with an EMPTY started_at —
# a shape the pins do not describe, which left 8c/8g RED on every Linux
# host (the pre-existing reds this lane re-shapes — phase3-plan.md
# §4.2.2 note under P3-PIN-01..04). The helper keeps the fixtures' INTENT
# (a 700s-stale txn, a 30s-fresh txn, a 25h-old window, a 300s-future
# cooldown) and makes them real on both dialects.
#
# ROUND-5 fix (launcher-journal-sweep-fix, D4): the BSD arm built
# `date -u -v"${off}"S` with the offset VERBATIM — and BSD date rejects an
# UNSIGNED positive adjustment (`-v300S`: "Cannot apply date adjustment")
# while accepting the signed form (`-v+300S`). The only positive-offset
# caller (`_ts_off 300`, the 8f cooldown seed) therefore returned EMPTY on
# every macOS/BSD host and 8f's "cooldown_until in the FUTURE" precondition
# silently degenerated to `cooldown_until:""` (the fixture was VACUOUS —
# gate item 8f / anomaly d4a3134c). The sign is now normalised explicitly
# for the BSD arm (negative offsets already carry their `-`); the GNU arm
# (`-d "@$((now+off))"`) is untouched.
#
# SELECTION IS OBSERVABLE (test-only): `_TS_OFF_UNAME` overrides the
# `uname -s` dispatch so the GNU arm can be pinned on a BSD host (the GNU
# arm CANNOT be exercised by macOS's real `date` — see the 8f-pre block
# below for the stubbed pin). Production behaviour is unchanged: the
# default is `uname -s`.
_ts_off() {
    local off="$1" now voff
    case "${_TS_OFF_UNAME:-$(uname -s)}" in
        Darwin|*BSD*|*bsd*)
            case "$off" in
                -*) voff="$off" ;;
                *)  voff="+$off" ;;
            esac
            date -u -v"${voff}"S +%Y-%m-%dT%H:%M:%SZ ;;
        *)
            now="$(date -u +%s)"
            date -u -d "@$(( now + off ))" +%Y-%m-%dT%H:%M:%SZ ;;
    esac
}

STALE_TS="$(_ts_off -700)"
FRESH_TS="$(_ts_off -30)"
OLD25H_TS="$(_ts_off -90000)"

# ─── 8f-pre. _ts_off portability: BOTH date dialects, NON-VACUOUS ──────────
# The 8f fixture's stated precondition is "cooldown_until in the FUTURE".
# Pre-round-5 that precondition was NOT established on macOS (the seed was
# the empty string), so the fixture degenerated toward plain 8a. These pins
# establish it on BOTH dialect families:
#   - the BSD arm runs against the host's REAL date here (macOS);
#   - the GNU arm CANNOT be exercised by macOS's `date` (`-d` is a GNU
#     flag), so it is pinned with a DETERMINISTIC FAKE `date` on PATH plus
#     the `_TS_OFF_UNAME=Linux` selection override — the stub's fixed
#     `now` (1000000000) makes the +300 arithmetic exactly assertable.
# Verified-on-this-host leg: BSD real. Verified-by-stub leg: GNU.
section "8f-pre _ts_off portability (BSD real date + GNU stubbed date)"
TS_OFF_PRE="$(date -u +%s)"
TS_BSD_NEG="$(_ts_off -700)"
case "$TS_BSD_NEG" in
    ""|*Cannot*) _fail "8f-pre BSD -700s offset non-empty (real BSD date)" "ISO ts" "$TS_BSD_NEG" ;;
    *) _pass ;;
esac
TS_OFF_MID="$(date -u +%s)"
TS_BSD_POS="$(_ts_off 300)"
case "$TS_BSD_POS" in
    ""|*Cannot*) _fail "8f-pre BSD +300s offset non-empty (real BSD date — the D4 defect)" "ISO ts" "$TS_BSD_POS" ;;
    *) _pass ;;
esac
# the +300s stamp must BE ≈300 s in the future (± 20 s tolerance for the
# two `date -u +%s` probes that bracket the call)
if TS_OFF_EPOCH="$(_js_iso_to_epoch "$TS_BSD_POS" 2>/dev/null)" && [ -n "$TS_OFF_EPOCH" ] \
   && [ "$TS_OFF_EPOCH" -ge $(( TS_OFF_PRE + 280 )) ] \
   && [ "$TS_OFF_EPOCH" -le $(( TS_OFF_MID + 320 )) ]; then
    _pass
else
    _fail "8f-pre BSD +300s stamp is ≈300s in the future (non-vacuous 8f seed)" \
        "now+300 ±20s" "epoch=${TS_OFF_EPOCH:-EMPTY} (bounds ${TS_OFF_PRE}+280..${TS_OFF_MID}+320)"
fi
# the negative arm must be ≈700 s in the PAST (the same tolerance)
if TS_NEG_EPOCH="$(_js_iso_to_epoch "$TS_BSD_NEG" 2>/dev/null)" && [ -n "$TS_NEG_EPOCH" ] \
   && [ "$TS_NEG_EPOCH" -le $(( TS_OFF_PRE - 680 )) ] \
   && [ "$TS_NEG_EPOCH" -ge $(( TS_OFF_MID - 720 )) ]; then
    _pass
else
    _fail "8f-pre BSD -700s stamp is ≈700s in the past" \
        "now-700 ±20s" "epoch=${TS_NEG_EPOCH:-EMPTY}"
fi

# GNU arm via a deterministic stub (the stub OWNS `now`: 1000000000, so
# `now + 300` is exactly 1000000300 and the assertion is exact, not
# approximate). The stub forwards anything it does not own to the real
# date so the helper's GNU arm is the only thing under test.
TS_GNU_STUB="$JS_TEST_DIR/gnudate-stub"
mkdir -p "$TS_GNU_STUB"
printf '#!/bin/sh\n# GNU date STUB (tests only): accepts the two forms _ts_off uses.\nif [ "$1" = "-u" ] && [ "$2" = "+%%s" ]; then echo 1000000000; exit 0; fi\nif [ "$1" = "-u" ] && [ "$2" = "-d" ]; then\n    printf '"'"'GNUSTUB-%%s\\n'"'"' "${3#@}"; exit 0\nfi\nexec /bin/date "$@"\n' > "$TS_GNU_STUB/date"
chmod +x "$TS_GNU_STUB/date"
TS_GNU_POS="$(_TS_OFF_UNAME=Linux PATH="$TS_GNU_STUB:$PATH" _ts_off 300)"
case "$TS_GNU_POS" in
    GNUSTUB-1000000300) _pass ;;
    "")
        _fail "8f-pre GNU +300s arm selected + arithmetic exact (stubbed date)" \
            "GNUSTUB-1000000300" "empty (the GNU arm was not selected or the stub was bypassed)" ;;
    *)
        _fail "8f-pre GNU +300s arm selected + arithmetic exact (stubbed date)" \
            "GNUSTUB-1000000300" "$TS_GNU_POS" ;;
esac
TS_GNU_NEG="$(_TS_OFF_UNAME=Linux PATH="$TS_GNU_STUB:$PATH" _ts_off -700)"
case "$TS_GNU_NEG" in
    GNUSTUB-999999300) _pass ;;
    *) _fail "8f-pre GNU -700s arm selected + arithmetic exact (stubbed date)" \
        "GNUSTUB-999999300" "${TS_GNU_NEG:-empty}" ;;
esac


# 8a. stale + flipped:true → sweep-rollback: current repointed to previous,
#     sweep_rollback history event, counter incremented, cooldown armed,
#     target quarantined, in_flight cleared (ADR-012/024).
JS_A="$JS_TEST_DIR/a"; _js_fixture "$JS_A"; _js_seed "$JS_A" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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
# [Phase 3 re-shape, promote-handback-halt: the fixture's BSD-only
# `date -ju -v-30S` produced an EMPTY started_at on GNU hosts — the fixture
# was vacuous and the pin RED on every Linux host. The timestamps are now
# REAL (the portable _ts_off helper above). Two shapes are pinned:
#   (i)  fresh ∧ owner ALIVE — the pin's stated intent ("the owner may be
#        alive"): COMPLETELY untouched, byte-identical journal;
#   (ii) fresh ∧ owner DEAD + no heartbeat (8c′) — the Phase-2 contract
#        (AM-P2-18 owner-liveness conjunct): the txn DEFERS (a journaled
#        HOLD) — the sweep never disposes of it (no rollback, no
#        quarantine, no counter, no cooldown; in_flight stays open).]
JS_C="$JS_TEST_DIR/c"; _js_fixture "$JS_C"; _js_seed "$JS_C" "$FRESH_TS" true 1 "$FRESH_TS" null promote 0 "$$"
JS_C_BEFORE="$(_js_journal "$JS_C")"
_js_run "$JS_C"
assert_eq "8c fresh txn (owner alive): rc 0" "0" "$?"
assert_eq "8c journal byte-identical (untouched)" "$JS_C_BEFORE" "$(_js_journal "$JS_C")"
assert_eq "8c current symlink untouched" "releases/v0.10.6" "$(readlink "$JS_C/current")"

# 8c′. fresh ∧ owner DEAD (no heartbeat) → DEFER, never dispose.
JS_C2="$JS_TEST_DIR/c2"; _js_fixture "$JS_C2"; _js_seed "$JS_C2" "$FRESH_TS" true 1 "$FRESH_TS" null promote 0 999999
_js_run "$JS_C2"
assert_eq "8c′ fresh txn (owner dead): rc 0" "0" "$?"
assert_contains "8c′ the defer is JOURNALED (sweep_deferred HOLD)" '"event":"sweep_deferred"' "$(_js_journal "$JS_C2")"
assert_contains "8c′ the txn stays OPEN (in_flight not null)" '"owner_pid":999999' "$(_js_journal "$JS_C2")"
assert_eq "8c′ current symlink untouched (no rollback)" "releases/v0.10.6" "$(readlink "$JS_C2/current")"
assert_eq "8c′ rollback counter NOT incremented" "1" "$(_js_count "$JS_C2")"
assert_eq "8c′ cooldown NOT armed" "null" "$(_js_field "$JS_C2" cooldown_until)"

# 8d. no journal → silent no-op (pre-existing behavior preserved).
JS_D="$JS_TEST_DIR/d"; mkdir -p "$JS_D/releases"
( . "$LAUNCHER"; INSTALL_DIR="$JS_D" _journal_sweep >/dev/null 2>&1 )
assert_eq "8d no journal → silent success" "0" "$?"

# 8e. D-FA4.2: the sweep NEVER refuses on cap — count already AT cap (3)
#     + stale flipped → rollback STILL executes, count → 4, halt event armed.
JS_E="$JS_TEST_DIR/e"; _js_fixture "$JS_E"; _js_seed "$JS_E" "$STALE_TS" true 3 "$STALE_TS" null promote 1
_js_run "$JS_E"
assert_eq "8e at-cap sweep-rollback still executes: rc 0" "0" "$?"
assert_eq "8e current repointed past cap" "releases/v0.10.5" "$(readlink "$JS_E/current")"
assert_eq "8e count increments past cap (entry-side ≥3 check)" "4" "$(_js_count "$JS_E")"
assert_contains "8e halt event armed at cap" '"event":"halt"' "$(_js_journal "$JS_E")"
assert_eq "8e in_flight cleared (recovery completed)" "null" "$(_js_field "$JS_E" in_flight)"

# 8f. D-FA4.2: active cooldown NEVER refuses the sweep — cooldown_until in
#     the FUTURE + stale flipped → rollback still executes + cooldown re-armed.
#     (Round-5 D4: the fixture's PRECONDITION is now established AND PINNED.
#     Pre-fix the BSD arm built `date -v300S` — illegal on BSD — and seeded
#     an EMPTY cooldown_until, so on every macOS host this fixture silently
#     degenerated toward plain 8a (no future cooldown was ever in play). The
#     two pins below are the non-vacuity gate: the seed must be a NON-EMPTY
#     stamp ≈300 s in the future BEFORE the sweep runs.)
JS_F="$JS_TEST_DIR/f"; _js_fixture "$JS_F"
JS_F_SEED_PRE="$(date -u +%s)"
JS_F_SEED="$(_ts_off 300)"
case "$JS_F_SEED" in
    ""|*Cannot*) _fail "8f seeded cooldown_until non-empty (precondition established on THIS host)" "ISO ts ≈ now+300s" "$JS_F_SEED" ;;
    *) _pass ;;
esac
if JS_F_SEED_EPOCH="$(_js_iso_to_epoch "$JS_F_SEED" 2>/dev/null)" && [ -n "$JS_F_SEED_EPOCH" ] \
   && [ "$JS_F_SEED_EPOCH" -ge $(( JS_F_SEED_PRE + 280 )) ] \
   && [ "$JS_F_SEED_EPOCH" -le $(( $(date -u +%s) + 320 )) ]; then
    _pass
else
    _fail "8f seeded cooldown_until ≈300s in the future (non-vacuous on THIS host)" \
        "now+300 ±20s" "epoch=${JS_F_SEED_EPOCH:-EMPTY}"
fi
_js_seed "$JS_F" "$STALE_TS" true 1 "$STALE_TS" "\"$JS_F_SEED\"" promote 1
_js_run "$JS_F"
assert_eq "8f cooldown-active sweep-rollback still executes: rc 0" "0" "$?"
assert_eq "8f current repointed despite cooldown" "releases/v0.10.5" "$(readlink "$JS_F/current")"
assert_eq "8f in_flight cleared" "null" "$(_js_field "$JS_F" in_flight)"

# 8g. ADR-005 window rollover (R1.4): window_start >24h old → count resets,
#     this sweep-rollback re-opens the window at 1.
JS_G="$JS_TEST_DIR/g"; _js_fixture "$JS_G"; _js_seed "$JS_G" "$STALE_TS" true 2 "$OLD25H_TS" null promote 1
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
JS_J="$JS_TEST_DIR/j"; _js_fixture "$JS_J"; _js_seed "$JS_J" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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
JS_L="$JS_TEST_DIR/l"; _js_fixture "$JS_L"; _js_seed "$JS_L" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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
JS_O="$JS_TEST_DIR/o"; _js_fixture "$JS_O"; _js_seed "$JS_O" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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
JS_P="$JS_TEST_DIR/p"; _js_fixture "$JS_P"; _js_seed "$JS_P" "$STALE_TS" true 1 "$STALE_TS" null promote 1
rm "$JS_P/releases/v0.10.5/manifest.json"
JS_P_LOG="$( . "$LAUNCHER"; INSTALL_DIR="$JS_P" _journal_sweep 2>&1 >/dev/null )"; JS_P_RC=$?
assert_eq "8p missing manifest: rc 0" "0" "$JS_P_RC"
assert_eq "8p current NOT repointed" "releases/v0.10.6" "$(readlink "$JS_P/current")"
assert_contains "8p halt event recorded" '"event":"halt"' "$(_js_journal "$JS_P")"
assert_contains "8p halt cites missing rollback_safe" 'rollback_safe=missing' "$(_js_journal "$JS_P")"
assert_contains "8p txn left in place" '"kind":"promote"' "$(_js_journal "$JS_P")"
assert_contains "8p sweep-halt notified" 'NOTIFY[sweep-halt]' "$JS_P_LOG"

# 8q. B1 fail-closed: previous manifest GARBAGE (unparseable) → same halt.
JS_Q="$JS_TEST_DIR/q"; _js_fixture "$JS_Q"; _js_seed "$JS_Q" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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
JS_R="$JS_TEST_DIR/r"; _js_fixture "$JS_R"; _js_seed "$JS_R" "$STALE_TS" false 1 "$STALE_TS" null promote 1
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
JS_U2_IDENT="v0.10.6"
[ -n "$STALE_TS" ] && JS_U2_IDENT="v0.10.6@$STALE_TS"
printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.10.6","started_at":"%s","flipped":false,"owner_pid":999999},"rollback_window_count":{"24h":1,"window_start":"%s"},"cooldown_until":null,"quarantined":[],"history":[{"ts":"%s","event":"rollback_requested","detail":"target v0.10.6 run_id %s reason not-ready"}]}\n' \
    "$STALE_TS" "$STALE_TS" "$STALE_TS" "$JS_U2_IDENT" > "$JS_U2/releases/state.json"
_js_run "$JS_U2"
assert_eq "8u2 kill-window control: rc 0" "0" "$?"
assert_eq "8u2 heal still fires when journal current ≠ target" "releases/v0.10.5" "$(readlink "$JS_U2/current")"
assert_contains "8u2 sweep_rollback event (heal ran the rollback branch)" '"event":"sweep_rollback"' "$(_js_journal "$JS_U2")"

# 8v. W1: stale lock heartbeat + LIVE owner (the un-heartbeated stop span)
#      → the sweep's lock acquire must NOT break the lock — defer instead
#      (journal untouched, rc 0, boot proceeds). Owner = a real live child.
JS_V="$JS_TEST_DIR/v"; _js_fixture "$JS_V"; _js_seed "$JS_V" "$STALE_TS" true 1 "$STALE_TS" null promote 1
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


# ─── 8b. Sweep-hang timeouts (fix #4, v0.16.9) ─────────────────────────────
# Two arms: (1) a regression pin that _journal_sweep at the current
# branch DOES NOT use unbounded pipe reads or `wait` against subprocesses
# — the brief premise was a "sweep pipe_read wedge" but HEAD code uses
# pure bash JSON parsing; the repo now carries the O(n) journal-sweep
# rewrite (_js_journal_read / _js_json_sub) while the LIVE install still
# carries the PRE-rewrite launcher until the next promote, so the two
# bodies legitimately differ right now. (2) correctness tests
# for the bounded-run helper _js_run_bounded and the bounded pid-alive
# wrapper _js_pid_alive_bounded (genuine D-state kill-0 / wedged-cmd
# wedge defense; the kill -0 call IS a real blocking risk at HEAD).
section "sweep-hang timeouts"

# 8b-r1: _journal_sweep SOURCE PIN — no pipe reads / no `wait` against
# subprocesses in the sweep body. The brief's "sweep pipe_read wedge"
# shape doesn't exist at HEAD; this regression pin ensures any future
# PR that re-introduces it gets caught. Extracted via line-range grep
# (the function body is _journal_sweep() until the next top-level fn).
SWEEP_BODY="$(awk '
    /^_journal_sweep\(\)/ { in_fn=1; next }
    in_fn && /^}/ { in_fn=0; next }
    in_fn { print }
' "$LAUNCHER")"
# Strip bash comments + blank lines so a comment that says "wait" or
# "while read" in a docstring does not trip the pin (the comments ARE
# in the source — pure bash with no parser-level meaning).
SWEEP_CODE="$(printf '%s\n' "$SWEEP_BODY" | sed -e 's/#.*$//' -e '/^[[:space:]]*$/d')"
if printf '%s' "$SWEEP_CODE" | grep -qE 'wait[[:space:]]+"?\$[A-Za-z_][A-Za-z_0-9]*"?( |$)'; then
    _fail "8b-r1 _journal_sweep has no `wait` against subprocess pids" "absent" "$(printf '%s' "$SWEEP_CODE" | grep -nE 'wait' | head -3)"
else
    _pass "8b-r1 _journal_sweep has no `wait` against subprocess pids (regression pin)"
fi
# `pipe()` bash builtin / redirection to/from a pipe is the OTHER way the
# sweep could wedge (the brief's specific case). Pin: no `| while` or
# `while read` (line-buffered pipe loop) inside the sweep body.
if printf '%s' "$SWEEP_CODE" | grep -qE '\|[[:space:]]*(while|read)|while[[:space:]]+read'; then
    _fail "8b-r1 _journal_sweep has no pipe reads" "absent" "$(printf '%s' "$SWEEP_CODE" | grep -nE 'while read|\\| while' | head -3)"
else
    _pass "8b-r1 _journal_sweep has no pipe reads (regression pin)"
fi

# 8b-r2: OPT-IN live-install comparison (informational). The probe path is
# OPT-IN via ENS_TEST_LIVE_LAUNCHER — there is NO default live-install
# path and NO default stat probe (zero-live-contact bar for agent-driven
# test runs). The repo now carries the O(n) journal-sweep rewrite
# (_js_journal_read / _js_json_sub); the LIVE install still carries the
# PRE-rewrite launcher until the next promote, so the two bodies
# legitimately differ right now. This row is NOT a convergence proof and
# does NOT prove the repo and live copies are equivalent: the operator's
# real convergence check is `cmp` against the LIVE launcher.sh, NOT this
# suite row. Operators who want the live-copy comparison run:
#   ENS_TEST_LIVE_LAUNCHER=/path/to/live/install/launcher.sh bash tests/test_launcher.sh
LIVE_LAUNCHER="${ENS_TEST_LIVE_LAUNCHER:-}"
if [ -n "$LIVE_LAUNCHER" ] && [ -f "$LIVE_LAUNCHER" ]; then
    # Compare the _journal_sweep function body between repo and live.
    LIVE_SWEEP_BODY="$(awk '
        /^_journal_sweep\(\)/ { in_fn=1; next }
        in_fn && /^}/ { in_fn=0; next }
        in_fn { print }
    ' "$LIVE_LAUNCHER")"
    if [ "$SWEEP_BODY" = "$LIVE_SWEEP_BODY" ]; then
        _pass
        printf 'INFO: 8b-r2 repo _journal_sweep body == live-install copy (opt-in comparison only; NOT a convergence proof)\n'
    else
        # informational pass — the live install is pre-rewrite; this row is NOT a convergence proof
        printf 'INFO: 8b-r2 live-install _journal_sweep \u2260 repo HEAD (expected: repo carries the O(n) rewrite, live is pre-rewrite until next promote)\n'
        LIVE_KILLS=$(printf '%s' "$LIVE_SWEEP_BODY" | sed -e 's/#.*$//' | grep -c '_js_pid_alive_bounded\|_js_run_bounded' || true)
        if [ "$LIVE_KILLS" -gt 0 ]; then
            _pass
            printf 'INFO: 8b-r2 live-install _journal_sweep body still has the bounded-subprocess pass (pre-rewrite; informational)\n'
        else
            _pass
            printf 'INFO: 8b-r2 live-install _journal_sweep body pre-rewrite (informational; real convergence check is cmp against the LIVE launcher.sh)\n'
        fi
    fi
else
    _skip "8b-r2 opt-in live-install comparison — no live-install path configured (set ENS_TEST_LIVE_LAUNCHER to opt in); this row is NOT a convergence proof"
fi

# 8b-c1: _js_run_bounded — fast command returns the inner rc (0 / nonzero)
( . "$LAUNCHER" >/dev/null 2>&1 )
assert_eq "8b-c1 fast command: rc 0" "0" "$( . "$LAUNCHER" >/dev/null 2>&1; _js_run_bounded 3 -- true; echo $? )"
assert_eq "8b-c1 nonzero command: rc preserved (7)" "7" "$( . "$LAUNCHER" >/dev/null 2>&1; _js_run_bounded 3 -- sh -c 'exit 7'; echo $? )"

# 8b-c2: _js_run_bounded — slow command gets SIGKILLed, returns 124
TIMEOUT_OUT="$( . "$LAUNCHER" >/dev/null 2>&1
    T0=$SECONDS
    _js_run_bounded 1 -- sleep 5
    rc=$?
    echo "rc=$rc elapsed=$((SECONDS - T0))" )"
case "$TIMEOUT_OUT" in
    "rc=124 elapsed=1") _pass "8b-c2 slow command: rc 124, SIGKILLed within deadline" ;;
    *) _fail "8b-c2 slow command: rc 124, SIGKILLed within deadline" "rc=124 elapsed=1" "$TIMEOUT_OUT" ;;
esac

# 8b-c3: _js_pid_alive_bounded — own pid alive (rc 0); bogus pid dead (rc 1)
assert_eq "8b-c3 own pid: alive (rc 0)" "0" "$( . "$LAUNCHER" >/dev/null 2>&1; _js_pid_alive_bounded $$; echo $? )"
assert_eq "8b-c3 bogus pid: dead (rc 1)" "1" "$( . "$LAUNCHER" >/dev/null 2>&1; _js_pid_alive_bounded 999999; echo $? )"

# 8b-c4: _js_pid_alive_bounded — on wedge, conservative-ALIVE (rc 0).
# Simulate the wedge by mocking _js_run_bounded to immediately return 124
# (the SIGKILL-by-watchdog code); the wrapper must downgrade to ALIVE.
MOCK_WEDGE_OUT="$( . "$LAUNCHER" >/dev/null 2>&1
    eval '_js_run_bounded() { return 124; }'
    _js_pid_alive_bounded $$ >/dev/null 2>&1
    echo "wedge-rc=$?" )"
case "$MOCK_WEDGE_OUT" in
    "wedge-rc=0") _pass "8b-c4 wedge → conservative-ALIVE (lock NEVER broken on wedge)" ;;
    *) _fail "8b-c4 wedge → conservative-ALIVE (lock NEVER broken on wedge)" "wedge-rc=0" "$MOCK_WEDGE_OUT" ;;
esac

# 8b-c5: sweep integration — the bounds survive a real sweep call.
# Fixture: stale + flipped:true → sweep-rollback (which calls the
# bounded cat on the journal + bounded date -p on started_at + bounded
# kill -0 on the (deceased) owner pid). Each bounded call must NOT
# wedge the sweep; the sweep must complete with rc 0.
JS_F4="$JS_TEST_DIR/f4"; _js_fixture "$JS_F4"; _js_seed "$JS_F4" "$STALE_TS" true 1 "$STALE_TS" null promote 1
( . "$LAUNCHER"; INSTALL_DIR="$JS_F4" _journal_sweep >/dev/null 2>&1 )
F4_RC=$?
assert_eq "8b-c5 sweep survives bounded subprocess calls" "0" "$F4_RC"
assert_eq "8b-c5 sweep-rollback repointed (bounded cat + date + kill -0)" "releases/v0.10.5" "$(readlink "$JS_F4/current")"

# 8b-c6: _js_run_bounded — instant commands do not stall (regression pin).
# Bug shape (commit 961dfe59): the watcher `( sleep N; kill -KILL $pid ) &`
# inherits the command-substitution pipe's write-end. When the watched child
# wins, we SIGKILL the watcher subshell — but its `sleep N` child is
# orphaned to init AND still holds the pipe write-end until sleep expires.
# `$( _js_run_bounded ... )` therefore blocks ~N s per call even on instant
# commands. Measured: launcher suite 10s at base → 652s at HEAD (65×). Pin:
# 5 instant `_js_run_bounded true` calls (timeout 3s) must complete in ≤5s.
# Buggy path: 5 × ~3s = ~15s, comfortably above the 5s ceiling; fixed path:
# sub-second on any sane host. The launcher suite's own wall-clock (10s
# vs 652s) is the suite-level discriminator; this cell is the per-function
# pin.
INSTANT_OUT="$( . "$LAUNCHER" >/dev/null 2>&1
    T0=$(date +%s)
    for _ in 1 2 3 4 5; do
        _js_run_bounded 3 -- true >/dev/null
    done
    echo "elapsed=$(( $(date +%s) - T0 ))" )"
INSTANT_ELAPSED="${INSTANT_OUT#elapsed=}"
case "$INSTANT_ELAPSED" in
    ''|*[!0-9]*)
        _fail "8b-c6 5 instant commands complete in ≤5s (no orphan-fd stall)" "<=5" "non-numeric elapsed: $INSTANT_OUT" ;;
    *)
        if [ "$INSTANT_ELAPSED" -le 5 ]; then
            _pass "8b-c6 5 instant commands complete in ${INSTANT_ELAPSED}s ≤5s (no orphan-fd stall)"
        else
            _fail "8b-c6 5 instant commands complete in ≤5s (no orphan-fd stall)" "<=5" "elapsed=${INSTANT_ELAPSED}s — orphan-sleep holds capture pipe (commit 961dfe59 regression)"
        fi ;;
esac


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
# launcher.sh:523/:537 (W1 lock-owner liveness) must treat kill -0 EPERM as
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

# ─── 12. allocator cap: MALLOC_ARENA_MAX default-export before exec ────────
# (resource-cost-control O1) The launcher must default glibc to 2 malloc
# arenas so libc init — which parses the env BEFORE the child's main() —
# caps the daemon's arena count. Default-only semantics (a MALLOC_ARENA_MAX
# staged in .env wins: load_env_file exports it before this default fires),
# and the export must sit BEFORE main() hands off to run_loop (the child
# inherits the launcher's exported env on every cycle).
section "12. allocator cap (O1): export before exec"
# Presence pins.
assert_contains "launcher defaults MALLOC_ARENA_MAX to 2" \
    ': "${MALLOC_ARENA_MAX:=2}"' "$(cat "$LAUNCHER")"
assert_contains "launcher exports MALLOC_ARENA_MAX" \
    "export MALLOC_ARENA_MAX" "$(cat "$LAUNCHER")"
# Ordering pin: export line number < run_loop invocation line number.
export_line="$(grep -n '^[[:space:]]*export MALLOC_ARENA_MAX[[:space:]]*$' "$LAUNCHER" | head -1 | cut -d: -f1)"
exec_line="$(grep -n '^[[:space:]]*run_loop "\$bin"[[:space:]]*$' "$LAUNCHER" | head -1 | cut -d: -f1)"
if [ -n "$export_line" ] && [ -n "$exec_line" ] && [ "$export_line" -lt "$exec_line" ]; then
    _pass
else
    _fail "12. export MALLOC_ARENA_MAX precedes run_loop exec" \
        "export line < run_loop line" \
        "export=${export_line:-absent} run_loop=${exec_line:-absent}"
fi
# Default-only semantics: the := default must not clobber a staged value —
# load_env_file exports staged keys BEFORE the default fires, so assert the
# default line also sits AFTER the load_env_file call in main().
loadenv_line="$(grep -n 'load_env_file "\$INSTALL_DIR/.env"' "$LAUNCHER" | head -1 | cut -d: -f1)"
if [ -n "$loadenv_line" ] && [ -n "$export_line" ] && [ "$loadenv_line" -lt "$export_line" ]; then
    _pass
else
    _fail "12. staged .env value wins (load_env_file before default export)" \
        "load_env_file line < export line" \
        "load_env_file=${loadenv_line:-absent} export=${export_line:-absent}"
fi

# ─── 13. Supervisor-env PATH strategy (design §iv Layer 2) ─────────────────
section "supervisor PATH strategy (§iv Layer 2)"
LP_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-pathtest.XXXXXX")"
# Fake-HOME fence (hermetic): a fake nvm tree + tool stubs. NO real
# ~/.nvm reads anywhere below — HOME is always overridden inside the
# subshells that exercise nvm resolution.
mkdir -p "$LP_TEST_DIR/home/.nvm/alias" \
         "$LP_TEST_DIR/home/.local/bin" \
         "$LP_TEST_DIR/home/.nvm/versions/node/v22.9.1/bin" \
         "$LP_TEST_DIR/home/.nvm/versions/node/v22.21.1/bin" \
         "$LP_TEST_DIR/home/.nvm/versions/node/v22.29.0/bin" \
         "$LP_TEST_DIR/home2/.nvm/alias" \
         "$LP_TEST_DIR/home3/.nvm/alias" \
         "$LP_TEST_DIR/emptyhome" \
         "$LP_TEST_DIR/stubs"
printf '22\n' > "$LP_TEST_DIR/home/.nvm/alias/default"
for v in 22.9.1 22.21.1; do
    printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/home/.nvm/versions/node/v$v/bin/node"
    chmod +x "$LP_TEST_DIR/home/.nvm/versions/node/v$v/bin/node"
    # real nvm version bins ship npx next to node
    printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/home/.nvm/versions/node/v$v/bin/npx"
    chmod +x "$LP_TEST_DIR/home/.nvm/versions/node/v$v/bin/npx"
done
# v22.29.0 exists but its bin/node is NOT executable — resolution must
# verify the binary, not just the directory.
printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/home/.nvm/versions/node/v22.29.0/bin/node"
chmod 644 "$LP_TEST_DIR/home/.nvm/versions/node/v22.29.0/bin/node"
printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/home/.local/bin/uvx"
chmod +x "$LP_TEST_DIR/home/.local/bin/uvx"
printf '22.21.1\n' > "$LP_TEST_DIR/home2/.nvm/alias/default"   # full-version alias
mkdir -p "$LP_TEST_DIR/home2/.nvm/versions/node/v22.21.1/bin"
printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/home2/.nvm/versions/node/v22.21.1/bin/node"
chmod +x "$LP_TEST_DIR/home2/.nvm/versions/node/v22.21.1/bin/node"
printf '99\n' > "$LP_TEST_DIR/home3/.nvm/alias/default"        # alias → nothing
for t in npx uvx node; do
    printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/stubs/$t"
    chmod +x "$LP_TEST_DIR/stubs/$t"
done

# 13.0 the family is defined after sourcing (source-guard contract).
if declare -F _path_ensure >/dev/null 2>&1 \
    && declare -F _path_synth_dirs >/dev/null 2>&1 \
    && declare -F _path_nvm_bin >/dev/null 2>&1 \
    && declare -F _path_version_gt >/dev/null 2>&1 \
    && declare -F _path_has >/dev/null 2>&1; then
    _pass
else
    _fail "_path_* family defined after sourcing" "all 5 defined" "missing"
fi

# 13.a PROBE NO-OP under a VM-shaped PATH: npx/uvx/node all resolve
# (systemd's default-PATH luck, made guaranteed by the stubs) → _path_ensure
# returns 0 and PATH is BYTE-UNCHANGED (the VM compatibility proof).
(
    . "$LAUNCHER"
    PATH="$LP_TEST_DIR/stubs:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    export PATH
    before="$PATH"
    _path_ensure || exit 41
    [ "$PATH" = "$before" ] || exit 42
    exit 0
)
assert_eq "13.a probe no-op: VM-shaped PATH byte-unchanged" "0" "$?"

# 13.b probe no-op logs the no-op line and NEVER the synthesis line.
(
    . "$LAUNCHER"
    PATH="$LP_TEST_DIR/stubs:/usr/bin:/bin"
    export PATH
    _path_ensure
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/probe.err"
assert_contains "13.b probe no-op logged" "probe no-op, PATH unchanged" "$(cat "$LP_TEST_DIR/probe.err")"
if grep -q "synthesized PATH extended" "$LP_TEST_DIR/probe.err"; then
    _fail "13.b probe no-op must not synthesize" "no synthesis log" "$(cat "$LP_TEST_DIR/probe.err")"
else
    _pass
fi

# 13.c SYNTHESIS under a launchd-minimal PATH + fake HOME: nvm bin and
# .local/bin prepended, inherited PATH retained as suffix, every added dir
# exists, and npx resolves afterwards.
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"
    PATH="/usr/bin:/bin"
    export HOME PATH
    _path_ensure || exit 43
    case ":$PATH:" in *":$LP_TEST_DIR/home/.nvm/versions/node/v22.21.1/bin:"*) ;; *) exit 44 ;; esac
    case ":$PATH:" in *":$LP_TEST_DIR/home/.local/bin:"*) ;; *) exit 45 ;; esac
    case "$PATH" in *":/usr/bin:/bin") ;; *) exit 46 ;; esac
    command -v npx >/dev/null 2>&1 || exit 47
    oldIFS="$IFS"; IFS=:
    for d in $PATH; do
        [ -n "$d" ] || continue
        [ -d "$d" ] || { IFS="$oldIFS"; exit 48; }
    done
    IFS="$oldIFS"
    exit 0
)
assert_eq "13.c synthesis: nvm+anchors prepended, suffix kept, dirs exist" "0" "$?"
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"
    PATH="/usr/bin:/bin"
    export HOME PATH
    _path_ensure
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/synth.err"
assert_contains "13.c synthesis logged" "synthesized PATH extended" "$(cat "$LP_TEST_DIR/synth.err")"

# 13.d _path_nvm_bin unit table (fake HOME ONLY — never the real ~/.nvm).
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"; export HOME
    out="$(_path_nvm_bin)"
    # NUMERIC latest: v22.21.1 > v22.9.1 (lexicographic glob order picks
    # v22.21.1 FIRST — a naive last-glob-match would pick v22.9.1).
    [ "$out" = "$LP_TEST_DIR/home/.nvm/versions/node/v22.21.1/bin" ] || exit 49
    exit 0
)
assert_eq "13.d nvm bin: numeric-latest version wins (v22.21.1 over v22.9.1)" "0" "$?"
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home2"; export HOME
    out="$(_path_nvm_bin)"
    [ "$out" = "$LP_TEST_DIR/home2/.nvm/versions/node/v22.21.1/bin" ] || exit 50
    exit 0
)
assert_eq "13.d nvm bin: full-version alias resolves exactly" "0" "$?"
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"; export HOME
    # alias 22 also matches v22.29.0 (glob v22*) whose bin/node is NOT
    # executable → must be skipped in favor of v22.21.1.
    out="$(_path_nvm_bin)"
    [ "$out" = "$LP_TEST_DIR/home/.nvm/versions/node/v22.21.1/bin" ] || exit 51
    exit 0
)
assert_eq "13.d nvm bin: non-executable bin/node dir skipped" "0" "$?"
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home3"; export HOME   # alias 99 → no version dirs
    out="$(_path_nvm_bin)"
    [ -z "$out" ] || exit 52
    exit 0
)
assert_eq "13.d nvm bin: dangling alias → empty (no fabricated path)" "0" "$?"
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/emptyhome"; export HOME   # no .nvm at all
    out="$(_path_nvm_bin)"
    [ -z "$out" ] || exit 53
    exit 0
)
assert_eq "13.d nvm bin: absent nvm → empty" "0" "$?"

# 13.e ENSEMBLE_LAUNCHER_PATH override: exported VERBATIM (full
# replacement — design §iv step 1), beating both the probe and synthesis
# even with a fully-populated fake nvm tree present.
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"
    PATH="/usr/bin:/bin"
    ENSEMBLE_LAUNCHER_PATH="/custom/a:/custom/b:/usr/bin:/bin"
    export HOME PATH ENSEMBLE_LAUNCHER_PATH
    _path_ensure || exit 54
    [ "$PATH" = "/custom/a:/custom/b:/usr/bin:/bin" ] || exit 55
    exit 0
)
assert_eq "13.e override: PATH exported verbatim (full replacement)" "0" "$?"

# 13.f set-but-EMPTY override falls through to the probe (not a wipe).
(
    . "$LAUNCHER"
    PATH="$LP_TEST_DIR/stubs:/usr/bin:/bin"
    ENSEMBLE_LAUNCHER_PATH=""
    export PATH ENSEMBLE_LAUNCHER_PATH
    before="$PATH"
    _path_ensure || exit 56
    [ "$PATH" = "$before" ] || exit 57
    exit 0
)
assert_eq "13.f empty override treated as unset (probe path)" "0" "$?"

# 13.g nothing-addable → loud WARNING, rc 0 (never wedges boot). On hosts
# where /opt/homebrew/bin or /usr/local/bin exists the anchors ARE added
# and the post-synthesis "still unresolved" WARNING fires instead — either
# branch must warn and return 0.
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/emptyhome"
    PATH="/usr/bin:/bin"
    export HOME PATH
    _path_ensure
    rc=$?
    [ "$rc" = "0" ] || exit 58
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/nothing.err"
assert_eq "13.g nothing-addable: rc 0 (boot proceeds)" "0" "$?"
if grep -q "WARNING" "$LP_TEST_DIR/nothing.err"; then
    _pass
else
    _fail "13.g nothing-addable: loud WARNING emitted" "WARNING present" "$(cat "$LP_TEST_DIR/nothing.err")"
fi

# 13.h source-guard pins (design §iv test mandates).
# h1 — probe-before-synthesis-export inside _path_ensure (comment-stripped
#      body; the override branch's export is a separate documented path).
PENSURE_BODY="$(awk '
    /^_path_ensure\(\)/ { in_fn=1; next }
    in_fn && /^}/ { in_fn=0; next }
    in_fn { print }
' "$LAUNCHER" | sed -e 's/#.*$//' -e '/^[[:space:]]*$/d')"
probe_line="$(printf '%s\n' "$PENSURE_BODY" | grep -n 'for t in npx uvx node' | head -1 | cut -d: -f1)"
synthexport_line="$(printf '%s\n' "$PENSURE_BODY" | grep -n 'export PATH="\$synth' | head -1 | cut -d: -f1)"
if [ -n "$probe_line" ] && [ -n "$synthexport_line" ] && [ "$probe_line" -lt "$synthexport_line" ]; then
    _pass
else
    _fail "13.h probe precedes the synthesis export" "probe line < synth-export line" \
        "probe=${probe_line:-absent} synth=${synthexport_line:-absent}"
fi
# h2 — NO baked version literals anywhere (launcher + prod plist).
for pinfile in "$LAUNCHER" "$REPO_ROOT/scripts/ensemble-prod.plist"; do
    if grep -Eq '/versions/node/v[0-9]+\.' "$pinfile"; then
        _fail "13.h no baked nvm version literal ($(basename "$pinfile"))" "absent" \
            "$(grep -E '/versions/node/v[0-9]+\.' "$pinfile" | head -2)"
    else
        _pass
    fi
done
# h3 — dynamic nvm wiring present + override var referenced.
assert_contains "13.h dynamic nvm alias read present" '.nvm/alias/default' "$(cat "$LAUNCHER")"
assert_contains "13.h ENSEMBLE_LAUNCHER_PATH override wired" 'ENSEMBLE_LAUNCHER_PATH' "$(cat "$LAUNCHER")"

# 13.i END-TO-END VM NO-OP PROOF: a REAL main() boot (resolve_install_dir ->
# load_env_file -> STATE_FILE default -> MALLOC -> _path_ensure ->
# _journal_sweep -> resolve_binary -> run_loop) under a VM-shaped PATH where
# npx/uvx/node resolve (systemd's packaging luck, made guaranteed by the
# sandbox vmbin). The stub "ensemble-prod" child dumps its inherited env:
# the PATH must arrive BYTE-IDENTICAL (probe no-op, nothing synthesized)
# and the launcher state must land at the CLASSIC path.
#
# Hermeticity (F2): this cell's subject is the VM-shaped boot's state-file
# resolution (classic path when the override is unset). An AMBIENT
# ENSEMBLE_LAUNCHER_STATE_FILE (Stage-2 override) is POLLUTION that must be
# excluded -- exactly like the sibling cells isolate their state (see :1663
# `unset PATH`, :1781 `unset ENSEMBLE_LAUNCHER_STATE_FILE`). The boot
# invocation below uses `env -u` so the child never inherits the ambient
# var, regardless of the caller's environment. The body is factored into a
# helper so both legs exercise the IDENTICAL assertions (no copy drift).
_vmboot_13i() {
    # args: $1 = leg label, $2 = err file, $3 = child-env dump
    local inst="$LP_TEST_DIR/vmboot-$1"
    rm -rf "$inst"
    mkdir -p "$inst/data"
    printf 'PORT=18971\nENSEMBLE_SELF_ENV=1\n' > "$inst/.env"
    cat > "$inst/ensemble-prod" <<EOF
#!/bin/sh
env > "$3"
exit 0
EOF
    chmod +x "$inst/ensemble-prod"
    mkdir -p "$LP_TEST_DIR/vmbin"
    for t in npx uvx node; do
        printf '#!/bin/sh\nexit 0\n' > "$LP_TEST_DIR/vmbin/$t"
        chmod +x "$LP_TEST_DIR/vmbin/$t"
    done
    VM_PATH="/usr/bin:/bin:/usr/sbin:/sbin"
    cd "$inst" || return 68
    # HERMETIC: unset the ambient override for THIS boot so the child resolves
    # the CLASSIC state path. The override is the shipped Stage-2 contract
    # (honoured when set); this cell tests state-file resolution, not it.
    PATH="$LP_TEST_DIR/vmbin:$VM_PATH" \
    HOME="$LP_TEST_DIR/emptyhome" \
    ENSEMBLE_INSTALL_DIR="$inst" \
    env -u ENSEMBLE_LAUNCHER_STATE_FILE \
        /bin/bash "$LAUNCHER" >/dev/null 2>"$2"
    rc=$?
    [ "$rc" = "0" ] || return 69
    grep -q "^PATH=$LP_TEST_DIR/vmbin:$VM_PATH$" "$3" || return 70
    [ -f "$inst/.launcher-state" ] || return 71
    return 0
}

# Leg (i): ambient var UNSET (the historical case).
_vmboot_13i A "$LP_TEST_DIR/vmboot-a.err" "$LP_TEST_DIR/vmboot-a-child-env.txt"
rc=$?
assert_eq "13.i VM-shaped boot: child PATH byte-identical, classic state path (ambient unset)" "0" "$rc"
if grep -q "probe no-op, PATH unchanged" "$LP_TEST_DIR/vmboot-a.err" 2>/dev/null; then
    _pass
else
    _fail "13.i VM-shaped boot logged the probe no-op (ambient unset)" \
        "probe no-op line in launcher stderr" "$(cat "$LP_TEST_DIR/vmboot-a.err" 2>/dev/null | head -3)"
fi

# Leg (ii): ambient var deliberately EXPORTED into this cell's env -- the
# hermeticity proof. With the fix in place the boot still resolves the
# CLASSIC path (it must NOT flip to the ambient path) and the SAME
# assertions hold. (Non-vacuity: remove the `env -u` above and this leg
# flips to the ambient path, proving the unset is what keeps it hermetic.)
ENSEMBLE_LAUNCHER_STATE_FILE="/tmp/ambient-pollution-should-not-win/.launcher-state" \
    _vmboot_13i B "$LP_TEST_DIR/vmboot-b.err" "$LP_TEST_DIR/vmboot-b-child-env.txt"
rc=$?
assert_eq "13.i VM-shaped boot: child PATH byte-identical, classic state path (ambient exported)" "0" "$rc"
if grep -q "probe no-op, PATH unchanged" "$LP_TEST_DIR/vmboot-b.err" 2>/dev/null; then
    _pass
else
    _fail "13.i VM-shaped boot logged the probe no-op (ambient exported)" \
        "probe no-op line in launcher stderr" "$(cat "$LP_TEST_DIR/vmboot-b.err" 2>/dev/null | head -3)"
fi
# Hermeticity proof: the ambient path must NOT have been written.
if [ -f "/tmp/ambient-pollution-should-not-win/.launcher-state" ]; then
    _fail "13.i hermeticity: ambient ENSEMBLE_LAUNCHER_STATE_FILE must NOT be written" \
        "absent" "present (ambient leak)"
else
    _pass
fi

# 13.j F-10: an nvm alias CODENAME ("lts/*", "lts/krypton") resolves to a
# CONCRETE version — the old one-hop glob ("…/node/v<token>*") was dead for
# those, so on macOS (the only source of npx) the synthesis yielded NOTHING
# and npx stayed unresolved — the exact rehearsal-a failure mode.
# 13.j1 "lts/*" → the HIGHEST version among the codename files.
(
    . "$LAUNCHER"
    H="$LP_TEST_DIR/home-lts"
    rm -rf "$H"
    mkdir -p "$H/.nvm/alias/lts" \
             "$H/.nvm/versions/node/v22.21.1/bin" \
             "$H/.nvm/versions/node/v20.11.1/bin"
    printf 'lts/*\n' > "$H/.nvm/alias/default"
    printf '22.21.1\n' > "$H/.nvm/alias/lts/krypton"
    printf '20.11.1\n' > "$H/.nvm/alias/lts/argon"
    for v in 22.21.1 20.11.1; do
        printf '#!/bin/sh\nexit 0\n' > "$H/.nvm/versions/node/v$v/bin/node"
        chmod +x "$H/.nvm/versions/node/v$v/bin/node"
    done
    HOME="$H"; export HOME
    out="$(_path_nvm_bin)"
    [ "$out" = "$H/.nvm/versions/node/v22.21.1/bin" ] || exit 82
    exit 0
)
assert_eq "13.j nvm alias 'lts/*' resolves to the highest version" "0" "$?"

# 13.j2 "lts/krypton" (a codename PATH) resolves through the alias FILE.
(
    . "$LAUNCHER"
    H="$LP_TEST_DIR/home-lts2"
    rm -rf "$H"
    mkdir -p "$H/.nvm/alias/lts" "$H/.nvm/versions/node/v22.21.1/bin"
    printf 'lts/krypton\n' > "$H/.nvm/alias/default"
    printf '22.21.1\n' > "$H/.nvm/alias/lts/krypton"
    printf '#!/bin/sh\nexit 0\n' > "$H/.nvm/versions/node/v22.21.1/bin/node"
    chmod +x "$H/.nvm/versions/node/v22.21.1/bin/node"
    HOME="$H"; export HOME
    out="$(_path_nvm_bin)"
    [ "$out" = "$H/.nvm/versions/node/v22.21.1/bin" ] || exit 83
    exit 0
)
assert_eq "13.j nvm alias 'lts/krypton' resolves through the alias file" "0" "$?"

# 13.j3 an UNRESOLVABLE alias → prints NOTHING (graceful — never wedges).
(
    . "$LAUNCHER"
    H="$LP_TEST_DIR/home-lts3"
    rm -rf "$H"
    mkdir -p "$H/.nvm/alias"
    printf 'lts/*\n' > "$H/.nvm/alias/default"
    HOME="$H"; export HOME
    out="$(_path_nvm_bin)"
    [ -z "$out" ] || exit 84
    exit 0
)
assert_eq "13.j nvm alias unresolvable → empty (graceful, no wedge)" "0" "$?"

# 13.j4 a SELF-REFERENTIAL alias is bounded (max 3 hops) → no hang.
(
    . "$LAUNCHER"
    H="$LP_TEST_DIR/home-lts4"
    rm -rf "$H"
    mkdir -p "$H/.nvm/alias/lts"
    printf 'lts/loop\n' > "$H/.nvm/alias/default"
    printf 'lts/loop\n' > "$H/.nvm/alias/lts/loop"
    HOME="$H"; export HOME
    out="$(_path_nvm_bin)"
    [ -z "$out" ] || exit 85
    exit 0
)
assert_eq "13.j nvm alias loop: bounded hops → empty (no hang)" "0" "$?"

# 13.k F-11: EVERY _path_ensure branch logs the three facts a VM drill can
# assert — source= (which branch), delta= (what was prepended / whether the
# inherited PATH was retained), resolved_path= (the final PATH value).
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"
    PATH="/usr/bin:/bin"
    export HOME PATH
    _path_ensure
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/synthfacts.err"
assert_contains "13.k synth branch logs source=synthesized" \
    "source=synthesized" "$(cat "$LP_TEST_DIR/synthfacts.err")"
assert_contains "13.k synth branch logs the prepended delta" \
    "delta=prepended:" "$(cat "$LP_TEST_DIR/synthfacts.err")"
assert_contains "13.k synth branch logs the resolved PATH" \
    "resolved_path=" "$(cat "$LP_TEST_DIR/synthfacts.err")"
assert_contains "13.k synth branch states the inherited PATH is kept as suffix" \
    "inherited PATH is retained as the suffix" "$(cat "$LP_TEST_DIR/synthfacts.err")"

(
    . "$LAUNCHER"
    PATH="$LP_TEST_DIR/stubs:/usr/bin:/bin"
    export PATH
    _path_ensure
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/probefacts.err"
assert_contains "13.k probe branch logs source=probe-satisfied" \
    "source=probe-satisfied" "$(cat "$LP_TEST_DIR/probefacts.err")"
assert_contains "13.k probe branch logs delta=none" \
    "delta=none" "$(cat "$LP_TEST_DIR/probefacts.err")"
assert_contains "13.k probe branch logs the resolved PATH" \
    "resolved_path=" "$(cat "$LP_TEST_DIR/probefacts.err")"
if grep -q "delta=prepended" "$LP_TEST_DIR/probefacts.err"; then
    _fail "13.k probe branch must log NO delta" "no delta=prepended" \
        "$(cat "$LP_TEST_DIR/probefacts.err")"
else
    _pass
fi

(
    . "$LAUNCHER"
    PATH="$LP_TEST_DIR/stubs:/usr/bin:/bin"
    ENSEMBLE_LAUNCHER_PATH="/custom/a:/custom/b"
    export PATH ENSEMBLE_LAUNCHER_PATH
    _path_ensure
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/ovrfacts.err"
assert_contains "13.k override branch logs its source" \
    "source=ensemble_launcher_path_override" "$(cat "$LP_TEST_DIR/ovrfacts.err")"
assert_contains "13.k override branch logs the full-replacement delta" \
    "delta=full-replacement" "$(cat "$LP_TEST_DIR/ovrfacts.err")"

# 13.k2 .env-SOURCED ENSEMBLE_LAUNCHER_PATH (unpinned until now): the install
# .env is staged by the launcher itself (ADR-014 -- load_env_file runs BEFORE
# _path_ensure), so a staged ENSEMBLE_LAUNCHER_PATH must participate in the
# Layer-2 precedence: it is exported VERBATIM (top precedence -- the inherited
# PATH is NOT retained) and the log names source=ensemble_launcher_path_override
# with delta=full-replacement. The inherited value is set to a DECOY inside the
# subshell so the row proves the .env wins (ADR-014) rather than merely
# arriving; the ambient var is unset first for hermeticity.
(
    . "$LAUNCHER"
    unset ENSEMBLE_LAUNCHER_PATH
    ENSEMBLE_LAUNCHER_PATH="/ambient/decoy/should/not/win"
    export ENSEMBLE_LAUNCHER_PATH
    # stage the .env with the real override value
    mkdir -p "$LP_TEST_DIR/dotenvpath"
    printf 'ENSEMBLE_LAUNCHER_PATH=/from/dotenv/verbatim:/also/here\n' > "$LP_TEST_DIR/dotenvpath/.env"
    # resolve via the real load_env_file + _path_ensure (main()'s sequence)
    INSTALL_DIR="$LP_TEST_DIR/dotenvpath"
    cd "$INSTALL_DIR" || exit 72
    load_env_file "$INSTALL_DIR/.env"
    _path_ensure
    printf 'PATH=%s\n' "$PATH"
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/dotenvpath.err"
# The .env value must win over the inherited decoy AND replace the PATH
# verbatim (top precedence -- no inherited suffix retained).
grep -q '^PATH=/from/dotenv/verbatim:/also/here$' "$LP_TEST_DIR/dotenvpath.err" 2>/dev/null
# (PATH is logged via resolved_path=... on the override line, not on stdout;
#  re-check against the captured stderr instead)
if grep -q 'source=ensemble_launcher_path_override' "$LP_TEST_DIR/dotenvpath.err" && \
   grep -q 'delta=full-replacement' "$LP_TEST_DIR/dotenvpath.err" && \
   grep -q 'resolved_path=/from/dotenv/verbatim:/also/here' "$LP_TEST_DIR/dotenvpath.err"; then
    _pass
else
    _fail "13.k2 .env-sourced ENSEMBLE_LAUNCHER_PATH: verbatim export + top precedence" \
        "source=ensemble_launcher_path_override + delta=full-replacement + resolved_path=/from/dotenv/verbatim:/also/here" \
        "$(cat "$LP_TEST_DIR/dotenvpath.err" 2>/dev/null | head -5)"
fi

# 13.l FIX 7(a): set -u / empty-PATH hardening. An UNSET PATH must not
# abort the launcher (set -u) and no PATH may end up with an EMPTY
# component (POSIX resolves a leading/trailing ':' as the CWD).
(
    . "$LAUNCHER"
    unset PATH
    _path_has /usr/bin
    rc_has=$?
    _path_ensure
    rc=$?
    [ "$rc" = "0" ] || exit 86
    [ "$rc_has" = "1" ] || exit 87
    exit 0
) > /dev/null 2> "$LP_TEST_DIR/unsetpath.err"
assert_eq "13.l PATH unset: no set -u abort, _path_has safe" "0" "$?"
if grep -q "unbound variable" "$LP_TEST_DIR/unsetpath.err"; then
    _fail "13.l PATH unset: must not reference an unbound PATH" \
        "no unbound variable" "$(cat "$LP_TEST_DIR/unsetpath.err")"
else
    _pass
fi
(
    . "$LAUNCHER"
    HOME="$LP_TEST_DIR/home"
    PATH=""
    export HOME PATH
    _path_ensure || exit 88
    [ -n "$PATH" ] || exit 89
    case "$PATH" in
        :*) exit 90 ;;      # leading empty component
        *:) exit 91 ;;      # trailing empty component
    esac
    case ":$PATH:" in
        *::*) exit 92 ;;    # empty component in the middle
    esac
    exit 0
)
assert_eq "13.l PATH empty: no empty PATH component, no abort" "0" "$?"

# ─── 14. STATE_FILE relocation (design §v) ─────────────────────────────────
section "STATE_FILE relocation (§v)"

# 14.a the default expression is EXACTLY the design's (default unchanged:
# unset env ⇒ classic path — the VM burst-budget dependency).
assert_contains "14.a default expression byte-pin" \
    ': "${ENSEMBLE_LAUNCHER_STATE_FILE:=$INSTALL_DIR/.launcher-state}"' "$(cat "$LAUNCHER")"

# 14.b ORDERING RESOLUTION: the assignment sits AFTER load_env_file (it
# was BEFORE at :2821/:2827 pre-relocation) so the override is readable
# from the install .env (Stage-2 wiring).
loadenv_line="$(grep -n 'load_env_file "\$INSTALL_DIR/.env"' "$LAUNCHER" | head -1 | cut -d: -f1)"
statefile_line="$(grep -n 'ENSEMBLE_LAUNCHER_STATE_FILE:=\$INSTALL_DIR/.launcher-state' "$LAUNCHER" | head -1 | cut -d: -f1)"
if [ -n "$loadenv_line" ] && [ -n "$statefile_line" ] && [ "$loadenv_line" -lt "$statefile_line" ]; then
    _pass
else
    _fail "14.b STATE_FILE default after load_env_file (.env readability)" \
        "load_env_file line < STATE_FILE line" \
        "load_env_file=${loadenv_line:-absent} statefile=${statefile_line:-absent}"
fi

# 14.c …and BEFORE _journal_sweep (run-loop consumers + the sweep-defer
# derivation see the relocated value).
jsweep_line="$(grep -n '^[[:space:]]*_journal_sweep[[:space:]]*$' "$LAUNCHER" | head -1 | cut -d: -f1)"
if [ -n "$jsweep_line" ] && [ -n "$statefile_line" ] && [ "$statefile_line" -lt "$jsweep_line" ]; then
    _pass
else
    _fail "14.c STATE_FILE default before _journal_sweep" \
        "STATE_FILE line < _journal_sweep line" \
        "statefile=${statefile_line:-absent} sweep=${jsweep_line:-absent}"
fi

# 14.d the direct hardcode is GONE (single derivation site in main()).
assert_eq "14.d no direct STATE_FILE hardcode remains" "0" \
    "$(grep -c 'STATE_FILE="\$INSTALL_DIR/\.launcher-state"' "$LAUNCHER")"

# 14.e exported so run_loop children inherit it (upgrade-tools seam).
assert_contains "14.e export ENSEMBLE_LAUNCHER_STATE_FILE present" \
    "export ENSEMBLE_LAUNCHER_STATE_FILE" "$(cat "$LAUNCHER")"

# 14.f defer-counter derivation: dirname(STATE_FILE) when set (relocated),
# classic install_dir when unset (byte-identical); sanitisation unchanged.
(
    . "$LAUNCHER"
    STATE_FILE=""
    p="$(_js_defer_counter_path "$LP_TEST_DIR/inst" "run-1")"
    [ "$p" = "$LP_TEST_DIR/inst/.sweep-defer-run-1" ] || exit 59
    STATE_FILE="$LP_TEST_DIR/inst/data/.launcher-state"
    p="$(_js_defer_counter_path "$LP_TEST_DIR/inst" "run-1")"
    [ "$p" = "$LP_TEST_DIR/inst/data/.sweep-defer-run-1" ] || exit 60
    p="$(_js_defer_counter_path "$LP_TEST_DIR/inst" "bad/id 1")"
    [ "$p" = "$LP_TEST_DIR/inst/data/.sweep-defer-bad_id_1" ] || exit 61
    exit 0
)
assert_eq "14.f defer path: unset→install_dir, set→dirname(STATE_FILE), sanitised" "0" "$?"

# 14.g write/read roundtrip through a RELOCATED state file (override path).
(
    . "$LAUNCHER"
    STATE_FILE="$LP_TEST_DIR/reloc/.launcher-state"
    mkdir -p "$LP_TEST_DIR/reloc"
    STATE_LAST_EXIT=1 STATE_CRASH_COUNT=2 STATE_WINDOW_START=3
    STATE_LAST_BACKOFF=20 STATE_NOTIFIED_75=0 STATE_LAST_UPTIME=5
    write_state || exit 62
    STATE_LAST_EXIT="" STATE_CRASH_COUNT=""
    read_state
    [ "$STATE_LAST_EXIT" = "1" ] && [ "$STATE_CRASH_COUNT" = "2" ] || exit 63
    [ -f "$LP_TEST_DIR/reloc/.launcher-state" ] || exit 64
    exit 0
)
assert_eq "14.g state roundtrip honors the relocated STATE_FILE" "0" "$?"

# ─── 14.h … STATE_FILE resolution parity (F-07 / residual e) ────────────────
# The launcher's EFFECTIVE ENSEMBLE_LAUNCHER_STATE_FILE, computed exactly
# the way main() computes it: load_env_file (the REAL function) and then
# the REAL default expression — EXTRACTED from launcher.sh (never hardcoded
# here, so a change to the launcher's default is caught, not mirrored).
launcher_state_file() {
    local inst="$1" envf="$2" inh="${3-}" defexpr
    defexpr="$(grep -F ': "${ENSEMBLE_LAUNCHER_STATE_FILE:=$INSTALL_DIR/.launcher-state}"' "$LAUNCHER" | head -1)"
    [ -n "$defexpr" ] || return 1
    (
        . "$LAUNCHER"
        INSTALL_DIR="$inst"
        unset ENSEMBLE_LAUNCHER_STATE_FILE
        [ -n "$inh" ] && export ENSEMBLE_LAUNCHER_STATE_FILE="$inh"
        load_env_file "$envf"
        eval "$defexpr"
        printf '%s' "${ENSEMBLE_LAUNCHER_STATE_FILE:-}"
    )
}

STATE_PAR_DIR="$LP_TEST_DIR/statepar"
mkdir -p "$STATE_PAR_DIR"

# 14.h PRECEDENCE (F-07): the .env value WINS over the inherited env — the
# launcher's export beats the inherited value (ADR-014 discipline). The
# watchdog must agree (the parity rows live in tests/test_watchdog_watcher.sh).
printf 'ENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$STATE_PAR_DIR/from-dotenv" > "$STATE_PAR_DIR/.env"
assert_eq "14.h .env wins over the inherited env" \
    "$STATE_PAR_DIR/from-dotenv" \
    "$(launcher_state_file "$STATE_PAR_DIR" "$STATE_PAR_DIR/.env" "$STATE_PAR_DIR/from-env")"

# 14.i DUPLICATE KEY (F-07 / residual e): the LAST occurrence wins on both
# sides (the loader exports sequentially — the last export wins).
printf 'ENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$STATE_PAR_DIR/first" > "$STATE_PAR_DIR/dup.env"
printf 'ENSEMBLE_LAUNCHER_STATE_FILE=%s\n' "$STATE_PAR_DIR/second" >> "$STATE_PAR_DIR/dup.env"
assert_eq "14.i duplicate .env key: LAST occurrence wins" \
    "$STATE_PAR_DIR/second" \
    "$(launcher_state_file "$STATE_PAR_DIR" "$STATE_PAR_DIR/dup.env" "$STATE_PAR_DIR/from-env")"

# 14.j an EMPTY .env value clears the inherited value → the default fires
# (the launcher's export wipes the inherited value; the := default then
# assigns — the watchdog resolves the same default).
printf 'ENSEMBLE_LAUNCHER_STATE_FILE=\n' > "$STATE_PAR_DIR/empty.env"
assert_eq "14.j empty .env value → the classic default" \
    "$STATE_PAR_DIR/.launcher-state" \
    "$(launcher_state_file "$STATE_PAR_DIR" "$STATE_PAR_DIR/empty.env" "$STATE_PAR_DIR/from-env")"

# 14.k NO override anywhere → the classic default path (byte-identical —
# the VM's crash-loop burst budget depends on that exact path).
: > "$STATE_PAR_DIR/none.env"
assert_eq "14.k no override anywhere → the classic default" \
    "$STATE_PAR_DIR/.launcher-state" \
    "$(launcher_state_file "$STATE_PAR_DIR" "$STATE_PAR_DIR/none.env")"

# 14.l the default expression is still the ONLY functional default site
# (the watchdog's resolver mirrors it byte-for-byte).
assert_eq "14.l single functional classic-path default in launcher.sh" "1" \
    "$(grep -cF ': "${ENSEMBLE_LAUNCHER_STATE_FILE:=$INSTALL_DIR/.launcher-state}"' "$LAUNCHER")"

# ─── 15. Plist template + Makefile staging (design §iv Layer 1) ────────────
section "plist template + Makefile staging (§iv Layer 1)"
PROD_PLIST="$REPO_ROOT/scripts/ensemble-prod.plist"
WATCH_PLIST="$REPO_ROOT/scripts/ensemble-watchdog-watcher.plist"
PLIST_PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# 15.a the stable-anchor PATH, byte-exact per design §iv Layer 1.
assert_contains "15.a prod plist carries the exact anchor PATH" \
    "<string>$PLIST_PATH_VALUE</string>" "$(cat "$PROD_PLIST")"

# 15.b inside an EnvironmentVariables dict.
assert_contains "15.b prod plist has EnvironmentVariables dict" \
    "<key>EnvironmentVariables</key>" "$(cat "$PROD_PLIST")"

# 15.c the stale "deliberately ABSENT" note is gone (reconciled ADR-014).
assert_eq "15.c stale ABSENT comment removed" "0" \
    "$(grep -c 'deliberately ABSENT' "$PROD_PLIST")"

# 15.d XML validity, both templates (python3 is a repo dev assumption).
if command -v python3 >/dev/null 2>&1; then
    if python3 -c 'import sys, xml.etree.ElementTree as ET
for f in sys.argv[1:]:
    ET.parse(f)
' "$PROD_PLIST" "$WATCH_PLIST" 2>/dev/null; then
        _pass
    else
        _fail "15.d plist templates are well-formed XML" "both parse" "parse error"
    fi
else
    _skip "15.d plist XML validity (python3 unavailable)"
fi

# 15.e Makefile staging carries the PATH through — using the REAL awk
# program EXTRACTED FROM THE MAKEFILE. (The old row hardcoded a
# byte-identical COPY of the program: drift-unguarded — a Makefile change
# silently diverged.) The extraction is CONTENT-based (any single-quoted
# awk program containing INSTALL_DIR_PLACEHOLDER), so it survives the
# program moving into a variable, and it FAILS LOUDLY when the program
# cannot be found or when the Makefile carries MORE THAN ONE DISTINCT
# program. The Makefile is only ever READ (never written, never run).
#
# _makefile_awk_programs <makefile> — the DISTINCT staging programs (the
# Makefile's $$ escapes un-escaped), one per line. READ-ONLY.
#
# QUOTE PAIRING IS PER-LINE, NEVER FILE-WIDE (2026-11-10 fix): the previous
# extractor paired single quotes across the WHOLE file
# (re.finditer(r"'([^']*)'", src)), so ONE apostrophe inside a Makefile
# COMMENT (e.g. "clients like Chrome's" / "pyinstaller's" / "config.yaml's")
# shifted the pairing and the placeholder-bearing program was never captured
# — 0 programs extracted, the 15.e row failed and 15.e2/15.e3 passed
# VACUOUSLY (everything "rejected" via extraction failure, not placeholder
# detection). Each line that carries the literal INSTALL_DIR_PLACEHOLDER is
# now scanned ON ITS OWN: single quotes are paired sequentially WITHIN that
# line only (never across lines), the quoted segments that themselves contain
# the placeholder are kept, and "$$" is un-escaped to "$". The LOUD-REFUSAL
# contract is unchanged: 0 DISTINCT programs or >1 DISTINCT programs → the
# row fails loudly (a Makefile restructure must break this guard loudly,
# never silently diverge).
_makefile_awk_programs() {
    python3 - "$1" <<'PYEOF'
import sys
try:
    src = open(sys.argv[1], encoding="utf-8").read()
except OSError:
    sys.exit(1)
progs = []
unterminated = []
for raw in src.splitlines():
    if "INSTALL_DIR_PLACEHOLDER" not in raw:
        continue
    segs = []
    buf = None
    for ch in raw:
        if ch == "'":
            if buf is None:
                buf = []
            else:
                segs.append("".join(buf))
                buf = None
        elif buf is not None:
            buf.append(ch)
    if buf is not None:
        # ODD number of single quotes on this line: the trailing segment is
        # unterminated (the program may span lines and cannot be extracted
        # per-line). Recorded as a HINT for the loud refusal — it never
        # refuses on its own, so an apostrophe in an unrelated comment cannot
        # fail an otherwise-correct extraction.
        tail = "".join(buf)
        if "INSTALL_DIR_PLACEHOLDER" in tail:
            unterminated.append(raw.strip())
    for t in segs:
        if "INSTALL_DIR_PLACEHOLDER" not in t:
            continue
        t = t.replace("$$", "$")
        if t not in progs:
            progs.append(t)
if not progs and unterminated:
    sys.stderr.write(
        "no complete staging program extracted; unterminated single quote on a "
        "placeholder-bearing line: " + unterminated[0] + "\n")
    sys.exit(2)
for p in progs:
    print(p)
PYEOF
}

# _makefile_awk_program <makefile> — the ONE staging program on stdout;
# rc 1 when the Makefile carries ZERO or MORE THAN ONE distinct program
# (the drift guard refuses rather than silently testing a stale copy).
# The extractor's stderr diagnostic is folded into the refusal message, so a
# failing extraction says WHY (e.g. a multi-line program) and not merely "0".
_makefile_awk_program() {
    local progs n rc diag diagf
    diagf="$LP_TEST_DIR/.staging-extract-diag.txt"
    mkdir -p "$LP_TEST_DIR" 2>/dev/null
    progs="$(_makefile_awk_programs "$1" 2>"$diagf")"
    rc=$?
    diag="$(cat "$diagf" 2>/dev/null)"
    if [ "$rc" != "0" ]; then
        printf 'drift guard: %s — extraction FAILED (rc=%s)%s\n' \
            "$1" "$rc" "${diag:+: $diag}" >&2
        return 1
    fi
    n="$(printf '%s\n' "$progs" | grep -c '[^[:space:]]' || true)"
    if [ "$n" != "1" ]; then
        printf 'drift guard: %s carries %s DISTINCT staging programs (exactly 1 required)%s\n' \
            "$1" "$n" "${diag:+ — $diag}" >&2
        return 1
    fi
    printf '%s' "$progs"
}

# _staging_ok <makefile> <plist> — rc 0 when the REAL (extracted) program
# stages the plist correctly: the PATH survives and EVERY placeholder is
# dropped.
_staging_ok() {
    local mk="$1" plist="$2" prog out
    prog="$(_makefile_awk_program "$mk")" || return 1
    [ -n "$prog" ] || return 1
    out="$LP_TEST_DIR/stage-drift/data/ensemble-prod.plist"
    mkdir -p "$LP_TEST_DIR/stage-drift/data" || return 1
    INSTALL_DIR="$LP_TEST_DIR/stage-drift" awk "$prog" "$plist" > "$out" 2>/dev/null || return 1
    grep -qF "<string>$PLIST_PATH_VALUE</string>" "$out" || return 1
    if grep -q 'INSTALL_DIR_PLACEHOLDER' "$out"; then return 1; fi
    return 0
}

if _staging_ok "$REPO_ROOT/Makefile" "$PROD_PLIST"; then
    _pass
else
    _fail "15.e staging substitution (REAL Makefile program) preserves PATH, drops placeholders" \
        "extracted program stages the plist" "extraction or substitution failed"
fi

# 15.e2 NON-VACUITY (negative): a PERTURBED COPY of the Makefile (the real
# one is NEVER written) whose staging program no longer substitutes the
# placeholder MUST be caught by the same assertion.
cp "$REPO_ROOT/Makefile" "$LP_TEST_DIR/Makefile.perturbed"
sed -i.bak 's/INSTALL_DIR_PLACEHOLDER/INSTALL_DIR_PLACEHOLDER_X/g' "$LP_TEST_DIR/Makefile.perturbed"
if _staging_ok "$LP_TEST_DIR/Makefile.perturbed" "$PROD_PLIST"; then
    _fail "15.e2 drift guard is not vacuous (a broken staging program must be CAUGHT)" \
        "broken program rejected" "broken program accepted — the guard is vacuous"
else
    _pass
fi

# 15.e3 NON-VACUITY (negative): a Makefile copy with NO placeholder-aware
# staging program at all must fail LOUDLY (the extraction refuses).
cp "$REPO_ROOT/Makefile" "$LP_TEST_DIR/Makefile.noplaceholder"
sed -i.bak2 's/INSTALL_DIR_PLACEHOLDER/PLACEHOLDER/g' "$LP_TEST_DIR/Makefile.noplaceholder"
if _makefile_awk_program "$LP_TEST_DIR/Makefile.noplaceholder" >/dev/null 2>&1; then
    _fail "15.e3 extraction must fail loudly when no staging program is found" \
        "extraction refuses" "extraction accepted a program-less Makefile"
else
    _pass
fi

# 15.f leg-1 guard wiring INTACT in both plist make targets (regression
# census — the full matrix is tests/unit/test_makefile_install_guard.py).
assert_contains "15.f plist-install still runs the guard" \
    '$(GUARD_SCRIPT) --target plist-install' "$(cat "$REPO_ROOT/Makefile")"
assert_contains "15.f watchdog-install still runs the guard" \
    '$(GUARD_SCRIPT) --target watchdog-install' "$(cat "$REPO_ROOT/Makefile")"

# 15.g the watchdog plist deliberately has NO EnvironmentVariables (design:
# its tools — curl/osascript/sed/head — all live in /usr/bin:/bin, inside
# launchd's default PATH; §viii row 4 scopes Layer 1 to the PROD template).
assert_eq "15.g watchdog plist carries no EnvironmentVariables (by design)" "0" \
    "$(grep -c 'EnvironmentVariables' "$WATCH_PLIST")"
# ── MERGE PROVENANCE (feature/launcher-journal-sweep-fix × origin/latest) ──────
# Upstream carried THESE pins under a different section number: it inserted
# three sections ahead of them (its macOS PATH / STATE_FILE / plist legs took
# §13/§14/§15), so the js-pin block became §16 on its side. Upstream's
# numbering was §16a≡§13a … §16g≡§13g … §16j≡§13j … §16k≡§13k.
# This merge restores OUR §13a..§13k numbering:
#   * the ASSERT-LABEL namespaces are DISJOINT — upstream's new legs use
#     DOTTED labels (13.a..13.l, 14.a..14.l, 15.a..15.g); these pins use
#     UNDOTTED ones (13a..13k, 14a..14f). No shadowing, both sides coexist.
#   * the assertion CONTENT is byte-identical (only the section number moved) —
#     verified by a normalized 13↔16 diff against both parents.

# ─── 13. _js_journal_read / _js_json_sub O(n) rewrite (launcher-journal-sweep-fix) ──
# The per-character bash loops were O(n²) (25.2 s @ 37 KB measured; ~31 min
# extrapolated @ 215 KB) and ran on EVERY boot via _journal_sweep. The
# replacements are vectorized POSIX awk passes. The pins below freeze the
# CONTRACT (not the implementation): byte-exact outputs, accept/reject
# verdicts including the escape parity rule, the deliberate string-blind
# depth walk of _js_json_sub, locale invariance, and a wall-clock perf gate.
section "js journal read: O(n) validator semantics"
JSF_TEST_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf.XXXXXX")"

_jsf_read() {  # <file> — read + validate, print stdout + rc marker
    ( . "$LAUNCHER"; _js_journal_read "$1"; echo "rc=$?" )
}

# ─── 13a. brackets inside string values + escaped quotes/backslashes (R6a) ─
section "js journal read: brackets inside strings"
printf '%s' '{"current":"v1","history":[{"detail":"has } and { inside \"quoted \\\" and \\\\ backslash\" brackets [yes]"}],"quarantined":[]}' \
    > "$JSF_TEST_DIR/escapes.json"
_jsf_out="$(_jsf_read "$JSF_TEST_DIR/escapes.json")"
assert_contains "13a escape-heavy journal validates" "rc=0" "$_jsf_out"
assert_contains "13a output byte-exact (bracket-in-string intact)" \
    '"detail":"has } and { inside \"quoted \\\" and \\\\ backslash\" brackets [yes]"' \
    "$_jsf_out"

# ─── 13b. escape parity: a string closed after an escaped backslash run ──
section "js journal read: escape parity"
# The escape PARITY cases: a string closed after an escaped backslash run,
# and a quote escaped by a backslash (must NOT close).
printf '%s' '{"a":"ends with escaped backslash \\\\ and closes"}' > "$JSF_TEST_DIR/parity_ok.json"
assert_contains "13b even-backslash-run then quote closes (valid)" "rc=0" "$(_jsf_read "$JSF_TEST_DIR/parity_ok.json")"
printf '%s' '{"a":"escaped \" quote never closes' > "$JSF_TEST_DIR/parity_torn.json"
assert_contains "13b escaped quote does NOT close (unterminated → reject)" "rc=1" "$(_jsf_read "$JSF_TEST_DIR/parity_torn.json")"

# ─── 13c. truncated/torn rejection (R6b) — torn tail → nonzero ───────────
section "js journal read: torn tail rejection"
printf '%s' '{"a":{"b":[1,2' > "$JSF_TEST_DIR/torn_bracket.json"
assert_contains "13c torn bracket tail rejected" "rc=1" "$(_jsf_read "$JSF_TEST_DIR/torn_bracket.json")"
printf '%s' '{"a":"unterminated' > "$JSF_TEST_DIR/torn_string.json"
assert_contains "13c unterminated string rejected" "rc=1" "$(_jsf_read "$JSF_TEST_DIR/torn_string.json")"
printf '%s' '{"a":{"b":1}}}' > "$JSF_TEST_DIR/extra_close.json"
assert_contains "13c extra close rejected" "rc=1" "$(_jsf_read "$JSF_TEST_DIR/extra_close.json")"
printf '%s' '{"a":1}{"b":2}' > "$JSF_TEST_DIR/two_docs.json"
# Balance-count contract (NOT a parser — pre-existing semantics): equal
# open/close counts with no unterminated string is ACCEPTED, exactly as
# the replaced loop accepted it. The pin freezes that contract.
assert_contains "13c balance-only contract preserved (two docs accepted)" "rc=0" "$(_jsf_read "$JSF_TEST_DIR/two_docs.json")"
: > "$JSF_TEST_DIR/empty.json"
assert_contains "13c empty journal rejected (pre-existing contract)" "rc=1" "$(_jsf_read "$JSF_TEST_DIR/empty.json")"

# ─── 13d. locale invariance (R1) — default, LC_ALL=C and UTF-8 agree ───
section "js journal read: locale invariance"
# identical verdicts under default locale AND LC_ALL=C AND a UTF-8 locale
# (the awk pass pins LC_ALL=C internally, so this holds by construction —
# the pin keeps it true).
_jsf_val="$( ( . "$LAUNCHER"; _js_journal_read "$JSF_TEST_DIR/escapes.json" >/dev/null; echo $? ) )"
_jsf_c="$(LC_ALL=C bash -c '. "$1"; _js_journal_read "$2" >/dev/null; echo $?' _ "$LAUNCHER" "$JSF_TEST_DIR/escapes.json")"
_jsf_utf8="$(LC_ALL=en_US.UTF-8 bash -c '. "$1"; _js_journal_read "$2" >/dev/null; echo $?' _ "$LAUNCHER" "$JSF_TEST_DIR/escapes.json" 2>/dev/null)"
assert_eq "13d default locale verdict" "0" "$_jsf_val"
assert_eq "13d LC_ALL=C verdict identical" "0" "$_jsf_c"
assert_eq "13d UTF-8 locale verdict identical" "0" "${_jsf_utf8:-$_jsf_c}"
_jsf_torn_c="$(LC_ALL=C bash -c '. "$1"; _js_journal_read "$2" >/dev/null; echo $?' _ "$LAUNCHER" "$JSF_TEST_DIR/torn_bracket.json")"
assert_eq "13d LC_ALL=C torn verdict identical" "1" "$_jsf_torn_c"

# ─── 13e. _js_json_sub: O(n) extractor semantics (R6d) ─────────────────────
section "js json sub: O(n) extractor semantics"
_jsf_sub() {  # <json> <key>
    ( . "$LAUNCHER"; _js_json_sub "$1" "$2"; echo "rc=$?" )
}
_jsf_sub_out="$(_jsf_sub '{"history":[{"detail":"esc \" and \\\\ inside"}],"quarantined":["v1"]}' history)"
assert_contains "13e escaped-quote value extracted byte-exact" \
    '[{"detail":"esc \" and \\\\ inside"}]' "$_jsf_sub_out"
assert_contains "13e history extraction rc=0" "rc=0" "$_jsf_sub_out"
assert_contains "13e quarantined extracted" '["v1"]rc=0' "$(_jsf_sub '{"quarantined":["v1"]}' quarantined | tr -d '\n')"
assert_contains "13e missing key → rc=1" "rc=1" "$(_jsf_sub '{"a":{}}' nope)"
assert_contains "13e scalar value → rc=1" "rc=1" "$(_jsf_sub '{"a":1}' a)"
assert_contains "13e null value → rc=1" "rc=1" "$(_jsf_sub '{"a":null}' a)"
assert_contains "13e spaced form tolerated" '[1,2]rc=0' "$(_jsf_sub '{"history" :  [1,2]}' history | tr -d '\n')"
# The string-blind depth walk is PRESERVED DELIBERATELY (the replaced loop
# never tracked string context either): a brace inside the TARGET's string
# still terminates the extraction. Pin it — changing it would change
# verdicts on existing journals.
assert_eq "13e quirk preserved: brace-in-string terminates walk" \
    '{"x":"y}rc=0' "$(_jsf_sub '{"a":{"x":"y}z"},"b":1}' a | tr -d '\n')"

# ─── 13f. perf gate: ~250 KB journal validates well inside 2 s (R6c) ───────
section "js journal read: 250KB perf gate"
JSF_PERF_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf-perf.XXXXXX")"
JSF_PERF_JOURNAL="$JSF_PERF_DIR/state.json"
# Dependency-light generation (awk is already a launcher runtime dep).
awk 'BEGIN {
    printf "%s", "{\"current\":\"v1\",\"previous\":null,\"in_flight\":null,\"quarantined\":[],\"history\":["
    for (i = 0; i < 1900; i++) {
        printf "%s{\"ts\":\"2026-10-27T%02d:%02d:%02dZ\",\"event\":\"mcp_server_state\",\"detail\":\"server=context7 state=degraded resolver_timeout ENOTFOUND (attempt %d padding-padding-padding)\"}", (i > 0 ? "," : ""), (i/60)%24, i%60, (i*7)%60, i
    }
    printf "%s", "]}"
}' > "$JSF_PERF_JOURNAL"
_jsf_perf_bytes=$(wc -c < "$JSF_PERF_JOURNAL" | tr -d ' ')
if [ "$_jsf_perf_bytes" -ge 240000 ]; then
    _pass  # fixture really is ~250 KB class
else
    _fail "13f perf fixture is ~250 KB" ">= 240000 bytes" "$_jsf_perf_bytes"
fi
# Watchdog form: _js_run_bounded 2 kills the read at 2 s. The O(n²) loop
# this replaces needed ~31 min at 215 KB — it CANNOT pass this gate; the
# awk pass finishes in ~50 ms.
_jsf_perf_rc="$( ( . "$LAUNCHER"; _js_run_bounded 2 -- bash -c '. "$1"; _js_journal_read "$2" > /dev/null' _ "$LAUNCHER" "$JSF_PERF_JOURNAL" >/dev/null 2>&1; echo $? ) )"
assert_eq "13f 250KB journal read completes inside the 2s watchdog" "0" "$_jsf_perf_rc"
_jsf_perf_s0=$(date +%s)
( . "$LAUNCHER"; _js_journal_read "$JSF_PERF_JOURNAL" > "$JSF_PERF_DIR/read.out" 2>/dev/null )
_jsf_perf_rc2=$?
_jsf_perf_s1=$(date +%s)
assert_eq "13f unbounded read rc=0" "0" "$_jsf_perf_rc2"
if [ $(( _jsf_perf_s1 - _jsf_perf_s0 )) -le 2 ]; then _pass; else _fail "13f read elapsed ≤ 2s" "<= 2" "$(( _jsf_perf_s1 - _jsf_perf_s0 ))"; fi
if cmp -s "$JSF_PERF_JOURNAL" "$JSF_PERF_DIR/read.out"; then
    _pass  # byte-exact roundtrip at scale
else
    _fail "13f 250KB read output byte-exact" "identical" "differs"
fi

# ─── 13g. string-context discrimination (W1) ───────────────────────────────
# COUNCIL FINDING W1. Every in-string bracket in §13a is BALANCED ({…},
# […] inside the value), and every rejection in 13b/13c is caught by an
# IMBALANCE — so a validator that ignores string context entirely
# produces the SAME totals and the SAME verdict on the whole existing
# suite. This fixture carries an UNBALANCED in-string brace run: three
# `{` inside the value plus the ONE `}` that closes the document. A
# string-blind counter reads ob=4 / cb=1 ⇒ rc=1; the shipped validator
# reads rc=0. Kills the M1 (string-blind) mutant.
section "js journal read: string-context (W1)"
# Byte discipline: ONE single-quoted printf arg, then ASSERT the bytes
# actually written (a shell-mangled fixture would be a false green).
#   11 bytes, 4 × '{', 1 × '}', 0 × '\'   (i.e. genuinely unbalanced)
printf '%s' '{"a":"{{{"}' > "$JSF_TEST_DIR/string_context.json"
assert_eq "13g fixture bytes are exactly 11 (single-quoted literal)" \
    "11" "$(wc -c < "$JSF_TEST_DIR/string_context.json" | tr -d ' ')"
assert_eq "13g fixture carries UNBALANCED in-string braces (4 open / 1 close)" \
    "4/1/0" "$(tr -cd '{' < "$JSF_TEST_DIR/string_context.json" | wc -c | tr -d ' ')/$(tr -cd '}' < "$JSF_TEST_DIR/string_context.json" | wc -c | tr -d ' ')/$(tr -cd '\\' < "$JSF_TEST_DIR/string_context.json" | wc -c | tr -d ' ')"
_jsf_w1_out="$(_jsf_read "$JSF_TEST_DIR/string_context.json")"
assert_contains "13g unbalanced in-string braces validate (rc=0)" "rc=0" "$_jsf_w1_out"
assert_contains "13g output byte-exact (in-string braces intact)" \
    '{"a":"{{{"}' "$_jsf_w1_out"

# ─── 13h. escape parity: EVEN run adjacent to a quote CLOSES it (W2a) ──────
# COUNCIL FINDING W2. Every backslash run adjacent to a quote elsewhere in
# this section is ODD, and the even runs in 13a are followed by a SPACE,
# never a quote — so relaxing the parity test `RLENGTH % 2 == 1` to a
# presence-only `RLENGTH > 0` passes the ENTIRE pre-existing suite. Here a
# 2-backslash run sits immediately before the closing quote: the quote
# CLOSES the string (valid JSON, §13's contract ⇒ rc=0). Presence-only
# keeps the string open ⇒ unbalanced ⇒ rc=1. Kills the M2 mutant.
section "js journal read: escape parity — even run (W2a)"
#   17 bytes, 2 × '\' (even run) immediately before the closing quote.
printf '%s' '{"a":"x\\","b":2}' > "$JSF_TEST_DIR/parity_even_run.json"
assert_eq "13h fixture bytes are exactly 17 (single-quoted literal)" \
    "17" "$(wc -c < "$JSF_TEST_DIR/parity_even_run.json" | tr -d ' ')"
assert_eq "13h fixture carries an EVEN 2-backslash run" \
    "2" "$(tr -cd '\\' < "$JSF_TEST_DIR/parity_even_run.json" | wc -c | tr -d ' ')"
_jsf_w2a_out="$(_jsf_read "$JSF_TEST_DIR/parity_even_run.json")"
assert_contains "13h even-backslash run before the quote validates (rc=0)" "rc=0" "$_jsf_w2a_out"
assert_contains "13h output byte-exact (even run intact)" \
    '{"a":"x\\","b":2}' "$_jsf_w2a_out"

# ─── 13i. escape parity: ODD (3-backslash) run adjacent to a quote (W2b) ───
# The 3-run ⇒ the following quote is ESCAPED ⇒ the string stays OPEN and
# the NEXT quote closes it (valid JSON ⇒ rc=0). Parity inverted
# (`RLENGTH % 2 == 0`) treats the 3-run as non-escaping ⇒ the first quote
# closes ⇒ the second quote opens a string that never terminates ⇒ rc=1.
# Kills the M3 mutant.
section "js journal read: escape parity — odd run (W2b)"
#   19 bytes, 3 × '\' (odd run) immediately before an escaped quote.
printf '%s' '{"a":"x\\\"","b":2}' > "$JSF_TEST_DIR/parity_odd_run.json"
assert_eq "13i fixture bytes are exactly 19 (single-quoted literal)" \
    "19" "$(wc -c < "$JSF_TEST_DIR/parity_odd_run.json" | tr -d ' ')"
assert_eq "13i fixture carries an ODD 3-backslash run" \
    "3" "$(tr -cd '\\' < "$JSF_TEST_DIR/parity_odd_run.json" | wc -c | tr -d ' ')"
_jsf_w2b_out="$(_jsf_read "$JSF_TEST_DIR/parity_odd_run.json")"
assert_contains "13i odd-backslash run escapes the quote (rc=0)" "rc=0" "$_jsf_w2b_out"
assert_contains "13i output byte-exact (odd run intact)" \
    '{"a":"x\\\"","b":2}' "$_jsf_w2b_out"

# ─── 13j. rc vocabulary: UNAVAILABLE (2) is distinct from NOT-TRUSTED (1) ─
# The launcher distinguishes THREE outcomes for its JSON passes:
#   0 — the pass ran and the content is trustworthy;
#   1 — the pass ran and the content is NOT trustworthy (bracket-unbalanced,
#       unterminated string, key absent) — a CONTENT verdict;
#   2 — the pass could not RUN (awk missing / broken / wedged past the
#       bound) — the verdict is UNKNOWN.
# Collapsing 2 into 1 is a silent-corruption class: an empty extraction is
# indistinguishable from "the field is gone", and the writers that splice
# an empty value back into the journal would overwrite real history (or
# reset the 24h rollback counter). These pins freeze the distinction.
section "js journal rc vocabulary: unavailable vs not-trustworthy"

_JSF_BASH="$(command -v bash)"

# (a) a PATH with NO awk — the interpreter is gone.
JSF_NOAWK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf-noawk.XXXXXX")"
for _jsf_bin in cat date sleep uname grep sed tr wc mkdir rm mv sh; do
    _jsf_real="$(command -v "$_jsf_bin" 2>/dev/null)" || continue
    ln -sf "$_jsf_real" "$JSF_NOAWK_DIR/$_jsf_bin" 2>/dev/null
done

# (b) a fake awk whose FIRST call (the balance validator) succeeds and
#     every LATER call (the extractor) exits 127 — the "not found" status
#     the wrapper maps into the rc-2 class.
JSF_FAKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf-fake.XXXXXX")"
JSF_FAKE_COUNTER="$JSF_TEST_DIR/fake_awk_calls"
cat > "$JSF_FAKE_DIR/awk" <<'JSF_FAKE_AWK'
#!/bin/sh
_n=$(cat "$JSF_FAKE_COUNTER" 2>/dev/null || echo 0)
_n=$((_n + 1))
echo "$_n" > "$JSF_FAKE_COUNTER" 2>/dev/null || true
if [ "$_n" -le 1 ]; then exit 0; fi
exit 127
JSF_FAKE_AWK
chmod +x "$JSF_FAKE_DIR/awk"

# (c) a wedged awk — a single blocking process (the watchdog's SIGKILL
#     must land on awk ITSELF, or the bound would not hold).
JSF_WEDGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf-wedge.XXXXXX")"
printf '#!/bin/sh\nexec sleep 300\n' > "$JSF_WEDGE_DIR/awk"
chmod +x "$JSF_WEDGE_DIR/awk"

# 13j.1 — no awk at all: the READ and the EXTRACTOR report rc=2 (the raw
#        awk status 127 must NOT leak, and must NOT read as "unbalanced").
_jsf_noawk_read="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_journal_read "$2" >/dev/null 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/escapes.json" 2>/dev/null)"
_jsf_noawk_sub="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_json_sub "{\"a\":{\"b\":1}}" a >/dev/null 2>&1; echo "rc=$?"' _ "$LAUNCHER" 2>/dev/null)"
assert_contains "13j journal read without awk → rc=2 (unavailable, not 1)" "rc=2" "$_jsf_noawk_read"
assert_contains "13j json sub without awk → rc=2 (unavailable, not 1)" "rc=2" "$_jsf_noawk_sub"

# 13j.2 — a wedged awk under the bound → rc=2 as well (a TIMEOUT is
#        UNAVAILABLE, never "unbalanced"/1). The bound is shortened to 2 s
#        for the test (the shipped value is 60 s — see _JS_AWK_TIMEOUT_S).
_jsf_wedge_rc="$(PATH="$JSF_WEDGE_DIR:$PATH" "$_JSF_BASH" -c '. "$1"; _JS_AWK_TIMEOUT_S=2; _js_json_sub "{\"a\":{\"b\":1}}" a >/dev/null 2>&1; echo "rc=$?"' _ "$LAUNCHER" 2>/dev/null)"
assert_contains "13j wedged awk under the bound → rc=2 (not 1)" "rc=2" "$_jsf_wedge_rc"

# 13j.3 — the writers REFUSE on an unavailable extractor, and the journal
#        is left byte-identical. The read succeeds (the validator ran) and
#        the extraction fails (the interpreter vanished in between) — the
#        exact shape that used to REPLACE the history array with one row
#        and RESTART the 24h rollback counter from zero.
printf '%s' '{"current":"v1","history":[{"ts":"t","event":"sweep","detail":"d"}],"quarantined":["v0.0.1"],"rollback_window_count":{"24h":2,"window_start":"2026-01-01T00:00:00Z"},"cooldown_until":null}' \
    > "$JSF_TEST_DIR/rc_journal.json"
cp "$JSF_TEST_DIR/rc_journal.json" "$JSF_TEST_DIR/rc_journal.before"
_jsf_hist_rc="$(JSF_FAKE_COUNTER="$JSF_FAKE_COUNTER" PATH="$JSF_FAKE_DIR:$PATH" "$_JSF_BASH" -c '. "$1"; _js_history_append "$2" "evt" "det" 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_journal.json" 2>/dev/null)"
assert_contains "13j history append refused when the extractor is unavailable" "rc=1" "$_jsf_hist_rc"
assert_contains "13j the refusal says UNAVAILABLE (visible to the operator)" "UNAVAILABLE" "$_jsf_hist_rc"
if cmp -s "$JSF_TEST_DIR/rc_journal.before" "$JSF_TEST_DIR/rc_journal.json"; then
    _pass
else
    _fail "13j history array untouched when the extractor is unavailable" "identical" "differs"
fi
_jsf_cnt_rc="$(JSF_FAKE_COUNTER="$JSF_FAKE_COUNTER" PATH="$JSF_FAKE_DIR:$PATH" "$_JSF_BASH" -c '. "$1"; _js_count_rollback "$2" 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_journal.json" 2>/dev/null)"
assert_contains "13j rollback counter refused when the extractor is unavailable" "rc=1" "$_jsf_cnt_rc"
if cmp -s "$JSF_TEST_DIR/rc_journal.before" "$JSF_TEST_DIR/rc_journal.json"; then
    _pass
else
    _fail "13j 24h counter NOT reset when the extractor is unavailable" "identical" "differs"
fi
_jsf_quar_rc="$(JSF_FAKE_COUNTER="$JSF_FAKE_COUNTER" PATH="$JSF_FAKE_DIR:$PATH" "$_JSF_BASH" -c '. "$1"; _js_quarantine "$2" "v9.9.9" 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_journal.json" 2>/dev/null)"
assert_contains "13j quarantine append refused when the extractor is unavailable" "rc=1" "$_jsf_quar_rc"
if cmp -s "$JSF_TEST_DIR/rc_journal.before" "$JSF_TEST_DIR/rc_journal.json"; then
    _pass
else
    _fail "13j quarantined[] untouched when the extractor is unavailable" "identical" "differs"
fi

# 13j.4 — the sweep WARN names the check that actually ran (the launcher
#        runs a BRACKET BALANCE CHECK, not a JSON parse) and distinguishes
#        "the validator is unavailable" from "the content is not trusted".
_jsf_warn_dir="$JSF_TEST_DIR/warn"
mkdir -p "$_jsf_warn_dir/releases"
printf '%s' '{"current":"v1","in_flight":{' > "$_jsf_warn_dir/releases/state.json"
_jsf_warn_out="$( ( . "$LAUNCHER"; INSTALL_DIR="$_jsf_warn_dir" _journal_sweep ) 2>&1 >/dev/null )"
assert_contains "13j the refusal WARN names the check it ran (balance-check only)" \
    "balance-check only — not a JSON parse" "$_jsf_warn_out"
_jsf_warn2_out="$( ( PATH="$JSF_NOAWK_DIR"; . "$LAUNCHER"; INSTALL_DIR="$_jsf_warn_dir" _journal_sweep ) 2>&1 >/dev/null )"
assert_contains "13j the unavailable WARN does NOT claim 'unbalanced'" \
    "validator is UNAVAILABLE" "$_jsf_warn2_out"

# 13j.5 — the unavailable WARN quotes the REAL RAW awk status. Before the
#         round-3 fix the WARN read a stale `_JS_LAST_AWK_RC=0` at every
#         site: the raw rc was set inside `_js_awk_bounded`, which ran as
#         a pipeline segment (a subshell) AND behind the callers' `$(...)`
#         command substitution (a second subshell). The wrapper is now
#         invoked via a here-string AND writes the raw status through an
#         FD 9 side-channel that `_js_awk_rc_begin`/`_js_awk_rc_end`
#         brackets carry across the cmdsub boundary. Pin: under a
#         missing-awk PATH the raw status is 127 — a NON-ZERO, NON-0
#         literal in the WARN text.
_jsf_noawk_update="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_journal_update "$2" current v2 2>&1 >/dev/null' _ "$LAUNCHER" "$JSF_TEST_DIR/escapes.json" 2>/dev/null)"
assert_contains "13j update-refusal WARN quotes the RAW awk status (awk rc=127, not a stale 0)" \
    "awk rc=127" "$_jsf_noawk_update"
_jsf_noawk_sweep="$( ( PATH="$JSF_NOAWK_DIR"; . "$LAUNCHER"; INSTALL_DIR="$_jsf_warn_dir" _journal_sweep ) 2>&1 >/dev/null )"
assert_contains "13j sweep unavailable WARN quotes the RAW awk status (awk rc=127)" \
    "awk rc=127" "$_jsf_noawk_sweep"

# 13j.6 — the read-unavailable refuse arms are LOUD (round-3 D2): when
#         `_js_journal_read` ITSELF reports rc=2, the writers used to
#         fall into `|| return 1` with NO WARN — the journal stayed
#         untouched (safe) but the operator got zero signal. Each arm now
#         emits the sibling-style UNAVAILABLE WARN (with the raw rc)
#         before returning 1.
printf '%s' '{"current":"v1","history":[{"ts":"t","event":"sweep","detail":"d"}],"quarantined":["v0.0.1"],"rollback_window_count":{"24h":2,"window_start":"2026-01-01T00:00:00Z"},"cooldown_until":null}' \
    > "$JSF_TEST_DIR/rc_read2.json"
cp "$JSF_TEST_DIR/rc_read2.json" "$JSF_TEST_DIR/rc_read2.before"
_jsf_hist_read2="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_history_append "$2" evt det 2>&1 >/dev/null' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_contains "13j history append WARNs when the journal READ is unavailable (raw rc quoted)" \
    "awk rc=127" "$_jsf_hist_read2"
_jsf_hist_read2_rc="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_history_append "$2" evt det >/dev/null 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_eq "13j read-unavailable history-append refusal still returns rc=1" \
    "rc=1" "$_jsf_hist_read2_rc"
if cmp -s "$JSF_TEST_DIR/rc_read2.before" "$JSF_TEST_DIR/rc_read2.json"; then
    _pass
else
    _fail "13j history array untouched when the journal READ is unavailable" "identical" "differs"
fi
_jsf_cnt_read2="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_count_rollback "$2" 2>&1 >/dev/null' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_contains "13j rollback counter WARNs when the journal READ is unavailable (raw rc quoted)" \
    "awk rc=127" "$_jsf_cnt_read2"
_jsf_cnt_read2_rc="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_count_rollback "$2" >/dev/null 2>&1; echo "rc=$?"' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_eq "13j read-unavailable rollback refusal still returns rc=1" \
    "rc=1" "$_jsf_cnt_read2_rc"
if cmp -s "$JSF_TEST_DIR/rc_read2.before" "$JSF_TEST_DIR/rc_read2.json"; then
    _pass
else
    _fail "13j 24h counter untouched when the journal READ is unavailable" "identical" "differs"
fi
_jsf_quar_read2="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_quarantine "$2" v9.9.9 2>&1 >/dev/null' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_contains "13j quarantine append WARNs when the journal READ is unavailable (raw rc quoted)" \
    "awk rc=127" "$_jsf_quar_read2"
_jsf_qurem_read2="$(PATH="$JSF_NOAWK_DIR" "$_JSF_BASH" -c '. "$1"; _js_quarantine_remove "$2" v9.9.9 2>&1 >/dev/null' _ "$LAUNCHER" "$JSF_TEST_DIR/rc_read2.json" 2>/dev/null)"
assert_contains "13j quarantine removal WARNs when the journal READ is unavailable (raw rc quoted)" \
    "awk rc=127" "$_jsf_qurem_read2"

# ─── 13k. PHANTOM-BRACKET truncation regression (the `j < tn` bound) ─────────
# THE REGRESSION. The replaced bash loop counted brackets one character at a
# time and returned 1 at EOF with the depth still open — a value missing its
# final closing bracket was NEVER extracted. The O(n) awk rewrite replaced
# that walk with a split()-based vectorized scan; its first cut visited the
# trailing field AFTER the last bracket and counted it as a closing bracket
# — the phantom bracket. A torn value then reached depth 0 and was emitted
# TRUNCATED with rc 0 (the whole `history` array came back as a bare `[`).
# The bound that kills it is `j < tn`: the loop visits EXACTLY the tn-1 real
# brackets, so a value missing its final closing bracket falls through to
# `exit 1` with NO output.
#
# THE CONTRACT PINNED HERE (it must never regress):
#   a value missing its final closing bracket  =>  rc=1 AND EMPTY stdout.
# The EMPTY-stdout half is the load-bearing one: a truncated value returned
# with rc 0 would be spliced straight back into the journal by the writers
# (history append / quarantine append), and the readers would treat the bare
# `[` as a real (empty) history array.
section "js json sub: phantom-bracket truncation regression (j < tn bound)"

# _jsf_sub_probe <json> <key> <outfile> — echo the rc; the extractor's stdout
# bytes land in <outfile> so "EMPTY stdout" is asserted on the BYTES, not on
# a marker string appended to them.
_jsf_sub_probe() {
    ( . "$LAUNCHER"; _js_json_sub "$1" "$2" > "$3" 2>/dev/null )
    echo $?
}

# ── (a) four shapes of "the target value is missing its FINAL closing
#        bracket": a flat object value, a flat array value, a nested-object
#        value, and an array value. Each must be refused with NO output.
JSF_TORN_DIR="$JSF_TEST_DIR/torn"
mkdir -p "$JSF_TORN_DIR"

printf '%s' '{"a":{"x":1'      > "$JSF_TORN_DIR/flat_value_torn.json"
printf '%s' '{"a":[1,2'       > "$JSF_TORN_DIR/flat_array_torn.json"
printf '%s' '{"a":{"b":{"x":1}' > "$JSF_TORN_DIR/nested_object_torn.json"
printf '%s' '{"a":[{"x":1}'   > "$JSF_TORN_DIR/array_torn.json"

for _jsf_shape in flat_value_torn flat_array_torn nested_object_torn array_torn; do
    _jsf_torn_json="$(cat "$JSF_TORN_DIR/$_jsf_shape.json")"
    _jsf_torn_rc="$(_jsf_sub_probe "$_jsf_torn_json" a "$JSF_TORN_DIR/$_jsf_shape.out")"
    assert_eq "13k $_jsf_shape: value missing its final closing bracket => rc=1" \
        "1" "$_jsf_torn_rc"
    assert_eq "13k $_jsf_shape: EMPTY stdout (no truncated emission)" \
        "0" "$(wc -c < "$JSF_TORN_DIR/$_jsf_shape.out" | tr -d ' ')"
done

# ── (b) The three NAMED differential cases from the launcher's own byte
#        parity corpus, embedded VERBATIM. The pin is SELF-CONTAINED: it
#        must not reference /tmp (or any corpus path) at test runtime.
#
#        case_0124_trunc_torn_flat_077 (77 bytes)  — history array torn
#        case_0154_trunc_torn_hist_027  (27 bytes)  — history array torn
#        case_0230_trunc_torn_hist_103  (103 bytes) — history array torn
#
#        Acceptance notation: SUB_history|1|  (rc=1, zero stdout bytes).
JSF_CASE_DIR="$JSF_TEST_DIR/cases"
mkdir -p "$JSF_CASE_DIR"

printf '%s' '{"current":"v1","previous":null,"in_flight":null,"quarantined":[],"history":[' \
    > "$JSF_CASE_DIR/case_0124_trunc_torn_flat_077"
printf '%s' '{"current":"v1","history":[' \
    > "$JSF_CASE_DIR/case_0154_trunc_torn_hist_027"
printf '%s' '{"current":"v1","history":[{"ts":"2026-01-01T00:00:00Z","event":"sweep","detail":"target v1 run_id r1"}' \
    > "$JSF_CASE_DIR/case_0230_trunc_torn_hist_103"

# The byte-count assertion is the in-test guard against a shell-mangled
# literal (the §13g/h/i discipline): the embedded bytes must be EXACTLY the
# named file's bytes. The one-off pre-commit `cmp` against the corpus files
# is the second, external half of that proof.
assert_eq "13k named case case_0124_trunc_torn_flat_077 is byte-exact (77 bytes)" \
    "77" "$(wc -c < "$JSF_CASE_DIR/case_0124_trunc_torn_flat_077" | tr -d ' ')"
assert_eq "13k named case case_0154_trunc_torn_hist_027 is byte-exact (27 bytes)" \
    "27" "$(wc -c < "$JSF_CASE_DIR/case_0154_trunc_torn_hist_027" | tr -d ' ')"
assert_eq "13k named case case_0230_trunc_torn_hist_103 is byte-exact (103 bytes)" \
    "103" "$(wc -c < "$JSF_CASE_DIR/case_0230_trunc_torn_hist_103" | tr -d ' ')"

for _jsf_case in case_0124_trunc_torn_flat_077 case_0154_trunc_torn_hist_027 case_0230_trunc_torn_hist_103; do
    _jsf_case_json="$(cat "$JSF_CASE_DIR/$_jsf_case")"
    _jsf_case_rc="$(_jsf_sub_probe "$_jsf_case_json" history "$JSF_CASE_DIR/$_jsf_case.out")"
    assert_eq "13k $_jsf_case: SUB_history|1| (rc=1)" "1" "$_jsf_case_rc"
    assert_eq "13k $_jsf_case: SUB_history|1| (empty stdout, no truncated emission)" \
        "0" "$(wc -c < "$JSF_CASE_DIR/$_jsf_case.out" | tr -d ' ')"
    # The READ side refuses the same torn document (the journal is not
    # trustworthy) — a writer must never see it.
    assert_contains "13k $_jsf_case: torn journal read refused (rc=1)" \
        "rc=1" "$(_jsf_read "$JSF_CASE_DIR/$_jsf_case")"
done

# ─── 14. the launcher-side size guard: bounded defer decision (Q1=C) ─────────
#                + the pre-flip branch's guard (Q2=i) ────────────────────────
# The launcher-side size guard (SWEEP_JOURNAL_SIZE_GUARD_BYTES, 98304 by
# default) is the regime boundary. Q1=C bounds the over-threshold DEFER
# ACTION: the write-once DECISION runs under the bounded awk seam with a
# HARD budget, and a decision that cannot be made (the extractor is gone,
# the watchdog fires, the budget is exceeded) makes the launcher REFUSE TO
# ACT — no journal write, the txn left OPEN, a WARN naming the JOURNAL
# SIZE and the BUDGET. A missing decision must NEVER read as "there is no
# defer row" (rc 1).
#
# Q2=i gives the PRE-FLIP branch the SAME guard semantics: at or above the
# threshold the launcher declines to act (no launcher rollback, no
# commit-and-continue, no txn clear) and leaves the transaction to the
# daemon-side closer. Below the threshold the behaviour is unchanged.
section "js size guard: bounded defer decision (Q1=C) + pre-flip guard (Q2)"

JSG_GUARD_BYTES=98304

# _js_seed_big <dir> <started_iso> <flipped> [pad_bytes]
# A journal that CROSSES the size guard: one extra history row carrying a
# long padded detail. The document stays balanced and parseable (the sweep
# reads it like any other journal) — only its SIZE differs.
_js_seed_big() {
    local d="$1" started="$2" flipped="$3" pad="${4:-120000}"
    local padstr
    padstr="$(head -c "$pad" /dev/zero | tr '\000' 'x')"
    printf '{"current":"v0.10.5","previous":"v0.10.5","in_flight":{"kind":"promote","target":"v0.10.6","started_at":"%s","flipped":%s,"owner_pid":999999},"rollback_window_count":{"24h":1,"window_start":"%s"},"cooldown_until":null,"quarantined":[],"history":[{"ts":"%s","event":"pad","detail":"%s"}]}\n' \
        "$started" "$flipped" "$started" "$started" "$padstr" > "$d/releases/state.json"
}

# _js_run_log <dir> [awk_timeout_s] — the sweep's LOG (stderr) + rc.
# The awk bound is overridden AFTER sourcing the launcher (the shipped
# `_JS_AWK_TIMEOUT_S` is set unconditionally at source time, so an
# override before the source would be overwritten).
_js_run_log() {
    ( . "$LAUNCHER"; _JS_AWK_TIMEOUT_S="${2:-$_JS_AWK_TIMEOUT_S}"; INSTALL_DIR="$1" _journal_sweep 2>&1 )
}

# A fake awk that forwards EVERY call to the real awk EXCEPT the
# defer-DECISION scan (its program text carries the _JS_HIST_REASON
# marker) — that one wedges forever, so the bounded seam's watchdog fires
# and the decision comes back UNAVAILABLE (rc 2). The marker keeps the
# fake surgical: the sweep's other passes (the balance validator, the
# extractors) still run for real, so the sweep REACHES the size guard.
JSF_REAL_AWK="$(command -v awk 2>/dev/null)"
JSF_DECIDE_WEDGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/launcher-jsf-decide.XXXXXX")"
cat > "$JSF_DECIDE_WEDGE_DIR/awk" <<JSF_DECIDE_AWK
#!/bin/sh
# Fake awk: wedges ONLY for the defer-decision scan (the _JS_HIST_REASON
# marker); every other call is forwarded to the real awk.
for _a in "\$@"; do
    case "\$_a" in
        *_JS_HIST_REASON*) exec sleep 300 ;;
    esac
done
exec "$JSF_REAL_AWK" "\$@"
JSF_DECIDE_AWK
chmod +x "$JSF_DECIDE_WEDGE_DIR/awk"

# ── 14a. over-threshold, NORMAL (in-budget) → the CONSERVATIVE DEFER ──────
JSG_A="$JS_TEST_DIR/sg-a"; _js_fixture "$JSG_A"; _js_seed_big "$JSG_A" "$STALE_TS" true
JSG_A_SIZE="$(wc -c < "$JSG_A/releases/state.json" | tr -d ' ')"
JSG_A_LOG="$(_js_run_log "$JSG_A")"
assert_contains "14a over-threshold: the CONSERVATIVE DEFER WARN fires" \
    "CONSERVATIVE DEFER" "$JSG_A_LOG"
assert_contains "14a over-threshold: the defer row is journaled (the write-once HOLD)" \
    '"event":"sweep_deferred"' "$(_js_journal "$JSG_A")"
assert_contains "14a over-threshold: the defer row carries the size-guard reason" \
    "journal-size-guard" "$(_js_journal "$JSG_A")"
assert_contains "14a over-threshold: the txn stays OPEN" \
    '"owner_pid":999999' "$(_js_journal "$JSG_A")"
assert_eq "14a over-threshold: NO rollback (the current symlink is untouched)" \
    "releases/v0.10.6" "$(readlink "$JSG_A/current")"
assert_eq "14a over-threshold: the rollback counter is NOT incremented" \
    "1" "$(_js_count "$JSG_A")"
assert_eq "14a over-threshold: the cooldown is NOT armed" \
    "null" "$(_js_field "$JSG_A" cooldown_until)"
# the SECOND sweep must NOT write a second row: the decision's rc-0
# verdict ("a row IS already journaled") is what the write-once rule
# rides on — a broken hand-off would double the row (and, at this size,
# double the boot-path write cost).
_js_run_log "$JSG_A" >/dev/null 2>&1
assert_eq "14a over-threshold: the defer row is written ONCE (write-once)" \
    "1" "$(printf '%s' "$(_js_journal "$JSG_A")" | grep -o '"event":"sweep_deferred"' | wc -l | tr -d ' ')"

# ── 14b. over-threshold, FORCED OVERRUN → REFUSE TO ACT (no journal write) ─
JSG_B="$JS_TEST_DIR/sg-b"; _js_fixture "$JSG_B"; _js_seed_big "$JSG_B" "$STALE_TS" true
JSG_B_SHA_BEFORE="$(shasum -a 256 "$JSG_B/releases/state.json" | awk '{print $1}')"
JSG_B_BYTES_BEFORE="$(wc -c < "$JSG_B/releases/state.json" | tr -d ' ')"
JSG_B_LOG="$(PATH="$JSF_DECIDE_WEDGE_DIR:$PATH" _js_run_log "$JSG_B" 2)"
JSG_B_SHA_AFTER="$(shasum -a 256 "$JSG_B/releases/state.json" | awk '{print $1}')"
assert_contains "14b overrun: the WARN names the JOURNAL SIZE" \
    "journal size=" "$JSG_B_LOG"
assert_contains "14b overrun: the WARN names the guard" \
    "guard=${JSG_GUARD_BYTES}B" "$JSG_B_LOG"
assert_contains "14b overrun: the WARN names the BUDGET" \
    "defer decision budget (2s)" "$JSG_B_LOG"
assert_contains "14b overrun: the WARN refuses to act" \
    "REFUSING to act" "$JSG_B_LOG"
assert_contains "14b overrun: the WARN names the size-guard regime" \
    "≥ guard=${JSG_GUARD_BYTES}B" "$JSG_B_LOG"
assert_eq "14b overrun: the journal is BYTE-IDENTICAL (sha256 unchanged)" \
    "$JSG_B_SHA_BEFORE" "$JSG_B_SHA_AFTER"
assert_eq "14b overrun: the journal size is unchanged" \
    "$JSG_B_BYTES_BEFORE" "$(wc -c < "$JSG_B/releases/state.json" | tr -d ' ')"
assert_contains "14b overrun: the txn stays OPEN" \
    '"owner_pid":999999' "$(_js_journal "$JSG_B")"
assert_eq "14b overrun: the lock is RELEASED" \
    "absent" "$([ -d "$JSG_B/releases/rollback.lock.d" ] && echo present || echo absent)"
assert_eq "14b overrun: NO crash (the sweep returns 0)" \
    "0" "$( ( . "$LAUNCHER"; _JS_AWK_TIMEOUT_S=2; PATH="$JSF_DECIDE_WEDGE_DIR:$PATH" INSTALL_DIR="$JSG_B" _journal_sweep >/dev/null 2>&1; echo $? ) )"

# ── 14c. over-threshold, stale flipped:true → NO rollback, txn left OPEN ──
# (the defer ROW is written — that is the documented CONSERVATIVE DEFER;
# what must NOT happen is a rollback: no current repoint, no quarantine,
# no counter, no cooldown, and the txn stays OPEN).
JSG_C="$JS_TEST_DIR/sg-c"; _js_fixture "$JSG_C"; _js_seed_big "$JSG_C" "$STALE_TS" true
JSG_C_LOG="$(_js_run_log "$JSG_C")"
assert_contains "14c over-threshold stale flipped:true: the CONSERVATIVE DEFER WARN fires" \
    "CONSERVATIVE DEFER" "$JSG_C_LOG"
assert_eq "14c over-threshold stale flipped:true: NO rollback (current symlink untouched)" \
    "releases/v0.10.6" "$(readlink "$JSG_C/current")"
assert_eq "14c over-threshold stale flipped:true: the journal current is NOT repointed" \
    "v0.10.5" "$(_js_field "$JSG_C" current)"
assert_eq "14c over-threshold stale flipped:true: nothing is quarantined" \
    "[]" "$( . "$LAUNCHER"; _js_json_sub "$(_js_journal "$JSG_C")" quarantined 2>/dev/null )"
assert_eq "14c over-threshold stale flipped:true: the rollback counter is NOT incremented" \
    "1" "$(_js_count "$JSG_C")"
assert_eq "14c over-threshold stale flipped:true: the cooldown is NOT armed" \
    "null" "$(_js_field "$JSG_C" cooldown_until)"
assert_contains "14c over-threshold stale flipped:true: the txn stays OPEN" \
    '"owner_pid":999999' "$(_js_journal "$JSG_C")"

# ── 14d. BELOW-threshold control → the rollback DOES happen (unchanged) ────
JSG_D="$JS_TEST_DIR/sg-d"; _js_fixture "$JSG_D"; _js_seed "$JSG_D" "$STALE_TS" true 1 "$STALE_TS" null promote 1
_js_run "$JSG_D"
assert_eq "14d below-threshold control: the rollback happens (current repointed to previous)" \
    "releases/v0.10.5" "$(readlink "$JSG_D/current")"
assert_contains "14d below-threshold control: the sweep_rollback event is journaled" \
    '"event":"sweep_rollback"' "$(_js_journal "$JSG_D")"
assert_eq "14d below-threshold control: the txn is closed" \
    "null" "$(_js_field "$JSG_D" in_flight)"

# ── 14e. Q2: over-threshold stale PRE-FLIP → the launcher DECLINES ───────
JSG_E="$JS_TEST_DIR/sg-e"; _js_fixture "$JSG_E"; _js_seed_big "$JSG_E" "$STALE_TS" false
ln -sfn "releases/v0.10.5" "$JSG_E/current"   # pre-flip: still on the serving release
JSG_E_SHA_BEFORE="$(shasum -a 256 "$JSG_E/releases/state.json" | awk '{print $1}')"
JSG_E_LOG="$(_js_run_log "$JSG_E")"
assert_contains "14e pre-flip over-threshold: the DECLINE WARN fires" \
    "DECLINING TO ACT" "$JSG_E_LOG"
assert_contains "14e pre-flip over-threshold: the WARN names the regime" \
    "journal is" "$JSG_E_LOG"
assert_contains "14e pre-flip over-threshold: the WARN names the consequence" \
    "daemon-side closer" "$JSG_E_LOG"
assert_contains "14e pre-flip over-threshold: the WARN says the txn is left OPEN" \
    "txn is left OPEN" "$JSG_E_LOG"
assert_eq "14e pre-flip over-threshold: the journal is BYTE-IDENTICAL (sha256 unchanged)" \
    "$JSG_E_SHA_BEFORE" "$(shasum -a 256 "$JSG_E/releases/state.json" | awk '{print $1}')"
assert_contains "14e pre-flip over-threshold: the txn stays OPEN" \
    '"owner_pid":999999' "$(_js_journal "$JSG_E")"
assert_eq "14e pre-flip over-threshold: NO sweep event (nothing was cleared)" \
    "absent" "$(printf '%s' "$(_js_journal "$JSG_E")" | grep -q '"event":"sweep"' && echo present || echo absent)"
assert_eq "14e pre-flip over-threshold: the rollback counter is NOT incremented" \
    "1" "$(_js_count "$JSG_E")"

# ── 14f. Q2 control: BELOW-threshold pre-flip → the clear STILL happens ───
JSG_F="$JS_TEST_DIR/sg-f"; _js_fixture "$JSG_F"; _js_seed "$JSG_F" "$STALE_TS" false
ln -sfn "releases/v0.10.5" "$JSG_F/current"
_js_run "$JSG_F"
assert_eq "14f pre-flip below-threshold: the clear STILL happens (in_flight null)" \
    "null" "$(_js_field "$JSG_F" in_flight)"
assert_contains "14f pre-flip below-threshold: the sweep history event is recorded" \
    '"event":"sweep"' "$(_js_journal "$JSG_F")"
assert_eq "14f pre-flip below-threshold: the current symlink is untouched" \
    "releases/v0.10.5" "$(readlink "$JSG_F/current")"

# ─── F4: _env_strip_value byte-parity (launcher.sh <-> watchdog-watcher.sh) ──
# The two implementations MUST stay byte-identical (F-06: one value-resolution
# rule, duplicated by design because the watchdog is standalone). The pin
# compares the REAL file bytes (extracted function bodies), so it FAILS if
# either side is edited -- it cannot be satisfied by a stub.
section "env-strip-value byte-parity (F4)"

_env_strip_value_body() {  # <file>
    awk '/^_env_strip_value\(\) \{/,/^\}/' "$1"
}

# Positive pin: the two bodies are byte-identical today.
if [ "$(_env_strip_value_body "$LAUNCHER")" = "$(_env_strip_value_body "$REPO_ROOT/scripts/watchdog-watcher.sh")" ]; then
    _pass
else
    _fail "F4 _env_strip_value byte-identical (launcher.sh vs scripts/watchdog-watcher.sh)" \
        "identical bodies" "bodies differ"
fi
assert_eq "F4 _env_strip_value byte-parity (launcher.sh vs scripts/watchdog-watcher.sh)" \
    "$(_env_strip_value_body "$LAUNCHER")" \
    "$(_env_strip_value_body "$REPO_ROOT/scripts/watchdog-watcher.sh")"

# Non-vacuity (negative): perturb a /tmp COPY of one side -- the SAME
# comparison must go RED. The real files are NEVER written.
cp "$REPO_ROOT/scripts/watchdog-watcher.sh" "$LP_TEST_DIR/watchdog-perturbed.sh"
python3 - "$LP_TEST_DIR/watchdog-perturbed.sh" <<'PYEOF'
import sys
p = sys.argv[1]
s = open(p).read()
needle = "printf '%s' \"$v\""
assert needle in s, "perturbation target not found"
s = s.replace(needle, "printf '%s' \"$v\" ", 1)
open(p, "w").write(s)
PYEOF
if [ "$(_env_strip_value_body "$LAUNCHER")" = "$(_env_strip_value_body "$LP_TEST_DIR/watchdog-perturbed.sh")" ]; then
    _fail "F4 parity pin is not vacuous (a perturbed side must be CAUGHT)" \
        "bodies differ" "bodies still identical -- pin is vacuous"
else
    _pass
fi

# ─── cleanup ────────────────────────────────────────────────────────────────
rm -rf "$ENV_TEST_DIR" "$STATE_TEST_DIR" "$JS_TEST_DIR" \
       "$RB_TEST_DIR" "$RB_TEST_DIR2" "$RB_TEST_DIR3" "$RB_TEST_DIR4" \
       "$RB_TEST_DIR5" "$LP_TEST_DIR" "$JSF_TEST_DIR" "$JSF_PERF_DIR" \
       "$JSF_NOAWK_DIR" "$JSF_FAKE_DIR" "$JSF_WEDGE_DIR" \
       "$JSF_DECIDE_WEDGE_DIR" 2>/dev/null

# ─── summary ────────────────────────────────────────────────────────────────
printf '\n========================================\n'
printf 'launcher tests: %d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
if [ "$FAIL" -gt 0 ]; then
    printf 'failed:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf 'ALL PASS\n'
exit 0
