#!/usr/bin/env bash
# Test Pack: regression_integration_test (non-chat-source slice)
# Scope: tests/integration/ EXCLUDING tests/integration/test_chat_source_*.py
# (962 tests at 2026-09-19). The chat-source family is hoisted into
# regression_chat_source_integration_test.sh — see that pack for the
# addopts-defect fix.
# Timeout: 5 minutes (300s) — 283s solo per measurement 2026-09-19
# (merge-gate tester observation for the integration slice under xdist
# load); fits under the 290s inner / 300s outer dual-layer guard.
#
# Split from regression_integration_opencode_e2e_test.sh (P-12 in
# PACKS.md) — that combined pack breached 300s solo at the merge
# gate. The split shape is:
#
#   * regression_chat_source_integration_test.sh — chat-source-only,
#     --override-ini="addopts=" -m integration (fixes the addopts
#     defect; 50 tests, ~22s).
#   * regression_integration_test.sh (this pack) — non-chat-source
#     integration slice (962 tests, ~283s).
#   * regression_opencode_e2e_test.sh — tests/opencode/ + tests/e2e/
#     (~520 tests, ~17s).
#
# All three of the new packs fit under their respective 290s inner
# guards (this one's 283s is the tightest; 7s margin).
#
# Invocation contract:
#   timeout 300 bash test/packs/regression_integration_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_integration_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Integration-slice regression pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 290s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 290s .venv/bin/python -m pytest \
  tests/integration/ \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  --ignore-glob='**/test_chat_source_*.py' \
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
