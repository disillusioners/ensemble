# G2b Gate-Surface Scope — Reproducibility Rider

**Commission:** designer-od-lane-fix · rider to version-staging
**Purpose:** record the EXACT gate-surface commands used for the two G2b net-new-delta scopes, so the identical pytest command can be re-run on both sides of any future diff.

## Preamble — the delta discipline

The G2b no-regression method is a symmetric A/B: run the **identical pytest command** on both sides of the diff (base vs HEAD), collect the sorted FAILED test-id lists, and diff them with `comm`: `comm -13 base-sorted head-sorted` = **net_new** (red at HEAD, green at base — candidate regressions), `comm -23 base-sorted head-sorted` = **fixed** (green at HEAD, red at base). Set-theory note: **tests green at the comparison base can never appear in `fixed`** — `fixed` only contains previously-red ids; consequently the remediation gate is **net_new = ∅** AND **previously-red tests green at HEAD**. A net_new id is not automatically a production regression — root-cause each one (test-double drift and G1-class stack-edited-test discrimination are the two known benign families in this commission — see tester RESULTS 2026-10-06, G2b).

---

## Scope 1 — Developer 17-file spawn-adjacent surface (F1/F1.1 net-new deltas)

The exact one-line command (run from the worktree root, `.venv/bin/python` resolves inside the worktree — editable-pth trap checked):

```
.venv/bin/python -m pytest tests/test_council_tools.py tests/unit/tools/test_version_tag_tool_resolution.py tests/unit/tools/test_spawn_instance_default_version.py tests/unit/tools/test_spawn_councilor_default_version.py tests/unit/test_append_allowed_models.py tests/unit/test_spawn_instance_input.py tests/unit/test_spawn_lane_mcp_preload.py tests/integration/test_spawn_intelligence_tier.py tests/integration/test_spawn_default_unchanged.py tests/test_spawn_team_members.py tests/test_spawn_instance_instructive_errors.py tests/test_spawn_instance_validation.py tests/test_spawn_limit_edge_cases.py tests/tools/test_instance_tools.py tests/tools/test_send_message_status_guard.py tests/tools/test_send_message_task_repo_guard.py tests/tools/test_send_message_context_param.py tests/tools/test_send_message_load_skill.py -q --no-header -p no:cacheprovider
```

**Base-variant note:** when running against pre-`037adb83` bases where `tests/unit/test_spawn_lane_mcp_preload.py` does not exist (the file was parked at base), append `--ignore=tests/unit/test_spawn_lane_mcp_preload.py` to the same command.

---

## Scope 2 — Tester 142-file / 11-chunk full-surface scope (G2b wider-surface delta)

**Provenance:** quoted verbatim from the tester's G2b artifacts (2026-10-06 run). Full flat list recorded once at `/tmp/g2b-files.txt` (142 files); chunk membership at `/tmp/g2b-chunk-01.txt` … `/tmp/g2b-chunk-11.txt`; construction = 8 named packs + grep/filename discovery (`/tmp/g2b-named.txt`, `/tmp/g2b-find.txt`, `/tmp/g2b-grep.txt`), deduped, e2e/packs/conftest excluded. Narrative: `.agents/tester/RESULTS/2026-10-06-designer-od-lane-fix-final-gates.md` §G2(b). `/tmp` is volatile — the authoritative enumeration is reproduced below.

**Invocation shape per chunk:** `timeout 300 .venv/bin/python -m pytest <chunk files> -q --tb=short` — run all 11 chunks per state (base, then HEAD after a single daemon/-flip visit), 300s per-chunk cap. FAILED ids extracted per state, sorted, then `comm -13` (net_new) / `comm -23` (fixed). Observed on the 2026-10-06 run: 3,958 tests, base ≈8.0 min / head ≈7.9 min, max chunk 141s, zero timeouts, collection-error delta ∅.

### Chunk 01
```
tests/integration/test_agent_bootstrap.py
tests/integration/test_api_messages.py
tests/integration/test_chat_source_live_injection_e2e.py
tests/integration/test_completion_report.py
tests/integration/test_inner_soul.py
tests/integration/test_inner_soul_standalone.py
tests/integration/test_instance_title_e2e.py
tests/integration/test_maintenancer_end_to_end.py
tests/integration/test_maintenancer_spawn_resolves_tools.py
tests/integration/test_mcp_lifecycle.py
tests/integration/test_message_metadata_lifecycle_wiring.py
tests/integration/test_message_queue_e2e.py
tests/integration/test_scheduled_tasks_e2e.py
tests/integration/test_spawn_default_unchanged.py
tests/integration/test_spawn_intelligence_tier.py
tests/integration/test_spawn_pause_resume_dispatch_durable.py
tests/integration/test_wc_wake_pure_hang.py
tests/postgres/test_wanderer_completion_reporting_pg.py
```

### Chunk 02
```
tests/test_chart_image_delivery_e2e.py
tests/test_governor_recursion_acceptance_walk.py
tests/test_llm_load_balance_integration.py
```

### Chunk 03
```
tests/helpers/send_message_fixtures.py
tests/helpers/unified_spawn_fixtures.py
tests/job_queue/test_defer_deadlock.py
tests/job_queue/test_defer_queue.py
tests/job_queue/test_f1_mint_processor_tripfire.py
tests/job_queue/test_instance_pause.py
tests/job_queue/test_instance_termination_job_cleanup.py
tests/job_queue/test_job_feedback_observer.py
tests/job_queue/test_job_processor.py
tests/job_queue/test_job_processor_admission_starvation.py
tests/job_queue/test_job_processor_project_id.py
tests/job_queue/test_option_b_message_routing.py
tests/job_queue/test_orphan_active_job_recovery.py
tests/job_queue/test_round2_council_fixes.py
tests/job_queue/test_seam_invariants.py
tests/job_queue/test_system_default_project_backfill.py
tests/message_queue_redesign/test_task_repository.py
tests/repositories/test_skill_repository.py
tests/services/test_instance_lifecycle_h10_l14.py
```

### Chunk 04
```
tests/services/test_instance_messaging_skill_injection.py
tests/services/test_option_b_message_branching.py
tests/services/test_skill_metrics_service.py
tests/services/test_skill_search_interval_messaging.py
tests/test_api.py
tests/test_chart_tools.py
tests/test_chart_tools_reuse_integration.py
tests/test_council_tools.py
tests/test_finalize_job_h15.py
tests/test_governor_integration.py
tests/test_manager.py
tests/test_migration_api_comprehensive.py
tests/test_observer_correlation.py
tests/test_observer_late_msg.py
tests/test_observer_race1.py
tests/test_progressive_dispatch.py
tests/test_registry.py
```

### Chunk 05
```
tests/test_scheduler_adapter.py
tests/test_scheduler_api.py
tests/test_slack_thread_manager.py
tests/test_snapshot_behavior_spot.py
tests/test_sources_mapper.py
tests/test_sources_registry.py
tests/test_sources_system_fix.py
tests/test_spawn_instance_instructive_errors.py
tests/test_spawn_instance_validation.py
tests/test_spawn_limit_edge_cases.py
tests/test_spawn_team_members.py
tests/test_system_log_tools.py
tests/test_tool_filter.py
```

### Chunk 06
```
tests/tools/test_send_message_context_param.py
tests/tools/test_send_message_load_skill.py
tests/unit/rag/test_workspace_scoping.py
tests/unit/services/test_completion_registry.py
tests/unit/services/test_context_messages_stable_id.py
tests/unit/services/test_invoked_as_tool.py
tests/unit/services/test_w5_claim_order_wc_wake.py
tests/unit/test_ari_agent.py
tests/unit/test_ari_worker_integration.py
tests/unit/test_attestation_conditional_scanner.py
tests/unit/test_attestation_scanner.py
tests/unit/test_builtin_mcp_servers.py
tests/unit/test_caller_model_overrides_seam.py
tests/unit/test_capability_resolver.py
tests/unit/test_coder_agent.py
tests/unit/test_context_key.py
```

### Chunk 07
```
tests/unit/test_context_messages.py
tests/unit/test_devops_agent.py
tests/unit/test_finalize_on_replace.py
tests/unit/test_gaia_agent.py
tests/unit/test_governor_recursion_guard.py
tests/unit/test_job_processor_status_guard.py
tests/unit/test_llm_config_override.py
tests/unit/test_maintenancer_fan_in_valve.py
tests/unit/test_maintenancer_skill_versions_consistent.py
tests/unit/test_mcp_cold_load_race.py
tests/unit/test_mcp_concurrent.py
tests/unit/test_mcp_config.py
tests/unit/test_mcp_connection_manager.py
tests/unit/test_mcp_kb_integration.py
tests/unit/test_mcp_kb_server.py
tests/unit/test_mcp_kb_server_context.py
tests/unit/test_mcp_lazy_init.py
tests/unit/test_mcp_managed_session.py
tests/unit/test_mcp_quote_sanitization.py
```

### Chunk 08
```
tests/unit/test_mcp_resilience.py
tests/unit/test_mcp_runtime_integration.py
tests/unit/test_mcp_server_crud.py
tests/unit/test_mcp_service.py
tests/unit/test_mcp_stdio_timeout.py
tests/unit/test_mcp_stdio_wrapper.py
tests/unit/test_mcp_task_scoped.py
tests/unit/test_mcp_test_connection.py
tests/unit/test_mcp_tool_filter.py
tests/unit/test_mcp_tool_timeout.py
tests/unit/test_mcp_warmup_pool.py
tests/unit/test_mcp_warmup_pool_env_refresh.py
tests/unit/test_meta_tag_parsing.py
```

### Chunk 09
```
tests/unit/test_phase4_manager_decomposition.py
tests/unit/test_plane_mcp.py
tests/unit/test_project_manager_agent.py
tests/unit/test_restore_preserve_version_tag.py
tests/unit/test_reviewer_v2_agent.py
tests/unit/test_skill_bank_repository.py
tests/unit/test_skill_clone_service.py
tests/unit/test_skill_search_interval.py
tests/unit/test_spawn_instance_input.py
tests/unit/test_spawn_intelligence_tier.py
tests/unit/test_utils.py
tests/unit/test_wanderer_agent.py
tests/unit/test_worker_agent.py
```

### Chunk 10
```
tests/unit/tools/test_instance_tools.py
tests/unit/tools/test_knowledge_tools.py
tests/unit/tools/test_mcp_set_env_tools.py
tests/unit/tools/test_privileged_category_system_log.py
tests/unit/tools/test_prompt_section_reference_integrity.py
tests/unit/tools/test_service_spawner.py
tests/unit/tools/test_snapshot_tools.py
tests/unit/tools/test_snapshot_v3.py
tests/unit/tools/test_spawn_councilor_default_version.py
tests/unit/tools/test_spawn_instance_default_version.py
```

### Chunk 11
```
tests/unit/tools/test_version_tag_tool_resolution.py
```

### Flat list (142 files, sorted; mirrors `/tmp/g2b-files.txt`)

```
tests/helpers/send_message_fixtures.py
tests/helpers/unified_spawn_fixtures.py
tests/integration/test_agent_bootstrap.py
tests/integration/test_api_messages.py
tests/integration/test_chat_source_live_injection_e2e.py
tests/integration/test_completion_report.py
tests/integration/test_inner_soul.py
tests/integration/test_inner_soul_standalone.py
tests/integration/test_instance_title_e2e.py
tests/integration/test_maintenancer_end_to_end.py
tests/integration/test_maintenancer_spawn_resolves_tools.py
tests/integration/test_mcp_lifecycle.py
tests/integration/test_message_metadata_lifecycle_wiring.py
tests/integration/test_message_queue_e2e.py
tests/integration/test_scheduled_tasks_e2e.py
tests/integration/test_spawn_default_unchanged.py
tests/integration/test_spawn_intelligence_tier.py
tests/integration/test_spawn_pause_resume_dispatch_durable.py
tests/integration/test_wc_wake_pure_hang.py
tests/job_queue/test_defer_deadlock.py
tests/job_queue/test_defer_queue.py
tests/job_queue/test_f1_mint_processor_tripfire.py
tests/job_queue/test_instance_pause.py
tests/job_queue/test_instance_termination_job_cleanup.py
tests/job_queue/test_job_feedback_observer.py
tests/job_queue/test_job_processor.py
tests/job_queue/test_job_processor_admission_starvation.py
tests/job_queue/test_job_processor_project_id.py
tests/job_queue/test_option_b_message_routing.py
tests/job_queue/test_orphan_active_job_recovery.py
tests/job_queue/test_round2_council_fixes.py
tests/job_queue/test_seam_invariants.py
tests/job_queue/test_system_default_project_backfill.py
tests/message_queue_redesign/test_task_repository.py
tests/postgres/test_wanderer_completion_reporting_pg.py
tests/repositories/test_skill_repository.py
tests/services/test_instance_lifecycle_h10_l14.py
tests/services/test_instance_messaging_skill_injection.py
tests/services/test_option_b_message_branching.py
tests/services/test_skill_metrics_service.py
tests/services/test_skill_search_interval_messaging.py
tests/test_api.py
tests/test_chart_image_delivery_e2e.py
tests/test_chart_tools.py
tests/test_chart_tools_reuse_integration.py
tests/test_council_tools.py
tests/test_finalize_job_h15.py
tests/test_governor_integration.py
tests/test_governor_recursion_acceptance_walk.py
tests/test_llm_load_balance_integration.py
tests/test_manager.py
tests/test_migration_api_comprehensive.py
tests/test_observer_correlation.py
tests/test_observer_late_msg.py
tests/test_observer_race1.py
tests/test_progressive_dispatch.py
tests/test_registry.py
tests/test_scheduler_adapter.py
tests/test_scheduler_api.py
tests/test_slack_thread_manager.py
tests/test_snapshot_behavior_spot.py
tests/test_sources_mapper.py
tests/test_sources_registry.py
tests/test_sources_system_fix.py
tests/test_spawn_instance_instructive_errors.py
tests/test_spawn_instance_validation.py
tests/test_spawn_limit_edge_cases.py
tests/test_spawn_team_members.py
tests/test_system_log_tools.py
tests/test_tool_filter.py
tests/tools/test_send_message_context_param.py
tests/tools/test_send_message_load_skill.py
tests/unit/rag/test_workspace_scoping.py
tests/unit/services/test_completion_registry.py
tests/unit/services/test_context_messages_stable_id.py
tests/unit/services/test_invoked_as_tool.py
tests/unit/services/test_w5_claim_order_wc_wake.py
tests/unit/test_ari_agent.py
tests/unit/test_ari_worker_integration.py
tests/unit/test_attestation_conditional_scanner.py
tests/unit/test_attestation_scanner.py
tests/unit/test_builtin_mcp_servers.py
tests/unit/test_caller_model_overrides_seam.py
tests/unit/test_capability_resolver.py
tests/unit/test_coder_agent.py
tests/unit/test_context_key.py
tests/unit/test_context_messages.py
tests/unit/test_devops_agent.py
tests/unit/test_finalize_on_replace.py
tests/unit/test_gaia_agent.py
tests/unit/test_governor_recursion_guard.py
tests/unit/test_job_processor_status_guard.py
tests/unit/test_llm_config_override.py
tests/unit/test_maintenancer_fan_in_valve.py
tests/unit/test_maintenancer_skill_versions_consistent.py
tests/unit/test_mcp_cold_load_race.py
tests/unit/test_mcp_concurrent.py
tests/unit/test_mcp_config.py
tests/unit/test_mcp_connection_manager.py
tests/unit/test_mcp_kb_integration.py
tests/unit/test_mcp_kb_server.py
tests/unit/test_mcp_kb_server_context.py
tests/unit/test_mcp_lazy_init.py
tests/unit/test_mcp_managed_session.py
tests/unit/test_mcp_quote_sanitization.py
tests/unit/test_mcp_resilience.py
tests/unit/test_mcp_runtime_integration.py
tests/unit/test_mcp_server_crud.py
tests/unit/test_mcp_service.py
tests/unit/test_mcp_stdio_timeout.py
tests/unit/test_mcp_stdio_wrapper.py
tests/unit/test_mcp_task_scoped.py
tests/unit/test_mcp_test_connection.py
tests/unit/test_mcp_tool_filter.py
tests/unit/test_mcp_tool_timeout.py
tests/unit/test_mcp_warmup_pool.py
tests/unit/test_mcp_warmup_pool_env_refresh.py
tests/unit/test_meta_tag_parsing.py
tests/unit/test_phase4_manager_decomposition.py
tests/unit/test_plane_mcp.py
tests/unit/test_project_manager_agent.py
tests/unit/test_restore_preserve_version_tag.py
tests/unit/test_reviewer_v2_agent.py
tests/unit/test_skill_bank_repository.py
tests/unit/test_skill_clone_service.py
tests/unit/test_skill_search_interval.py
tests/unit/test_spawn_instance_input.py
tests/unit/test_spawn_intelligence_tier.py
tests/unit/test_utils.py
tests/unit/test_wanderer_agent.py
tests/unit/test_worker_agent.py
tests/unit/tools/test_instance_tools.py
tests/unit/tools/test_knowledge_tools.py
tests/unit/tools/test_mcp_set_env_tools.py
tests/unit/tools/test_privileged_category_system_log.py
tests/unit/tools/test_prompt_section_reference_integrity.py
tests/unit/tools/test_service_spawner.py
tests/unit/tools/test_snapshot_tools.py
tests/unit/tools/test_snapshot_v3.py
tests/unit/tools/test_spawn_councilor_default_version.py
tests/unit/tools/test_spawn_instance_default_version.py
tests/unit/tools/test_version_tag_tool_resolution.py
```

**Integrity check at time of quoting:** chunk membership = flat list exactly (concatenation of chunks 01–11, deduped = 142 unique paths, matching `/tmp/g2b-files.txt` line-for-line).
