#!/usr/bin/env bash
# Test Pack: c2_question_deferred_pause_unit_test — C2 deferred pause (Solution A) verification
# Timeout: 5 minutes (300s)
#
# Verifies the C2 fix where ``question_pause_node`` no longer calls
# ``pause_instance_cascade()`` from within the graph task. Instead it sets
# a deferred-pause marker via ``manager.set_deferred_question_pause`` and the
# actual cascade runs AFTER the graph completes, in the ``finally`` block of
# ``_process_message_with_tracking`` / ``send_message`` in
# ``daemon/services/instance_messaging.py`` — after ``_graph_tasks.pop`` —
# so the cascade cannot self-cancel the running task.
#
# Coverage:
#   - tests/unit/test_question_deferred_pause_callback.py
#   - tests/unit/test_question_graph.py
#   - tests/test_question_manager.py
#   - tests/test_question_tools.py
#   - tests/test_question_untested_paths.py
#   - tests/unit/services/test_question_pause_completion_guard.py
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: c2_question_deferred_pause_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the command-level hard cap.
timeout 300s .venv/bin/pytest \
  tests/unit/test_question_deferred_pause_callback.py \
  tests/unit/test_question_graph.py \
  tests/test_question_manager.py \
  tests/test_question_tools.py \
  tests/test_question_untested_paths.py \
  tests/unit/services/test_question_pause_completion_guard.py \
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
