#!/usr/bin/env bash
# test/packs/hide_button_symptom_e2e.sh
#
# Pack: hide_button_symptom_e2e
# Internal watchdog (Layer 2): 240s — e2e-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# THIN RUNNER. The hide-button-symptom spec is NOT yet authored — pack 4 worker
# owns the spec file. If the spec is missing at run time, this pack fails fast
# with "SPEC MISSING" so the test leader can route the missing-spec follow-up
# without this script blocking on a hung playwright invocation.
#
# When the spec is present, the script runs playwright in serial mode
# (--workers=1) and exits 0/1/124 per the test-pack contract.
#
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

PACK_NAME="hide_button_symptom_e2e"
SPEC="e2e/hide-button-symptom.spec.ts"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Spec:    frontend/${SPEC}"
echo "Repo:    ${REPO_ROOT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if [ ! -f "${FRONTEND_DIR}/${SPEC}" ]; then
    echo "SPEC MISSING: frontend/${SPEC}"
    echo "Pack 4 worker must author this spec before pack D can pass."
    echo "RESULT: FAIL"
    exit 1
fi

cd "${FRONTEND_DIR}" || {
    echo "FAIL: cannot cd to ${FRONTEND_DIR}"
    echo "RESULT: FAIL"
    exit 1
}

# Layer 2 — internal watchdog.
timeout 240 npx playwright test "${SPEC}" --workers=1 --reporter=line
exit_code=$?

echo
if [ "${exit_code}" -eq 124 ]; then
    echo "RESULT: TIMEOUT (internal 240s cap exceeded)"
    exit 124
elif [ "${exit_code}" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL (playwright exit=${exit_code})"
    exit 1
fi
