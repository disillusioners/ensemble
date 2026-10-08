#!/usr/bin/env bash
# Test Pack: d1_seam_pairing_unit_test — D1 entry-seam pairing tail-guard.
#
# Scope: tests/unit/services/test_d1_seam_pairing_guard.py (9 tests). The
# D1 entry-seam pairing tail-guard (cf210e32 reference) defends the
# poisoned checkpoint tail at the enqueue seam so the agent_node never
# reads an unmatched tool_call. This is the standalone pairing-guard unit
# surface (paired with the langgraph_2013 mimic in the wc-wake pack).
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

echo "=== Test Pack: d1_seam_pairing_unit_test (test_d1_seam_pairing_guard.py, 9 tests) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/services/test_d1_seam_pairing_guard.py \
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
