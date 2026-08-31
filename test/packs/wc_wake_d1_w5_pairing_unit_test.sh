#!/usr/bin/env bash
# Test Pack: wc_wake_d1_w5_pairing_unit_test — D1-seam + W5-claim + injection
# tool-pairing unit tests for the wc-wake phase-1 gate.
#
# Background. wc-wake-report-integrity gates the ENSEMBLE_WC_WAKE_ENQUEUE
# kill-switch on five interlocking unit surfaces:
#
#   * D1 entry-seam pairing tail-guard (cf210e32) — guards the poisoned
#     checkpoint tail at the enqueue seam so the agent_node never reads
#     an unmatched tool_call.
#   * D1 LangGraph-2013 red/green proof (fd565123) — exercises the same
#     seam against a structural LangGraph-2013 turn shape (tool_call +
#     ToolMessage + AI + HumanMessage) so parity is pinned.
#   * W5 two-turn claim order + M1 requeue identity + S9 terminal-after-
#     turn-1 + FIFO single-turn invariant (6b0ec75c) — claim_pending_task
#     ordering under non-terminal + terminal parents.
#   * Injection tool-pairing (the R1+R2 module) — deterministic placeholder
#     ids (R1) and the CLE-mirror regression for the poisoned-tail rebuild
#     (R2), both ship inside test_injection_tool_pairing.py.
#
# Single pytest invocation so the four files share one process — the
# W1 council (pollution-triage) demands autouse flag-cache reset on
# every flag-touching suite, which all four already install. Running
# them together proves the cross-file pollution vectors are sealed.
#
# TEST-ENV ONLY. No production code changes, no daemon boot, no ports.
#
# Dual-layer timeout (per test-pack skill):
#   - Layer 1 (command-level): caller wraps with `timeout 300`
#   - Layer 2 (script-internal): `timeout 150s` on the pytest process
#     (unit-pack cap is 2 min; we cap at 150s for margin).
#
# Exit codes (per test-pack skill):
#   0   PASS
#   1   FAIL
#   124 TIMEOUT
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=== Test Pack: wc_wake_d1_w5_pairing_unit_test ==="
echo "(D1 entry-seam + D1 2013-mimic + W5 claim order + injection tool-pairing)"

cd "$PROJECT_DIR"

# Layer 2 (script-internal): 150s hard cap on the pytest process.
# The four files together are <100 tests and run in <2s; 150s is a
# margin-rich safety net. -p no:cacheprovider avoids writing
# .pytest_cache into the worktree.
timeout 150s .venv/bin/pytest \
  tests/unit/services/test_d1_seam_langgraph_2013_mimic.py \
  tests/unit/services/test_d1_seam_pairing_guard.py \
  tests/unit/services/test_w5_claim_order_wc_wake.py \
  tests/unit/graph/test_injection_tool_pairing.py \
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
