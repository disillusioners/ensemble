#!/usr/bin/env bash
# LCA user-intent merge gate pack — MATRIX CORE lane (unit + unit/tools + migration + probe)
# Pack: lcau_matrix_core_unit_test
# Worktree: feature/lca-judge-user-intent @ 47b56df8 (base a6442bff)
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 240s guard below.
# Invocation contract: timeout 300 bash test/packs/lcau_matrix_core_unit_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcau_matrix_core_unit_test"
FILES=(
  # tests/unit/ (32 files)
  tests/unit/test_attestation_b_redaction_stage3.py
  tests/unit/test_attestation_conditional_gate_outcomes.py
  tests/unit/test_attestation_conditional_scanner.py
  tests/unit/test_attestation_construction_site_sweep.py
  tests/unit/test_attestation_dry_logging.py
  tests/unit/test_attestation_epoch_replay.py
  tests/unit/test_attestation_fail_open.py
  tests/unit/test_attestation_fused_judge.py
  tests/unit/test_attestation_fused_judge_truncation.py
  tests/unit/test_attestation_gate.py
  tests/unit/test_attestation_judge_resolver.py
  tests/unit/test_attestation_judge_wiring.py
  tests/unit/test_attestation_ledger.py
  tests/unit/test_attestation_ledger_failopen.py
  tests/unit/test_attestation_marker_scanner.py
  tests/unit/test_attestation_marker_supersede_lca.py
  tests/unit/test_attestation_marker_wiring.py
  tests/unit/test_attestation_nudge_inject.py
  tests/unit/test_attestation_report_judge.py
  tests/unit/test_attestation_resolver.py
  tests/unit/test_attestation_resolver_activation.py
  tests/unit/test_attestation_resolver_stage2.py
  tests/unit/test_attestation_resolver_user_intent.py
  tests/unit/test_attestation_scanner.py
  tests/unit/test_attestation_stage3_census.py
  tests/unit/test_attestation_user_answer_pending_decide.py
  tests/unit/test_child_terminal_contradiction.py
  tests/unit/test_long_tool_nudge.py
  tests/unit/test_nudge_behavior.py
  tests/unit/test_response_validation.py
  tests/unit/test_spawn_intelligence_tier.py
  tests/unit/test_symptom_repair_ladder.py
  # tests/unit/tools/ (5 files)
  tests/unit/tools/test_attestation_prompt_contract.py
  tests/unit/tools/test_attestation_registration.py
  tests/unit/tools/test_attestation_tool.py
  tests/unit/tools/test_service_registration.py
  tests/unit/tools/test_service_tools.py
  # tests/migration/ (1 file)
  tests/migration/test_attestation_migration.py
  # tests/probe/ (2 files)
  tests/probe/lca2_judge_truncation_repro.py
  tests/probe/lca2_live_fused_judge_probe.py
)
echo "=== Test Pack: ${PACK} ==="
echo "File count: ${#FILES[@]} (expected 40 = 32 unit + 5 tools + 1 migration + 2 probe)"
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
