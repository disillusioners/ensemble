#!/usr/bin/env bash
# Defer-blocked API unit test pack — BE acceptance gate for the
# defer-blocked API surface.
#
# Exercises tests/unit/routers/test_defer_blocked_api.py covering:
#   * TestConsistencyPin — route executes derived witness statement;
#     gate vs surface matrix parity; queued-only fixture agreement;
#     defer-lane semantics (no block, no hold, counts pending).
#   * TestPurity — endpoint and direct resolver emit zero DML.
#   * TestBoundedQueryCount — exactly two selects per request (and
#     SELECT COUNT(*) flattens to witnesses×2).
#
# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 150s via `timeout` wrapper below
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: defer_blocked_api_unit_test ==="
cd "$PROJECT_DIR"

# Sanity guard — optionally fail fast when EXPECTED_BRANCH is set.
# Shared-worktree hazard: an external `git checkout` mid-run invalidates
# dispatched test results. Set EXPECTED_BRANCH to a concrete branch to
# enable this check; the merged pack otherwise runs branch-agnostic.
ACTUAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
EXPECTED_BRANCH="${EXPECTED_BRANCH:-}"
if [[ -n "${EXPECTED_BRANCH}" ]]; then
  if [[ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" ]]; then
    echo "RESULT: BRANCH-DRIFT (expected ${EXPECTED_BRANCH}, got ${ACTUAL_BRANCH})"
    exit 1
  fi
  echo "RESULT: BRANCH-CHECK (expected ${EXPECTED_BRANCH}, got ${ACTUAL_BRANCH})"
else
  echo "RESULT: SKIP (set EXPECTED_BRANCH to enforce branch guard)"
fi

timeout 110s .venv/bin/pytest \
  tests/unit/routers/test_defer_blocked_api.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
EXIT_CODE=$?
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
