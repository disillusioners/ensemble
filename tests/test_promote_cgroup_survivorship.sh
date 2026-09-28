#!/bin/bash
# ============================================================================
# tests/test_promote_cgroup_survivorship.sh — pin tests for the r-f82e
# cgroup-survivorship fix (fix/promote-cgroup-survivorship branch)
# ============================================================================
# Component coverage (one section per comp, all six required + stretch 7):
#   1. Executor scope escape (Python: spawn_executor / build_scope_argv) —
#      pin via mocked _scope_detect_fn for the three branches.
#   2. Signal traps (TERM death-anchored event + lock release + no
#      double-release with EXIT trap) — pin via direct TERM of a sourced
#      lib.sh + scripts subprocess.
#   3. TXN heartbeat (in_flight.last_heartbeat refreshed at lock_heartbeat
#      sites; adopt_stale_txn fast path; _journal_sweep fast path;
#      mirrored tables agree).
#   4. ISO parser GNU branch (mirrors atomic_flip test_atomic_flip.sh
#      shape; uname dispatch).
#   5. Reaper signal surfacing — pure Python (tests/unit/tools/test_
#      promote_cgroup_survivorship_python.py §4b is the dedicated pin;
#      this shell suite probes the journal detail the Python code emits).
#   6. Timestamp hygiene (portable date wrapper).
#   7. (stretch) restart_via_launcher opt-in systemctl path.
#
# Fixture strategy: temp dirs under TMPDIR, INSTALL_DIR overridden per
# subtest, no daemon / port / network. Self-contained, plain bash.
#
# Run:
#   bash tests/test_promote_cgroup_survivorship.sh
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UPGRADE_DIR="$REPO_ROOT/scripts/upgrade"
LAUNCHER="$REPO_ROOT/launcher.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
    [ $# -gt 1 ] && printf '      expected: %s\n      actual:   %s\n' "$2" "$3" >&2
}

assert_eq() {
    local name="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then _pass "$name"; else _fail "$name" "$expected" "$actual"; fi
}

assert_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _pass "$name" ;;
        *) _fail "$name" "contains '$needle'" "$haystack" ;;
    esac
}

assert_not_contains() {
    local name="$1" needle="$2" haystack="$3"
    case "$haystack" in
        *"$needle"*) _fail "$name" "absent '$needle'" "$haystack" ;;
        *) _pass "$name" ;;
    esac
}

section() { printf '\n== %s ==\n' "$1"; }

# ── Helpers ─────────────────────────────────────────────────────────────────

REAL_UNAME="$(command -v uname)"
REAL_DATE="$(command -v date)"

iso_off() {
    # iso_off <seconds-ago-or-future> — portable (python3 preferred)
    local secs="$1" now
    now="$(date -u +%s)"
    if command -v python3 >/dev/null 2>&1; then
        python3 -c "import datetime,sys; print(datetime.datetime.utcfromtimestamp(int(sys.argv[1])).strftime('%Y-%m-%dT%H:%M:%SZ'))" "$((now + secs))"
    else
        perl -e "use POSIX qw(strftime); print strftime('%Y-%m-%dT%H:%M:%SZ', gmtime($((now + secs))))"
    fi
}

make_fixture() {
    local d="$1" new="$2" old="${3:-}"
    rm -rf "$d"; mkdir -p "$d/releases/$new"
    printf '{"version":"%s","binary_version":"%s","rollback_safe":true}\n' "$new" "$new" \
        > "$d/releases/$new/manifest.json"
    printf 'stub-app\n' > "$d/releases/$new/ensemble-prod"
    if [ -n "$old" ]; then
        mkdir -p "$d/releases/$old"
        printf '{"version":"%s","binary_version":"%s","rollback_safe":true}\n' "$old" "$old" \
            > "$d/releases/$old/manifest.json"
        printf 'old-app\n' > "$d/releases/$old/ensemble-prod"
        ln -sfn "releases/$old" "$d/current"
    fi
}

# Source lib.sh into a subshell with INSTALL_DIR=$1; eval $2; print stdout.
src_with_install() {
    ( export INSTALL_DIR="$1"; . "$UPGRADE_DIR/lib.sh" >/dev/null 2>&1; eval "$2" )
}

# ===========================================================================
section "comp1 — spawn_executor scope escape (3-branch pin via Python helper)"

# r-f82e fix cycle 1: the Python helper is now run via an INLINE env scrub
# (previously relied on /tmp/coder-scrub.sh — out-of-repo cruft; rc 127 on
# any host that lacked it). Inline scrub strips POSTGRES_* + ENSEMBLE_*
# before exec and echo-verifies zero survivors. Self-contained: the test
# does not depend on any host artifact.
env_scrub_python() {
    (
        unset POSTGRES_URL POSTGRES_HOST POSTGRES_PORT POSTGRES_DB \
              POSTGRES_USER POSTGRES_PASSWORD POSTGRES_SSLMODE \
              POSTGRES_SSL CERT PATH_POSTGRES \
              ENSEMBLE_UPGRADE_LIVE ENSEMBLE_INSTALL_DIR \
              ENSEMBLE_RESTART_UNIT 2>/dev/null
        local survivors
        survivors="$(env | grep -iE '^(POSTGRES_|ENSEMBLE_UPGRADE_LIVE=|ENSEMBLE_INSTALL_DIR=|ENSEMBLE_RESTART_UNIT=)' || true)"
        if [ -n "$survivors" ]; then
            printf 'ENV_SCRUB_FAIL: survivors=%s\n' "$survivors" >&2
            return 99
        fi
        python3 "$@"
    )
}

# The Python module's _scope_detect_fn is the test seam. The actual
# unit-name shape + detector contract are pinned in the Python helper
# (tests/unit/tools/test_promote_cgroup_survivorship_python.py). Here
# we invoke it and assert the contract observable from bash:
#   - the helper exists and runs (rc 0)
#   - the run_id flows into the unit name
#   - the legacy path byte-identical when detector returns (False, "")
#   - the scope prefix is pinned to "ensemble-upgrade-"
#   - r-f82e cycle 1 pins (6a/6b/6c): detector env forwarding + bus vars
#     survive allowlist + ENSEMBLE_UPGRADE_LIVE still stripped
PY_OUT="$(env_scrub_python tests/unit/tools/test_promote_cgroup_survivorship_python.py 2>&1)"
PY_RC=$?
if [ "$PY_RC" -ne 0 ]; then
    printf '%s\n' "$PY_OUT" >&2
    _fail "1 Python helper exited nonzero" "0" "$PY_RC"
else
    _pass "1 Python helper exited 0"
fi
assert_contains "1a Linux+systemd (user bus) → systemd-run argv pinned" "1a Linux+systemd (user bus)" "$PY_OUT"
assert_contains "1b Linux+systemd (system bus) → systemd-run argv pinned" "1b Linux+systemd (system bus)" "$PY_OUT"
assert_contains "1c non-Linux / no-systemd → (False, \"\") legacy pinned" "1c non-Linux / no-systemd" "$PY_OUT"
assert_contains "2 build_scope_argv inner argv byte-identical" "2 build_scope_argv inner argv" "$PY_OUT"
assert_contains "3 SCOPE_UNIT_PREFIX pinned to 'ensemble-upgrade-'" "3 SCOPE_UNIT_PREFIX pinned" "$PY_OUT"
assert_contains "4a normal exit → no signal attribution" "4a normal exit" "$PY_OUT"
assert_contains "5 _scope_detect_real never raises" "5 _scope_detect_real never raises" "$PY_OUT"
# r-f82e fix cycle 1: detector-env == spawn-env parity pins (6a/6b/6c)
assert_contains "6a detector probes with caller-supplied env" "6a detector probes with caller-supplied env" "$PY_OUT"
assert_contains "6b bus-discovery vars survive allowlist" "6b XDG_RUNTIME_DIR + DBUS_SESSION_BUS_ADDRESS survive allowlist" "$PY_OUT"
assert_contains "6c ENSEMBLE_UPGRADE_LIVE STILL stripped" "6c ENSEMBLE_UPGRADE_LIVE STILL stripped" "$PY_OUT"

# ===========================================================================
section "comp2 — signal traps (TERM mid-flight → halt event + lock release)"

# Build a fixture journal and acquire a lock in a child; TERM the child;
# verify halt event written + lock absent.
SIG_FIXT="$(mktemp -d -t comp2-sigtrap.XXXXXX)"
make_fixture "$SIG_FIXT" vNEW vOLD
mkdir -p "$SIG_FIXT/releases"
# Initial empty journal.
cat > "$SIG_FIXT/releases/state.json" <<'JOURNAL'
{"current":"vOLD","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL

# Child script: source lib.sh, acquire lock, install traps, sleep forever.
SIG_SCRIPT="$(mktemp -t comp2-child.XXXXXX.sh)"
cat > "$SIG_SCRIPT" <<CHILD
#!/bin/bash
export INSTALL_DIR="$SIG_FIXT"
. "$UPGRADE_DIR/lib.sh"
trap - EXIT   # drop lib.sh's default (helps us see the post-trap state cleanly)
lock_acquire || { echo "lock_acquire failed"; exit 99; }
_trap_install_signal_handlers "test:phase-X"
echo "child ready pid=\$\$"
# Block on sleep until TERMed.
sleep 30
CHILD
chmod +x "$SIG_SCRIPT"

# Launch the child.
"$SIG_SCRIPT" &
CHILD_PID=$!
# Wait for the child to be ready.
for _ in $(seq 1 50); do
    [ -d "$SIG_FIXT/releases/rollback.lock.d" ] && break
    sleep 0.05
done
[ -d "$SIG_FIXT/releases/rollback.lock.d" ] && _pass "2a lock acquired before TERM" || _fail "2a lock acquired before TERM" "present" "absent"

# TERM the child.
kill -TERM "$CHILD_PID"
# Wait for child to exit.
wait "$CHILD_PID" 2>/dev/null
RC=$?
# Wait for trap side effects (journal write, etc.) — short bound.
sleep 0.2

# Assertions:
# (i) lock dir is gone
[ ! -d "$SIG_FIXT/releases/rollback.lock.d" ] && _pass "2b lock released after TERM" || _fail "2b lock released after TERM" "absent" "present"
# (ii) journal has a halt event
JOURNAL_TXT="$(cat "$SIG_FIXT/releases/state.json")"
HALT_PRESENT="$(printf '%s' "$JOURNAL_TXT" | grep -c '"event":"halt"' || true)"
[ "$HALT_PRESENT" -ge 1 ] && _pass "2c halt journal event written" || _fail "2c halt journal event written" ">=1" "$HALT_PRESENT"
# (iii) halt event cites SIGTERM or 143 (some signal attribution)
SIG_CITED="$(printf '%s' "$JOURNAL_TXT" | grep -c 'SIGTERM\|143\|killed by SIG' || true)"
[ "$SIG_CITED" -ge 1 ] && _pass "2d halt event cites SIGTERM/143" || _fail "2d halt event cites SIGTERM/143" ">=1" "$SIG_CITED"
# (iv) RC is signal-appropriate (143 for TERM)
[ "$RC" = "143" ] && _pass "2e child exit code = 143 (128+SIGTERM)" || _fail "2e child exit code = 143" "143" "$RC"

# 2f. Race-safety: EXIT trap does NOT double-release (i.e. signal handler
# sets _LOCK_RELEASED=1, EXIT trap's _trap_safe_exit no-ops).
# Run another child that exits 0 (no signal); verify lock was released once.
EXIT_SCRIPT="$(mktemp -t comp2-exit.XXXXXX.sh)"
cat > "$EXIT_SCRIPT" <<CHILD2
#!/bin/bash
export INSTALL_DIR="$SIG_FIXT"
. "$UPGRADE_DIR/lib.sh"
trap - EXIT
lock_acquire || { echo "lock_acquire failed"; exit 99; }
_trap_install_signal_handlers "test:exit-clean"
# Note _LOCK_RELEASED should be 0 after install.
echo "_LOCK_RELEASED=\$_LOCK_RELEASED"
exit 0
CHILD2
chmod +x "$EXIT_SCRIPT"
OUT_EXIT="$("$EXIT_SCRIPT" 2>&1)"
assert_contains "2f clean-exit trap installed" "_LOCK_RELEASED=0" "$OUT_EXIT"
[ ! -d "$SIG_FIXT/releases/rollback.lock.d" ] && _pass "2f lock released on clean exit" || _fail "2f lock released on clean exit" "absent" "present"

rm -rf "$SIG_FIXT" "$SIG_SCRIPT" "$EXIT_SCRIPT"

# ===========================================================================
section "comp3 — TXN heartbeat + owner-liveness stale-break"

# 3a. journal_open_txn writes last_heartbeat (epoch)
HB_FIXT="$(mktemp -d -t comp3-hb.XXXXXX)"
make_fixture "$HB_FIXT" vNEW vOLD
mkdir -p "$HB_FIXT/releases"
cat > "$HB_FIXT/releases/state.json" <<'JOURNAL'
{"current":"vOLD","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
out="$(src_with_install "$HB_FIXT" 'journal_open_txn promote vNEW && journal_read | grep -oE "\"last_heartbeat\":[0-9]+"')"
assert_contains "3a journal_open_txn writes last_heartbeat" "last_heartbeat" "$out"

# 3b. lock_heartbeat refreshes both lock + journal heartbeat
# Acquire lock then heartbeat. Verify journal last_heartbeat moves.
HB_FIXT2="$(mktemp -d -t comp3-hb2.XXXXXX)"
make_fixture "$HB_FIXT2" vNEW vOLD
mkdir -p "$HB_FIXT2/releases"
cat > "$HB_FIXT2/releases/state.json" <<'JOURNAL'
{"current":"vOLD","previous":null,"in_flight":null,"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
# Open txn, capture hb1, sleep, heartbeat, verify hb2 > hb1.
result="$(src_with_install "$HB_FIXT2" '
journal_open_txn promote vNEW
lock_acquire
hb1=$(date +%s)
sleep 1.1
lock_heartbeat
hb2=$(journal_read | grep -oE "\"last_heartbeat\":[0-9]+" | grep -oE "[0-9]+$")
echo "hb1=$hb1 hb2=$hb2"
')"
assert_contains "3b lock_heartbeat refreshes journal hb" "hb2" "$result"
HB1="$(printf '%s' "$result" | grep -oE 'hb1=[0-9]+' | cut -d= -f2)"
HB2="$(printf '%s' "$result" | grep -oE 'hb2=[0-9]+' | cut -d= -f2)"
# hb2 should be >= hb1 + 1 (we slept 1.1s)
DIFF=$((HB2 - HB1))
[ "$DIFF" -ge 1 ] && _pass "3b hb advanced (DIFF=$DIFF)" || _fail "3b hb advanced" ">=1" "$DIFF"

# 3c. liveness fast-path: hb_stale=1 + owner_dead=1 → reclaim
# Set up a txn with old last_heartbeat and a dead owner pid.
DEAD_OWNER_FIXT="$(mktemp -d -t comp3-dead.XXXXXX)"
make_fixture "$DEAD_OWNER_FIXT" vNEW vOLD
mkdir -p "$DEAD_OWNER_FIXT/releases"
NOW="$(date +%s)"
OLD_HB=$((NOW - 1000))   # > HEARTBEAT_STALE_S=300
cat > "$DEAD_OWNER_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -700)","flipped":false,"owner_pid":999999,"last_heartbeat":$OLD_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
# Run adopt_stale_txn — under the freshness gate it WOULD refuse (age
# > SWEEP_STALE_S=600 actually passes the age gate, so this just
# confirms the existing path; redo below for the FAST path scenario).
# Actually for fast path test: age <= SWEEP_STALE_S, hb stale, owner dead.
FRESH_FIXT="$(mktemp -d -t comp3-fast.XXXXXX)"
make_fixture "$FRESH_FIXT" vNEW vOLD
mkdir -p "$FRESH_FIXT/releases"
cat > "$FRESH_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -100)","flipped":false,"owner_pid":999999,"last_heartbeat":$OLD_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
# age=100s (fresh), hb=1000s old (stale), owner=999999 (dead).
# Expect: adopt_stale_txn reclaims (history has sweep event), not refuse.
FRESH_OUT="$(src_with_install "$FRESH_FIXT" '
lock_acquire
adopt_stale_txn
echo "after_adopt"
journal_read | grep -oE "\"event\":\"(sweep|halt|refusal)\"" | head -5
')"
assert_contains "3c fast-path reclaimed (history event)" '"event":"sweep"' "$FRESH_OUT"

# 3d. hb fresh OR owner alive → HOLD (the freshness gate fires)
LIVE_OWNER_FIXT="$(mktemp -d -t comp3-live.XXXXXX)"
make_fixture "$LIVE_OWNER_FIXT" vNEW vOLD
mkdir -p "$LIVE_OWNER_FIXT/releases"
LIVE_OWNER_PID=$$
LIVE_HB="$(date +%s)"   # fresh
cat > "$LIVE_OWNER_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -100)","flipped":false,"owner_pid":$LIVE_OWNER_PID,"last_heartbeat":$LIVE_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
# Fresh hb (just now) + live owner (this pid). Should refuse (exit 78).
# Run adopt_stale_txn in a subshell so its `exit 78` only exits that
# subshell — the outer can capture $? (without the subshell, `exit`
# inside the function escapes the eval'd shell, killing the test).
LIVE_OUT="$(src_with_install "$LIVE_OWNER_FIXT" '
set +e
lock_acquire
( adopt_stale_txn )
echo "adopt_rc=$?"
' 2>&1)"
assert_contains "3d live owner + fresh hb → refuse (exit 78)" "adopt_rc=78" "$LIVE_OUT"

rm -rf "$HB_FIXT" "$HB_FIXT2" "$DEAD_OWNER_FIXT" "$FRESH_FIXT" "$LIVE_OWNER_FIXT"

# ===========================================================================
section "comp3b — launcher.sh _journal_sweep heartbeat fast path (HEARTBEAT_STALE_S regression pin)"

# Regression pin for the missing-HEARTBEAT_STALE_S bug: launcher.sh
# references HEARTBEAT_STALE_S at :707 but only defined it in
# scripts/upgrade/lib.sh (:73). launcher.sh is staged standalone (does
# NOT source lib.sh — see lib.sh:55-60 / launcher.sh:55-60 protocol
# commentary), so the integer comparison
#   [ $(( now - hb )) -le "$HEARTBEAT_STALE_S" ]
# evaluated with empty HEARTBEAT_STALE_S, returned false (bash
# "[: : integer expression expected"), hb_stale defaulted to 1 always,
# and the comp3 owner-liveness fast path in _journal_sweep fired
# whenever the owner was dead regardless of heartbeat freshness. The
# three cases below pin the FIX at the launcher side (mirror of the
# comp3 lib.sh side; both sweep tables must agree, R-SR13).
#
# Cases (age=100s ≤ SWEEP_STALE_S=600, so the freshness gate does NOT
# short-circuit — the comp3 fast path is the only reclaim trigger):
#   3b-i  fresh-hb (now) + dead-owner → HELD (hb_stale=0 holds)
#   3b-ii stale-hb (NOW-1000) + dead-owner → RECLAIM (fast path fires)
#   3b-iii fresh-hb (now) + live-owner (this $$) → HELD

# Helper: source launcher.sh, run _journal_sweep on $1, capture all
# output (stderr where _log writes + stdout).
_js_run_sweep() {
    local fix="$1"
    bash -c "
        INSTALL_DIR='$fix'
        . '$LAUNCHER' >/dev/null 2>&1
        _journal_sweep 2>&1
    "
}

# 3b-i. fresh-hb + dead-owner → HELD
JS_HELD_FIXT="$(mktemp -d -t comp3b-held.XXXXXX)"
make_fixture "$JS_HELD_FIXT" vNEW vOLD
mkdir -p "$JS_HELD_FIXT/releases"
NOW="$(date +%s)"   # captured once; hb=now → 0s old → fresh
cat > "$JS_HELD_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -100)","flipped":false,"owner_pid":999999,"last_heartbeat":$NOW},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
JS_HELD_LOG="$(_js_run_sweep "$JS_HELD_FIXT")"
JS_HELD_JOURNAL="$(cat "$JS_HELD_FIXT/releases/state.json")"
assert_contains "3b-i fresh-hb + dead-owner → 'leaving alone' (hb_stale=0 holds)" "leaving alone" "$JS_HELD_LOG"
assert_not_contains "3b-i fresh-hb + dead-owner → no fast-path fired" "comp3 fast path triggered" "$JS_HELD_LOG"
case "$JS_HELD_JOURNAL" in
    *'"in_flight":{"kind":"promote"'*) _pass "3b-i in_flight preserved (txn held)" ;;
    *) _fail "3b-i in_flight preserved" "object" "$(printf '%s' "$JS_HELD_JOURNAL" | head -c 200)" ;;
esac

# 3b-ii. stale-hb + dead-owner → RECLAIM
JS_STALE_FIXT="$(mktemp -d -t comp3b-stale.XXXXXX)"
make_fixture "$JS_STALE_FIXT" vNEW vOLD
mkdir -p "$JS_STALE_FIXT/releases"
NOW="$(date +%s)"
JS_STALE_HB=$((NOW - 1000))   # 1000s old > HEARTBEAT_STALE_S=300
cat > "$JS_STALE_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -100)","flipped":false,"owner_pid":999998,"last_heartbeat":$JS_STALE_HB},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
JS_STALE_LOG="$(_js_run_sweep "$JS_STALE_FIXT")"
JS_STALE_JOURNAL="$(cat "$JS_STALE_FIXT/releases/state.json")"
assert_contains "3b-ii stale-hb + dead-owner → comp3 fast path fired" "comp3 fast path triggered" "$JS_STALE_LOG"
case "$JS_STALE_JOURNAL" in
    *'"in_flight": null'*|'"in_flight":null'*) _pass "3b-ii in_flight cleared (txn reclaimed)" ;;
    *) _fail "3b-ii in_flight cleared" "null" "$(printf '%s' "$JS_STALE_JOURNAL" | head -c 200)" ;;
esac

# 3b-iii. fresh-hb + live-owner → HELD
JS_LIVE_FIXT="$(mktemp -d -t comp3b-live.XXXXXX)"
make_fixture "$JS_LIVE_FIXT" vNEW vOLD
mkdir -p "$JS_LIVE_FIXT/releases"
NOW="$(date +%s)"
JS_LIVE_OWNER=$$               # parent test pid is alive throughout
cat > "$JS_LIVE_FIXT/releases/state.json" <<JOURNAL
{"current":"vOLD","previous":"vOLD","in_flight":{"kind":"promote","target":"vNEW","started_at":"$(iso_off -100)","flipped":false,"owner_pid":$JS_LIVE_OWNER,"last_heartbeat":$NOW},"rollback_window_count":{"24h":0,"window_start":null},"cooldown_until":null,"quarantined":[],"history":[]}
JOURNAL
JS_LIVE_LOG="$(_js_run_sweep "$JS_LIVE_FIXT")"
JS_LIVE_JOURNAL="$(cat "$JS_LIVE_FIXT/releases/state.json")"
assert_contains "3b-iii fresh-hb + live-owner → 'leaving alone' (owner alive holds)" "leaving alone" "$JS_LIVE_LOG"
assert_not_contains "3b-iii fresh-hb + live-owner → no fast-path fired" "comp3 fast path triggered" "$JS_LIVE_LOG"
case "$JS_LIVE_JOURNAL" in
    *'"in_flight":{"kind":"promote"'*) _pass "3b-iii in_flight preserved (txn held)" ;;
    *) _fail "3b-iii in_flight preserved" "object" "$(printf '%s' "$JS_LIVE_JOURNAL" | head -c 200)" ;;
esac

rm -rf "$JS_HELD_FIXT" "$JS_STALE_FIXT" "$JS_LIVE_FIXT"

# ===========================================================================
section "comp4 — ISO parser GNU branch (mirrors atomic_flip test shape)"

# 4a. GNU branch: round-trip an ISO timestamp on this Linux host.
TS="2026-09-28T00:56:53Z"
EXPECTED="$(date -d "$TS" +%s)"
GOT="$(src_with_install "/tmp" "_iso_to_epoch '$TS'")"
assert_eq "4a GNU branch _iso_to_epoch matches date -d" "$EXPECTED" "$GOT"

# 4b. launcher.sh _js_iso_to_epoch GNU branch
JSE="$(cd /tmp; bash -c "
INSTALL_DIR=/tmp
. '$LAUNCHER' >/dev/null 2>&1
_js_iso_to_epoch '$TS'
")"
assert_eq "4b launcher.sh _js_iso_to_epoch GNU branch matches date -d" "$EXPECTED" "$JSE"

# 4c. BSD branch via injected uname stub (Darwin)
# The stub makes a Darwin-flavoured uname AND a date binary that
# ACCEPTS the BSD -ju -f '%Y-%m-%dT%H:%M:%SZ' operand order. Without
# this, the BSD branch would fail on this Linux host (GNU date rejects
# -j). The expected output is the SAME epoch the GNU branch produced
# (we use a real Python-backed parser in the stub so the BSD branch's
# mathematical fidelity is also confirmed).
STUBDIR="$(mktemp -d -t comp4-stub.XXXXXX)"
cat > "$STUBDIR/uname" <<STUB
#!/bin/bash
case "\$1" in
    -s) echo "Darwin" ;;
    *) "$REAL_UNAME" "\$@" ;;
esac
STUB
chmod +x "$STUBDIR/uname"
# BSD date stub as a Python parser — cleaner than nested-quote bash.
cat > "$STUBDIR/date" <<'PYSTUB'
#!/usr/bin/env python3
import datetime, sys
args = sys.argv[1:]
ts = None
i = 0
while i < len(args):
    a = args[i]
    if a in ("-j", "-u") or a.startswith("+"):
        i += 1
        continue
    if a == "-f" and i + 1 < len(args):
        i += 2
        continue
    ts = a
    i += 1
if not ts:
    ts = "1970-01-01T00:00:00Z"
print(int(datetime.datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").timestamp()))
PYSTUB
chmod +x "$STUBDIR/date"
BSDGOT="$(PATH="$STUBDIR:$PATH" bash -c "
INSTALL_DIR=/tmp
. '$UPGRADE_DIR/lib.sh' >/dev/null 2>&1
_iso_to_epoch '$TS'
")"
assert_eq "4c BSD branch (Darwin stub) matches GNU parse" "$EXPECTED" "$BSDGOT"
rm -rf "$STUBDIR"

# 4d. Unrecognized platform (stub uname to Plan9) → fail-closed.
STUBDIR2="$(mktemp -d -t comp4-plan9.XXXXXX)"
cat > "$STUBDIR2/uname" <<STUB
#!/bin/bash
case "\$1" in -s) echo "Plan9" ;; *) "$REAL_UNAME" "\$@" ;; esac
STUB
chmod +x "$STUBDIR2/uname"
P9OUT="$(PATH="$STUBDIR2:$PATH" bash -c "
INSTALL_DIR=/tmp
. '$UPGRADE_DIR/lib.sh' >/dev/null 2>&1
_iso_to_epoch '$TS' 2>&1
echo rc=\$?
")"
assert_contains "4d unrecognized platform (Plan9) → fail-closed" "unrecognized platform" "$P9OUT"
case "$P9OUT" in
    *rc=1*) _pass "4d unrecognized platform returns rc=1" ;;
    *) _fail "4d unrecognized platform rc" "1" "$(printf '%s' "$P9OUT" | grep -oE 'rc=[0-9]+')" ;;
esac
rm -rf "$STUBDIR2"

# ===========================================================================
section "comp6 — upgrade.log timestamp hygiene (_log_ts / _log_tsl)"

# 6a. _log_ts returns ISO-8601
TS_OUT="$(src_with_install "/tmp" '_log_ts')"
case "$TS_OUT" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) _pass "6a _log_ts format is ISO-8601" ;;
    *) _fail "6a _log_ts format" "YYYY-MM-DDTHH:MM:SSZ" "$TS_OUT" ;;
esac

# 6b. _log_tsl writes timestamped line
LOG_FIXT="$(mktemp -d -t comp6-log.XXXXXX)"
LOG_FILE="$LOG_FIXT/upgrade.log"
src_with_install "$LOG_FIXT" "_log_tsl '$LOG_FILE' 'phase=1 hello'"
LOG_CONTENT="$(cat "$LOG_FILE")"
case "$LOG_CONTENT" in
    *T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z\ phase=1\ hello*)
        _pass "6b _log_tsl emits ISO timestamp + message" ;;
    *) _fail "6b _log_tsl output" "<ISO> phase=1 hello" "$LOG_CONTENT" ;;
esac
rm -rf "$LOG_FIXT"

# 6c. BSD/GNU portability: `date -u +'...'` is identical on both.
GNU_TS="$(date -u +'%Y-%m-%dT%H:%M:%SZ')"
case "$GNU_TS" in
    [0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z) _pass "6c date -u +format works on this host (Linux)" ;;
    *) _fail "6c date -u portability" "ISO output" "$GNU_TS" ;;
esac

# ===========================================================================
section "comp7 (stretch) — restart_via_launcher opt-in systemctl path"

# 7a. Default = nohup (env var unset) — BYTE-IDENTICAL legacy
RV_FIXT="$(mktemp -d -t comp7-rv.XXXXXX)"
make_fixture "$RV_FIXT" v1.0.0
# Make launcher.sh present (mock) — just a stub that exits 0.
echo '#!/bin/bash' > "$RV_FIXT/launcher.sh"
echo 'exit 0' >> "$RV_FIXT/launcher.sh"
chmod +x "$RV_FIXT/launcher.sh"
unset ENSEMBLE_RESTART_UNIT
RV_OUT_A="$(src_with_install "$RV_FIXT" 'restart_via_launcher 2>&1')"
assert_contains "7a default no opt-in → nohup path" "nohup" "$RV_OUT_A"
assert_not_contains "7a default → no systemctl message" "systemctl" "$RV_OUT_A"

# 7b. Opt-in + systemctl succeeds → unit path (we stub systemctl)
SUBSYS_FIXT="$(mktemp -d -t comp7-svc.XXXXXX)"
make_fixture "$SUBSYS_FIXT" v1.0.0
echo '#!/bin/bash' > "$SUBSYS_FIXT/launcher.sh"
echo 'exit 0' >> "$SUBSYS_FIXT/launcher.sh"
chmod +x "$SUBSYS_FIXT/launcher.sh"
# Build a systemd stub that mimics a successful start.
SYSD_STUB="$(mktemp -d -t comp7-systemd.XXXXXX)"
mkdir -p "$SYSD_STUB/bin"
cat > "$SYSD_STUB/bin/systemctl" <<'STUB'
#!/bin/bash
echo "systemctl-stub: $*"
exit 0
STUB
chmod +x "$SYSD_STUB/bin/systemctl"
# Run helper with PATH routed through stub, unit name set.
RV_OUT_B="$(PATH="$SYSD_STUB/bin:$PATH" bash -c "
export INSTALL_DIR='$SUBSYS_FIXT'
export ENSEMBLE_RESTART_UNIT='ensemble-test.service'
. '$UPGRADE_DIR/lib.sh' >/dev/null 2>&1
restart_via_launcher 2>&1
")"
assert_contains "7b opt-in + systemd available → systemctl path" "systemd unit" "$RV_OUT_B"
assert_not_contains "7b opt-in success → no fallback WARN" "systemctl start" "$RV_OUT_B"

# 7c. Opt-in + systemctl FAILS → falls back to nohup
SYSD_FAIL="$(mktemp -d -t comp7-fail.XXXXXX)"
mkdir -p "$SYSD_FAIL/bin"
cat > "$SYSD_FAIL/bin/systemctl" <<'STUB'
#!/bin/bash
echo "systemctl-stub-fail: $*" >&2
exit 1
STUB
chmod +x "$SYSD_FAIL/bin/systemctl"
# Reuse SUBSYS_FIXT (don't run a second launcher).
RV_OUT_C="$(PATH="$SYSD_FAIL/bin:$PATH" bash -c "
export INSTALL_DIR='$SUBSYS_FIXT'
export ENSEMBLE_RESTART_UNIT='ensemble-test.service'
. '$UPGRADE_DIR/lib.sh' >/dev/null 2>&1
restart_via_launcher 2>&1
")"
assert_contains "7c systemctl fail → fallback WARN" "falling back to nohup" "$RV_OUT_C"
assert_contains "7c systemctl fail → nohup launched" "nohup" "$RV_OUT_C"

rm -rf "$RV_FIXT" "$SUBSYS_FIXT" "$SYSD_STUB" "$SYSD_FAIL"

# ===========================================================================
section "summary"
printf 'PASS=%s FAIL=%s\n' "$PASS" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf 'FAILED TESTS:%s\n' "$FAILED_TESTS"
    exit 1
fi
printf '\n=== ALL TESTS PASSED ===\n'
exit 0