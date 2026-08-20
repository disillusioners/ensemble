#!/usr/bin/env bash
# test/packs/adjacent_chat_unit_test.sh
#
# Pack: adjacent_chat_unit_test
# Internal watchdog (Layer 2): 120s — unit-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Discovers adjacent unit specs (InstancesViewStateService + chat page) at run
# time; only specs that exist on disk are passed to jest. If none exist, the
# pack fails fast rather than running the whole suite.
#
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

PACK_NAME="adjacent_chat_unit_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
FRONTEND_DIR="${REPO_ROOT}/frontend"

# Candidate adjacent specs (exist-checked at run time).
CANDIDATE_SPECS=(
    "src/app/services/instances-view-state.service.spec.ts"
    "src/app/pages/chat/chat.component.spec.ts"
)

declare -a SPECS=()
for candidate in "${CANDIDATE_SPECS[@]}"; do
    if [ -f "${FRONTEND_DIR}/${candidate}" ]; then
        SPECS+=("${candidate}")
    fi
done

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Specs (${#SPECS[@]}):"
for s in "${SPECS[@]}"; do
    echo "  - frontend/${s}"
done
echo "Repo:    ${REPO_ROOT}"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

if [ ${#SPECS[@]} -eq 0 ]; then
    echo "FAIL: no adjacent spec files found on disk"
    echo "RESULT: FAIL"
    exit 1
fi

cd "${FRONTEND_DIR}" || {
    echo "FAIL: cannot cd to ${FRONTEND_DIR}"
    echo "RESULT: FAIL"
    exit 1
}

# Layer 2 — internal watchdog.
timeout 120 npx jest "${SPECS[@]}" --no-coverage
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
