#!/usr/bin/env bash
# test/packs/snapshots_v2_webauto_test.sh
#
# Snapshots v2 merge gate — real-browser web-automation test pack.
#
# Runs the Playwright gate spec (frontend/e2e/snapshots-v2-gate.spec.ts)
# against the FE dev server with ALL API calls stubbed via page.route
# (NO daemon, NO database). Playwright's own webServer lifecycle starts
# and stops `ng serve`; this pack never kills anything it did not spawn.
#
# Timeout model (dual layer):
#   1. `timeout 280` around the playwright invocation (internal layer).
#   2. Deadline self-check on total pack elapsed time (hard ceiling).
#
# Port selection: first FREE port in 14199..14210 at run time. An
# occupied port is NEVER killed — the pack skips to the next candidate.
#
# Exit codes: 0 = PASS, 1 = FAIL, 124 = TIMEOUT.
set -u

EVID_DIR="${SNAPV2_GATE_EVID_DIR:-/tmp/snapv2-gate-evidence}"
PORT_START=14199
PORT_END=14210
INTERNAL_TIMEOUT=280        # seconds around the playwright invocation
DEADLINE_CEILING=320        # total pack hard ceiling (self-check layer)

mkdir -p "$EVID_DIR"

echo "=== Test Pack: snapshots_v2_webauto ==="
PACK_START_TS=$(date +%s)

# ── Locate frontend (pack lives in test/packs/, frontend is a sibling) ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FE_DIR="$(cd "$SCRIPT_DIR/../../frontend" 2>/dev/null && pwd)"
if [ ! -d "$FE_DIR" ] || [ ! -f "$FE_DIR/playwright.snapshots-v2stub.config.ts" ]; then
    echo "RESULT: FAIL"
    echo "frontend dir or gate config not found (looked at: $FE_DIR)"
    exit 1
fi

# ── Pick first FREE port (never kill an occupant — skip to next) ──
PORT=""
for p in $(seq "$PORT_START" "$PORT_END"); do
    if ss -ltnH "sport = :$p" 2>/dev/null | grep -q .; then
        echo "port $p occupied — skipping (occupant untouched)"
        continue
    fi
    PORT="$p"
    break
done
if [ -z "$PORT" ]; then
    echo "RESULT: FAIL"
    echo "no free port in $PORT_START..$PORT_END"
    exit 1
fi
echo "port: $PORT"

# ── Cleanup trap: kill ONLY processes we spawned (the playwright client; ──
# ng serve is its child and playwright tears it down via webServer lifecycle).
PLAY_PID=""
cleanup() {
    if [ -n "$PLAY_PID" ] && kill -0 "$PLAY_PID" 2>/dev/null; then
        pkill -TERM -P "$PLAY_PID" 2>/dev/null
        kill -TERM "$PLAY_PID" 2>/dev/null
        for _ in 1 2 3 4; do
            kill -0 "$PLAY_PID" 2>/dev/null || break
            sleep 1
        done
        kill -KILL "$PLAY_PID" 2>/dev/null
    fi
}
trap cleanup EXIT INT TERM

# ── Run the gate (internal timeout layer) ──
cd "$FE_DIR" || { echo "RESULT: FAIL"; exit 1; }

set +e
timeout --signal=TERM --kill-after=10 "$INTERNAL_TIMEOUT" \
    env \
        SNAPV2_GATE_PORT="$PORT" \
        PLAYWRIGHT_JSON_OUTPUT_NAME="$EVID_DIR/results.json" \
        PLAYWRIGHT_BROWSERS_PATH="${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}" \
    npx playwright test \
        --config playwright.snapshots-v2stub.config.ts \
        --reporter=line,json \
    >"$EVID_DIR/run-output.log" 2>&1 &
PLAY_PID=$!
wait "$PLAY_PID"
RC=$?
PLAY_PID=""
set -e

PACK_END_TS=$(date +%s)
ELAPSED=$((PACK_END_TS - PACK_START_TS))

# ── Deadline self-check (second layer) ──
RESULT="FAIL"
if [ "$RC" -eq 124 ] || [ "$RC" -eq 137 ]; then
    RESULT="TIMEOUT"
elif [ "$ELAPSED" -gt "$DEADLINE_CEILING" ]; then
    RESULT="TIMEOUT"
elif [ "$RC" -eq 0 ]; then
    RESULT="PASS"
fi

# ── Port-freed check (playwright owns ng serve teardown) ──
sleep 1
if ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
    echo "WARN: port $PORT still listening after run (webServer teardown incomplete)"
fi

echo "---- leg output (tail) ----"
tail -n 40 "$EVID_DIR/run-output.log" 2>/dev/null || true
echo "---------------------------"
echo "evidence dir: $EVID_DIR"
echo "port: $PORT  elapsed: ${ELAPSED}s  playwright rc: $RC"
echo "RESULT: $RESULT"

if [ "$RESULT" = "PASS" ]; then
    exit 0
elif [ "$RESULT" = "TIMEOUT" ]; then
    exit 124
else
    exit 1
fi
