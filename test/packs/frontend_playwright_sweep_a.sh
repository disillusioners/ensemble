#!/usr/bin/env bash
# Test Pack: frontend_playwright_sweep_a
# Existing-suite regression sweep — Group A (feature-adjacent specs) for the
# instances-state-cache gate (app shell/routing/chat-mount changes).
#
# Group A (6 specs):
#   auto-scroll-to-bottom, instances-project-tabs, project-tabs,
#   send-pause-button, tab-workspace-sync-bugfix, tab-workspace-sync-final
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

echo "=== Test Pack: frontend_playwright_sweep_a ==="

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
    e2e/auto-scroll-to-bottom.spec.ts \
    e2e/instances-project-tabs.spec.ts \
    e2e/project-tabs.spec.ts \
    e2e/send-pause-button.spec.ts \
    e2e/tab-workspace-sync-bugfix.spec.ts \
    e2e/tab-workspace-sync-final.spec.ts \
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
