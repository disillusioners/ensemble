#!/usr/bin/env bash
# Test Pack: frozen_tool_name_discovery_unit_test
# Tests: tests/unit/tools/test_frozen_tool_name_discovery.py
# Timeout: 2 minutes (120s)
#
# Covers the frozen-binary tool-name discovery fix (commit 4f326f8d):
# KNOWN_TOOL_NAMES static fallback in daemon/tools/_tool_registry.py —
# frozen-mode simulation (patched __file__ → empty tmp dir), static/merge
# behavior, coverage of all 30 incident tool names, end-to-end zero
# project-manager warnings in frozen mode.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: frozen_tool_name_discovery_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/test_frozen_tool_name_discovery.py \
  --tb=short -q 2>&1 || EXIT_CODE=$?

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
