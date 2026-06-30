# Plan Overview: Defer Queue + Job/Task Seam Bugfix

## Objective
Fix 19 bugs (P1, P2, F1–F17) in the dual work-tracking tables (JobItems and Tasks) that cause silent job deadlocks, premature defer-queue admission, lock corruption, and drift between the two tables. Delivered as 3 sequenced PRs following the 3-PR recommendation from the bug document.

## Scope Assessment
**LARGE** — 19 bugs across ~15 files in the daemon layer. Root causes are already analyzed into 4 solution categories (A, B, C, D). The fixes are logic-level (backend-agnostic), but must be tested on both SQLite (default suite) and PostgreSQL (guard triggers). Each category is independently shippable, and the document prescribes a 3-PR sequencing that balances risk and dependency.

## Context
- Project: agents-ensemble
- Working Directory: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- Bug document: `docs/bugs/defer-queue-and-job-task-seam-bugs.md`
- Architecture decision: Keep the two-table model (JobItems for queue-policy, Tasks for execution). Harden the seam, don't merge.

## Bug Summary

### Category A — Make the join key real (P1, F11)
The `job_queue_items.metadata.message_id` is never stamped at admission time. The cross-system guard in `claim_pending_task` relies on it matching `task.message_id` — NULL breaks the carve-out, self-deadlocking the instance's own task.

### Category B — One shared "active work" predicate (P2, F2, F8)
The defer idle-gates (Gate A in `job_processor.py` and Gate B/observer in `job_queue_service.py`) only count JobItem rows, not Task rows. Virtual jobs (spawn_instance) write zero JobItem rows → defer gate passes while work is in flight. `maintenance._is_idle` is also blind to both active jobs and all tasks. The `is_deferred` flag is never wired from queue type to `enqueue_message`.

### Category C — Reconcile, don't assume (F1, F3, F4, F5, F6, F7, F10, F12, F13, F14, F15)
11 drift bugs across lock-release scoping, list_work dedup, lossy status mapping, missing periodic reconciler, retry work_id instability, stale task cancellation, and observer hardening.

### Category D — Test the invariant on SQLite (F17)
PostgreSQL-only triggers enforce lock↔admission invariants. SQLite has none. Need default-suite tests that seed drift states and assert seam contracts.

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Bugs Closed | Est. Time |
|-------|------|-----------|-------------|----------|-------------|-----------|
| 1 | Join Key + Shared Idle Predicate + Test Infra | Stamp `message_id`, wire `is_deferred`, create shared active-work predicate, add invariant tests | None | — (root) | P1, P2, F11, F17 | 6–8h |
| 2 | Worst Drift Bugs — Lock Scoping + Dedup + Status Map | Scope lock release per-queue, fix list_work dedup by message_id, fix lossy status filter | Phase 1 (HARD — F1 requires message_id stamping) | HARD (F1 non-functional without Phase 1 Tasks 1–2) | F1, F3, F4, F7 | 4–6h |
| 3 | Reconciliation Infrastructure | Periodic reconciler, retry watcher migration, second defer gate, observer hardening | Phase 1, Phase 2 | tight (needs lock-release fix from Phase 2) | F2, F5, F6, F8, F10, F12, F13, F14, F15 | 8–10h |

> **Note:** F9 (PostgreSQL-only post-commit re-arm trigger violation) and F16 (lossy legacy API fallback) are deferred — F9 is PG-only and isolated to re-arm logic; F16 is a narrow fallback path. Both can be addressed in a follow-up.

### Coupling Assessment

| Phase Pair | Coupling | Rationale |
|------------|----------|-----------|
| 1 → 2 | **HARD** | F1's dedup by `(instance_id, message_id)` requires Phase 1's `message_id` stamping (Tasks 1–2). Without stamped `message_id` on JobItems, the dedup cannot match Task turns to their driving JobItem and is non-functional. |
| 2 → 3 | **tight** | Phase 3's periodic reconciler must understand the lock-scoping semantics established in Phase 2 (F4/F7 fix). The observer hardening (F13/F14/F15) depends on the correct status semantics from F3's fix. |
| 1 → 3 | **loose** | Phase 3 uses the shared active-work predicate from Phase 1 (B category) for F8 (second defer gate) and F5 (reconciler). Interface-only dependency. |

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Stamping `message_id` breaks existing JobItem rows with NULL metadata | medium | Backfill migration: `UPDATE job_queue_items SET metadata = json_set(metadata, '$.message_id', ...)` for active jobs; NULL-safe guard in readers regardless |
| Shared predicate changes defer-queue timing semantics | high | Comprehensive SQLite invariant tests (Phase 1 Category D); run existing 21 defer tests + 6 deadlock tests |
| Lock-release scoping (F4/F7) changes `_finalize_terminal` behavior | high | Dedicated test for cancel-on-queued-sibling; run `test_jq_proxy_phase4_finalize_terminal.py` suite |
| Periodic reconciler causes false-positive force-cancels | high | Conservative thresholds; reconciler only acts on clear drift states (active JobItem + pending Task with no heartbeat); log-only mode initially |
| PostgreSQL trigger violations during testing | medium | All new tests must pass on both SQLite and PG; run `tests/postgres/` suite explicitly |
| Retry `work_id` change breaks existing watchers | high | Watcher migration is atomic within `schedule_retry` transaction; test exact-match watcher survival; `notify_work_watchers` contract unchanged |
| Reconciler bypasses `_is_idle` and runs during active work | medium | Reconciler registered on StaleTaskRecovery's loop (own asyncio task), NOT MaintenanceService._loop; conservative thresholds prevent false-positives |

## Success Criteria
- [ ] P1: A defer-queue job's Task is actually claimed and completes (not stuck "processing")
- [ ] P2: Defer-queue job stays pending while virtual jobs (Task-only) are active in the same project
- [ ] F11: `has_pending_tasks_blocked_by_busy_instance` does not misclassify freshly-admitted jobs
- [ ] F17: SQLite default-suite test exercises the lock↔admission invariant
- [ ] F1: `list_work` by instance_id shows both the driving task and standalone message turns
- [ ] F3: `/api/work?status=failed` returns failed jobs; `?status=completed` excludes failed/cancelled; NULL `terminal_reason` falls back to `completed`
- [ ] F4/F7: `cancel_job` on a queued sibling does not release an active job's lock
- [ ] F5: Periodic reconciler catches stuck "processing" JobItem + "pending" Task
- [ ] F6: Watcher rows migrated from parent to child `work_id` inside retry transaction (exact-match preserved)
- [ ] F8: Second defer idle-gate (observer path) uses shared predicate
- [ ] All existing tests pass (8000+ SQLite unit tests)
- [ ] PostgreSQL test suite passes (`tests/postgres/`)

## Tracking
- Created: 2026-06-30
- Last Updated: 2026-06-30 (rev. 2 — reviewer feedback incorporated)
- Status: draft
