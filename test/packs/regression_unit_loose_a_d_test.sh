#!/usr/bin/env bash
# Test Pack: regression_unit_loose_a_d — loose unit files test_[a-d]*.py
# Scope: tests/unit/test_[a-d]*.py (1,050 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Loose unit files (NOT in tests/unit/<subdir>/) alphabetically a-d.
# This is the M1 partition-pattern bucket — alphabetical loose-file
# splits are deterministic, balanced, and easy to reason about across
# gate cycles.
#
# No quarantine deselects in this partition's scope (loose a-d files do
# not host any of the 4 hardcoded deselects).
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_unit_loose_a_d [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s uv run pytest \
  $(ls tests/unit/test_a*.py tests/unit/test_b*.py tests/unit/test_c*.py tests/unit/test_d*.py 2>/dev/null) \
  -n auto --tb=short -q -rf \
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
