#!/usr/bin/env bash
# Test Pack: hard_delete_mock_test — 3-level tree cascade + checkpoint cleanup
# Timeout: 5 minutes (300s)
#
# Pack for the extended hard-delete cascade mock integration tests at
# ``tests/test_hard_delete_mock_integration.py``. The companion to
# ``test/packs/hard_delete_unit_test.sh`` — that pack covers the
# 2-level tree + API endpoint contracts; this pack covers the deeper
# tree / failure-mode / FK-safety contracts at the repository and
# lifecycle service layers.
#
# Covers (6 scenarios + 1 bonus isolation test):
#   1. 3-level tree cascade complete (root → child → grandchild) —
#      every dependent row in all 10 cascade tables is wiped, and
#      unrelated instances survive untouched.
#   2. Idempotency on a 3-level tree — second call is safe no-op.
#   3. Empty / single-instance tree — leaf-only with no dependents.
#   4. Already-terminated instance hard delete — destructive path is
#      not gated on status.
#   5. Checkpoint cleanup best-effort — service-level: mocked
#      adelete_thread raise; DB cascade still completes and the
#      failed thread IDs surface in checkpoint_errors.
#   6. Cascade order FK-safety — real FKs (job_watchers.instance_id
#      and instance_mappings.source_id) are satisfied at DELETE time.
#
# Uses .venv/bin/pytest because the system pytest is broken on this
# host. The project venv (Python 3.13.3, pytest 9.0.2) works correctly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: hard_delete_mock_test ==="

cd "$PROJECT_DIR"

# Run with timeout - kill if hangs. 300s is the project standard.
timeout 300s .venv/bin/pytest \
  tests/test_hard_delete_mock_integration.py \
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
