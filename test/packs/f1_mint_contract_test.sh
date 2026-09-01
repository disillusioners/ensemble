#!/usr/bin/env bash
# test/packs/f1_mint_contract_test.sh
#
# Pack: f1_mint_contract_test — f1-misfire gate scope 6
# Branch: feature/f1-misfire-fix @ e6cd5fc8
#
# Mint / linkage contract gate. Verifies the shared tripwire
# ``_assert_linkage_contract`` (daemon/services/messaging_types.py:41) is
# wired at all 4 documented call sites, that the structurally-safe
# enqueue_message_job mint site is documented as not-needing-a-tripwire,
# that the 4 call sites carry the right source= label, and that the
# processor tripwire WARN fires on a real linkage violation.
#
# Components:
#   1. Dev-test wrap — 4 tests at tests/job_queue/test_orphan_active_job_recovery.py
#        ::TestObserverMintLinkageContract (2 tests: kwargs carry work_id
#                                           + observer tripfire WARN)
#        ::TestProcessorRespawnMintLinkageContract (2 tests: crash-recovery
#                                                   + orphan-resume kwargs)
#   2. Behavioral gap-check — tests/job_queue/test_f1_mint_processor_tripfire.py
#        drives the JobProcessor crash-recovery branch with a MISMATCHED
#        enqueue result, asserts the linkage WARN fires AND dispatch
#        never-fails (council W1).
#   3. Structural greps — 4 tripwire call sites present, structurally-safe
#        comment present at instance_messaging.py:2138-2149, source=
#        labels correct (Observer + 3x JobProcessor).
#
# Timeout: dual-layer — script-internal 150s (Layer 2), caller wraps with
#          `timeout 300` (Layer 1, 5-min hard cap).
# Exit codes: 0=PASS, 1=FAIL, 124=TIMEOUT.
# Test code only; daemon/ is read-only.

set -uo pipefail

PACK_NAME="f1_mint_contract_test"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"

cd "${PROJECT_DIR}"

echo "=== Test Pack: ${PACK_NAME} ==="
echo "Repo:    ${PROJECT_DIR}"
echo "Branch:  $(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo

# ── 1) Dev-test wrap (4 tests by node ID, explicit list — no broad dirs) ──
echo "── [1/3] dev-test wrap (4 tests by node ID) ──"
DEV_OUT=$(timeout 60s .venv/bin/pytest \
  "tests/job_queue/test_orphan_active_job_recovery.py::TestObserverMintLinkageContract" \
  "tests/job_queue/test_orphan_active_job_recovery.py::TestProcessorRespawnMintLinkageContract" \
  --override-ini="addopts=" --tb=short -q 2>&1)
DEV_EXIT=$?
echo "${DEV_OUT}" | tail -5
DEV_PASSED=$(echo "${DEV_OUT}" | grep -oE "[0-9]+ passed" | head -1 || echo "0 passed")
echo "dev-tests exit=${DEV_EXIT} — ${DEV_PASSED}"
echo

# ── 2) Behavioral gap-check (processor tripfire spot) ──
echo "── [2/3] behavioral gap-check — processor tripfire WARN ──"
BEH_OUT=$(timeout 60s .venv/bin/pytest \
  "tests/job_queue/test_f1_mint_processor_tripfire.py" \
  --override-ini="addopts=" --tb=short -q 2>&1)
BEH_EXIT=$?
echo "${BEH_OUT}" | tail -5
BEH_PASSED=$(echo "${BEH_OUT}" | grep -oE "[0-9]+ passed" | head -1 || echo "0 passed")
echo "behavioral exit=${BEH_EXIT} — ${BEH_PASSED}"
echo

# ── 3) Structural greps (read-only, in-pack verification) ──
echo "── [3/3] structural greps (in-pack, read-only) ──"
STR_FAIL=0

# (a) 4 tripwire call sites + 1 definition (expect ≥5 total matches).
CALL_SITES=$(grep -c "_assert_linkage_contract" \
  daemon/services/messaging_types.py \
  daemon/services/job_feedback_observer.py \
  daemon/services/job_processor.py 2>/dev/null \
  | awk -F: '{s+=$2} END {print s}')
# messaging_types.py:1 (def), job_feedback_observer.py:2 (import + 1 call),
# job_processor.py:4 (import + 3 calls) → total 7.
if [ "${CALL_SITES}" -lt 5 ]; then
  echo "  [FAIL] (a) expected ≥5 _assert_linkage_contract matches across 3 files, got ${CALL_SITES}"
  STR_FAIL=1
else
  echo "  [PASS] (a) ${CALL_SITES} matches across messaging_types / observer / processor"
fi

# (b) Structural-safety comment at instance_messaging.py:2138-2149 — verify
# the distinctive phrase is present.
if grep -qF "no re-mint seam to trip over" daemon/services/instance_messaging.py; then
  echo "  [PASS] (b) structural-safety comment present in instance_messaging.py"
else
  echo "  [FAIL] (b) structural-safety comment missing — expected 'no re-mint seam to trip over'"
  STR_FAIL=1
fi

# (c) source= labels — observer uses "Observer", processor uses "JobProcessor".
if grep -qE 'source="Observer"' daemon/services/job_feedback_observer.py; then
  echo "  [PASS] (c1) observer tripwire uses source=\"Observer\""
else
  echo "  [FAIL] (c1) observer tripwire missing source=\"Observer\""
  STR_FAIL=1
fi

PROC_SOURCES=$(grep -cE 'source="JobProcessor"' daemon/services/job_processor.py)
if [ "${PROC_SOURCES}" -eq 3 ]; then
  echo "  [PASS] (c2) all 3 processor call sites use source=\"JobProcessor\""
else
  echo "  [FAIL] (c2) expected 3 source=\"JobProcessor\" call sites, got ${PROC_SOURCES}"
  STR_FAIL=1
fi
echo

# ── Aggregate ──
echo "── Aggregate ──"
if [ "${DEV_EXIT}" -eq 124 ]; then
  echo "Dev-test wrap TIMEOUT (exit 124)"
  echo "RESULT: TIMEOUT"
  exit 124
fi
if [ "${BEH_EXIT}" -eq 124 ]; then
  echo "Behavioral gap-check TIMEOUT (exit 124)"
  echo "RESULT: TIMEOUT"
  exit 124
fi
if [ "${DEV_EXIT}" -ne 0 ] || [ "${BEH_EXIT}" -ne 0 ] || [ "${STR_FAIL}" -ne 0 ]; then
  echo "Dev-tests:    exit=${DEV_EXIT} (${DEV_PASSED})"
  echo "Behavioral:   exit=${BEH_EXIT} (${BEH_PASSED})"
  echo "Structural:   exit=${STR_FAIL} (0=PASS, 1=FAIL)"
  echo "RESULT: FAIL"
  exit 1
fi

echo "Dev-tests:    exit=0 (${DEV_PASSED})"
echo "Behavioral:   exit=0 (${BEH_PASSED})"
echo "Structural:   3/3 sub-checks PASS"
echo "RESULT: PASS"
exit 0
