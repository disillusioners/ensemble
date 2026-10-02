#!/usr/bin/env bash
# Test Pack: kms_lane1_regression_unit_test
#
# LANE-1 regression: the PRE-EXISTING (8b520d54 base) __KMS_REF__
# minted-handle flow must keep working under the new env-ref sentinel
# lane. These files predate the branch and pin byte-identity for the
# KMS-Lite minted-handle substitute, the resolver, the migration, the
# opendesign builtin, the configure-builtin idempotency, the log
# redaction filter (which must NOT redact __KMS_REF__ markers), and
# the P3 e2e cycle (which exercises the full KMS-Lite lifecycle).
#
# Files covered (all pre-branch, all with __KMS_REF__/KMS_HANDLE pins):
#   tests/unit/services/test_kms_lite.py                   (KMS-Lite store)
#   tests/unit/services/test_kms_resolver.py               (resolver)
#   tests/unit/services/test_kms_raw_row_migration.py      (raw-row migration)
#   tests/unit/test_opendesign_builtin.py                  (builtin — most
#                                                            KMS_REF pins)
#   tests/unit/test_configure_builtin_idempotency.py       (configure-builtin
#                                                            idempotency)
#   tests/unit/test_log_redaction_filter.py                (must not redact
#                                                            __KMS_REF__)
#   tests/unit/test_p3_e2e_cycle.py                        (e2e KMS-Lite
#                                                            lifecycle)
#
# The new-on-branch files (test_ens_env_tools.py,
# test_ens_env_registration.py, test_mcp_set_env_tools.py,
# test_kms_attach_env_ref.py, test_env_key_policy.py) are
# exercised by sibling packs (ens_env_tools, mcp_set_env,
# env_key_kms) — NOT by this regression lane.
#
# Dual-layer timeout per the 2026-10-02 staging convention:
#   - Layer 2 (script-internal): 110s — interrupts hung tests
#   - Layer 1 (command-level):   120s via `timeout` wrapper below
#
# SIZE NOTE: 151 collected tests across 7 files. If a worker observes
# TIMEOUT against this pack, split into per-file packs (the file list
# above is the natural split point) rather than widening the timeout —
# the ≤120s ceiling is the Stage-1 gate contract.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: kms_lane1_regression_unit_test ==="
cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/services/test_kms_lite.py"
  "tests/unit/services/test_kms_resolver.py"
  "tests/unit/services/test_kms_raw_row_migration.py"
  "tests/unit/test_opendesign_builtin.py"
  "tests/unit/test_configure_builtin_idempotency.py"
  "tests/unit/test_log_redaction_filter.py"
  "tests/unit/test_p3_e2e_cycle.py"
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

echo "[note] Running ${#EXISTING_FILES[@]} test file(s):"
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
