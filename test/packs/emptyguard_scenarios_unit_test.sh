#!/usr/bin/env bash
# Test Pack: emptyguard_scenarios_unit_test — Empty-response-guard scenario-level unit tests
# Ad-hoc pack for leader's P1 a-k coverage (worktree-local, uncommitted).
# Timeout: outer 280s (bash timeout) + inner 60s (pytest --override-ini=timeout=60) per-test.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: emptyguard_scenarios_unit_test ==="

cd "$PROJECT_DIR"

timeout 280s .venv/bin/pytest \
  tests/unit/test_empty_response_guard.py \
  tests/unit/test_empty_guard_config.py \
  tests/unit/test_response_validation.py \
  tests/unit/test_nudge_behavior.py \
  tests/unit/test_llm_error_classifier.py \
  tests/unit/test_attestation_construction_site_sweep.py \
  tests/unit/services/test_keyword_extraction.py \
  --override-ini="timeout=60" \
  -rfE -p no:cacheprovider --tb=short -q 2>&1

EXIT_CODE=$?

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