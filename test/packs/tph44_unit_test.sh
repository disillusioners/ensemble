#!/usr/bin/env bash
# Test Pack: tph44_unit_test — Tool-pairing full-history heal core suite.
#
# Scope: tests/unit/tool_pairing_history/ (44 tests, dual files:
# test_full_history_heal.py + test_w2_wiring.py). The R1+R2 full-history
# pairing-aware validation heal flow: W2 wiring (compaction-side pairing
# reconcile, see compaction.py:1740) + full-history heal history walker.
#
# Branch pin: fix/tool-pairing-full-history-heal @ 86c1bc041.
# Base: latest @ 9be991d56. This gate proves the patch's import-time
# wiring on the worker's narrow 44-test surface WITHOUT booting any daemon
# or touching any protected port.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services. Read-only on production tree.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 240`
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process
#     with `--override-ini="addopts="` to neutralize the repo-level
#     pytest addopts so the timeout flag does not double-up. The 44 tests
#     run in <5s; 210s is a margin-rich safety net.
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

echo "=== Test Pack: tph44_unit_test (tool_pairing_history/, 44 tests) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/tool_pairing_history/ \
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
