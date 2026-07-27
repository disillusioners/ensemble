#!/usr/bin/env bash
# Test Pack: reviewer_v2_validation_test — Reviewer [v2] agent validation
# Timeout: 2 minutes (120s) — script-internal layer (dual-layer guard layer 2)
#
# Validates the reviewer[v2] agent's structural contract (meta.json,
# skill-set.yaml, 6 skill-template frontmatters) and registry version
# resolution, plus the underlying registry versioning + versioning API.
#
# Uses .venv/bin/pytest because the system pytest is broken on this host
# (see c2_core_regression_unit_test.sh). The --override-ini="addopts=" clears
# the default `-m 'not integration and not postgres'` filter so the registry
# versioning tests (unmarked) run.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: reviewer_v2_validation_test ==="

cd "$PROJECT_DIR"

# Layer 2 (internal): 120s cap on the pytest run itself.
timeout 120s .venv/bin/pytest \
  tests/test_registry.py::TestAgentVersioning \
  tests/test_agent_versioning_api.py \
  tests/unit/test_reviewer_v2_agent.py \
  --override-ini="addopts=" \
  --tb=short -q 2>&1

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
