# Phase 0 — Baseline Test Run (T0.5)

> Date: 2026-09-03
> Branch: `feature/langgraph-checkpoint-perf-v2 @ 2f80d45b`
> Pinned DSN: `postgresql://ensemble:<pw>@localhost:5432/ensemble_cpv2_test`
> Test scope: default unit suite (v2 `addopts = "-m 'not integration and not postgres'"` retained) + 16 explicit `--deselect` for documented quarantine entries.
> Test run: `uv run pytest tests/unit/ tests/services/ tests/repositories/ tests/job_queue/ tests/test_*.py tests/manager/ tests/message_queue_redesign/ tests/static/ tests/migration/ tests/api/ tests/tools/ tests/property/ tests/performance/ tests/manual/ --ignore=tests/packs --ignore=tests/e2e --ignore=tests/postgres --ignore=tests/integration --ignore=tests/opencode --ignore=tests/regression --ignore=tests/lint --ignore=tests/helpers --ignore=tests/mocks -o addopts= --tb=no -q -p no:cacheprovider --no-header $DESELECT_ARGS`

## Totals

```
214 failed, 14871 passed, 246 skipped, 10 deselected, 5 xfailed, 35 errors in 504.45s
```

- **14871 passed** (97.05% of runnable tests)
- **214 failed** + **35 errors** = **249 total failures** (~1.6% of total tests)
- **10 deselected** (out of 16 attempted; 6 deselects did not match a target test — the test names in v2 have shifted)
- **5 xfailed** (expected failures — not counted as regressions)

## Exclusion list (per T0.5 acceptance + per QUARANTINE.md)

The 16 explicit `--deselect` entries applied (per the dispatcher brief + T0.5 plan + QUARANTINE.md rows 10-14):

1. `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_valid_path` (QUARANTINE row 25)
2. `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_path_traversal_rejected` (row 26)
3. `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_invalid_format_sanitized` (row 27)
4. `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_archive_nonexistent_returns_not_found` (row 28)
5. `tests/unit/tools/test_archive_lifecycle.py::TestAccessMemoryArchive::test_access_normal_file_still_works` (row 29)
6. `tests/job_queue/test_watcher_repository_concurrent.py::test_first_call_inserts` (row 44)
7. `tests/job_queue/test_watcher_repository_concurrent.py::test_concurrent_threads_default_events_single_row` (row 44)
8. `tests/job_queue/test_jober_watch_integration.py::test_add_watch_creates_record` (row 44)
9. `tests/job_queue/test_in_progress_guard.py::test_completed_with_no_waiting_runs_normal_path` (row 44)
10. `tests/job_queue/test_in_progress_guard.py::test_waiting_for_none_treated_as_zero` (row 44)
11. `tests/job_queue/test_job_feedback_observer.py::TestObserverSkipsTerminated::test_observer_skips_terminated_status` (row 44)
12. `tests/job_queue/test_phase2_feedback_verify.py::test_observer_completion_then_termination_skips_termination` (row 44)
13. `tests/message_queue_redesign/test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_with_instance_filter_under_concurrency` (row 11)
14. `tests/unit/test_infra_tools.py::TestInfraAssetListTool::test_list_filter_by_type` (row 12)
15. `tests/message_queue_redesign/test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_concurrent_only_one_worker_wins` (row 13)
16. `tests/services/test_skill_evolution_service.py::TestCheckABTestResolution::test_ab_resolution_force_resolve` (row 14)

## Per-file results (FAIL counts; "doc" = documented in QUARANTINE.md; "ctx" = context-flake per row 43 family; "NEW" = not in QUARANTINE.md)

| File | FAIL | Status | Disposition |
|------|------|--------|-------------|
| tests/test_persistence.py | 15 | doc | row 18 (SQLite migration cascade family) |
| tests/test_progressive_dispatch.py | 18 | doc | row 18 |
| tests/test_memory_integration.py | 10 | doc | row 18 (signature-drift follow-up) |
| tests/test_spawn_limit_edge_cases.py | 9 | doc | row 18 |
| tests/test_migration_api_comprehensive.py | 1 | doc | row 18 (meta-test harness assertion-contract bug) |
| tests/test_injection_api.py | 26 | doc (sibling) | row 42 (M2-gate base-verified: messages.py:258 mock-await class) — same root, NOT enumerated by name in QUARANTINE |
| tests/manager/test_skill_service_init.py | 3 | doc | row 31 (SQLite migration 20260714_000001 family) |
| tests/manager/test_phase4_metrics_trigger_init.py | 6 | doc | row 31 / row 18 sibling (same `Migration 20260714_000001` signature) |
| tests/services/test_instance_messaging_compaction_guard.py | 1 | doc | row 42 |
| tests/services/test_skill_evolution_service.py | 1 | doc | row 14 (one of two; one was deselected) |
| tests/services/test_instance_messaging_queue_routing.py | 1 | doc | row 42 (registry/sentinel drift family) |
| tests/job_queue/test_in_progress_guard.py | 4 | doc | row 44 (2 deselected + 2 residual stale-fixture family members) |
| tests/job_queue/test_watcher_repository_concurrent.py | 2 | doc | row 44 |
| tests/job_queue/test_jober_watch_integration.py | 1 | doc | row 44 |
| tests/job_queue/test_job_feedback_observer.py | 1 | doc | row 44 (residual) |
| tests/job_queue/test_phase2_feedback_verify.py | 1 | doc | row 44 (residual) |
| tests/property/test_turn_state_machine.py | 1 | doc | row 30 |
| tests/static/test_chokepoint_callers.py | 2 | doc | row 38 |
| tests/test_agents_api.py | 2 | doc | row 19 |
| tests/test_api.py | 2 | doc | row 42 |
| tests/test_enqueue_shared.py | 1 | doc | row 19 |
| tests/test_innate_skills_refactoring.py | 1 | doc | row 38 |
| tests/test_llm_load_balance_meta_loading.py | 1 | doc | row 38 |
| tests/test_models.py | 1 | doc | row 42 (TestErrorCodes) |
| tests/test_skill_evolution_config.py | 2 | doc | row 19 |
| tests/test_terminal_orphan_matrix.py | 1 | doc | row 19 |
| tests/test_models_split.py | 1 | doc | row 19 |
| tests/unit/test_api_router_extraction.py | 1 | doc | row 40 |
| tests/unit/test_coder_agent.py | 1 | doc | row 19 |
| tests/unit/test_coder_developer_migration.py | 5 | doc | row 19 |
| tests/unit/test_context7_builtin.py | 4 | doc | row 17 |
| tests/unit/test_devops_agent.py | 3 | doc | row 19 |
| tests/unit/test_hide_kb_instances.py | 5 | doc | row 19 |
| tests/unit/test_job_processor_status_guard.py | 4 | doc | row 19 |
| tests/unit/test_llm_allowed_models_precedence.py | 2 | doc | row 42 |
| tests/unit/test_paused_auto_resume_fallback.py | 5 | doc | row 42 |
| tests/unit/test_terminal_reason_mirror_set_regression.py | 1 | doc | row 42 |
| tests/unit/test_validate_agent_id_compat.py | 1 | doc | row 19 |
| tests/unit/test_vision.py | 1 | doc | row 42 |
| tests/unit/test_wanderer_agent.py | 2 | doc | row 19 |
| tests/unit/test_watcher_context_builder.py | 9 | doc | row 16 |
| tests/unit/test_watchover_decision.py | 28 | doc | row 16 |
| tests/unit/test_watchover_edge_cases.py | 3 | doc | row 16 |
| tests/unit/test_watchover_integration.py | 4 | doc | row 16 |
| tests/unit/test_watchover_phase5.py | 3 | doc | row 16 |
| tests/unit/test_webfetch_builtin.py | 2 | doc | row 17 |
| tests/unit/services/test_job_queue_proxy_phase1.py | 7 | doc | row 19 (8F total; 1 might have been the deselected one — different family) |
| tests/api/test_instance_ui_prefs_api.py | 2 | doc | row 38 |
| tests/unit/test_phase4_manager_decomposition.py | 1 | doc | row 38 (subdirs-sweep) |
| tests/test_main_entry.py | 3 | ctx (PASS in isolation) | row 43 (M2-gate partition-context flakes) |
| tests/unit/test_task_reconciliation.py | 6 | ctx (PASS in isolation) | row 43 (M2-gate partition-context flakes) |
| tests/performance/test_context_api_latency.py | 2 | ctx (PASS in isolation) | row 43 |
| **tests/test_settings_api.py** | **12** | **NEW** | **NOT in QUARANTINE.md** — `psycopg.errors.InvalidSchemaName: no schema has been selected to create in` (the `pg_engine` fixture builds its own DSN from `PG_TEST_*` env defaults — db `ensemble_test`, password `ensemble_dev` — and ignores `POSTGRES_URL`/`POSTGRES_DB`; the connected DB `ensemble_test` is missing its `public` schema, so the role-wide `search_path=public` resolves to nothing and unqualified `CREATE TABLE` fails with SQLSTATE 3F000. `ensemble_cpv2_test`'s `public` schema EXISTS and accepts DDL as role `ensemble` — probe-verified. Rider probe 2026-09-03: `phase0-rider-probe.md`). **STOP trigger.** |
| **tests/unit/test_builtin_mcp_servers.py** | **17** | **NEW** | **NOT in QUARANTINE.md** — `AttributeError: Mock object has no attribute 'slash_commands'` (`mock_config` lacks the `slash_commands` field added by the slash-commands subsystem; same root pattern as row 17's blueprint family, but a different missing attribute). **STOP trigger.** |

**Doc total: 199 FAIL across 49 files.**
**Ctx total: 11 FAIL across 3 files (PASS in isolation; row 43 family).**
**NEW total: 29 FAIL across 2 files — NOT in QUARANTINE.md.**

## STOP CONDITION FIRED (per phase0-plan.md T0.5 coupling)

Per phase0-plan.md T0.5:
> If the T0.5 baseline reveals failures NOT explained by the documented quarantine exclusion list (a: 5 TestAccessMemoryArchive in tests/unit/tools/test_archive_lifecycle.py; b: the 7-node mission stale-fixture family listed in T0.5; c: other .agents/tester/QUARANTINE.md entries), stop after documenting phase0-baseline.md — the plan requires architect adjudication before Phases 1..5 proceed on a contaminated signal.

**29 new pre-existing failures (test_settings_api.py × 12 + test_builtin_mcp_servers.py × 17) are NOT in QUARANTINE.md.**

Both root causes are pre-existing test infra / fixture drift (introduced by features added after the test files froze):
- `test_settings_api.py` last touched 2026-07-22 (`6ceb6c31 fix: use call-time reference for SYSTEM_DEFAULT_PROJECT_ID…`); the `pg_engine` fixture lacks `PG_TEST_*`/`POSTGRES_*` DSN alignment — it targets `ensemble_test` (default `PG_TEST_DB`), whose `public` schema is missing; `search_path` setup is not the lever.
- `test_builtin_mcp_servers.py` last touched 2026-08-30 (`694b091c fix(governor): recursive-spawn guard`); the `mock_config` lacks `slash_commands` (a field added by the slash-commands subsystem 2026-09-01 per the project blueprint).

Neither root cause is on the checkpoint-performance port's code path. They are independent pre-existing failures that the v2 base state carries.

## Recommended disposition (for dispatcher / architect)

1. **Add a new QUARANTINE.md row** documenting the 29 NEW pre-existing failures with attribution:
   - `test_settings_api.py × 12` — PG schema search_path fixture drift (pre-existing, base-attributed)
   - `test_builtin_mcp_servers.py × 17` — mock_config slash_commands attribute fixture drift (pre-existing, base-attributed)
   - Sign-off: `family-level entry; no pack deselect — sweep-visible`
2. **OR** the dispatcher / architect confirms these are out-of-scope for this port and the port proceeds with the documented regression signal (29 pre-existing failures NOT in the port's diff).
3. **OR** the dispatcher / architect investigates the root cause and provides a fix that is then verified pre-port (this would be a separate workstream).

## WC-wake kill-switch state

Recorded in `phase0-state.md` (per the dispatcher's T0.5 acceptance criterion):
- Env: `ENSEMBLE_WC_WAKE_ENQUEUE=unset` (default OFF)
- config.yaml: no `wc_wake_enqueue` key (default OFF)
- Current state: **OFF** (default — legacy WC→injection)

Phase 5 T5.16 + drift-regression will verify the kill-switch state is preserved across the port.