#!/usr/bin/env bash
# Test Pack: c2_cleanup_resume_unit_test
# Timeout: 5 minutes (300s)
#
# Coverage for hard-delete cleanup and resume-gate / resume-children paths
# interacting with the C2 deferred-pause fix (instance state cleanup,
# resume gating, child resume / notification / waiting-children logic).
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: c2_cleanup_resume_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the command-level hard cap.
timeout 300s .venv/bin/pytest \
  tests/test_instance_hard_delete.py \
  tests/test_hard_delete_mock_integration.py \
  tests/test_resume_gate.py \
  tests/unit/test_resume_child_notification.py \
  tests/unit/test_resume_message_append.py \
  tests/unit/test_resume_waiting_children.py \
  tests/unit/test_child_resume.py \
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
