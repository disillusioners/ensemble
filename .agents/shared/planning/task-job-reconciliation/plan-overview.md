# Plan Overview: Task↔JobItem Reconciliation Fix + Defensive Idle-Gate + Visibility

Date: 2026-08-11 (revised 2026-08-11: C1, C2, W1, W2, W3, W4 corrections applied; further revised 2026-08-11: C3 POST-COMMIT placement, C4 EXISTS check, C5 F14 rationale, C6 race window, W6 CURRENT_TIMESTAMP, W7 _ensure_postgres_columns statements list, W8 CI parity check Task 7, W9 Deployment Order)
Author: planner[v2] via plan-creation worker
Status: Ready for Review (Phases 1-3) / Draft (Phase 4, with reviewer corrections applied 2026-08-11)

## Objective

Eliminate the deadlock where a `paused` task whose linked JobItem is already terminal blocks defer/background queues indefinitely. The system will reconcile the Task to `cancelled` when its JobItem becomes terminal, the idle-gate predicates will defensively exclude such orphaned tasks from active-work counts, a one-shot migration will backfill existing rows, and the resulting "bad state" condition will become observable (per-queue badge, system-wide preflight) and fixable via the existing System Cleanup button (fourth bucket).

## Scope

### In Scope

- **Phase 1**: Code Fix — Reconciliation. Add a new POST-COMMIT Step 4 to `_finalize_job_db_sync` (after `reconcile_turn_mirror` at line 3469) that transitions orphaned `paused`/`pending` tasks to `cancelled` when their linked JobItem transitions to terminal (`done`/`dead`). Step 4 opens its own `engine.begin()` block — does NOT share the caller's `WriteGuardSession`. The reconciliation SQL is self-contained: it verifies the JobItem is terminal via `AND EXISTS` subquery.
- **Phase 2**: Code Fix — Defensive Idle-Gate. Update the two TaskRepository predicates (`has_active_non_deferred_work`, `has_active_non_background_work`) to exclude `paused`/`pending` tasks whose linked JobItem is terminal. **Apply the fix to BOTH the running+paused AND pending-only branches in BOTH predicates — 6 SQL locations total** (reviewer correction C2, 2026-08-11: a PENDING task whose JobItem is already terminal would otherwise still be counted, leaving the deadlock partially unfixed on that path). Verify (no changes expected) the two JobRepository predicates.
- **Phase 3**: Data Migration. Create a one-shot migration to backfill existing stuck tasks. Mirror the SQL as a Python-list tuple in the `statements` list of `_ensure_postgres_columns()` in `daemon/manager.py` for PostgreSQL (placeholder name `_POSTGRES_STARTUP_STATEMENTS` does NOT exist — see Phase 3 Task 2, W7 correction).
- **Phase 4**: Visibility + Enhanced Cleanup. Add per-queue `bad_state_jobs` count (backend repository + schema + service; the new `count_bad_state_tasks` and `batch_reconcile_bad_state_tasks` are SYNC methods taking `engine: Engine`, not async session methods — service callers wrap in `asyncio.to_thread`), a `GET /api/jobs/cleanup/preflight` endpoint for the system-wide count (no `is_write_paused` guard — must work during write pause, reviewer correction W1, 2026-08-11), a fourth bucket in `cleanup_non_terminal_jobs` (`reconciled_bad_state`) that batch-reconciles bad-state Tasks via the cleanup button, a frontend `.count-badge.bad-state` using `$accent-rose`, a red-glow + tooltip on the System Cleanup button when bad-state exists, snackbar text mentioning the new bucket, an optional enhanced confirm dialog, and an extension of the `_queue_to_response` model branch to query the count (reviewer correction W3, 2026-08-11 — approach (a) recommended so the badge is consistent across all router paths).

### Out of Scope

- Refactoring the pause/resume subsystem (separate concern; tracked in `pause-resume-redesign/`).
- Adding a `reconciled_at` column to `task` (follow-up only if metrics are needed).
- Adding per-task metric counters for reconciliation events (follow-up; could be added to `skill_metrics_service`-style subsystem later).
- Removing `paused` from the idle-gate entirely — paused tasks with **active** JobItems correctly block (pause-first crash recovery MUST be preserved per `daemon/services/instance_lifecycle.py:1866`).
- Migration for non-terminal (e.g., `failed`) JobItems — `AdmissionState` has explicit terminal states `done` and `dead`; `failed` is a Task-side concept, not JobItem.

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Reconciliation Code Fix | When JobItem transitions to terminal, reconcile linked Task to `cancelled` (POST-COMMIT Step 4 in `_finalize_job_db_sync` after `reconcile_turn_mirror` at line 3469; uses `AND EXISTS` JobItem terminal subquery — self-contained) | 7 | tight with Phase 2 (defense-in-depth), loose with Phase 3 (migration catches pre-fix data), tight with Phase 4 (sister single-task method) | pending |
| 2 | Defensive Idle-Gate | Exclude `paused`/`pending` tasks with terminal JobItems from idle-gate counts (BOTH branches — running+paused AND pending-only — in BOTH predicates; 6 SQL locations total) | 11 | tight with Phase 1 (same predicates), loose with Phase 3, tight with Phase 4 (same bad-state definition) | pending |
| 3 | Data Migration | Backfill existing stuck tasks; mirror as Python-list tuple in `_ensure_postgres_columns()` `statements` list in `daemon/manager.py` for PostgreSQL | 7 | loose with Phase 1+2 (operates on existing data), loose with Phase 4 (runtime vs DDL layer) | pending |
| 4 | Bad State Visibility + Enhanced Cleanup | Make bad-state rows observable (per-queue count, frontend badge) and fixable via the System Cleanup button (fourth bucket, batch reconciliation). New repository methods are SYNC (`engine: Engine`, not session); service callers wrap in `asyncio.to_thread`. Preflight endpoint intentionally has no `is_write_paused` guard (W1). | 23 | tight with Phase 1+2 (same bad-state predicate), tight with `JobCleanupResponse` invariant (must NOT change), loose with Phase 3 (DDL vs API) | pending |

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 |
|---|---|---|---|---|
| Phase 1 | — | tight (same predicate semantics, same Task↔JobItem linkage) | loose (migration catches pre-fix data) | tight (Phase 4's `batch_reconcile_bad_state_tasks` is the batch sibling of Phase 1's `reconciled_terminal_task`) |
| Phase 2 | tight | — | loose (defense-in-depth for migration edge cases) | tight (Phase 4 reverses the join — instead of excluding bad-state from idle-gate, Phase 4 counts and reconciles it) |
| Phase 3 | loose | loose | — | loose (Phase 3 is DDL one-shot; Phase 4 is runtime API — different layer) |
| Phase 4 | tight | tight | loose | — |

**Tight coupling rationale (Phase 1 ↔ Phase 2):**
- Both depend on `task.work_id = job_queue_items.job_id` linkage semantics.
- Both use the same `AdmissionState` terminal set (`done`, `dead`).
- Phase 2's NOT EXISTS predicate is the "defensive" twin of Phase 1's reconciliation — if Phase 1 misses a row (e.g., due to a race), Phase 2 ensures the idle-gate still doesn't block.
- **Reviewer correction C2 (2026-08-11):** Phase 2's NOT EXISTS exclusion must be applied to BOTH branches (running+paused AND pending-only) in BOTH predicates — 6 SQL locations total. The pending-only branch's existing `NOT EXISTS (admission_state='queued')` exclusion does NOT cover terminal JobItems; a PENDING task whose JobItem is already `done`/`dead` would still be counted otherwise.

**Tight coupling rationale (Phase 1+2 ↔ Phase 4):**
- The bad-state definition is identical across all three phases: `task.status IN ('paused','pending')` AND `job_queue_items.admission_state IN ('done','dead')` AND `job_queue_items.deleted_at IS NULL`.
- Phase 4's `batch_reconcile_bad_state_tasks` is the batch sibling of Phase 1's `reconcile_terminal_task` (same guard, same target, batch shape).
- Phase 4's `count_bad_state_tasks` is the inquirer form of Phase 2's NOT EXISTS exclusion — same predicate, different join direction.

**Tight coupling rationale (Phase 4 ↔ `JobCleanupResponse`):**
- The `validate_total_processed` invariant (`total_processed == cancelled_queued + cancelled_active`) is a contract for the two primary cleanup buckets. Phase 4's `reconciled_bad_state` MUST follow the `orphaned_reaped` pattern (excluded from `total_processed`) — changing the invariant would break the existing contract for callers that depend on it.

**Loose coupling rationale (Phase 1/2 ↔ Phase 3):**
- Phase 3 is a one-shot backfill that operates on data state, not code paths.
- Phase 1+2 are forward-fix; Phase 3 is the retroactive catch-up.
- All three phases can ship in the same release, or Phase 3 can ship first (e.g., during a maintenance window).

**Loose coupling rationale (Phase 4 ↔ Phase 3):**
- Phase 3 ships a one-shot SQL UPDATE; Phase 4 ships runtime code that handles the same condition via the API. They target the same end-state but at different layers (DDL vs API). If Phase 4 ships first, Phase 3 is largely redundant; if Phase 3 ships first, Phase 4 is the durable runtime guarantee.

## Deployment Order

**Recommended Deployment Order: Phase 2 → Phase 3 → Phase 1 → Phase 4**

1. **Phase 2 (idle-gate fix) FIRST** — provides immediate symptom relief. Even if reconciliation hasn't shipped, the defensive idle-gate unblocks stuck queues on the next predicate evaluation.
2. **Phase 3 (data migration) SECOND** — cleans up existing stuck rows. Run during a maintenance window or deploy alongside Phase 2.
3. **Phase 1 (reconciliation) THIRD** — the root-cause fix. Prevents new stuck tasks from forming.
4. **Phase 4 (visibility + cleanup) LAST** — UX layer. Depends on Phase 1's reconciliation logic concept.

All four phases SHOULD ship in the same release, but if phased rollout is needed, this order minimizes user-visible impact.

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | Phase 1 reconciliation accidentally cancels a `running` task | High | Low | `WHERE status IN ('paused', 'pending')` guard in `reconcile_terminal_task`; explicit test (Task 5 in Phase 1) |
| 2 | Phase 2 NOT EXISTS subquery causes performance regression on the idle-gate hot path | Medium | Medium | Add composite index `ix_job_queue_items_work_id_admission_state (job_id, admission_state, deleted_at)`; benchmark in Phase 2 Task 6 |
| 3 | Phase 3 migration cancels legitimate `pending` tasks (data corruption) | High | Low | Idempotent `WHERE status IN ('paused', 'pending')` guard; dry-run preview in staging; DOWN is no-op (intentional — reverting reintroduces the bug) |
| 4 | PostgreSQL mirror missing from `manager.py` startup list → only SQLite gets the migration fix | Critical | Low | Required checklist item; **both** SQLite (`.sql`) AND PostgreSQL (Python-list) MUST be updated together; tests on both backends in Phase 3 Task 5 |
| 5 | Dual-driver SQL portability violation (SQLite-only `rowid`, PostgreSQL-only `DROP CONSTRAINT`, JSONB operators) | Medium | Low | Use `WHERE EXISTS (subquery)` pattern (already verified portable on both drivers); avoid banned operators |
| 6 | Phase 1 Step 4 Task UPDATE failure blocks Step 1-3 JobItem finalization | High | Low | Best-effort + try/except + `logger.warning`; do NOT wrap in the same transaction as Steps 1-3; catch `Exception` separately, not `BaseException`. Step 4 is POST-COMMIT (after `reconcile_turn_mirror` at line 3469) and opens its own `engine.begin()` — Step 4 failure cannot roll back Steps 1-3 |
| 7 | `paused` task with ACTIVE JobItem incorrectly excluded by Phase 2 NOT EXISTS | High | Low | Subquery only matches `admission_state IN ('done','dead')`; active/queued JobItems excluded by definition; explicit test (Task 5 in Phase 2) |
| 8 | Phase 3 migration runs before Phase 1+2 deployed → new stuck tasks form afterward | Medium | Low | Migration is forward-compatible (cancels stuck tasks regardless of code state); Phase 1+2 prevent new occurrences; recommend shipping all 3 phases in the same release |
| 9 | `_finalize_job_db_sync` Step 4 race with concurrent Task state change (paused→running via resume) | Low | Low | F14 pending-tasks gate (checking `status == 'pending'` ONLY) does NOT block finalization for PAUSED tasks — reconciliation of PAUSED tasks therefore runs unconditionally once the JobItem is terminal. The Step 4 `AND EXISTS` JobItem terminal subquery is what guarantees pause-first crash recovery; F14 only defers while PENDING work exists. Race severity remains Low provided `_resume_cascade_db_sync` catches `InvalidTransitionError` (see Risk 16 and Phase 1 Task 7 verification) |
| 10 | Migration UPDATE on large `task` table takes lock for too long | Low | Medium | SQLite is single-writer so non-issue there; PostgreSQL UPDATE is row-level locked; if >100K rows affected, consider batching in a follow-up |
| 11 | Phase 4 `reconciled_bad_state` accidentally included in `validate_total_processed`, breaking the two-bucket invariant | High | Low | Explicit non-modification guidance in Task 10; docstring pinned to the existing two-bucket language; CI test (Phase 4 Task 21) asserts `total_processed == cancelled_queued + cancelled_active` |
| 12 | Phase 4 frontend red-glow pulse animation triggers accessibility complaints (motion-sensitivity) | Low | Medium | `@media (prefers-reduced-motion: reduce)` block disables the animation (Phase 4 Task 16); pulse-glow is subtle (8px box-shadow), not a full flicker |
| 13 | Phase 4 `batch_reconcile_bad_state_tasks` UPDATE on PostgreSQL takes a long lock | Medium | Low | Same row-level lock semantics as Phase 3; if a project has >100K stuck rows, batch in a follow-up |
| 14 | Phase 4 ships before Phase 3 — the system-wide count could return >0 for legacy bad-state rows from before Phase 3 ever ran | Low | High | Phase 4's reconciliation removes them via the cleanup button; the count drops to 0 after one click. Document in release notes. |
| 15 | Phase 4 preflight endpoint (`GET /api/jobs/cleanup/preflight`) **intentionally has NO `is_write_paused` guard** (reviewer correction W1, 2026-08-11) | Low | High | The preflight is a read-only COUNT query — it must work even during write pause (database migration) because that is precisely when bad-state items are most likely to accumulate (writes are paused, so reconciliation cannot run). The endpoint never calls a mutating service, so the lack of a guard is safe — it cannot race with the migration. Frontend polling restricted to page lifetime + onRefresh() + post-cleanup (no timer-based polling in v1). |
| 16 | Phase 1 Step 4 reconciliation↔resume race (C6): (a) Step 4 fires while PAUSED + resume not yet committed → Task silently CANCELLED; (b) Step 4 fires first (PAUSED→CANCELLED) then resume cascade tries PAUSED→RUNNING → InvalidTransitionError | Low | Low | The `AND EXISTS` JobItem terminal subquery already excludes ACTIVE JobItems, so window (a) only triggers if the JobItem transitioned to terminal between gate-check and Step 4 (extremely narrow). Window (b) requires confirmation that `_resume_cascade_db_sync` catches `InvalidTransitionError` and logs at DEBUG — Phase 1 Task 7 documents this finding. Race severity stays Low ONLY if this catch path is confirmed |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | `paused` task with terminal JobItem transitions to `cancelled` within one finalization cycle | Unit test: stub `_finalize_job_db_sync`, call with `done` decision, assert Task status | Pass 100% of test cases |
| 2 | `running` task with terminal JobItem is NOT touched | Unit test: stub `_finalize_job_db_sync`, assert Task status remains `running` | Pass |
| 3 | Idle-gate predicate returns `False` for stuck tasks (does not block defer/background queues) | Integration test: create `paused` task + `done` JobItem; query `has_active_non_deferred_work` and `has_active_non_background_work` | Both predicates return `False` |
| 4 | Migration runs idempotently (second invocation updates 0 rows) | Run migration twice in test; assert row count delta | 0 rows on second run |
| 5 | All existing tests pass on both SQLite AND PostgreSQL | `pytest` on both DB backends (CI matrix) | 0 new failures |
| 6 | Defer queue unblocks after finalization | Integration test: enqueue defer job for instance with stuck task, assert queue proceeds within timeout | Job reaches next stage within 5 seconds |
| 7 | Pause-first crash recovery preserved | Test: pause instance, leave JobItem `active`, Task stays `paused`; resume completes Task | Pass |
| 8 | NOT EXISTS subquery performance acceptable on 10K-row `task` table | Benchmark test (Phase 2 Task 6) | < 50ms p95 |
| 9 | PostgreSQL mirror verified in `daemon/manager.py` startup list | Grep test: assert byte-identical UPDATE tuple exists in the `statements` list of `_ensure_postgres_columns()` (manager.py:4498-4536). The placeholder name `_POSTGRES_STARTUP_STATEMENTS` does NOT exist — that is a Phase 3 plan artifact to be replaced with the actual list | Pass |
| 10 | `JobQueueResponse.bad_state_jobs` is populated per queue | Integration test: seed bad-state row, GET `/api/queues/{id}`, assert field > 0 | Pass |
| 11 | `GET /api/jobs/cleanup/preflight` returns accurate system-wide bad-state count | Integration test: seed N bad-state rows, GET preflight, assert `bad_state_count == N` | Pass |
| 12 | `POST /api/jobs/cleanup` returns `reconciled_bad_state` populated; `total_processed` invariant preserved | Integration test: seed bad-state rows, POST cleanup, assert `reconciled_bad_state > 0` AND `total_processed == cancelled_queued + cancelled_active` | Both conditions hold |
| 13 | `batch_reconcile_bad_state_tasks` is idempotent | Unit test: run twice, assert second call returns 0 | 0 rows on second run |
| 14 | Frontend `.count-badge.bad-state` renders when `bad_state_jobs > 0` | Angular component test: queue with `bad_state_jobs: 3` renders badge with text "3 bad-state" | Text matches |
| 15 | System Cleanup button has `.has-bad-state` class + tooltip when `badStateCount() > 0` | Angular component test: seed `badStateCount.set(5)`, assert button class + tooltip text | Class + tooltip both correct |
| 16 | Red-glow animation respects `prefers-reduced-motion: reduce` | CSS test: assert `@media` block disables `animation` | Pass |
| 17 | Cleanup snackbar includes `reconciled_bad_state` count | Angular component test: mock service to return `reconciled_bad_state: 7`, trigger snackbar, assert text contains "7 bad-state" | Snackbar text matches |
| 18 | `count_bad_state_tasks` / `batch_reconcile_bad_state_tasks` are dual-driver | Both pass on SQLite AND PostgreSQL in CI matrix | 0 new failures |

## Research Insights

- **Root cause (confirmed)**: `_finalize_terminal` (`daemon/services/job_queue_service.py:1396`) and `_finalize_job_db_sync` (`daemon/services/job_feedback_observer.py:2802`) operate **exclusively** on `job_queue_items`, `instances`, and `job_locks` — they **NEVER** transition the linked Task. The pending-tasks gate in `_finalize_job_db_sync:3213-3241` COUNTs Task rows but does NOT TRANSITION them — **this is the gap**.
- **Existing precedent for NOT EXISTS exclusion**: `JobRepository.has_active_non_background_work` (`daemon/repositories/job_queue/repository.py:801-820`) already uses a NOT EXISTS subquery to exclude queued JobItems whose linked Task is pending (FIX 2B 2026-08-10). Phase 2 mirrors this exact pattern for the Task-side predicates.
- **Dual-driver constraint**: PostgreSQL is the PRIMARY dev/test DB (per project metadata). The migration runner (`daemon/migrations/runner.py`) **skips non-SQLite engines** — PostgreSQL is NOT migrated by the runner. Any new UPDATE-only migration MUST be mirrored as a tuple in the `statements` list of `_ensure_postgres_columns()` in `daemon/manager.py` (near lines 4498-4536). Reference: `daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql` + `daemon/manager.py:4498-4536` is the EXACT precedent for this dual-path pattern. Note: the placeholder name `_POSTGRES_STARTUP_STATEMENTS` does NOT exist — see Phase 3 plan Task 2 (W7).
- **Pause-first crash recovery**: `pause_instance_cascade` (`daemon/services/instance_lifecycle.py:1866`) does NOT transition Task rows directly. RUNNING→PAUSED happens via SuspendTurn (`daemon/services/turn_transitions.py`). JobItem stays `active` during pause; later finalized to `done`, but Task stays `paused` forever — this is the orphaned-state we are reconciling. **The reconciliation MUST only fire when JobItem is TRULY terminal** — this is enforced by the `AND EXISTS` JobItem terminal subquery inside `reconcile_terminal_task`, NOT by the F14 gate. The F14 pending-tasks gate (`_finalize_job_db_sync:3213-3241`) only checks `status == 'pending'` and defers finalization while pending PENDING tasks exist; PAUSED tasks do NOT block finalization. So the gating guarantee for PAUSED tasks lives in the SQL, not in F14.
- **Data model linkage**:
  - `task.work_id = job_queue_items.job_id` (UUID strings) — `daemon/repositories/task/models.py:136`.
  - `AdmissionState` (`daemon/repositories/job_queue/models.py:21-43`): terminal = `DONE`, `DEAD`. No explicit `CANCELLED` — cancellation routes through `done` via `target_status` param.
  - `TaskStatus` (`daemon/repositories/task/models.py:37-52`): terminal = `COMPLETED`, `FAILED`, `CANCELLED`.
  - **Reconciliation target is `CANCELLED`** (not `FAILED`) — the Task did not fail on its own; its JobItem was externally finalized. This matches the `CancellationReason` discriminator pattern in `daemon/services/task_processor.py` (per pause-first crash recovery convention).
- **Why `_finalize_job_db_sync` over `_finalize_terminal` for Step 4 placement**: `_finalize_job_db_sync` already contains a post-commit `reconcile_turn_mirror` block (lines 3456-3469) that opens its own `engine.begin()` after the in-session Steps 1-3 commit. Step 4 mirrors this pattern — placed AFTER line 3469, opening its own `engine.begin()` block. This POST-COMMIT placement is critical: if Step 4 ran inside the same `WriteGuardSession` as Steps 1-3 and failed pre-commit, it would roll back the JobItem finalization. The post-commit shape keeps Step 4 in a separate failure domain — Steps 1-3 stay durable; Step 4's failure is logged, not propagated. `_finalize_terminal` is the async entry point that delegates DB writes here — adding reconciliation to `_finalize_terminal` would create a second DB-write path outside the existing transaction.

## Open Questions

1. **Should the reconciliation log a metric event (e.g., `task_reconciled_to_cancelled`)?** Out of scope for this fix. The existing `logger.info("task.reconciled_to_cancelled", work_id=...)` in Phase 1 Task 1 implementation guidance is sufficient for the initial deployment. A proper counter/inc metric could be added in a follow-up if telemetry needs arise.
2. **Should `paused` tasks in Phase 1 Step 4 be transitioned via `turn_transitions.py` named transitions or via a direct UPDATE?** Recommendation: **direct UPDATE** for simplicity. `turn_transitions.py` is for in-flight state changes during graph execution; this is post-finalization cleanup. Confirm with developer during implementation.
3. **Performance impact of NOT EXISTS subquery on the idle-gate hot path (Phase 2)?** Recommend adding a composite index on `job_queue_items(job_id, admission_state, deleted_at)` (Phase 2 Task 10 includes a benchmark; index creation is recommended but listed as a separate sub-task, Task 11). If the index already exists in current schema, no action needed. Note: the terminal-JobItem NOT EXISTS exclusion is now applied to BOTH branches in BOTH predicates (reviewer correction C2, 2026-08-11 — 6 SQL locations total), so the benchmark should cover all 6 paths.
4. **Should the migration also update `updated_at` column, or leave it untouched?** Decision: **update it** (`updated_at = CURRENT_TIMESTAMP`) so that downstream observers (e.g., `StaleTaskRecovery`) can see the reconciliation event timestamp. This also matches the reference migration (`20260810_000001_fix_idle_gate_stuck_task_flags.sql`) pattern.
5. **What if `_ensure_postgres_columns()` `statements` list (manager.py:4498-4536) is structured differently than expected?** Phase 3 Task 2 implementation guidance has been updated to specify that the actual code path is the `statements` parameter of `_ensure_postgres_columns()` — a Python list of SQL string tuples executed via `conn.execute(text(stmt))` at startup. The placeholder name `_POSTGRES_STARTUP_STATEMENTS` does NOT exist in the codebase. Developer MUST read `daemon/manager.py:4498-4536` directly during implementation to confirm the list shape and add the new tuple adjacent to the existing reference mirror if present.
6. **Phase 4 — Should the System Cleanup button preflight count be polled on a timer (e.g. every 5s) or only on explicit refresh events?** Recommendation: explicit events only (page load + `onRefresh()` + post-cleanup). Timer-based polling creates DB load proportional to the number of operators viewing the Jobs page. If a polling strategy is later desired, push it to a server-sent event (SSE) channel or a websocket so the DB load is constant regardless of operator count. Confirm with developer during implementation.
7. **Phase 4 — Should `reconciled_bad_state` be visible in the existing `job_metadata` audit log (e.g., `cleanup_audit_log` table) if one exists?** Out of scope for Phase 4; the existing `logger.info` call (Task 23) is sufficient for v1. A proper audit-trail integration could be added in a follow-up.
