#!/usr/bin/env bash
# LCA advisory-note-removal merge gate — MATRIX pack (pack 10/10, Set B delivery plumbing, unit/services + root + services + job_queue + integration-dir)
# Pack: lcan_matrix_delivery_2_test
# Gate: feature/lca-remove-advisory-note @ 0a4fccb1 (delta 858b1038..0a4fccb1, 3 commits).
# Splitter-generated 2026-09-18. 42 files / 556 collected tests; est 100-280s; DEFAULT markers only
# Dual-layer timeout: outer 'timeout 300' at invocation + inner 280s guard below.
# Invocation contract: timeout 300 bash test/packs/lcan_matrix_delivery_2_test.sh  (from worktree root)
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PACK="lcan_matrix_delivery_2_test"
FILES=(
tests/integration/test_api_messages.py
tests/integration/test_context_freshness.py
tests/integration/test_context_hierarchy.py
tests/integration/test_context_in_graph.py
tests/integration/test_context_injection_integration.py
tests/integration/test_instance_messaging_first_turn_kv_partition.py
tests/integration/test_instance_messaging_partition_consistency.py
tests/integration/test_kv_ambient_real_service_flag_on_through_assembler.py
tests/integration/test_pause_race_resume_drain.py
tests/integration/test_pause_race_resume_flow.py
tests/integration/test_pause_race_window_held.py
tests/integration/test_persistence_synthetic_context_id_order.py
tests/integration/test_report_integrity_repro.py
tests/integration/test_ri_off_behavioral_probe.py
tests/job_queue/test_in_progress_guard.py
tests/services/test_instance_messaging_parent_resolution.py
tests/services/test_instance_messaging_shared_context_injection.py
tests/services/test_instance_messaging_task_context.py
tests/services/test_skill_search_interval_messaging.py
tests/test_cascade_integration.py
tests/test_child_completion_pending_task_guard.py
tests/test_deadlock_fix.py
tests/test_dependency_bus.py
tests/test_persistence.py
tests/unit/services/test_b4_child_report_obligation.py
tests/unit/services/test_b_fail_open.py
tests/unit/services/test_child_outcome_payload_surfacing.py
tests/unit/services/test_child_reports.py
tests/unit/services/test_context_messages_stable_id.py
tests/unit/services/test_critical_notes_phase2_filters.py
tests/unit/services/test_critical_notes_phase2_r25_id.py
tests/unit/services/test_critical_notes_render_phase1.py
tests/unit/services/test_invoked_as_tool.py
tests/unit/services/test_kv_ambient_config.py
tests/unit/services/test_kv_ambient_fresh_c3.py
tests/unit/services/test_parent_completion_idempotency_terminated.py
tests/unit/services/test_question_pause_completion_guard.py
tests/unit/services/test_report_integrity_guard.py
tests/unit/services/test_revive_non_replay.py
tests/unit/services/test_terminal_report_wake_bus.py
tests/unit/services/test_title_generation_trigger.py
tests/unit/services/test_vgap_b4_backstop_behavior.py
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
