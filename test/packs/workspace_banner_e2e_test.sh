#!/usr/bin/env bash
# test/packs/workspace_banner_e2e_test.sh
#
# Pack: workspace_banner_e2e
# Live UI verification (post-merge gate) for aca8aa2b (merged 3b4da6a6):
# workspace error-banner slim-strip layout + structured tree API error text.
#
# Scenarios (frontend/e2e/workspace-error-banner.spec.ts, serial):
#   T1  banner geometry (<120px, top band, no ws-btn overlap) + structured
#       text ("main_directory", NOT "Http failure") — synthetic project
#       4ad9f91b-2b69-4880-a3fa-464cd52b9ba0 (tree API 400s).
#   T2  interception probe: tab-bar ws-btn clickable while banner visible.
#   T3  success path: scratch project w/ real main_directory (AUTHORIZED
#       fixture; afterAll DELETEs the project + rm -rf's the /tmp dir).
#   T4  conditional dismiss-control check + tab-bar re-verify.
#
# Internal watchdog (Layer 2): 240s — e2e-type limit per test-pack skill.
# Layer 1 (outer) is the dispatcher's `timeout 300` wrap.
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
#
# Prereqs: dev FE :4199 + BE :8079 already running (never started/killed
# by this pack — shared dev infra). Evidence lands in
# frontend/test-results/ (screenshots + workspace-banner-evidence.json).
#
# Ref: agents-ensemble test-pack skill (dual-layer timeout, explicit RESULT).

PACK_NAME="workspace_banner_e2e"
SPEC="e2e/workspace-error-banner.spec.ts"
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
    echo "RESULT: FAIL"
    exit 1
fi

# Prereq smoke — dev infra must be reachable (report-only; we NEVER touch
# these processes, per the shared-dev-infra convention).
for port in 4199 8079; do
    if ! curl -s -o /dev/null --max-time 5 "http://localhost:${port}"; then
        echo "PREREQ FAIL: http://localhost:${port} not reachable"
        echo "RESULT: FAIL (prereq)"
        exit 1
    fi
done
echo "Prereqs: FE :4199 reachable, BE :8079 reachable."
echo

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
