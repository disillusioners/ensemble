#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 9/10, Set B delivery plumbing, tests/unit root)
# Pack: lcan_matrix_delivery_1_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 21 files / 497 collected tests; est 100-250s; DEFAULT markers only
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_delivery_1_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_delivery_1_test"
FILES=(
tests/unit/test_blueprint_injection.py
tests/unit/test_child_still_running_defer_bus_terminal.py
tests/unit/test_context_messages.py
tests/unit/test_critical_notes_config.py
tests/unit/test_lifecycle_hook_completion.py
tests/unit/test_llm_failover_v2.py
tests/unit/test_llm_failover_v2_adversarial.py
tests/unit/test_llm_failover_v2_resilience.py
tests/unit/test_long_tool_nudge_import_cycle.py
tests/unit/test_pause_never_dispatched_ghosts.py
tests/unit/test_pause_resume_terminate_tree_fix_p1.py
tests/unit/test_pause_tool_result_race.py
tests/unit/test_persistence_w2_parent_normalize.py
tests/unit/test_phase4_manager_decomposition.py
tests/unit/test_project_scope_guide_context_kind.py
tests/unit/test_ready_message_completion_report.py
tests/unit/test_report_deferred_marker_guards.py
tests/unit/test_report_repair.py
tests/unit/test_resume_router_deferred_recovery.py
tests/unit/test_root_instance_completion.py
tests/unit/test_symptom_repair_doc.py
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
