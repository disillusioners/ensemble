#!/usr/bin/env bash
# test/packs/app_component_unit_test.sh
#
# Pack: app_component_unit_test
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

PACK_NAME="app_component_unit_test"
SPEC="src/app/app.component.spec.ts"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Spec:    frontend/${SPEC}"
echo "Repo:    ${REPO_ROOT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if [ ! -f "${FRONTEND_DIR}/${SPEC}" ]; then
    echo "FAIL: spec not found at frontend/${SPEC}"
    echo "RESULT: FAIL"
    exit 1
fi

cd "${FRONTEND_DIR}" || {
    echo "FAIL: cannot cd to ${FRONTEND_DIR}"
    echo "RESULT: FAIL"
    exit 1
}

# Layer 2 — internal watchdog. Must interrupt a hung jest via subprocess timeout.
timeout 120 npx jest "${SPEC}" --no-coverage
exit_code=$?

echo
if [ "${exit_code}" -eq 124 ]; then
    echo "RESULT: TIMEOUT (internal 120s cap exceeded)"
    exit 124
elif [ "${exit_code}" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (jest exit=${exit_code})"
    exit 1
fi
