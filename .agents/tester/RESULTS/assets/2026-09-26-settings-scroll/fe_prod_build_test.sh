#!/bin/bash
# fe_prod_build_test.sh — Run Single Test Pack: Angular production build (acceptance, dd14d975)
# Scope: ONE production build via package.json scripts.build ("ng build"). Nothing else.
# Dual-layer timeout: outer `timeout 300` (caller) + internal 270s watchdog (this script).
# Output: all build output -> fe_prod_build.log

set -u

LOG="/home/nea/ensemble-src/.agents/tester/RESULTS/assets/2026-09-26-settings-scroll/fe_prod_build.log"
WATCHDOG_S=270

echo "=== Test Pack: fe_prod_build_test ==="
echo "Pack: Angular production build (npm run build)"
echo "Repo HEAD (expected dd14d975): $(git -C /home/nea/ensemble-src log -1 --format='%h %s')"
echo "Start: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "Internal watchdog: ${WATCHDOG_S}s"

cd /home/nea/ensemble-src/frontend || { echo "RESULT: FAIL (cannot cd into frontend)"; exit 1; }

: > "$LOG"

# setsid -> build runs in its own process group; watchdog can kill the whole tree
setsid npm run build > "$LOG" 2>&1 &
BUILD_PID=$!
echo "Build pid: $BUILD_PID (pgid $BUILD_PID), logging to $LOG"

ELAPSED=0
while kill -0 "$BUILD_PID" 2>/dev/null; do
  if [ "$ELAPSED" -ge "$WATCHDOG_S" ]; then
    echo "Internal watchdog fired at ${WATCHDOG_S}s — killing build process tree"
    kill -TERM -- -"$BUILD_PID" 2>/dev/null
    sleep 3
    kill -KILL -- -"$BUILD_PID" 2>/dev/null
    echo "--- last 30 lines of build log ---"
    tail -n 30 "$LOG"
    echo "RESULT: TIMEOUT (build exceeded ${WATCHDOG_S}s internal limit)"
    echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    exit 124
  fi
  sleep 5
  ELAPSED=$((ELAPSED + 5))
done

wait "$BUILD_PID"
BUILD_EXIT=$?

echo "Build exit code: $BUILD_EXIT"
echo "--- last 40 lines of build log ---"
tail -n 40 "$LOG"
echo "--- end log excerpt ---"
echo "Full log: $LOG"
echo "End: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ "$BUILD_EXIT" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL (build exit $BUILD_EXIT — see error lines above)"
  exit 1
fi
