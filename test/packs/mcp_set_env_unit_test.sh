#!/usr/bin/env bash
# Test Pack: mcp_set_env_unit_test
#
# Stage-1 OpenDesign self-provisioning: the new ``mcp_set_env`` tool surface
# (daemon/tools/ens_env_tools.py + daemon/routers/mcp_servers.py lane).
# Pinned behaviors per the recon-mandated test rig:
#   1. Happy path — merge into existing config.env preserving other keys
#      AND __KMS_REF__ markers byte-identical (R1 co-ownership).
#   2. Unknown server name → clear SERVER_NOT_FOUND error.
#   3. Secret-shaped key rejection (KEY/TOKEN/SECRET/PASSWORD substring)
#      → error points at kms_request / kms_attach; row untouched.
#   4. R1 freshness — a marker written by a concurrent lane (simulated by
#      a direct repo write BETWEEN two tool invocations) is NOT clobbered
#      by the second invocation. Proves the tool reads the row FRESH at
#      call time (no factory-time or first-call snapshot).
#   5. Schema-cache invalidation is called with the server NAME (cache is
#      name-keyed).
#   6. No key material in logs — audit lines carry key NAMES + counts only.
#   7. Trailing-newline pin: every Task-A file (test_ens_env_tools.py +
#      test_ens_env_registration.py + this file) ends in "\n" — prevents
#      edit-time regressions that flake the AST-source discovery.
#   8. Registration seam: KNOWN_TOOL_NAMES, CATEGORY_MODULES, instance.py
#      list-extend, worker ``infra`` opt-in, AST-source discovery.
#
# File covered:
#   tests/unit/tools/test_mcp_set_env_tools.py
#
# Dual-layer timeout per the 2026-10-02 staging convention:
#   - Layer 2 (script-internal): 110s — interrupts hung tests
#   - Layer 1 (command-level):   120s via `timeout` wrapper below
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: mcp_set_env_unit_test ==="
cd "$PROJECT_DIR"

CANDIDATE_FILES=(
  "tests/unit/tools/test_mcp_set_env_tools.py"
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
