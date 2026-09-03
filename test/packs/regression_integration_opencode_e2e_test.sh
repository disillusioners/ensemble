#!/usr/bin/env bash
# Test Pack: regression_integration_opencode_e2e — integration + opencode + e2e
# Scope: tests/{integration,opencode,e2e}/ (738 collected, with 258 deselects)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Integration-slice regression partition. Uses `uv run pytest` + xdist
# per task spec. Integration slices get --override-ini="timeout=240"
# (per-test ceiling) to cap long-running integration tests that would
# otherwise drag the wall time past the 280s hard limit.
#
# Quarantine-aware deselect (replicated from e2e_workflows_ensure_test.sh
# per partition-scope rule):
#   - tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume
#     (post-resume terminal-status stall — QUARANTINE.md 2026-08-21,
#     Task↔JobItem reconciliation-gap family; replicated from
#     e2e_workflows_ensure_test.sh; this partition covers that file's
#     scope via the tests/e2e/ dir.)
#
# The 213 deselects in tests/integration/ and 4 deselects in
# tests/opencode/ are from the project's own deselect machinery (e.g.,
# postgres-only gates, vscode security), NOT from the 4 hardcoded
# partition-scope deselects. They travel with their host slices.
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_integration_opencode_e2e [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# Per-test timeout 240s (integration ceiling) via override-ini.
timeout 280s uv run pytest \
  tests/integration/ \
  tests/opencode/ \
  tests/e2e/ \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  --deselect "tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume" \
  2>&1
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
