# Approval Tracking: task-job-reconciliation

## Iteration 001 — 2026-08-11

**Plan:** Task↔JobItem Reconciliation Fix + Defensive Idle-Gate + Bad State Visibility
**Slug:** task-job-reconciliation
**Mode:** Plan Approval (section-parallel, 3 workers)
**Verdict:** APPROVED

### Workers Dispatched
| Worker | Skill | Target | Verdict |
|--------|-------|--------|---------|
| approve-worker-backend (2b74f9ad) | plan-approval | Phases 1+2 | APPROVED (0 blocking, 9 notes) |
| approve-worker-data-ui (2a31aba7) | plan-approval | Phases 3+4 | APPROVED (0 blocking, 8 notes) |
| approve-worker-crosscut (b4f802dd) | plan-approval | Overview + cross-cutting | APPROVED (0 blocking, 5 notes) |

### Convergent Findings (deduplicated — flagged by 2+ workers)
1. Phase 1 Task 7 (`_resume_cascade_db_sync` InvalidTransitionError catch path) — must verify pre-merge. Determines whether C6 race stays Low severity.
2. F14 pending-tasks gate SQL (lines 3213-3241) — linchpin assumption that gate only checks status='pending', not 'paused'. Developer must read actual SQL.
3. `_ensure_postgres_columns()` exact location (manager.py:4498-4536) — pattern reference correct, function-start line drifts. W7 warning mitigates.
4. `manager._task_repo` access pattern — inconsistent between Task 6 (singleton) and Task 8 (fresh construct). Unify.
5. Deployment order vs phase numbering — P2→P3→P1→P4 is justified but confusing.

### Notes (non-blocking, all workers)
- Config flag TASK_RECONCILIATION_BEST_EFFORT added in Task 2 but unwired in Task 3 — wire or drop.
- Multiple-JobItem-per-Task edge case — depends on work_id stability across retries.
- Missing-JobItem zombie (Task with no linked JobItem) — pre-existing, not regression.
- work_id ↔ job_id naming bridge undocumented.
- Exit criterion language mixes implementation guidance with testable outcome.
- Inline vs module-level import; preflight bypasses is_write_paused (justified).
- Per-queue extra query — negligible, bulk optimization deferred.

All Notes are implementation hygiene caught by code review, not plan-completeness gaps.
