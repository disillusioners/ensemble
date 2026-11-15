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
# REVIEW PINS (2026-08-16, pre-merge batch):
#   5. M2 PIN:   launcher-owned stop — with a launcher present, ONLY the
#                launcher is TERMed; the daemon receives EXACTLY ONE TERM
#                (forwarded via the launcher trap; a 2nd TERM = uvicorn
#                force_exit bug class), exit code propagates; a no-launcher
#                straggler is TERMed directly; a second stop is a no-op.
#   6. M3 PIN:   WAIT_S resolution — default 70 (60s graceful + 10s
#                margin), env-derived (DAEMON_GRACEFUL_SHUTDOWN_
#                TIMEOUT_SECONDS in staged .env, +10, clamped 10..600),
#                malformed env → 70, explicit WAIT_S wins over env.
#   7. P4 EDGE:  the full WAIT_S resolution edge table the Phase-1
#                tester probe P4 never captured (harness authored,
#                output lost — deferred 2026-08-17, completed
#                2026-08-22): explicit ""/-5/abc, env ""/-100/601/599,
#                floor boundary (raw 0 → 10), quoted + `export `-prefixed
#                env forms, explicit pass-through below floor / above cap
#                (deliberate: an explicit override is an operator demand,
#                not a guess to clamp).
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
    [ -n "$FAKE_LAUNCHER_PID" ] && kill -9 "$FAKE_LAUNCHER_PID" 2>/dev/null
    [ -n "$STRAGGLER_PID" ] && kill -9 "$STRAGGLER_PID" 2>/dev/null
    rm -rf "$TMP_A" "$TMP_B" ${CLEANUP_DIRS:-}
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

# ─── 5. M2 PIN: launcher-owned stop gives the daemon EXACTLY ONE TERM ──────
# Fixture: a fake launcher (traps TERM, forwards to child, bounded wait,
# exits with the child's code — minimal clone of launcher.sh's trap
# behavior) + a fake daemon that COUNTS the TERMs it receives (a second
# TERM is exactly the uvicorn force_exit bug class this pin guards
# against) + a straggler daemon with NO launcher.
echo "== M2: launcher present → daemon receives exactly ONE TERM =="
TMP_C="$(mktemp -d /tmp/ae-ownertest-C.XXXXXX)"
SIGLOG="$TMP_C/daemon-signals.log"; : >"$SIGLOG"
CLEANUP_DIRS="$CLEANUP_DIRS $TMP_C"

# fake daemon: logs every TERM to $SIGLOG, sleeps as "graceful teardown"
cat > "$TMP_C/ensemble-prod" <<'EOF'
#!/bin/bash
# fake daemon — counts TERMs, "graceful teardown" = brief sleep
trap 'echo "TERM $(date +%s.%N) pid $$" >> "$SIGLOG"; sleep 0.7; exit 0' TERM
while :; do sleep 3600 & wait $!; done
EOF
chmod +x "$TMP_C/ensemble-prod"

# fake launcher: minimal clone of launcher.sh's TERM contract — trap
# forwards to child, bounded wait, exit with child's real exit code.
# Started as `./launcher.sh` (relative argv) — the EXACT shape deploy.sh
# starts (nohup ./launcher.sh), which Tier 1a's absolute anchor cannot
# classify; this pin therefore also locks the cwd-launcher classification.
cat > "$TMP_C/launcher.sh" <<'EOF'
#!/bin/bash
# fake launcher — launcher.sh trap semantics (forward, bounded wait, exit child code)
CHILD_PID=0
handle_term() {
    echo "LAUNCHER-RECEIVED-TERM pid $$" >> "$SIGLOG"
    [ "$CHILD_PID" -gt 0 ] && kill -TERM "$CHILD_PID" 2>/dev/null
}
trap handle_term TERM
"$BIN_DIR/ensemble-prod" &
CHILD_PID=$!
wait "$CHILD_PID"
code=$?
# if wait was interrupted by the trap, reap the child bounded
if kill -0 "$CHILD_PID" 2>/dev/null; then
    waited=0
    while kill -0 "$CHILD_PID" 2>/dev/null; do
        [ "$waited" -ge 10 ] && { kill -9 "$CHILD_PID" 2>/dev/null; break; }
        sleep 1; waited=$((waited+1))
    done
    wait "$CHILD_PID"; code=$?
fi
echo "LAUNCHER-EXIT $code" >> "$SIGLOG"
exit "$code"
EOF
chmod +x "$TMP_C/launcher.sh"

# (a) launcher + daemon pair — RELATIVE start (deploy.sh shape)
( cd "$TMP_C" && SIGLOG="$SIGLOG" BIN_DIR="$TMP_C" exec ./launcher.sh ) >/dev/null 2>&1 &
FAKE_LAUNCHER_PID=$!
sleep 1.0
FAKE_DAEMON_PID="$(pgrep -P "$FAKE_LAUNCHER_PID" | head -1)"
if [ -z "$FAKE_DAEMON_PID" ] || ! alive "$FAKE_DAEMON_PID"; then
    echo "fixture: fake daemon never started under launcher" >&2
    _fail "M2 fixture sanity (daemon alive under launcher)"
else
    DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_C" >"$TMP_C/dry-a.txt" 2>&1
    if grep -q "launcher-owned stop" "$TMP_C/dry-a.txt"; then _pass; else _fail "M2: relative-form launcher not classified launcher-first (cwd-launcher classification broken)"; fi
    if grep -q "would signal $FAKE_DAEMON_PID" "$TMP_C/dry-a.txt"; then
        _fail "M2 dry-run: daemon pid in the direct-signal plan despite live launcher"
    else
        _pass
    fi
    WAIT_S=15 bash "$STOP_SCRIPT" "$TMP_C" >"$TMP_C/stop-a.txt" 2>&1
    sleep 1.5
    if grep -q "launcher-owned stop" "$TMP_C/stop-a.txt"; then _pass; else _fail "M2: stop did not announce launcher-owned single-TERM pass"; fi
    if grep -q "SIGTERM $FAKE_DAEMON_PID" "$TMP_C/stop-a.txt"; then
        _fail "M2: daemon pid was TERMed DIRECTLY despite live launcher (double-TERM bug)"
    else
        _pass
    fi
    TERM_COUNT="$(grep -c '^TERM ' "$SIGLOG" 2>/dev/null || echo 0)"
    if [ "$TERM_COUNT" = "1" ]; then _pass; else _fail "M2: daemon received $TERM_COUNT TERMs (expected EXACTLY 1 — uvicorn force_exit class)"; fi
    if grep -q '^LAUNCHER-RECEIVED-TERM' "$SIGLOG"; then _pass; else _fail "M2: launcher never received the TERM (stop TERMed the wrong pid)"; fi
    if grep -q '^LAUNCHER-EXIT 0$' "$SIGLOG"; then _pass; else _fail "M2: launcher exit code did not propagate as child's 0"; fi
    alive "$FAKE_LAUNCHER_PID" && _fail "M2: launcher survived the stop" || _pass
    alive "$FAKE_DAEMON_PID"  && _fail "M2: daemon survived the stop"   || _pass
    if grep -q '^TERM ' "$SIGLOG"; then _pass; else _fail "M2: fake daemon never logged a TERM (fixture broken?)"; fi
fi
FAKE_LAUNCHER_PID=""
FAKE_DAEMON_PID=""

# (b) straggler daemon, NO launcher → direct TERM
echo "== M2: no-launcher straggler → daemon TERMed DIRECTLY =="
SIGLOG2="$TMP_C/straggler-signals.log"; : >"$SIGLOG2"
( cd "$TMP_C" && SIGLOG="$SIGLOG2" exec ./ensemble-prod ) >/dev/null 2>&1 &
STRAGGLER_PID=$!
sleep 1.0
WAIT_S=15 bash "$STOP_SCRIPT" "$TMP_C" >"$TMP_C/stop-b.txt" 2>&1
sleep 1.5
if grep -q "SIGTERM $STRAGGLER_PID" "$TMP_C/stop-b.txt"; then _pass; else _fail "M2(b): straggler daemon was not TERMed directly (no-launcher pass broken)"; fi
TERM_COUNT2="$(grep -c '^TERM ' "$SIGLOG2" 2>/dev/null || echo 0)"
if [ "$TERM_COUNT2" = "1" ]; then _pass; else _fail "M2(b): straggler received $TERM_COUNT2 TERMs (expected 1)"; fi
alive "$STRAGGLER_PID" && _fail "M2(b): straggler survived the stop" || _pass
STRAGGLER_PID=""

# (c) idempotency: second stop run finds nothing → clean exit 0
echo "== M2: idempotent second stop =="
WAIT_S=5 bash "$STOP_SCRIPT" "$TMP_C" >"$TMP_C/stop-c.txt" 2>&1
rc=$?
[ "$rc" = "0" ] && _pass || _fail "M2(c): second stop exited $rc (expected 0)"
grep -q "nothing to stop" "$TMP_C/stop-c.txt" && _pass || _fail "M2(c): second stop did not report nothing-to-stop"

# ─── 6. M3 PIN: WAIT_S resolution (default 70 / env-derived / malformed) ──
echo "== M3: WAIT_S resolution =="
TMP_D="$(mktemp -d /tmp/ae-ownertest-D.XXXXXX)"
CLEANUP_DIRS="$CLEANUP_DIRS $TMP_D"

# 6a. no env, no override → 70
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_D" >"$TMP_D/a.txt" 2>&1
grep -q "WAIT_S resolved to 70s" "$TMP_D/a.txt" && _pass || _fail "M3: default WAIT_S is not 70 (got: $(grep 'WAIT_S resolved' "$TMP_D/a.txt" || echo none))"

# 6b. staged env DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30 → 40
printf 'PORT=19124\nDAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_D/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_D" >"$TMP_D/b.txt" 2>&1
grep -q "WAIT_S resolved to 40s" "$TMP_D/b.txt" && _pass || _fail "M3: env-derived WAIT_S != 40 (got: $(grep 'WAIT_S resolved' "$TMP_D/b.txt" || echo none))"

# 6c. malformed env value → fallback to 70
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=garbage\n' > "$TMP_D/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_D" >"$TMP_D/c.txt" 2>&1
grep -q "WAIT_S resolved to 70s" "$TMP_D/c.txt" && _pass || _fail "M3: malformed env did not fall back to 70 (got: $(grep 'WAIT_S resolved' "$TMP_D/c.txt" || echo none))"

# 6d. explicit WAIT_S wins over env
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_D/.env"
WAIT_S=5 DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_D" >"$TMP_D/d.txt" 2>&1
grep -q "WAIT_S resolved to 5s" "$TMP_D/d.txt" && _pass || _fail "M3: explicit WAIT_S=5 did not win over env (got: $(grep 'WAIT_S resolved' "$TMP_D/d.txt" || echo none))"

# 6e. clamp: absurd env (100000 → cap 600)
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=100000\n' > "$TMP_D/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_D" >"$TMP_D/e.txt" 2>&1
grep -q "WAIT_S resolved to 600s" "$TMP_D/e.txt" && _pass || _fail "M3: cap 600 not applied (got: $(grep 'WAIT_S resolved' "$TMP_D/e.txt" || echo none))"

# ─── 7. P4 EDGE TABLE: WAIT_S resolution edges ─────────────────────────────
# Completes the Phase-1 tester probe P4 whose output was never captured
# (edge-probe worker lost its table; salvage found harness only). Every
# case asserts the RESOLVED value printed by the real script under
# DRY_RUN — no signal is ever sent, no process touched.
echo "== P4: WAIT_S edge table =="
TMP_E="$(mktemp -d /tmp/ae-ownertest-E.XXXXXX)"
CLEANUP_DIRS="$CLEANUP_DIRS $TMP_E"

_resolved() {  # $1 = output file; echoes the resolved value or "none"
    sed -n 's/.*WAIT_S resolved to \([0-9]*\)s.*/\1/p' "$1" | head -1
}
_expect_resolved() {  # $1 = output file, $2 = expected value, $3 = label
    local got
    got="$(_resolved "$1")"
    [ "$got" = "$2" ] && _pass || _fail "$3: expected WAIT_S=$2, got '${got:-none}' ($(grep 'WAIT_S resolved' "$1" || echo 'no resolution line'))"
}

# 7a. explicit WAIT_S="" (empty) is UNTSET — env-derived value wins
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_E/.env"
WAIT_S="" DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/a.txt" 2>&1
_expect_resolved "$TMP_E/a.txt" 40 "P4(a): empty explicit WAIT_S must fall through to env-derived"

# 7b. explicit WAIT_S=-5 (malformed) → default 70, NOT env-derived —
#     a malformed override never silently becomes a different number
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_E/.env"
WAIT_S="-5" DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/b.txt" 2>&1
_expect_resolved "$TMP_E/b.txt" 70 "P4(b): malformed explicit WAIT_S must fall back to default 70 (not env)"

# 7c. explicit WAIT_S=abc (malformed) → default 70
WAIT_S="abc" DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/c.txt" 2>&1
_expect_resolved "$TMP_E/c.txt" 70 "P4(c): non-numeric explicit WAIT_S must fall back to default 70"

# 7d. env value empty ("") → digits-only validation fails → default 70
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/d.txt" 2>&1
_expect_resolved "$TMP_E/d.txt" 70 "P4(d): empty env value must fall back to default 70"

# 7e. env value negative (-100) → not digits → default 70
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=-100\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/e.txt" 2>&1
_expect_resolved "$TMP_E/e.txt" 70 "P4(e): negative env value must fall back to default 70"

# 7f. env boundary pair: 601 → 611 → cap 600; 599 → 609 → cap 600
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=601\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/f1.txt" 2>&1
_expect_resolved "$TMP_E/f1.txt" 600 "P4(f1): env 601 (+10=611) must clamp to cap 600"
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=599\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/f2.txt" 2>&1
_expect_resolved "$TMP_E/f2.txt" 600 "P4(f2): env 599 (+10=609) must clamp to cap 600"

# 7g. floor boundary: env 0 → 0+10=10 — the floor binds at exactly 10
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=0\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/g.txt" 2>&1
_expect_resolved "$TMP_E/g.txt" 10 "P4(g): env 0 (+10=10) must resolve at the floor boundary 10"

# 7h. quoted env values are parsed (both quote styles) → 30+10=40
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS="30"\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/h1.txt" 2>&1
_expect_resolved "$TMP_E/h1.txt" 40 "P4(h1): double-quoted env value must be parsed"
printf "DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS='30'\n" > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/h2.txt" 2>&1
_expect_resolved "$TMP_E/h2.txt" 40 "P4(h2): single-quoted env value must be parsed"

# 7i. `export `-prefixed env form is accepted → 30+10=40
printf 'export DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_E/.env"
DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/i.txt" 2>&1
_expect_resolved "$TMP_E/i.txt" 40 "P4(i): export-prefixed env line must be parsed"

# 7j. explicit values pass through UNCLAMPED (deliberate contract):
#     an explicit WAIT_S is an operator demand — below-floor and
#     above-cap are honored as-is; only the derived paths clamp.
printf 'DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS=30\n' > "$TMP_E/.env"
WAIT_S=9 DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/j1.txt" 2>&1
_expect_resolved "$TMP_E/j1.txt" 9 "P4(j1): explicit WAIT_S=9 (below floor) must pass through"
WAIT_S=599 DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/j2.txt" 2>&1
_expect_resolved "$TMP_E/j2.txt" 599 "P4(j2): explicit WAIT_S=599 must pass through"
WAIT_S=600 DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/j3.txt" 2>&1
_expect_resolved "$TMP_E/j3.txt" 600 "P4(j3): explicit WAIT_S=600 must pass through"
WAIT_S=601 DRY_RUN=1 bash "$STOP_SCRIPT" "$TMP_E" >"$TMP_E/j4.txt" 2>&1
_expect_resolved "$TMP_E/j4.txt" 601 "P4(j4): explicit WAIT_S=601 (above cap) must pass through"

rm -rf "$TMP_D" "$TMP_E" 2>/dev/null

# ─── 8. MAJOR #3 (review round 2) — daemon-never-excluded stop sweep ───────
# The in-tree ancestor walk + the blanket $PPID collection skip DROPPED THE
# DAEMON when the sweep ran inside its tree: classified → filtered →
# nothing signalled → "is stopped" exit 0 while the daemon lived
# (reproduced live before the fix: sweep as the daemon's grandchild,
# "self-exclusion: pid <daemon> …", DRY_RUN would-signal count 0). Shipped
# shape: executor-identity-only exclusion ($$/$PPID) hard-guarded by the
# never-exclude-the-TARGET rule — a sweep candidate is signalled even when
# it is literally $PPID or $PPID's parent. The release_journal 16d TERM pin
# (sweep runs in the launchd job's group in-tree) is the standing regression
# guard for the group-based variant of this class.
TMP_C="$(mktemp -d /tmp/ae-ownertest-C.XXXXXX)"
CLEANUP_DIRS="$CLEANUP_DIRS $TMP_C"
export STOP_SCRIPT   # the fake-daemon heredocs are quoted — the daemon resolves the sweep path from the ENV
D2_PID="" ; D3_PID=""
m3_cleanup() {
    [ -n "$D2_PID" ] && kill -9 "$D2_PID" 2>/dev/null
    [ -n "$D3_PID" ] && kill -9 "$D3_PID" 2>/dev/null
    # the KILL -9 bypasses the daemon's TERM-forward trap: reap the orphaned
    # listener children by port so the scratch mirrors never leak.
    local _lp
    for _lp in 19131 19132; do
        local _pids
        _pids="$(lsof -ti:"$_lp" 2>/dev/null)"
        [ -n "$_pids" ] && kill -9 $_pids 2>/dev/null
    done
    return 0
}
# m3_wait_port_free <port> — an earlier leg's leaked listener (KILL -9
# bypasses the forward trap; the reaper races the bind) must not fail the
# next leg's startup: kill any holder, then wait until truly free.
m3_wait_port_free() {
    local _p="$1" _lp i
    for i in 1 2 3 4 5 6 7 8 9 10; do
        _lp="$(lsof -ti:"$_p" 2>/dev/null)"
        [ -n "$_lp" ] && kill -9 $_lp 2>/dev/null
        lsof -ti:"$_p" >/dev/null 2>&1 || return 0
        sleep 0.5
    done
    return 0
}
cat > "$TMP_C/ensemble-prod" <<'EOF'
#!/bin/bash
CHILD=""
trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM INT
python3 -m http.server "$LISTEN_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
CHILD=$!
# S1 shape: spawn the SWEEP from a grandchild shell (sweep.PPID.parent = US)
if [ "$SPAWN_SWEEP" = "1" ]; then
    bash -c "DRY_RUN=1 WAIT_S=3 bash '$STOP_SCRIPT' '$(pwd)' > '$(pwd)/s1.log' 2>&1" &
fi
wait "$CHILD"
EOF
chmod +x "$TMP_C/ensemble-prod"

echo "== S1: sweep runs as the daemon's GRANDCHILD — the daemon must still be signalled =="
m3_wait_port_free 19131
( cd "$TMP_C" && LISTEN_PORT=19131 SPAWN_SWEEP=1 exec ./ensemble-prod ) >/dev/null 2>&1 &
D2_PID=$!
sleep 1.5
alive "$D2_PID" || { echo "S1 fake daemon died immediately" >&2; exit 2; }
for _ in 1 2 3 4 5 6 7 8; do
    grep -q "would signal $D2_PID" "$TMP_C/s1.log" 2>/dev/null && break
    sleep 0.5
done
if grep -q "would signal $D2_PID" "$TMP_C/s1.log" 2>/dev/null; then
    _pass
else
    _fail "S1: the daemon (grandparent of the sweep, pid $D2_PID) was NOT selected — the ancestor exclusion dropped the TARGET again"
fi
# NOTE: the never-exclude GUARD line cannot fire in the S1 shape — the
# sweep's executor identity ($$/$PPID = the sweep + the child shell) is NOT
# a candidate there, so there is nothing to guard. The guard's live shape
# (identity == target) is exercised by S2 (sweep's PPID IS the daemon) and
# asserted on S2's log.
grep -q "is stopped" "$TMP_C/s1.log" 2>/dev/null && _pass || _fail "S1: DRY_RUN plan did not complete"
kill -9 "$D2_PID" 2>/dev/null; wait "$D2_PID" 2>/dev/null; D2_PID=""

echo "== S2: sweep spawned DIRECTLY by the daemon (PPID = daemon) — the daemon must die for real =="
cat > "$TMP_C/ensemble-prod" <<'EOF'
#!/bin/bash
CHILD=""
trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM INT
python3 -m http.server "$LISTEN_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
CHILD=$!
# S2 shape: the sweep IS this daemon's direct child (PPID = the daemon).
# The rc is written by the ORPHANED sweep (the daemon gets TERMed by the
# sweep and exits via its trap — it cannot write its own stop's rc).
if [ "$SPAWN_SWEEP" = "1" ]; then
    bash -c 'WAIT_S=6 bash "$0" "$1" > "$2" 2>&1; echo "sweep-rc=$?" > "$3"' \
        "$STOP_SCRIPT" "$(pwd)" "$(pwd)/s2.log" "$(pwd)/s2.rc" &
fi
wait "$CHILD"
EOF
chmod +x "$TMP_C/ensemble-prod"
m3_wait_port_free 19132
( cd "$TMP_C" && LISTEN_PORT=19132 SPAWN_SWEEP=1 exec ./ensemble-prod ) >/dev/null 2>&1 &
D3_PID=$!
# NOTE: no startup-liveness gate here — unlike S1 (DRY_RUN), the S2 sweep
# is REAL and TERMs the daemon within seconds; a dead daemon at +1.5s IS
# the leg's success shape. Completion is judged by the sweep's rc marker.
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    [ -f "$TMP_C/s2.rc" ] && break
    sleep 0.5
done
S2_RC="$(cat "$TMP_C/s2.rc" 2>/dev/null | tr -dc '0-9')"
[ "$S2_RC" = "0" ] && _pass || _fail "S2: the daemon-spawned sweep exited '$S2_RC' (want 0 — the daemon WAS a target and must be stopped)"
grep -q "SIGTERM $D3_PID" "$TMP_C/s2.log" 2>/dev/null && _pass || _fail "S2: the daemon (pid $D3_PID) was not TERMed by the sweep"

# S2b — the never-exclude GUARD's exact live shape: the sweep spawned as
# the daemon's DIRECT child (PPID = the daemon = a sweep candidate) under
# DRY_RUN — the guard must fire ("identity == target ⇒ the target wins")
# and the plan must include the daemon pid. The daemon SURVIVES (DRY_RUN).
cat > "$TMP_C/ensemble-prod" <<'EOF'
#!/bin/bash
CHILD=""
trap '[ -n "$CHILD" ] && kill "$CHILD" 2>/dev/null; exit 0' TERM INT
python3 -m http.server "$LISTEN_PORT" --bind 127.0.0.1 >/dev/null 2>&1 &
CHILD=$!
# S2b shape: DIRECT child (no wrapper) — sweep.PPID = this daemon
if [ "$SPAWN_SWEEP" = "1" ]; then
    DRY_RUN=1 WAIT_S=3 bash "$STOP_SCRIPT" "$(pwd)" > "$(pwd)/s2b.log" 2>&1 &
fi
wait "$CHILD"
EOF
chmod +x "$TMP_C/ensemble-prod"
m3_wait_port_free 19132
( cd "$TMP_C" && LISTEN_PORT=19132 SPAWN_SWEEP=1 exec ./ensemble-prod ) >/dev/null 2>&1 &
D3B_PID=$!
sleep 1.5
alive "$D3B_PID" || { echo "S2b fake daemon died immediately (DRY_RUN must not signal)" >&2; exit 2; }
for _ in 1 2 3 4 5 6 7 8; do
    grep -q "would signal $D3B_PID" "$TMP_C/s2b.log" 2>/dev/null && break
    sleep 0.5
done
grep -q "never-exclude guard" "$TMP_C/s2b.log" 2>/dev/null \
    && _pass \
    || _fail "S2b: the never-exclude-the-TARGET guard did not fire for the daemon-as-PPID candidate"
grep -q "would signal $D3B_PID" "$TMP_C/s2b.log" 2>/dev/null \
    && _pass \
    || _fail "S2b: the daemon (sweep's PPID, pid $D3B_PID) was not in the stop plan"
alive "$D3B_PID" && _pass || _fail "S2b: the daemon died under DRY_RUN (something signalled for real)"
kill -9 "$D3B_PID" 2>/dev/null; wait "$D3B_PID" 2>/dev/null; D3B_PID=""
m3_cleanup
S2_DEAD=0
for _ in 1 2 3 4 5; do
    alive "$D3_PID" || { S2_DEAD=1; break; }
    sleep 0.5
done
if [ "$S2_DEAD" = "1" ]; then
    _pass
else
    _fail "S2: the daemon (sweep's PPID, pid $D3_PID) SURVIVED its own stop sweep — the daemon-drop is back"
fi
kill -9 "$D3_PID" 2>/dev/null; wait "$D3_PID" 2>/dev/null; D3_PID=""
m3_cleanup

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
