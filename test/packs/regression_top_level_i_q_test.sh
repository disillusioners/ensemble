#!/usr/bin/env bash
# Test Pack: regression_top_level_i_q — top-level test_[i-q]*.py + service dirs
# Scope: tests/test_[i-q]*.py + tests/{services,repositories}/ (2,443 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Co-locates the i-q loose test files with the two heavy top-level dirs
# (services=592, repositories=378) — buckets are balanced by total test
# count, not by dir count. Largest non-job_queue partition at 2,443 tests;
# xdist parallelization is essential to keep wall time under the 280s
# cap (M1 P5 reference: 2,749 in 82s).
#
# No quarantine deselects in this partition's scope (loose i-q files +
# services/ + repositories/ do not host any of the 4 hardcoded
# deselects — completion_regression's test_dependency_bus.py is in the
# a-h bucket, turn_transitions_reconciler's test_turn_state_machine.py
# is in the property/ dir which lives in the a_h pack).
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_top_level_i_q [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s uv run pytest \
  $(ls tests/test_[i-q]*.py 2>/dev/null) \
  tests/services/ \
  tests/repositories/ \
  -n auto --tb=short -q -rf \
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
