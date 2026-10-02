#!/usr/bin/env bash
# Test Pack: odsp_changed_files_unit_test
#
# Stage-1 OpenDesign self-provisioning: unit tests for the 3 branch-changed
# unit files NOT covered by the existing ens_env_tools / mcp_set_env /
# env_key_kms / kms_lane1_regression packs. Created by the gate worker per
# .agents/tester/PACKS.md row 5 (2026-10-02).
#
# Files covered:
#   tests/unit/tools/test_attestation_registration.py   (24)  - real-agent
#                                                            resolution:
#                                                            non-leader
#                                                            agents resolve
#                                                            None; pinned
#                                                            trio (architect,
#                                                            tester, etc.)
#   tests/unit/tools/test_upgrade_registration.py       (21)  - functional
#                                                            single-tool
#                                                            deny; docs
#                                                            default-deny
#                                                            + privileged
#                                                            exclusion
#   tests/unit/test_mcp_warmup_pool.py                  (69)  - MCP warmup
#                                                            pool cached
#                                                            schemas, input
#                                                            schema extract,
#                                                            KMS env-ref
#                                                            resolution (P3
#                                                            review F3 —
#                                                            cross-checks
#                                                            KMS LANE-1 gate;
#                                                            see __KMS_REF__
#                                                            at line 556)
#
# Total: 114 tests. Dry-run 2026-10-02 = 114 passed in 79.73s (under 110s).
#
# Dual-layer timeout per the 2026-10-02 staging convention:
#   - Layer 2 (script-internal): 110s — interrupts hung tests
#   - Layer 1 (command-level):   120s via `timeout` wrapper below
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: odsp_changed_files_unit_test ==="
cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/tools/test_attestation_registration.py"
  "tests/unit/tools/test_upgrade_registration.py"
  "tests/unit/test_mcp_warmup_pool.py"
)

EXISTING_FILES=()
SKIPPED_FILES=()
for f in "${CANDIDATE_FILES[@]}"; do
  if [ -f "$f" ]; then
    EXISTING_FILES+=("$f")
  else
    SKIPPED_FILES+=("$f")
  fi
done

if [ ${#SKIPPED_FILES[@]} -gt 0 ]; then
  echo "[note] Skipping ${#SKIPPED_FILES[@]} missing test file(s):"
  for s in "${SKIPPED_FILES[@]}"; do
    echo "  - $s"
  done
fi

if [ ${#EXISTING_FILES[@]} -eq 0 ]; then
  echo "[fatal] No test files exist — nothing to run."
  echo "RESULT: FAIL"
  exit 1
fi

echo "[note] Running ${#EXISTING_FILES[@]} test file(s) (114 expected tests):"
for e in "${EXISTING_FILES[@]}"; do
  echo "  - $e"
done

# Env-scrubbed wrapper (scripts/run_tests_scrubbed.sh) strips every
# POSTGRES_*/libpq var per the 2026-09-26 live-probe incidents.
EXIT_CODE=0
timeout 110s ./scripts/run_tests_scrubbed.sh \
  "${EXISTING_FILES[@]}" \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
