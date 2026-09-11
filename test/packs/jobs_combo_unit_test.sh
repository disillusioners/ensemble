#!/usr/bin/env bash
# test/packs/jobs_combo_unit_test.sh
#
# Pack: jobs_combo_unit_test
# Ad-hoc BE pack for fix/jobs-status-combo-filter @ 19b40e49.
# Covers the EXACT 2 test files derived from the merge commits
# 33731abb (per-kind combo+terminal_reason-NULL) + b8644dde
# (terminal_reason canonicalization). Production touch:
# daemon/repositories/job_queue/repository.py + daemon/services/work_status.py.
#
# Classification contract:
#   - Derived scope = tests/integration/ + tests/unit/services/.
#   - Does NOT intersect tests/job_queue/, so ZERO expected known-failures.
#   - Any failure is NEW-suspect (report verbatim with assertion signature).
#
# Drift guard pins the run to fix/jobs-status-combo-filter @ 19b40e49.
#
# Dual-layer timeout:
#   - Layer 2 (inner): 150s watchdog here.
#   - Layer 1 (outer): dispatcher's `timeout 300` wrap.
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

set -euo pipefail

PACK_NAME="jobs_combo_unit_test"
EXPECTED_BRANCH="fix/jobs-status-combo-filter"
EXPECTED_COMMIT="19b40e49"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_DIR}"

# --- Drift guard -----------------------------------------------------------
ACTUAL_BRANCH="$(git rev-parse --abbrev-ref HEAD)"
ACTUAL_COMMIT="$(git rev-parse --short HEAD)"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${PROJECT_DIR}"
echo "Branch:  ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT}"
echo "Expected: ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"

if [ "${ACTUAL_BRANCH}" != "${EXPECTED_BRANCH}" ] || [ "${ACTUAL_COMMIT}" != "${EXPECTED_COMMIT}" ]; then
    echo "DRIFT: branch or commit mismatch (expected ${EXPECTED_BRANCH} @ ${EXPECTED_COMMIT}, got ${ACTUAL_BRANCH} @ ${ACTUAL_COMMIT})"
    echo "RESULT: FAIL"
    exit 1
fi

echo "Drift:   OK"
echo

# --- Derived targets (deterministic, no discovery) -------------------------
TARGETS=(
    "tests/integration/test_jobs_status_combo_filter_pin.py"
    "tests/unit/services/test_work_status_terminal_reason_variants.py"
)

echo "Targets (${#TARGETS[@]}):"
for t in "${TARGETS[@]}"; do
    echo "  - ${t}"
done
echo

# --- Deps gate: ensure venv pytest is available ---------------------------
if [ ! -x ".venv/bin/pytest" ]; then
    echo "venv missing — running `uv sync` to provision worktree-local deps"
    uv sync
fi

# Strict-bash idiom per brief: EXIT_CODE=0; cmd || EXIT_CODE=$?
EXIT_CODE=0
timeout 150 .venv/bin/pytest "${TARGETS[@]}" --tb=short -q 2>&1 || EXIT_CODE=$?

echo
if [ "${EXIT_CODE}" -eq 124 ]; then
    echo "RESULT: TIMEOUT (internal 150s cap exceeded)"
    exit 124
elif [ "${EXIT_CODE}" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (pytest exit=${EXIT_CODE})"
    exit 1
fi
