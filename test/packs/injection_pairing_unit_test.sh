#!/usr/bin/env bash
# Test Pack: injection_pairing_unit_test — Injection tool-pairing unit suite.
#
# Scope: tests/unit/graph/test_injection_tool_pairing.py (30 tests). The
# injection-edge tool-pairing layer (R1+R2 module): deterministic
# placeholder ids (R1) and the CLE-mirror regression for the poisoned
# tail rebuild (R2). Both live inside test_injection_tool_pairing.py —
# the canonical injection-edge pairing surface.
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

echo "=== Test Pack: injection_pairing_unit_test (test_injection_tool_pairing.py, 30 tests) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/graph/test_injection_tool_pairing.py \
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
