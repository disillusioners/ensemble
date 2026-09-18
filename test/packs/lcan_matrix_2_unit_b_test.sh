#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 2/10, Set A unit+tools slice B)
# Pack: lcan_matrix_2_unit_b_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 18 files / 473 collected tests; est 60-150s
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_2_unit_b_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_2_unit_b_test"
FILES=(
tests/unit/test_attestation_resolver_stage2.py
tests/unit/test_attestation_resolver_user_intent.py
tests/unit/test_attestation_scanner.py
tests/unit/test_attestation_stage3_census.py
tests/unit/test_attestation_user_answer_pending_decide.py
tests/unit/test_child_terminal_contradiction.py
tests/unit/test_lcau_anchor_security.py
tests/unit/test_lcau_caps_redaction.py
tests/unit/test_long_tool_nudge.py
tests/unit/test_nudge_behavior.py
tests/unit/test_response_validation.py
tests/unit/test_spawn_intelligence_tier.py
tests/unit/test_symptom_repair_ladder.py
tests/unit/tools/test_attestation_prompt_contract.py
tests/unit/tools/test_attestation_registration.py
tests/unit/tools/test_attestation_tool.py
tests/unit/tools/test_service_registration.py
tests/unit/tools/test_service_tools.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 240 uv run python -m pytest "${FILES[@]}" --tb=short -q
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
