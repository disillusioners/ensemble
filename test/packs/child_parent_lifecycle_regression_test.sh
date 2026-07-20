#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: child_parent_lifecycle_regression_test ==="
cd "$PROJECT_DIR"

# Broader regression: child/parent completion notification + instance lifecycle
# + message processing pipeline (Stage 6 child completion) + work resolver.
# All SQLite. Files verified to exist on fix/wanderer-completion-reporting branch.
# Script-internal timeout (Layer 2): 280s
# Command-level timeout (Layer 1): 300s
timeout 280s .venv/bin/pytest \
  tests/unit/services/test_child_reports.py \
  tests/unit/test_resume_child_notification.py \
  tests/unit/test_root_instance_completion.py \
  tests/unit/test_resume_waiting_children.py \
  tests/unit/test_instance_children_junction_c10.py \
  tests/unit/test_ready_message_completion_report.py \
  tests/services/test_instance_lifecycle_h10_l14.py \
  tests/services/test_instance_lifecycle_terminate.py \
  tests/test_instance_cascade.py \
  tests/test_pipeline_unified.py \
  tests/test_report_lane_phase2.py \
  tests/unit/services/test_work_resolver.py \
  -v --override-ini="addopts=" --tb=short -q 2>&1
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