#!/usr/bin/env bash
# fe_edit_agent_e2e_test — Live-DOM smoke for source edit/add agent select.
#
# This pack is FE-only verification of the WeakMap memoization fix for the
# SearchableSelectComponent toSelectOptions re-binding bug. It runs:
#   1. Starts a one-origin Node mock server (port 10180) serving
#      frontend/dist/frontend/browser + /api stubs.
#   2. Drives a bundled headless Chromium via Playwright-as-library through
#      the 5 acceptance scenarios (S1-S5) defined in the task.
#   3. Captures screenshots, PUT/POST payloads, console log to
#      /tmp/fe-edit-agent-smoke-20260916/ (or $FE_SMOKE_EVIDENCE_DIR).
#
# Layer 1 (outer): caller runs `timeout 300 bash fe_edit_agent_e2e_test.sh`.
# Layer 2 (inner): this script self-caps the harness at 240s.

set -euo pipefail

PACK_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACK_NAME="$(basename "${BASH_SOURCE[0]}" .sh)"
HARNESS_DIR="$PACK_DIR/$PACK_NAME"
REPO_ROOT="$(cd "$PACK_DIR/../.." && pwd)"

cd "$REPO_ROOT"

# Inner self-cap (5 - 1min slack so the outer `timeout 300` is the final guard)
INNER_TIMEOUT="${FE_EDIT_AGENT_INNER_TIMEOUT:-240}"

export FE_SMOKE_EVIDENCE_DIR="${FE_SMOKE_EVIDENCE_DIR:-/tmp/fe-edit-agent-smoke-20260916}"
mkdir -p "$FE_SMOKE_EVIDENCE_DIR"

# Frontend deps must already be installed (we never npm install).
if [ ! -d "$REPO_ROOT/frontend/node_modules/playwright" ]; then
  echo "FATAL: frontend/node_modules/playwright missing — run from a fully-installed worktree" >&2
  exit 2
fi

if [ ! -f "$REPO_ROOT/frontend/dist/frontend/browser/index.html" ]; then
  echo "FATAL: frontend/dist/frontend/browser/index.html missing — run \`cd frontend && npm run build\` before this pack" >&2
  exit 2
fi

echo "=== Test Pack: fe_edit_agent_e2e_test ==="
echo "(inner timeout ${INNER_TIMEOUT}s | evidence dir: $FE_SMOKE_EVIDENCE_DIR)"
echo "(repo: $REPO_ROOT | branch: $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD))"

PACK_START=$(date +%s)

# Inner cap. The script already traps + closes its server in finally{}.
set +e
timeout "$INNER_TIMEOUT" node "$HARNESS_DIR/harness.js"
rc=$?
set -e

PACK_END=$(date +%s)
PACK_ELAPSED=$((PACK_END - PACK_START))

echo
echo "(pack elapsed: ${PACK_ELAPSED}s)"

# Normalize exit codes per the pack contract:
#   0  PASS
#   1  FAIL (one or more scenarios failed)
#   124 TIMEOUT (inner `timeout` killed the harness)
#   2  setup error (no build / no playwright / mock server boot failure)
if [ "$rc" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
fi
if [ "$rc" -eq 2 ]; then
  echo "RESULT: FAIL (setup error)"
  exit 1
fi
# rc 0 or 1 are pass/fail per the harness — surface as-is.
exit "$rc"
