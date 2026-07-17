#!/usr/bin/env bash
# Test Pack: c2_messaging_lifecycle_unit_test
# Timeout: 5 minutes (300s)
#
# Coverage for messaging and lifecycle paths interacting with the C2
# deferred-pause fix (lifecycle terminate/cleanup, compaction guard,
# shared-context/skill injection, multi-reuse lifecycle).
#
# Uses .venv/bin/pytest because the system pytest in /opt/homebrew/bin
# is broken on this host. The project venv (Python 3.13.3, pytest 9.0.2)
# works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: c2_messaging_lifecycle_unit_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the command-level hard cap.
timeout 300s .venv/bin/pytest \
  tests/services/test_instance_lifecycle_h10_l14.py \
  tests/services/test_instance_lifecycle_terminate.py \
  tests/services/test_instance_messaging_compaction_guard.py \
  tests/services/test_instance_messaging_shared_context_injection.py \
  tests/services/test_instance_messaging_skill_injection.py \
  tests/services/test_multi_reuse_lifecycle.py \
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
