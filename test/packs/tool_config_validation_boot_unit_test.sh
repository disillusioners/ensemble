#!/usr/bin/env bash
# Test Pack: tool_config_validation_boot_unit_test
# Tests: tests/unit/tools/test_tool_config_validation_boot.py
# Timeout: 2 minutes (120s)
#
# Boot-path validation suite for the frozen-binary tool-name discovery
# fix (commit 4f326f8d): runs the exact boot validation path
# (AgentRegistry.discover() + validate_tool_configs(), plus the
# get_registry() boot wrapper) in SOURCE mode against the REAL
# agents/project-manager/meta.json —
#   1. zero "is neither a known category nor a known tool" warnings
#      for project-manager (prod-incident regression pin), and
#   2. a deliberately-unknown tool name (staged in tmp_path, no repo
#      file modified) STILL warns — no-false-negative guard.
# No daemon boot; no ports; no DB.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: tool_config_validation_boot_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 2 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 110s — interrupts hung tests
# Layer 1 (command-level): 120s via `timeout` wrapper below
EXIT_CODE=0
timeout 110s .venv/bin/pytest \
  tests/unit/tools/test_tool_config_validation_boot.py \
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
