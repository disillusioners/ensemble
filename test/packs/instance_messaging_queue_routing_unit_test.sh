#!/usr/bin/env bash
# Test Pack: instance_messaging_queue_routing_unit_test — queue_id routing
# Timeout: 2 minutes (120s)
#
# Unit pack for the optional ``queue_id`` parameter on
# ``InstanceMessagingService.enqueue_message_job`` and the HTTP message route
# that forwards to it (NORMAL / IDLE branch):
#   - tests/services/test_instance_messaging_queue_routing.py (8 tests)
#
# Covers four contract scenarios:
#   - queue_id=None (omitted) -> legacy default system_parallel_queue
#   - queue_id=<valid id in project> -> that queue is used
#   - queue_id=<id from different project> -> fallback to default, WARNING
#   - queue_id=<nonexistent id> -> fallback to default, WARNING
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: instance_messaging_queue_routing_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 120s is the services-test hard cap.
timeout 120s .venv/bin/pytest \
  tests/services/test_instance_messaging_queue_routing.py \
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
