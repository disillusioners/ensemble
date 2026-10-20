#!/usr/bin/env bash
# Test Pack: regression_integration_a_test — integration slice, half A
# Scope: 72 files under tests/integration/ (543 collected nodes at
# 2026-10-20) — the EXACT file list is in INTEGRATION_A_FILES below.
# Timeout: 5 minutes (300 s) — xdist parallelized
#
# === ORIGIN ===
# This pack replaces the retired P-12 partition
# `test/packs/regression_integration_opencode_e2e_test.sh`, DELETED upstream
# in `2c8d4b190` (2026-09-19) because the combined scope breached the 300 s
# cap. The PACKS.md row for P-12 (row ~1492) is a STALE POINTER to that
# deleted file — the tester lane owns PACKS.md and must re-point it at these
# three packs.
#
# The retired pack's recovered invocation was (verbatim, from
# `git show 2c8d4b190^:test/packs/regression_integration_opencode_e2e_test.sh`):
#
#   timeout 280s uv run pytest \
#     tests/integration/ tests/opencode/ tests/e2e/ \
#     -n auto --tb=short -q -rf \
#     --override-ini="timeout=240" \
#     --deselect "tests/e2e/test_e2e_workflows.py::test_pause_after_spawn_then_resume"
#
# This pack MIRRORS that invocation's marker selection, deselects and per-test
# timeout override. It deliberately does NOT clear the default addopts
# (`-m 'not integration and not postgres and not slow'`), so the slice stays
# comparable to the registered pack's collected baseline.
#
# `tests/opencode/` is EXCLUDED BY DESIGN: `daemon/opencode/` and
# `tests/opencode/` were archived to `legacy/` by `300ac0ba4`
# (`daemon.opencode` is unimportable). Resurrecting that half is out of scope.
#
# === SCOPE SPLIT ===
# The integration scope is split into two node-count-balanced halves by a
# longest-processing-time bin-pack over `pytest --collect-only -q`:
#   * half A — 72 files / 543 nodes
#   * half B — 73 files / 542 nodes
# The two halves carry an equal wall-clock weight under xdist, so neither
# breaches the 280 s inner guard on its own.
#
# MAINTENANCE CONTRACT: a NEW file under tests/integration/ must be assigned to
# exactly ONE of the two halves. Re-run the collect-only + bin-pack split when
# the halves drift apart (the file lists are explicit, not glob-derived, so a
# new file is NEVER silently skipped by either half).
#
# === DUAL-LAYER TIMEOUT ===
#   * Layer 2 (script-internal): 280 s — interrupts hung tests
#   * Layer 1 (command-level): 300 s via `timeout` wrapper
#   * Per-test timeout: 240 s via `--override-ini="timeout=240"`
#
# Invocation contract:
#   timeout 300 bash test/packs/regression_integration_a_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_integration_a_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
cd "$PROJECT_DIR"

# Runner resolution — `uv run pytest` mirrors the retired P-12 pack.
# `--no-sync` keeps the shared worktree venv untouched (no network sync,
# no re-resolve) while still running the project interpreter + pytest.
if [ -x "${HOME}/.local/bin/uv" ]; then
  PYTEST=( "${HOME}/.local/bin/uv" run --no-sync pytest )
elif command -v uv >/dev/null 2>&1; then
  PYTEST=( uv run --no-sync pytest )
else
  PYTEST=( "${PROJECT_DIR}/.venv/bin/python" -m pytest )
fi

INTEGRATION_A_FILES=(
'tests/integration/test_attestation_attest_first_e2e_independent.py'
'tests/integration/test_attestation_compaction.py'
'tests/integration/test_attestation_config.py'
'tests/integration/test_attestation_fail_open.py'
'tests/integration/test_attestation_hold_semantics_independent.py'
'tests/integration/test_attestation_in_graph_nudge_flow.py'
'tests/integration/test_attestation_marker_bound_enforcement_lca.py'
'tests/integration/test_attestation_mid_work_report_testcase.py'
'tests/integration/test_attestation_mode_tri_state.py'
'tests/integration/test_attestation_nudge_chaos.py'
'tests/integration/test_attestation_observability.py'
'tests/integration/test_attestation_revive_after_escalation.py'
'tests/integration/test_attestation_runbook_drift.py'
'tests/integration/test_attestation_stage2_failopen.py'
'tests/integration/test_attestation_stage3_zoo_test.py'
'tests/integration/test_attestation_wakeups_helper.py'
'tests/integration/test_auth_boot_preflight.py'
'tests/integration/test_auth_off_state.py'
'tests/integration/test_auth_public_lane_tag.py'
'tests/integration/test_auth_routes.py'
'tests/integration/test_auth_ux_contract.py'
'tests/integration/test_checkpoint_cleanup_job_wiring_pin.py'
'tests/integration/test_complete_cancel_route_through_transitions.py'
'tests/integration/test_context_in_graph.py'
'tests/integration/test_context_injection_integration.py'
'tests/integration/test_critical_notes_embed_pipeline.py'
'tests/integration/test_empty_guard_checkpoint_roundtrip.py'
'tests/integration/test_explorer_shared_context_paths.py'
'tests/integration/test_fix_b_inline_mirror_transition_incident_b.py'
'tests/integration/test_fresh_episode_attestation_reset_p0.py'
'tests/integration/test_initiative_message_integration.py'
'tests/integration/test_injection_echo_id_continuity.py'
'tests/integration/test_instance_messaging_partition_consistency.py'
'tests/integration/test_job_driven_enqueue_image_refs_facade.py'
'tests/integration/test_job_driven_enqueue_work_id_facade.py'
'tests/integration/test_kv_ambient_real_service_flag_on_through_assembler.py'
'tests/integration/test_lcan_legacy_checkpoint.py'
'tests/integration/test_lcancheck_family_separation.py'
'tests/integration/test_list_instances_include_descendants_facade.py'
'tests/integration/test_long_tool_nudge_e2e.py'
'tests/integration/test_m2_missions_runtime_contract.py'
'tests/integration/test_m3_per_kind_dispatch_pin.py'
'tests/integration/test_maintenancer_upgrade_gate_refuses.py'
'tests/integration/test_mcp_kb_public.py'
'tests/integration/test_message_metadata_liveness.py'
'tests/integration/test_message_metadata_prune.py'
'tests/integration/test_messages_ref_path_fallback.py'
'tests/integration/test_messages_ref_path_injection_e2e.py'
'tests/integration/test_messages_ref_path_worker_pool.py'
'tests/integration/test_module_lane_isolation.py'
'tests/integration/test_n3_per_kind_filter_pin.py'
'tests/integration/test_pause_race_enqueue_resume_flow.py'
'tests/integration/test_pause_race_resume_flow.py'
'tests/integration/test_pause_race_w7_jobitem_skip.py'
'tests/integration/test_pause_race_window_held_enqueue.py'
'tests/integration/test_pause_resume_watcher_rearm.py'
'tests/integration/test_persistence_w1_markers.py'
'tests/integration/test_report_wake_priority_claim.py'
'tests/integration/test_router_class_manifest.py'
'tests/integration/test_scope_matrix.py'
'tests/integration/test_spawn_intelligence_tier.py'
'tests/integration/test_spawn_pause_resume_dispatch_durable.py'
'tests/integration/test_tmp_image_cleanup_e2e.py'
'tests/integration/test_tmp_images_boot.py'
'tests/integration/test_tmp_images_e2e.py'
'tests/integration/test_tool_pairing_original_symptom.py'
'tests/integration/test_vscode_proxy.py'
'tests/integration/test_vscode_routing.py'
'tests/integration/test_vscode_security_integration.py'
'tests/integration/test_wake_vs_claim_exactly_once.py'
'tests/integration/test_wc_wake_pure_hang.py'
'tests/integration/test_webhook_dual_credential.py'
)

# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s "${PYTEST[@]}" \
  "${INTEGRATION_A_FILES[@]}" \
  -n auto --tb=short -q -rf \
  --override-ini="timeout=240" \
  2>&1 || EXIT_CODE=$?
if [ $EXIT_CODE -eq 124 ]; then
  echo "RESULT: TIMEOUT"
  exit 124
elif [ $EXIT_CODE -eq 0 ]; then
  echo "RESULT: PASS"
  exit 0
else
  echo "RESULT: FAIL"
  exit 1
fi
