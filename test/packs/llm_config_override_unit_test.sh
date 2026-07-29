#!/usr/bin/env bash
# Test Pack: llm_config_override_unit_test
# Tests: tests/unit/test_llm_config_override.py
# Timeout: 2 minutes (120s)
#
# Covers per-agent LLM model override: _build_llm_config, registry llm_model
# parsing, spawn_instance integration, model param + allowed_models validation,
# _resolve_model_override, restore_instance re-validation, CSV/JSON config parsing.
# F5 ripple: _restore_instance made async; this pack verifies no breakage.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: llm_config_override_unit_test ==="

cd "$PROJECT_DIR"

timeout 120s .venv/bin/pytest \
  tests/unit/test_llm_config_override.py \
  --tb=short -q 2>&1

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
