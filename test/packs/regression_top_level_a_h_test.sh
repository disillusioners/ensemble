#!/usr/bin/env bash
# Test Pack: regression_top_level_a_h — top-level test_[a-h]*.py + small dirs
# Scope: tests/test_[a-h]*.py + tests/{api,manager,property,migration,static,performance,lint}/ (1,072 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Quarantine-aware deselects (replicated from existing packs per
# partition-scope rule):
#   - tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert
#     (StaticPool session-refresh race — QUARANTINE.md 2026-08-29, replicated
#     from completion_regression_test.sh; this partition covers that
#     file's scope via the tests/test_d*.py bucket.)
#   - tests/property/test_turn_state_machine.py::TestTurnReconcilerStateMachine::test_state_machine
#     (pre-existing stale assert — QUARANTINE.md 2026-08-20, replicated
#     from turn_transitions_reconciler_unit_test.sh; this partition
#     includes tests/property/ in its dir scope.)
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_top_level_a_h [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s uv run pytest \
  $(ls tests/test_[a-h]*.py 2>/dev/null) \
  tests/api/ \
  tests/manager/ \
  tests/property/ \
  tests/migration/ \
  tests/static/ \
  tests/performance/ \
  tests/lint/ \
  -n auto --tb=short -q -rf \
  --deselect "tests/test_dependency_bus.py::TestGenerationCounterBump::test_per_parent_lock_serializes_db_insert" \
  --deselect "tests/property/test_turn_state_machine.py::TestTurnReconcilerStateMachine::test_state_machine" \
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
