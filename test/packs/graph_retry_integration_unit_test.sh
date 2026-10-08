#!/usr/bin/env bash
# Test Pack: graph_retry_integration_unit_test — Graph retry integration suite.
#
# Scope: tests/unit/test_graph_retry_integration.py (19 tests). The graph
# retry classification and recomposition integration tests assert the
# retry-aware graph behavior on transient vs permanent failures and
# propagation through the agent_node cycle. These tests are stateless
# (no daemon, no LLM) and verify structural retry wiring on this branch.
#
# Branch pin: fix/tool-pairing-full-history-heal @ 86c1bc041.
# Base: latest @ 9be991d56.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 240`
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
IFS=$'\n\t'
export PATH="/home/nea/.local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_DIR"

echo "=== Test Pack: graph_retry_integration_unit_test (test_graph_retry_integration.py, 19 tests) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/test_graph_retry_integration.py \
  --tb=short -q \
  --override-ini="addopts=" \
  --timeout=210 \
  -p no:cacheprovider 2>&1
RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  echo "Exit: $RC"
  exit 1
fi
