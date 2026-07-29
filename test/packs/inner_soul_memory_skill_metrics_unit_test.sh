#!/usr/bin/env bash
# Test Pack: inner_soul_memory_skill_metrics_unit_test
# Ad-hoc pack for W1+W2 changes in commit 9c2d95cc:
#   - daemon/tools/access_memory.py (version-aware path resolution)
#   - daemon/tools/inner_soul.py (version-aware path resolution)
#   - daemon/services/skill_metrics_service.py (version-aware skill injection gate)
# Timeout: 2 minutes (120s)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: inner_soul_memory_skill_metrics_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/tools/test_inner_soul_rejection.py \
  tests/unit/tools/test_inner_soul_compaction.py \
  tests/unit/tools/test_inner_soul_compound.py \
  tests/unit/tools/test_inner_soul_persona_preservation.py \
  tests/unit/tools/test_inner_soul_redirect.py \
  tests/unit/tools/test_memory_edge_cases.py \
  tests/services/test_skill_metrics_service.py \
  tests/services/test_skill_metric_scan.py \
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
