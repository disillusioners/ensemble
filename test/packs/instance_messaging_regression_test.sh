#!/usr/bin/env bash
# Test Pack: instance_messaging_regression_test — injection hooks regression
# Timeout: 2 minutes (120s)
#
# Regression pack for the injection hooks wired into
# ``_process_message_with_tracking`` in ``daemon/services/instance_messaging.py``:
#   - tests/services/test_instance_messaging_skill_injection.py        (pre-existing)
#   - tests/services/test_instance_messaging_shared_context_injection.py (new)
#
# The skill-injection coverage validates the pre-existing once-per-instance
# ``skill_injected`` flag and leader→child skill rendering. The
# shared-context-injection coverage validates the new
# ``shared_context_injected`` flag and the leader→child message-body
# injection that mirrors the existing ``project_injected`` semantics
# (flag is NOT set on failure/empty, so late-arriving metadata is
# picked up on the next message).
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: instance_messaging_regression_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 120s is the services-test hard cap.
timeout 120s .venv/bin/pytest \
  tests/services/test_instance_messaging_skill_injection.py \
  tests/services/test_instance_messaging_shared_context_injection.py \
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
