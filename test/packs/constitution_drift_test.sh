#!/usr/bin/env bash
# Constitution drift test pack — Phase 0 census gates (D1 + D4 + JAFP).
#
# Runs the writer/creator bidirectional census, the subset-only mint
# census, and the Fix A linkage-contract tests in a single pack so
# census-covered drift between source and the static KNOWN_* sets in
# daemon/job_state/constitution.py fails the gate. Bidirectional for
# writers/creators; subset-only for mints — a new source mint is a
# registration obligation (D4 checklist), not a test failure.
#
# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: constitution_drift_test ==="
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
  tests/unit/job_state/test_constitution_drift.py \
  tests/unit/services/test_linkage_contract_fail_closed.py \
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
