#!/usr/bin/env bash
# Test Pack: c2_pause_cascade_graph_unit_test — C2 deferred pause (Solution A) verification
# Timeout: 5 minutes (300s)
#
# Verifies the C2 fix where ``pause_instance_cascade()`` in
# ``daemon/services/instance_lifecycle.py`` no longer races the
# ``_graph_tasks[instance_id]`` task. The graph task is popped first in
# ``instance_messaging.py`` (both ``ainvoke`` and ``astream`` paths), and
# only then does the deferred-pause cascade run from the ``finally`` block.
#
# Coverage:
#   - tests/unit/test_pause_instance_cascade.py
#   - tests/unit/test_pause_flow_redesign.py
#   - tests/test_graph_task_cancellation.py
#   - tests/unit/test_tree_aware_pause_resume.py
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: c2_pause_cascade_graph_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the command-level hard cap.
timeout 300s .venv/bin/pytest \
  tests/unit/test_pause_instance_cascade.py \
  tests/unit/test_pause_flow_redesign.py \
  tests/test_graph_task_cancellation.py \
  tests/unit/test_tree_aware_pause_resume.py \
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
