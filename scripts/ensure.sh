#!/bin/bash
# Phase 5 quality gate: Verify dev.sh runs without crashes for 30 seconds

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_FILE="/tmp/ensemble_ensure_$$.log"
TIMEOUT=30

echo "=== Phase 5 Quality Gate: ensure.sh ==="
echo "Starting dev.sh with $TIMEOUT second timeout..."

# Run dev.sh in background, capture logs
# dev.sh is in project root (not scripts/)
bash "$SCRIPT_DIR/../dev.sh" > "$LOG_FILE" 2>&1 &
DEV_PID=$?

echo "dev.sh PID: $DEV_PID"
echo "Log file: $LOG_FILE"
echo ""

# Monitor for timeout or crash
elapsed=0
crashed=false

while [ $elapsed -lt $TIMEOUT ]; do
    if ! kill -0 $DEV_PID 2>/dev/null; then
        echo ""
        echo "=== CRASH DETECTED (exited before ${TIMEOUT}s) ==="
        crashed=true
        break
    fi
    sleep 1
    elapsed=$((elapsed + 1))
    echo -ne "\rElapsed: ${elapsed}s / ${TIMEOUT}s..."
done

echo ""
echo ""

if [ "$crashed" = true ]; then
    echo "=== FAIL: dev.sh crashed ==="
    echo ""
    echo "=== Last 50 lines of log ==="
    tail -50 "$LOG_FILE"
    echo ""
    echo "=== Full log ==="
    cat "$LOG_FILE"
    kill $DEV_PID 2>/dev/null || true
    rm -f "$LOG_FILE"
    exit 1
else
    echo "=== SUCCESS: dev.sh ran for ${TIMEOUT}s without crashing ==="
    echo ""
    echo "=== Server startup log ==="
    tail -30 "$LOG_FILE"
    kill $DEV_PID 2>/dev/null || true
    sleep 1
    kill -9 $DEV_PID 2>/dev/null || true
    rm -f "$LOG_FILE"
    echo ""
    echo "=== PASS ==="
    exit 0
fi
