#!/usr/bin/env bash
# Test Pack: instances_state_e2e_lazy
# Lazy-mount + hold-release acceptance for the instances-state-cache
# feature (re-drive items 2+3):
#   1. A→B switch — hold releases to B, no A-bleed, host identity, B draft
#   2. First-open lazy mount — absent cold, mounts once, keep-alive identity
#   3. Navigate-away-during-load race — no double-mount, no stuck loading
#   4. 404 nonexistent instance — not-found UI, no crash, sane nav
#
# NOTE: BUG5 (scoped CSS not matching the VCR-created chat host) is OPEN —
# layout-shaped failures are BUG5-fallout, not new findings.
#
# Assumes:
#   - Backend (daemon) running on localhost:8079 (PID 96878 — do NOT kill).
#   - Frontend dev server on localhost:4199 auto-started/reused by the
#     playwright.config webServer.
#
# Internal deadline: 240s. Outer caller wraps with `timeout 300`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
FRONTEND_DIR="$PROJECT_DIR/frontend"

echo "=== Test Pack: instances_state_e2e_lazy ==="

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
    e2e/instances-state-cache-lazy.spec.ts \
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
