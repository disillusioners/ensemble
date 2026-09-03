#!/usr/bin/env bash
# Test Pack: regression_unit_smaller_subdirs_routers — 6 small unit subdirs
# Scope: tests/unit/{graph,job_queue,job_state,rag,repositories,routers}/ (539 collected)
# Timeout: 5 minutes (300s) — xdist parallelized
#
# Unit-slice regression partition. Uses `uv run pytest` + xdist per task
# spec. Unit slices keep the pyproject per-test default; no
# --override-ini=timeout.
#
# Co-locates the smaller unit subdirs (graph=30, job_queue=54, job_state=10,
# rag=106, repositories=10, routers=329) to keep partition counts balanced
# — routers would otherwise be its own pack and small subdirs would each
# need a pack, exploding the partition count past 12.
#
# No quarantine deselects in this partition's scope.
#
# Pre-existing failure families MUST RUN and be adjudicated from failure
# inventory, NOT deselected: watchover cascade, sqlite migration 20260714,
# httpx pollution, subdirs-sweep, proxy_phase1, slash_commands, injection_api,
# vscode.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_unit_smaller_subdirs_routers [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Regression partition pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 280s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s uv run pytest \
  tests/unit/graph/ \
  tests/unit/job_queue/ \
  tests/unit/job_state/ \
  tests/unit/rag/ \
  tests/unit/repositories/ \
  tests/unit/routers/ \
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
