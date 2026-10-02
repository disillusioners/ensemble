#!/usr/bin/env bash
# Test Pack: env_key_kms_unit_test
#
# Stage-1 OpenDesign self-provisioning: the new shared ``env_key_policy``
# + the env-ref sentinel lane that lets ``kms_attach`` accept a bare env
# var name (e.g. ``BYOK_API_KEY``) instead of a literal value, with the
# actual value held in KMS-Lite in-memory and substituted at MCP spawn.
#
# Files covered:
#   tests/unit/services/test_env_key_policy.py  (W2 shared env-key policy)
#   tests/unit/tools/test_kms_attach_env_ref.py (env-ref sentinel lane
#                                                for kms_attach)
#   tests/unit/services/test_kms_resolver.py    (resolver accepts env-ref
#                                                IN ADDITION to its
#                                                pre-existing marker lane)
#
# Dual-layer timeout per the 2026-10-02 staging convention:
#   - Layer 2 (script-internal): 110s — interrupts hung tests
#   - Layer 1 (command-level):   120s via `timeout` wrapper below
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: env_key_kms_unit_test ==="
cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/services/test_env_key_policy.py"
  "tests/unit/tools/test_kms_attach_env_ref.py"
  "tests/unit/services/test_kms_resolver.py"
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
