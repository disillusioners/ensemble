#!/usr/bin/env bash
# Test Pack: regression_opencode_e2e_test
# Scope: tests/opencode/ + tests/e2e/ (excluding tests/e2e/test_context_injection_hybrid.py
# — a file that connection-errors at collection time against a stopped daemon;
# that file has its own PG/connectivity gates elsewhere and is NOT in this pack's
# contract).
# Timeout: 2 minutes (120s) — 17s solo per measurement 2026-09-19
# (merge-gate tester observation); ~120s with xdist under load.
#
# Split from regression_integration_opencode_e2e_test.sh (P-12 in
# PACKS.md) — once the integration slice is hoisted into
# regression_integration_test.sh + regression_chat_source_integration_test.sh
# (chat-source split), this pack handles the opencode + e2e halves
# which ran solo in 17s at the merge gate.
#
# Deselect machinery:
#   * --deselect tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume
#     (QUARANTINE.md 2026-08-21; replicated from the original pack —
#     post-resume terminal-status stall, Task↔JobItem reconciliation
#     gap family).
#   * --ignore=tests/e2e/test_context_injection_hybrid.py (live-daemon
#     collection-error; not in this pack's contract).
#
# Pre-existing reds (DOCUMENTED, NOT addressed here): the e2e
# "answer_dismiss / pause_during_report" test family (4 tests) is
# the same pre-existing red set the original pack surfaced per the
# merge-gate tester observation ("complete_cancel ×4"). Out of scope
# for this split.
#
# Invocation contract:
#   timeout 120 bash test/packs/regression_opencode_e2e_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_opencode_e2e_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

EXIT_CODE=0
timeout 110s .venv/bin/python -m pytest \
  tests/opencode/ \
  tests/e2e/ \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  --ignore=tests/e2e/test_context_injection_hybrid.py \
  --deselect "tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume" \
  2>&1 || EXIT_CODE=$?
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
