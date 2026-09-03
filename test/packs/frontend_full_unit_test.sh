#!/usr/bin/env bash
# Test Pack: frontend_full_unit_test — Full frontend Jest test suite
# Scope: ALL Angular component/service/model specs (chat, tab-bar, workspace, tab-state, etc.)
# Timeout: 5 minutes (300s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: frontend_full_unit_test ==="

# Stage-0 worktree bracket (hard gate on branch; SHA recorded as data only)
EXPECTED_BRANCH="${EXPECTED_BRANCH:-feature/mission-class}"
BRANCH="$(git -C "$PROJECT_DIR" rev-parse --abbrev-ref HEAD)"
SHA_BEFORE="$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
if [ "$BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "RESULT: FAIL (DRIFT) — branch=$BRANCH expected=$EXPECTED_BRANCH"
  exit 1
fi
echo "BRANCH=$BRANCH"
echo "GIT_SHA=$SHA_BEFORE"

cd "$PROJECT_DIR/frontend"

EXIT_CODE=0
timeout 300s npx jest \
  --no-coverage --no-cache 2>&1 || EXIT_CODE=$?

# Post-run drift check (SHA must not move during the run)
SHA_AFTER="$(git -C "$PROJECT_DIR" rev-parse --short HEAD)"
if [ "$SHA_AFTER" != "$SHA_BEFORE" ]; then
  echo "RESULT: FAIL (DRIFT-MID-RUN) — sha before=$SHA_BEFORE after=$SHA_AFTER"
  exit 1
fi
echo "GIT_SHA_AFTER=$SHA_AFTER"

if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
