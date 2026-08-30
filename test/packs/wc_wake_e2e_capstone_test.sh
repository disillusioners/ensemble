#!/usr/bin/env bash
# Test Pack: wc_wake_e2e_capstone_test — ON/OFF E2E capstone for the
# wc-wake phase-1 gate.
#
# Background. The pure-hang S6 acceptance suite
# (tests/integration/test_wc_wake_pure_hang.py) proves the flag-ON
# wake surface works on three routing sites. This capstone reuses
# the same boot harness pattern (real manager + real worker pool +
# scripted-LLM graph + hung child) to prove the FULL PICTURE for the
# phase-1 gate:
#
#   * ON-state (ENSEMBLE_WC_WAKE_ENQUEUE=1): the wake actually
#     happens — durable MessageQueue row, Task row, WC→RUNNING flip
#     in the bounded wake window, real graph turn processes the
#     message. Wake is PROMPT (bounded ≤ 60s; observed at HEAD is
#     ~5s flip + ~10s quiescence, well inside the bound).
#
#   * OFF-state (flag unset): the legacy stranding is byte-faithful
#     to the pre-branch behavior. HTTP POST returns 202 + "injected"
#     body, NO MessageQueue row is minted, NO Task row is created,
#     the parent stays WC throughout the bounded wait, the scripted
#     LLM never sees the wake token. The RAM FIFO accepts the
#     message but the agent_node never runs to consume it (the
#     documented defect — unchanged by this branch's OFF path).
#
# The OFF-state assertions are the kill-switch revert proof (C2-D2.5-
# FLIP / D2.5-FLIP). ON is the wake contract (C1-Q2 RESOLVED).
#
# Reuses the wake_harness pattern from tests/integration/test_wc_wake_pure_hang.py
# — no new harness was invented (per task spec). The Python module
# copies the helper fixtures (scripted-LLM, engine, parked-parent-with-
# hung-child) and adapts them for ONE HTTP surface under TWO flag
# states (rather than three surfaces under ONE flag state, which is
# what pure-hang covers).
#
# No ports below 10000: the harness never opens a socket — DaemonConfig
# declares port=8079 but the listener is never started; FastAPI is
# driven in-process via direct coroutine invocation (mirroring
# pure-hang's HTTP test). No socket-y cleanup is required.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 280s` cap on the pytest
#     process. At HEAD the two tests take ~8s combined; 280s is
#     margin-rich.
#
# Exit codes (per test-pack skill):
#   0   PASS (both ON and OFF assertions hold)
#   1   FAIL (any assertion failed)
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wc_wake_e2e_capstone_test ==="
echo "(ON-state durable wake + OFF-state legacy stranding — E2E capstone)"

cd "$PROJECT_DIR"

# Layer 2: 280s hard cap on the pytest process. Two tests, observed
# ~8s combined at HEAD; 280s is margin-rich.
timeout 280s .venv/bin/pytest \
  test/packs/wc_wake_e2e_capstone_test.py \
  --tb=short -q -ra -p no:cacheprovider 2>&1
RC=$?

if [ "$RC" -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ "$RC" -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
