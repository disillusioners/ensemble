#!/usr/bin/env bash
# LCA user-intent merge gate — matrix INTEGRATION lane, sub-pack intf-f (fast files)
# Pack: lcau_matrix_intf_f_integration_test
# Tip: 47b56df8 (feature/lca-judge-user-intent, base a6442bff).
# 20 fast files; sibling packs own the 11 real-LLM files (m/s1/s2/s3) + the 2 slow-lane files (ints).
# 9 live-LLM-gated tests auto-DESELECT via addopts (do NOT force-select).
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_intf_f_integration_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_intf_f_integration_test"
FILES=(
  tests/integration/test_attestation_c2_both_branches.py
  tests/integration/test_attestation_compaction.py
  tests/integration/test_attestation_config.py
  tests/integration/test_attestation_corpus_replay.py
  tests/integration/test_attestation_dry_mode.py
  tests/integration/test_attestation_marker_bound_enforcement_lca.py
  tests/integration/test_attestation_marker_routing_lca.py
  tests/integration/test_attestation_must_not_break.py
  tests/integration/test_attestation_nudge_supersede_lca.py
  tests/integration/test_attestation_o1_boot_assert.py
  tests/integration/test_attestation_observability.py
  tests/integration/test_attestation_performance.py
  tests/integration/test_attestation_runbook_drift.py
  tests/integration/test_attestation_stage2_failopen.py
  tests/integration/test_attestation_stage2_killswitch.py
  tests/integration/test_attestation_stage3_zoo_test.py
  tests/integration/test_attestation_user_answer_pending_lca.py
  tests/integration/test_empty_guard_checkpoint_roundtrip.py
  tests/integration/test_long_tool_nudge_e2e.py
  tests/integration/test_service_tool_flag_off_byte_identical.py
)
echo "=== Test Pack: ${PACK} ==="
START=$(date +%s)
timeout 280 uv run python -m pytest "${FILES[@]}" --tb=short -q
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