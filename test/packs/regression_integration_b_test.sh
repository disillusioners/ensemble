#!/usr/bin/env bash
# Test Pack: regression_integration_b_test — integration slice, half B
# Scope: 73 files under tests/integration/ (542 collected nodes at
# 2026-10-20) — the EXACT file list is in INTEGRATION_B_FILES below.
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
#   * half B — 73 files / 542 nodes
#   * half A — 72 files / 543 nodes
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
#   timeout 300 bash test/packs/regression_integration_b_test.sh
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
echo "=== Test Pack: regression_integration_b_test [$(git rev-parse --abbrev-ref HEAD) @ $(git rev-parse --short HEAD)] ==="
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

INTEGRATION_B_FILES=(
'tests/integration/gate_suites/test_gate_suite_pause_resume.py'
'tests/integration/test_api_messages.py'
'tests/integration/test_armed_absence_alist.py'
'tests/integration/test_attestation_attest_first_e2e.py'
'tests/integration/test_attestation_bound_escalation.py'
'tests/integration/test_attestation_c2_both_branches.py'
'tests/integration/test_attestation_corpus_replay.py'
'tests/integration/test_attestation_delegation_allow.py'
'tests/integration/test_attestation_dry_mode.py'
'tests/integration/test_attestation_idle_orphan_incident.py'
'tests/integration/test_attestation_incident_acceptance_lca.py'
'tests/integration/test_attestation_ledger_reset.py'
'tests/integration/test_attestation_live_descendants.py'
'tests/integration/test_attestation_marker_routing_lca.py'
'tests/integration/test_attestation_must_not_break.py'
'tests/integration/test_attestation_nudge_supersede_lca.py'
'tests/integration/test_attestation_o1_boot_assert.py'
'tests/integration/test_attestation_performance.py'
'tests/integration/test_attestation_stage2_killswitch.py'
'tests/integration/test_attestation_stale_watermark.py'
'tests/integration/test_attestation_user_answer_pending_lca.py'
'tests/integration/test_auth_csrf.py'
'tests/integration/test_auth_csrf_origin_policy.py'
'tests/integration/test_auth_middleware_derivation.py'
'tests/integration/test_auth_middleware_dispatch.py'
'tests/integration/test_auth_routes_idp_errors.py'
'tests/integration/test_bearer_lane.py'
'tests/integration/test_boot_report_recovery.py'
'tests/integration/test_consumer_enablement.py'
'tests/integration/test_context_freshness.py'
'tests/integration/test_context_hierarchy.py'
'tests/integration/test_empty_guard_error_lineage.py'
'tests/integration/test_existing_gates_parity.py'
'tests/integration/test_fix_c_surface_pin.py'
'tests/integration/test_fresh_episode_p0_facade_guards.py'
'tests/integration/test_get_instance_messages_observed_count_zero.py'
'tests/integration/test_get_instance_messages_response_shape_frozen_fixture.py'
'tests/integration/test_idp_failure_modes_p5.py'
'tests/integration/test_instance_messaging_first_turn_kv_partition.py'
'tests/integration/test_jobs_status_combo_filter_pin.py'
'tests/integration/test_kb_guard_status.py'
'tests/integration/test_lcau_incident_e2e.py'
'tests/integration/test_long_tool_nudge_graph_smoke.py'
'tests/integration/test_maintenancer_end_to_end.py'
'tests/integration/test_maintenancer_spawn_resolves_tools.py'
'tests/integration/test_message_metadata_deletion_paths_prune.py'
'tests/integration/test_message_metadata_hook_placement.py'
'tests/integration/test_message_metadata_lifecycle_wiring.py'
'tests/integration/test_message_metadata_retry_recovery.py'
'tests/integration/test_message_metadata_send_message_revive.py'
'tests/integration/test_messages_ref_path_e2e.py'
'tests/integration/test_mission_final_vocab_runtime.py'
'tests/integration/test_module_lanes.py'
'tests/integration/test_module_lanes_p5.py'
'tests/integration/test_no_saver_imports_in_routers.py'
'tests/integration/test_nuclear_cleanup_bucket5.py'
'tests/integration/test_pause_during_report_turn_reaches_completed.py'
'tests/integration/test_pause_race_resume_drain.py'
'tests/integration/test_pause_race_resume_reenqueue.py'
'tests/integration/test_pause_race_window_held.py'
'tests/integration/test_paused_auto_resume_facade_image_refs.py'
'tests/integration/test_persistence_synthetic_context_id_order.py'
'tests/integration/test_report_integrity_repro.py'
'tests/integration/test_ri_off_behavioral_probe.py'
'tests/integration/test_spawn_default_unchanged.py'
'tests/integration/test_tmp_image_cleanup_health.py'
'tests/integration/test_tmp_image_cleanup_lifespan.py'
'tests/integration/test_tool_pairing_original_symptom_v2.py'
'tests/integration/test_vacuum_maintenance_job_no_full.py'
'tests/integration/test_vscode_lifecycle_integration.py'
'tests/integration/test_vscode_loopback_gate.py'
'tests/integration/test_vscode_routing_e2e.py'
'tests/integration/test_work_resolver_dead_letter_binding.py'
)

# RESULT-echo: `|| EXIT_CODE=$?` list-context capture — under `set -e`, a bare
# `EXIT_CODE=$?` after a failing command never executes (silent exit, no RESULT).
EXIT_CODE=0
timeout 280s "${PYTEST[@]}" \
  "${INTEGRATION_B_FILES[@]}" \
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
