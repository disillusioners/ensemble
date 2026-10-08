#!/usr/bin/env bash
# Test Pack: tool_pairing_gate_pins_unit_test — Tool-pairing heal G2/G4/G5 gate pins.
#
# Scope: tests/unit/tool_pairing_history_gate/ (5 tests across 3 files:
# test_interleave_pin.py [G2: SystemMessage-interleave flag+heal + valid
# multi-call no-op smoke] + test_probe_perf.py [G4: 678-msg probe <10s +
# strict non-mutation] + test_idempotence.py [G5: double-heal no-op +
# both synth-id prefixes recognized]).
#
# Branch pin: fix/tool-pairing-full-history-heal.
# This pack proves the G2/G4/G5 boundary contracts of the
# 2013-bricking tool-pairing heal flow WITHOUT booting any daemon or
# touching any protected port.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
# No external services. Read-only on production tree.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 240`
#   - Layer 2 (script-internal): `timeout 210s` on the pytest process
#     with `--override-ini="addopts="` to neutralize the repo-level
#     pytest addopts so the timeout flag does not double-up. The 5
#     gate tests run in <1s; 210s is a margin-rich safety net (the G4
#     probe is bounded at 10s in the test itself per the gate spec).
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

echo "=== Test Pack: tool_pairing_gate_pins (tool_pairing_history_gate/, 5 tests: G2+G4+G5) ==="

# Layer 2 (script-internal): 210s hard cap on the pytest process.
timeout 210s .venv/bin/pytest \
  tests/unit/tool_pairing_history_gate/ \
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
