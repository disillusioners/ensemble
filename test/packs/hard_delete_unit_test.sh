#!/usr/bin/env bash
# Test Pack: hard_delete_unit_test — instance hard delete cascade
# Timeout: 5 minutes (300s)
#
# Pack for the hard-delete cascade feature implemented in
# ``daemon/repository.py::hard_delete_tree`` and
# ``daemon/services/instance_lifecycle.py::hard_delete_instance``.
# The cascade wipes all related DB rows in a single transaction across
# 10 tables (job_locks, job_queue_items, job_watchers, tasks, events,
# message_queue, dependency_watchers, instance_mappings,
# instance_hierarchy, instances) for every id returned by
# ``InstanceRepository.get_tree_ids()``.
#
# Covers:
#   - tests/test_instance_hard_delete.py
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: hard_delete_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the hard-delete-test hard cap.
timeout 300s .venv/bin/pytest \
  tests/test_instance_hard_delete.py \
  -v --tb=short -q 2>&1

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
