#!/usr/bin/env bash
# Test Pack: reload_tabs_e2e
# Runs the reload-tabs regression spec (hydrate-before-NavigationEnd fix):
#   - R-TAB-1  Multi-tab reload restores ALL tabs (original symptom)
#   - R-TAB-2  Detail-URL reload keeps tabs intact (the clobber path)
#   - R-TAB-3  Cold deep-link adds a tab without dropping persisted ones (F3)
#   - R-TAB-4  Fresh browser context: clean default tab state
#   - R-TAB-5  Detail open → Plan → back → same instance restored
#
# Assumes:
#   - Backend (daemon) running on localhost:8079 (do NOT kill).
#   - Frontend dev server on localhost:4199 auto-started by playwright.config
#     webServer (reuseExistingServer:true, 120s timeout).
#
# Internal deadline: 240s. Outer caller wraps with `timeout 300` to bound
# total runtime including process spawn.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

echo "=== Test Pack: reload_tabs_e2e ==="

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
    e2e/reload-tabs-regression.spec.ts \
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
