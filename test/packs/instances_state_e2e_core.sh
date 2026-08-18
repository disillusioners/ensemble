#!/usr/bin/env bash
# Test Pack: instances_state_e2e_core
# Runs the primary caching-journey spec (R1 + R5 + R6 partial):
#   - Open detail, capture identity marker + draft + scroll
#   - Navigate to Plan, assert chat hidden + SSE closed + zero console errors
#   - Navigate back, assert same DOM node + draft + scroll preserved
#   - Verify localStorage cache contains the instance id
#
# Assumes:
#   - Backend (daemon) running on localhost:8079 (PID 96878 — do NOT kill).
#   - Frontend dev server on localhost:4199 auto-started by playwright.config
#     webServer (reuseExistingServer:true, 120s timeout).
#
# Internal deadline: 240s. Outer caller wraps with `timeout 300` to bound
# total runtime including process spawn.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

echo "=== Test Pack: instances_state_e2e_core ==="

cd "$FRONTEND_DIR"

# ─── Pre-flight: backend reachable ──────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run the spec under an internal deadline ────────────────────────────────
# `timeout 240` bounds the inner run; the outer caller wraps with 300s.
# Playwright webServer config auto-starts ng serve on :4199 (or reuses an
# existing one) within 120s. If it can't, the test run fails fast — we do
# NOT block the script on it.
set +e
timeout 240 npx playwright test \
    e2e/instances-state-cache-core.spec.ts \
    --reporter=line \
    --workers=1 \
    2>&1
EXIT_CODE=$?
set -e

if [ $EXIT_CODE -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
elif [ $EXIT_CODE -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
else
    echo "RESULT: FAIL (exit $EXIT_CODE)"
    exit 1
fi
