#!/usr/bin/env bash
# Test Pack: stability_quick_wins_2_suites_unit_test — Quick-Wins #2 merge gate
# (feature/stability-quick-wins-2 @ b1159eca)
# Timeout: 2 minutes (120s) — target < 2 min runtime
#
# Covers TWO new branch acceptance test files that lack pack coverage
# until this script lands. Both files are part of the
# feature/stability-quick-wins-2 branch and have NO existing individual
# packs — this pack is the merge-gate view over both, mirroring
# buffer_response_header_family_unit_test.sh's single-shot gate pattern.
#
#   1. tests/unit/services/test_stability_quick_wins_2.py (11 tests)
#      Quick-Wins #2 items 1 + 2 (carrier / bus-fire):
#        - TestSendGateIgnoresTerminalInstanceCarriers ×4
#        - TestBusFirePostEnqueueRepurge          ×4
#        - 3 supporting tests
#      Branch gate acceptance file for the SendGate terminal-carrier
#      ignore and the bus-fire post-enqueue repurge fixes.
#   2. tests/unit/test_task_only_create_notify_work.py (4 tests)
#      Quick-Wins #2 item 5 (W3 — task_only_create + message_only_recreate):
#        - sync + async variants for both task_only_create and
#          message_only_recreate paths
#      Branch gate acceptance file for the task-only-create / notify-work
#      wiring.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 120s` pytest guard
#
# No deselection: no QUARANTINE.md entries for either file. These are the
# branch's NEW acceptance suites — running them green is the gate.
set -euo pipefail

# ─── SSL cleanup ─────────────────────────────────────────────────────────────
unset SSL_CERT_FILE
unset SSL_CERT_DIR

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: stability_quick_wins_2_suites_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/services/test_stability_quick_wins_2.py \
  tests/unit/test_task_only_create_notify_work.py \
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
