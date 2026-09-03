#!/usr/bin/env bash
# Test Pack: regression_job_queue — full tests/job_queue/ sweep
# Scope: tests/job_queue/ (1,650 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Single-dir partition — tests/job_queue/ is the second-largest test
# scope in the repo (1,650 tests). Isolates it so failure attribution
# is unambiguous when the gate breaks. M1 P5 reference: 1,650 was part
# of the 2,749 P5 bucket that ran in 82s; the stand-alone partition
# should fit comfortably under 280s with xdist.
#
# No quarantine deselects in this partition's scope (tests/job_queue/ does
# not host any of the 4 hardcoded deselects).
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_job_queue [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
timeout 280s uv run pytest \
  tests/job_queue/ \
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
