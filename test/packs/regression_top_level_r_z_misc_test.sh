#!/usr/bin/env bash
# Test Pack: regression_top_level_r_z_misc — top-level test_[r-z]*.py + design dirs
# Scope: tests/test_[r-z]*.py + tests/{message_queue_redesign,tools}/ (2,311 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Co-locates the r-z loose test files (1,567/1,579 with 12 deselects
# from the slice's own deselect machinery) with two design dirs
# (message_queue_redesign=470, tools=274) — buckets are balanced by
# total test count, not by dir count.
#
# No quarantine deselects in this partition's scope (loose r-z files +
# message_queue_redesign/ + tools/ do not host any of the 4 hardcoded
# deselects).
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_top_level_r_z_misc [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s uv run pytest \
  $(ls tests/test_[r-z]*.py 2>/dev/null) \
  tests/message_queue_redesign/ \
  tests/tools/ \
  -n auto --tb=short -q -rf \
  2>&1 || EXIT_CODE=$?
EXIT_CODE=${EXIT_CODE:-0}
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
