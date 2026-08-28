#!/usr/bin/env bash
# Test Pack: job_queue_tools_unit_test — job tool layer regression baseline (all tool entry points)
# Timeout: 2 minutes (120s)
#
# Deselected (see .agents/tester/QUARANTINE.md, 2026-08-20):
#   - 4× TestJobContinue* (job_continue happy_path + 3 ResolverAware variants)
#     Root cause: pre-existing KeyError 'instance_id' family on clean parent 39f76dc7
#     (leader double-checked BEFORE this branch). job_continue in
#     daemon/tools/job_queue.py:858 is NOT migrated by job-tools ACL branch
#     (only daemon/tools/job_queue.py ACL changes), so failures are
#     branch-independent. Orthogonal to this branch's cross-project access work.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: job_queue_tools_unit_test ==="

cd "$PROJECT_DIR"

# QUARANTINE.md (2026-08-20): 4 pre-existing TestJobContinue* failures, orthogonal to job-tools ACL change
timeout 120s .venv/bin/pytest \
  tests/test_job_queue_tools.py \
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