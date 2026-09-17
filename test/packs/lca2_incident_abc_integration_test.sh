#!/usr/bin/env bash
# RETIRED (Stage-3 adversarial-review round, 2026-09-17): the pytest file
# this pack drives (branch-scoped stage2 acceptance artifact) was DELETED —
# its tests self-skipped outside feature/lca-resolver-stage2 and its
# semantics are owned by tests/unit/test_attestation_stage3_census.py +
# TestLegacySitesDeleted + the R7 invariant suite. This pack script is
# kept as a historical record; running it will fail on the missing file.
# LCA stage2 incident-class E2E pack (Job 2a-c of 9) — independent
# tests for the three incident shapes (a) b08f40fe, (b) 98b59dd7,
# (c) 6a0d60c9.
#
# Pack: lca2_incident_abc_integration_test
#
# These tests fill the gaps in the existing
# tests/unit/test_attestation_resolver_stage2.py TestIncident* coverage:
#
#   * Shape (a): bound escalation through the FUSED deny path (4
#     sequential deny cycles → terminal_after_bound at denied_count=3);
#     retry-on-unparsable-first-response (2 HTTP attempts in 1 logical
#     invocation); legacy judge sites silent specifically under the
#     b08f40fe shape (the post-Stage-2 realistic path with the
#     child-report contradiction note in state).
#   * Shape (b): counter non-movement on the rescue path with a
#     pre-seeded denied_count (the rescue path bypasses both Phase-3
#     ledger writes); judge_invoked event-row field correctness on the
#     allow-via-rescue path.
#   * Shape (c): ZERO fused judge operator row emitted on the
#     answer-pending path (meta-bypass — no judge fires ⇒ no operator
#     row); three sentinel fields pinned together (fired=False,
#     bypass_reason=meta_bypass, judge_invoked=False).
#
# Dual-layer timeout: outer `timeout 300` at invocation + inner `290s`
# guard below (innate test-pack invariant — 5-min hard cap).
# Invocation contract:
#   timeout 300 bash test/packs/lca2_incident_abc_integration_test.sh
#     (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lca2_incident_abc_integration_test"
FILES=(
  tests/integration/test_attestation_stage2_incident_abc.py
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
