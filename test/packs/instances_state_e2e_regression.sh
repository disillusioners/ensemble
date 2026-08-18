#!/usr/bin/env bash
# Test Pack: instances_state_e2e_regression
# Covers the broader regression matrix:
#   - R6  Persistence: cold reload restores nav-link target, keeps overlay hidden
#   - R2  Sidebar: "All" project tab keeps showing multiple instances across navigation
#   - R4  Hide button: clicking it navigates to /instances without blank screen
#   - R5  Workspace overlay: layers above chat (z-index workspace=100 > chat=90)
#   - Terminate flow: terminates a fresh instance, clears localStorage cache,
#          nav link falls back to /instances
#
# Assumes:
#   - Backend (daemon) running on localhost:8079 (PID 96878 — do NOT kill).
#   - Frontend dev server on localhost:4199 auto-started by playwright.config
#     webServer (reuseExistingServer:true, 120s timeout).
#
# Internal deadline: 240s. Outer caller wraps with `timeout 300`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

echo "=== Test Pack: instances_state_e2e_regression ==="

cd "$FRONTEND_DIR"

# ─── Pre-flight: backend reachable ──────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run the spec under an internal deadline ────────────────────────────────
set +e
timeout 240 npx playwright test \
    e2e/instances-state-cache-regression.spec.ts \
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
