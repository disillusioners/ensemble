#!/usr/bin/env bash
# Test Pack: ri_off_behavioral_probe_test — P2 "OFF means OFF" behavioral probe
#
# Gate scope 2: THE core shipping-posture proof for the P2 report-integrity
# gate (branch feature/wc-wake-report-integrity). The shipping state is the
# kill-switch WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED OFF
# (unset). This pack authors and runs the BEHAVIORAL probe on the REAL
# completion path (JobFeedbackObserver._finalize_job — the production
# observer_finalize_job site) over a file-backed SQLite seam:
#
#   1. incident shape seeded in durable rows → REAL finalize with flag
#      UNSET → exactly ONE [ReportIntegrityGuard] soak log line, ZERO
#      durable writes by the guard, completion PROCEEDS, NO gate delay
#      (short-circuit < 100 ms; NOTICE budget never awaited);
#   2. parity: flag explicitly "0" behaves identically;
#   3. S3 scoping spot: flag OFF does NOT touch the always-on Wave-1
#      instruments ((c) marker suffix-append + NR-3 junk counter).
#
# Dual-layer timeout (test-pack skill, MANDATORY):
#   Layer 1 (caller):  timeout 300 ./test/packs/ri_off_behavioral_probe_test.sh
#   Layer 2 (here):    timeout 200 (script-internal — integration class,
#                      < the 300 s absolute cap)
#
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
# Expected: 4 tests (unset / explicit_zero / ON-contrast control / s3 spot)

PACK_NAME="ri_off_behavioral_probe_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PACK_START="$(date +%s)"
INTERNAL_TIMEOUT_S=200

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${REPO_ROOT}"
echo "Probe:   tests/integration/test_ri_off_behavioral_probe.py"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

cd "${REPO_ROOT}" || {
    echo "FAIL: cannot cd to ${REPO_ROOT}"
    echo "RESULT: FAIL"
    exit 1
}

if [ ! -x ".venv/bin/pytest" ]; then
    echo "FAIL: .venv/bin/pytest not found (run: uv sync)"
    echo "RESULT: FAIL"
    exit 1
fi

# Belt-and-braces: the probe pins the flag per sub-case via monkeypatch,
# but the baseline process env must NOT pre-flip the switch (ship state =
# unset). Sub-case "explicit_zero" re-sets it to "0" inside the probe.
unset WC_REPORT_INTEGRITY_B_TERMINAL_WAITING_GUARD_ENABLED 2>/dev/null || true

# Layer 2 — script-internal timeout interrupts a hung run.
timeout "${INTERNAL_TIMEOUT_S}s" .venv/bin/pytest \
    tests/integration/test_ri_off_behavioral_probe.py \
    --tb=short -q
PYTEST_RC=$?

PACK_END="$(date +%s)"
ELAPSED=$((PACK_END - PACK_START))
echo
echo "Pack elapsed: ${ELAPSED}s (internal cap ${INTERNAL_TIMEOUT_S}s)"

if [ "${PYTEST_RC}" -eq 124 ]; then
    echo "RESULT: TIMEOUT"
    exit 124
elif [ "${PYTEST_RC}" -eq 0 ]; then
    echo "RESULT: PASS"
    exit 0
else
    echo "RESULT: FAIL"
    exit 1
fi
