#!/usr/bin/env bash
# LCA stage2 incident-class E2E pack (Job 2d-e of 9) — independent
# tests for the multi-evaluation child-lie arc (d) and the non-delegated
# marker-row R4 short-circuit (e).
#
# Pack: lca2_incident_de_integration_test
#
# These tests fill the gaps in the existing
# tests/unit/test_attestation_resolver_stage2.py + the sibling
# tests/integration/test_attestation_stage2_incident_abc.py coverage:
#
#   * Shape (d): 3-evaluation child-lie arc through the REAL gate node
#     (``build_instance_graph`` + ``graph.ainvoke``); per-evaluation
#     judge invocation count = (1, 1, 0); total budget ≤ 4 HTTP
#     attempts across the arc; legacy judge sites silent across the
#     full multi-eval run; Δ1 + Δ3 evidence fusion shape visible in
#     the judge payload on eval 1; D4 evidence-citing hint citation
#     lands in the message tape; FIX-3 stable nudge id collapses the
#     eval-2 deny into the single surviving nudge block.
#   * Shape (e): R4 short-circuit mirror — non-delegated mission
#     (attestation_required=False) carries BOTH markers AND short
#     final-word-count; predicate short-circuits BEFORE A/B providers;
#     ZERO judge invocations; ZERO hints; ZERO nudges; ZERO
#     ``event=leader_completion_gate_fused_judge`` operator rows;
#     legacy judge sites silent; counter NOT incremented.
#
# Dual-layer timeout: outer `timeout 300` at invocation + inner `290s`
# guard below (innate test-pack invariant — 5-min hard cap).
# Invocation contract:
#   timeout 300 bash test/packs/lca2_incident_de_integration_test.sh
#     (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca2_incident_de_integration_test"
FILES=(
  tests/integration/test_attestation_stage2_incident_de.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 290 uv run python -m pytest "${FILES[@]}" --tb=short -q
RC=$?
END=$(date +%s)
echo "Pack inner runtime: $((END-START))s"
if [ $RC -eq 124 ]; then
  echo "RESULT: TIMEOUT"; exit 124
elif [ $RC -eq 0 ]; then
  echo "RESULT: PASS"; exit 0
else
  echo "RESULT: FAIL"; exit 1
fi