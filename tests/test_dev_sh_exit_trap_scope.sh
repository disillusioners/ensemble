#!/bin/bash
# ============================================================================
# tests/test_dev_sh_exit_trap_scope.sh — dev.sh EXIT-trap kill-set scope pin
# (fix/dev-exit-trap-pid-exact, 2026-10-20)
# ============================================================================
# Pins the invariant from the 2026-10-20 live-collateral incident
# (.agents/tester/LESSONS/2026-10-20-devsh-exit-trap-live-collateral.md):
#
#   A cleanup trap must NEVER match processes this boot did not create.
#   dev.sh's EXIT trap kills ONLY the pid recorded in
#   $DATA_DIR/vscode-server.pid (pid-exact), and contains NO pattern-based
#   kill — the old `pkill -f "code-server.*--bind-addr"` fallback SIGTERMed
#   the LIVE install's code-server on every dev boot whose pid-file branch
#   missed (it missed on EVERY real boot: the manager writes JSON and the
#   old trap kill -0 -ed the raw JSON string).
#
# Coverage:
#   (a) FUNCTIONAL — no pid file: a process whose cmdline matches the old
#       hazard pattern (`code-server ... --bind-addr ...`) SURVIVES cleanup
#       (pre-fix code killed it via the pkill fallback)
#   (b) FUNCTIONAL — JSON pid file (manager format): the recorded pid IS
#       killed (pre-fix code failed this: cat + kill -0 on raw JSON missed,
#       then fell through to pkill)
#   (c) FUNCTIONAL — stale pid in JSON file: cleanup does nothing and the
#       pattern-named process SURVIVES (the exact incident trigger shape)
#   (d) STATIC — zero `pkill`/`killall` anywhere in dev.sh (live-pattern
#       safety is absolute in this script)
#   (e) STATIC — the cleanup function still parses the pid file, kills via
#       the recorded-pid variable (`kill "$pid"`), and is still wired to
#       EXIT INT TERM
#   (f) `bash -n dev.sh` syntax gate
#
# Fixture strategy: everything under mktemp scratch; HOME fenced into a
# FAKE_HOME mktemp dir with an escape guard per the 2026-10-12 live-path-
# hazard convention (test scripts must NEVER resolve live-shaped paths).
# dev.sh is NEVER executed — the cleanup function is extracted with sed and
# sourced in the sandbox. rm operates ONLY on the two mktemp scratch dirs,
# after the escape guard verified them. No daemon, no ports, no network.
#
# Run:
#   bash tests/test_dev_sh_exit_trap_scope.sh
#   bash tests/test_dev_sh_exit_trap_scope.sh /path/to/candidate/dev.sh
#     (optional arg: check a candidate dev.sh — used for the mutation-
#      verified regression proof against the pre-fix version)
# ============================================================================

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEV_SH="${1:-$REPO_ROOT/dev.sh}"

PASS=0
FAIL=0
ok()  { PASS=$((PASS + 1)); printf '  PASS: %s\n' "$*"; }
bad() { FAIL=$((FAIL + 1)); printf '  FAIL: %s\n' "$*"; }

# --- live-path fence: mktemp FAKE_HOME + escape guard (2026-10-12 rule) ----
FAKE_HOME="$(mktemp -d "${TMPDIR:-/tmp}/devsh-trap-guard-home.XXXXXX")"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/devsh-trap-guard.XXXXXX")"
export HOME="$FAKE_HOME"
escape_guard_ok=1
for p in "$FAKE_HOME" "$SCRATCH"; do
    case "$p" in
        */agents-ensemble|*/agents-ensemble/*|*/ensemble-src*) escape_guard_ok=0 ;;
    esac
done
if [ "$escape_guard_ok" -ne 1 ]; then
    echo "FATAL: scratch resolved live-shaped — refusing to run" >&2
    exit 78
fi
guard_cleanup() {
    rm -rf "$SCRATCH" "$FAKE_HOME"
}
trap guard_cleanup EXIT

if [ ! -f "$DEV_SH" ]; then
    echo "FATAL: dev.sh not found at $DEV_SH" >&2
    exit 1
fi

# --- helpers ---------------------------------------------------------------
# Extract the cleanup() function body WITHOUT executing dev.sh.
extract_cleanup() {
    sed -n '/^cleanup() {/,/^}/p' "$DEV_SH"
}
EXTRACTED="$(extract_cleanup)"
if [ -z "$EXTRACTED" ]; then
    echo "FATAL: could not extract cleanup() from $DEV_SH" >&2
    exit 1
fi
# Define the extracted function in THIS shell (sandbox: dev.sh is never run).
eval "$EXTRACTED"
if ! declare -F cleanup >/dev/null 2>&1; then
    echo "FATAL: cleanup() not defined after sourcing extraction" >&2
    exit 1
fi

# Spawn a process whose cmdline matches the old hazard pattern
# `code-server.*--bind-addr` — owned by THIS test, pid recorded exactly.
# Sleeps are LONG (300s): a fixture must never self-expire inside the run.
spawn_pattern_process() {
    bash -c 'exec -a "code-server --bind-addr 127.0.0.1:19999" sleep 300' &
    PATTERN_PID=$!
}
# Wait until a forked background job has actually exec'd into its final
# binary (comm stops being bash/sh). Kills that land in the fork→exec
# window can MISS the exec'd process; polling /proc closes that race.
# On non-Linux (no /proc) this is a best-effort no-op.
wait_execed() {
    local pid=$1 i=0 comm=""
    [ -d /proc/self ] || { sleep 0.05; return 0; }
    while [ "$i" -lt 200 ]; do
        comm="$(cat "/proc/$pid/comm" 2>/dev/null || true)"
        if [ -n "$comm" ] && [ "$comm" != "bash" ] && [ "$comm" != "sh" ]; then
            return 0
        fi
        i=$((i + 1))
        sleep 0.01
    done
    return 1
}
kill_owned() { # reliably terminate + reap a process this test owns
    local pid=$1 i=0
    kill "$pid" 2>/dev/null || true
    while [ "$i" -lt 100 ] && kill -0 "$pid" 2>/dev/null; do
        i=$((i + 1))
        sleep 0.01
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -9 "$pid" 2>/dev/null || true
    fi
    wait "$pid" 2>/dev/null || true
}

DATA_DIR="$SCRATCH/data"
PID_FILE="$DATA_DIR/vscode-server.pid"

echo "== dev.sh EXIT-trap kill-set scope: $DEV_SH =="

# --- (a) no pid file: hazard-pattern process must SURVIVE cleanup ----------
spawn_pattern_process
DATA_DIR="$SCRATCH/data"
CLEANUP_DONE=0
cleanup                      # no pid file → must do nothing
sleep 0.2
if kill -0 "$PATTERN_PID" 2>/dev/null; then
    ok "(a) pattern-named process survives cleanup when no pid file exists"
else
    bad "(a) pattern-named process was KILLED by cleanup (unscoped pattern kill regression?)"
fi
kill_owned "$PATTERN_PID"

# --- (b) JSON pid file (manager format): recorded pid IS killed ------------
mkdir -p "$DATA_DIR"
sleep 300 &
RECORDED_PID=$!
wait_execed "$RECORDED_PID" || true
printf '{"pid": %d, "pgid": %d, "port": 8079, "started_at": "2026-10-20T00:00:00"}' \
    "$RECORDED_PID" "$RECORDED_PID" > "$PID_FILE"
CLEANUP_DONE=0
DATA_DIR="$DATA_DIR"
cleanup
sleep 0.2
if kill -0 "$RECORDED_PID" 2>/dev/null; then
    bad "(b) recorded pid (JSON pid file) NOT killed — pid-exact kill path broken"
else
    ok "(b) recorded pid (JSON pid file) killed by exact pid"
fi
kill_owned "$RECORDED_PID"

# --- (c) stale pid in JSON file: cleanup must be a NO-OP for the pattern ---
spawn_pattern_process
sleep 300 & STALE_TMP=$!
# Kill the stale only AFTER it has exec'd, so the SIGTERM cannot miss in
# the fork→exec window (a missed kill would block `wait` for the full
# sleep duration and break the (c) timing for the pattern process).
wait_execed "$STALE_TMP" || true
kill "$STALE_TMP" 2>/dev/null; wait "$STALE_TMP" 2>/dev/null || true
printf '{"pid": %d, "pgid": %d, "port": 8079, "started_at": "2026-10-20T00:00:00"}' \
    "$STALE_TMP" "$STALE_TMP" > "$PID_FILE"
CLEANUP_DONE=0
DATA_DIR="$DATA_DIR"
cleanup
sleep 0.2
if kill -0 "$PATTERN_PID" 2>/dev/null; then
    ok "(c) stale pid in pid file → cleanup is a no-op; pattern-named process survives"
else
    bad "(c) stale pid in pid file → cleanup KILLED the pattern-named process (fallback regression)"
fi
kill_owned "$PATTERN_PID"
rm -f "$PID_FILE"

# --- (d) STATIC: zero pkill/killall on EXECUTABLE lines of dev.sh ----------
# Comment-only lines are stripped first: prose may legitimately CITE the old
# incident pattern (documentation); the pin is that no executable line uses
# a pattern kill. Functional cases (a)+(c) prove the executed behavior.
strip_comment_lines() {
    grep -v '^[[:space:]]*#' "$DEV_SH" || true
}
if strip_comment_lines | grep -nE '(^|[^[:alnum:]_])(pkill|killall)([^[:alnum:]_]|$)' >/dev/null 2>&1; then
    bad "(d) dev.sh has an EXECUTABLE pkill/killall (unscoped pattern kill present):"
    strip_comment_lines | grep -nE '(^|[^[:alnum:]_])(pkill|killall)([^[:alnum:]_]|$)' | sed 's/^/       /'
else
    ok "(d) zero executable pkill/killall in dev.sh (live-pattern safety absolute)"
fi

# --- (e) STATIC: recorded-pid kill path wired ------------------------------
if printf '%s\n' "$EXTRACTED" | grep -F 'kill "$pid"' >/dev/null 2>&1; then
    ok "(e1) cleanup kills via the recorded-pid variable (kill \"\$pid\")"
else
    bad "(e1) cleanup has no recorded-pid kill path (kill \"\$pid\")"
fi
if printf '%s\n' "$EXTRACTED" | grep -q 'vscode-server.pid'; then
    ok "(e2) cleanup parses the manager pid file (vscode-server.pid)"
else
    bad "(e2) cleanup no longer reads the manager pid file"
fi
if grep -q '^trap cleanup EXIT INT TERM$' "$DEV_SH"; then
    ok "(e3) trap still wired: EXIT INT TERM"
else
    bad "(e3) trap registration changed: 'trap cleanup EXIT INT TERM' not found"
fi

# --- (f) syntax gate --------------------------------------------------------
if bash -n "$DEV_SH" 2>/dev/null; then
    ok "(f) bash -n $DEV_SH — syntax OK"
else
    bad "(f) bash -n $DEV_SH — syntax error"
fi

# --- teardown (recorded pids only) ------------------------------------------
printf '\n== %d passed, %d failed ==\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ]
