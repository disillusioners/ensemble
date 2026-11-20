#!/usr/bin/env bash
# Test Pack: schema_registry_bind_once_unit_test
# Tests: tests/unit/tools/test_schema_registry.py
#        tests/unit/tools/test_frozen_tool_name_discovery.py
# Timeout: 5 minutes (300s)
#
# Covers the ROW2-DEEP-DIVE §3 schema-registry lane (the flagship 23.16 s
# event-loop stall):
#
#   * the process-level registry memoises the pydantic ``args_schema`` per
#     tool FUNCTION, so a SECOND graph assembly of the same surface derives
#     (almost) no fresh pydantic model classes;
#   * the memoised ``model_json_schema()`` is byte-identical to the freshly
#     built one across the WHOLE tool surface;
#   * the OpenAI tool payload is byte-identical to a fresh
#     ``convert_to_openai_tool`` derivation (name / description / parameters
#     / key order);
#   * the MUTATION PIN: all the ``bind_tools`` sites in
#     ``daemon.graph.build_instance_llms`` — the VISION branch included —
#     consume ONE payload derivation.  Re-introducing a per-assembly rebuild
#     at any site turns this pack RED.
#   * the frozen tool-NAME discovery pack stays green (the registry must not
#     rename or re-scope any tool).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: schema_registry_bind_once_unit_test ==="

cd "$PROJECT_DIR"

# Unit pack — 5 min hard limit. Dual-layer timeout.
# Layer 2 (script-internal): 260s — interrupts hung tests
# Layer 1 (command-level): 300s via `timeout` wrapper below
EXIT_CODE=0
timeout 260s .venv/bin/pytest \
  tests/unit/tools/test_schema_registry.py \
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
