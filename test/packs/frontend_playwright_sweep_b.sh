#!/usr/bin/env bash
# Test Pack: frontend_playwright_sweep_b
# Existing-suite regression sweep — Group B for the instances-state-cache gate.
#
# Group B (6 specs):
#   queue-selector-regression, queue-selector-states, tab-workspace-sync,
#   workspace-file-tabs, workspace-state-preserve, workspace-toolbar-compact
#
# Sweep = BASELINE MEASUREMENT: pre-existing specs are NOT modified. Any
# failure is data (classify: feature-regression / pre-existing / test-infra).
#
# Assumes:
#   - Backend (daemon) running on localhost:8079 (PID 96878 — do NOT kill).
#   - Frontend dev server on localhost:4199 auto-started/reused by the
#     playwright.config webServer.
#
# Internal deadline: 270s. Outer caller wraps with `timeout 300`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

echo "=== Test Pack: frontend_playwright_sweep_b ==="

cd "$FRONTEND_DIR"

# ─── Pre-flight: backend reachable ──────────────────────────────────────────
if ! curl -s -m 5 http://localhost:8079/api/health >/dev/null 2>&1; then
    echo "SKIP: Daemon not running on localhost:8079 — start with ./dev.sh"
    echo "RESULT: SKIP"
    exit 0
fi

# ─── Run the sweep group under an internal deadline ─────────────────────────
set +e
timeout 270 npx playwright test \
    e2e/queue-selector-regression.spec.ts \
    e2e/queue-selector-states.spec.ts \
    e2e/tab-workspace-sync.spec.ts \
    e2e/workspace-file-tabs.spec.ts \
    e2e/workspace-state-preserve.spec.ts \
    e2e/workspace-toolbar-compact.spec.ts \
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
