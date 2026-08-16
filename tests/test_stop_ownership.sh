#!/bin/bash
# ============================================================================
# tests/test_stop_ownership.sh — safety property for scripts/stop-ensemble.sh
# ============================================================================
# INCIDENT REGRESSION TEST (2026-08-16): the Makefile's port-based stop
# killed the REAL prod daemon on a dev+prod coexistence host. The structural
# fix is ownership scoping: a stop scoped to INSTALL_DIR X must never touch
# processes owned by a different directory — even when they listen on the
# very port named in the command line.
#
# Proven with a FAKE install whose shape mirrors real prod exactly:
# an executable named `ensemble-prod` started as `./ensemble-prod` with
# cwd = its own install dir (relative argv — pgrep cannot anchor it; only
# cwd identifies it), listening on a >10000 port via an http.server child
# and forwarding SIGTERM to that child (launcher-style).
#
#   1. REPORT: the port holder is reported, never selected, by any stop.
#   2. SURVIVAL: stop scoped to a DIFFERENT install dir → fake survives.
#   3. KILL:     stop scoped to the fake's OWN dir → fake (and child) die.
#   4. ANCHOR:   a process merely MENTIONING a foreign dir in argv
#                (e.g. `tail -f <dir>/somefile`) is never selected by a
#                stop scoped to that dir.
#
# Plain bash, no new dependencies. Self-asserting; nonzero exit on failure.
#
#   bash tests/test_stop_ownership.sh
# ============================================================================
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STOP_SCRIPT="$REPO_ROOT/scripts/stop-ensemble.sh"

PASS=0
FAIL=0
FAILED_TESTS=""

_pass() { PASS=$((PASS + 1)); }
_fail() {
    FAIL=$((FAIL + 1))
    FAILED_TESTS="$FAILED_TESTS
  ✗ $1"
    printf 'FAIL: %s\n' "$1" >&2
}

alive() { kill -0 "$1" 2>/dev/null; }

# ─── 0. gates ───────────────────────────────────────────────────────────────
if bash -n "$STOP_SCRIPT" 2>/dev/null; then _pass; else _fail "stop-ensemble.sh passes bash -n"; fi
command -v python3 >/dev/null 2>&1 || { echo "python3 required" >&2; exit 2; }

# ─── 1. fake install A (owner) + fake install B (foreign) ──────────────────
TMP_A="$(mktemp -d /tmp/ae-ownertest-A.XXXXXX)"   # the fake's OWN dir (cwd)
TMP_B="$(mktemp -d /tmp/ae-ownertest-B.XXXXXX)"   # a different install dir
FAKE_PID=""
TAILER=""
cleanup() {
    [ -n "$FAKE_PID" ] && kill -9 "$FAKE_PID" 2>/dev/null
    [ -n "$TAILER" ] && kill -9 "$TAILER" 2>/dev/null
    rm -rf "$TMP_A" "$TMP_B"
}
trap cleanup EXIT INT TERM

PORT=19123   # >10000, unlikely to collide; verified free below
if lsof -ti:"$PORT" >/dev/null 2>&1; then
    echo "port $PORT busy — pick another" >&2
    exit 2
fi

# The fake daemon — shape mirrors REAL prod (verified on the live host):
#   * executable FILE named ensemble-prod, started as `./ensemble-prod`
#     (relative argv — only its cwd identifies it);
#   * cwd is its install dir;
#   * long-running, listens on a port via an http.server CHILD;
#   * forwards SIGTERM to the child (launcher-style) so a stop is clean.
cat > "$TMP_A/ensemble-prod" <<'EOF'
#!/bin/bash
# fake ensemble daemon: relative-argv shape + port listener + TERM forwarding
CHILD=""
trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM INT
python3 -m http.server "$LISTEN_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
CHILD=$!
wait "$CHILD"
EOF
chmod +x "$TMP_A/ensemble-prod"

( cd "$TMP_A" && LISTEN_PORT="$PORT" exec ./ensemble-prod ) >/dev/null 2>&1 &
FAKE_PID=$!
sleep 1.5

if ! alive "$FAKE_PID"; then
    echo "fake daemon died immediately" >&2
    exit 2
fi
# Wait for the listener child to bind.
BOUND=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if lsof -ti:"$PORT" >/dev/null 2>&1; then BOUND=1; break; fi
    sleep 0.5
done
[ "$BOUND" = "1" ] || { echo "fake never bound port $PORT" >&2; exit 2; }
FAKE_CWD="$(lsof -a -p "$FAKE_PID" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p' | head -1)"
FAKE_ARGS="$(ps -o args= -p "$FAKE_PID" 2>/dev/null)"
echo "fake daemon: pid=$FAKE_PID args=[$FAKE_ARGS] cwd=$FAKE_CWD port=$PORT"

# ─── 2. SURVIVAL: stop scoped to the FOREIGN dir B must not touch A ────────
echo "== stop scoped to foreign dir ($TMP_B) — fake must SURVIVE =="
WAIT_S=3 bash "$STOP_SCRIPT" "$TMP_B" "$PORT" >"$TMP_B/out.txt" 2>&1
grep -q "REPORT ONLY" "$TMP_B/out.txt" && _pass || _fail "port appears as report-only hint"
sleep 0.5
if alive "$FAKE_PID"; then _pass; else _fail "fake was KILLED by a foreign-scoped stop (SAFETY VIOLATION)"; fi
lsof -ti:"$PORT" >/dev/null 2>&1 && _pass || _fail "fake's listener died from a foreign-scoped stop"

# ─── 3. KILL: stop scoped to the fake's OWN dir must stop it ───────────────
echo "== stop scoped to the owner dir ($TMP_A) — fake must DIE =="
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_A" "$PORT" >"$TMP_A/dry.txt" 2>&1
grep -q "would signal $FAKE_PID" "$TMP_A/dry.txt" \
    && _pass \
    || _fail "owner-scoped stop did not select the fake (cwd ownership failed)"

WAIT_S=8 bash "$STOP_SCRIPT" "$TMP_A" "$PORT" >"$TMP_A/out.txt" 2>&1
sleep 0.5
if alive "$FAKE_PID"; then
    _fail "fake survived an owner-scoped stop (stop is ineffective)"
else
    _pass
fi
# The listener child must be gone too (TERM forwarding), freeing the port.
PORT_FREED=0
for _ in 1 2 3 4 5 6 7 8 9 10; do
    if ! lsof -ti:"$PORT" >/dev/null 2>&1; then PORT_FREED=1; break; fi
    sleep 0.5
done
[ "$PORT_FREED" = "1" ] && _pass || _fail "port not freed after owner-scoped stop"

# ─── 4. anchored-path rule: argv MENTION of a dir is not ownership ─────────
( cd "$TMP_A" && exec -a innocent-tail tail -f "$TMP_B/somefile" >/dev/null 2>&1 ) &
TAILER=$!
sleep 0.4
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_B" "$PORT" >"$TMP_B/out2.txt" 2>&1
if grep -q "would signal $TAILER" "$TMP_B/out2.txt" 2>/dev/null; then
    _fail "tail mentioning the dir in argv was selected (anchored-path violation)"
else
    _pass
fi
kill -9 "$TAILER" 2>/dev/null
TAILER=""
FAKE_PID=""

# ─── summary ────────────────────────────────────────────────────────────────
echo
echo "========================================"
if [ "$FAIL" -eq 0 ]; then
    echo "stop-ownership tests: $PASS passed, 0 failed — ALL PASS"
    exit 0
else
    echo "stop-ownership tests: $PASS passed, $FAIL failed"
    echo "$FAILED_TESTS"
    exit 1
fi
