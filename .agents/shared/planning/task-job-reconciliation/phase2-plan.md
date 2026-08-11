# Phase 2: Defensive Idle-Gate

## Objective

Update the idle-gate predicates so a `paused` task whose linked JobItem is terminal (`done`/`dead`) does NOT count as active work. This is defense-in-depth: Phase 1 prevents new occurrences going forward; Phase 2 ensures even if reconciliation misses a row (e.g., due to a race, transient DB failure, or pre-Phase-1 data), the idle-gate does not block defer/background queues forever.

The pause-first crash recovery convention is preserved: paused tasks whose JobItem is `active` or `queued` correctly continue to block — only orphaned paused tasks (JobItem already terminal) are excluded.

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Modify `TaskRepository.has_active_non_deferred_work` running+paused branch (project_id=None) at `daemon/repositories/task/repository.py:2045-2057` | none | Add `AND NOT EXISTS (SELECT 1 FROM job_queue_items _qi WHERE _qi.job_id = t.work_id AND _qi.admission_state IN ('done', 'dead') AND _qi.deleted_at IS NULL)` to the WHERE clause. Bind params: `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value` |
| 2 | Modify `TaskRepository.has_active_non_deferred_work` pending-only branch (project_id=None) at `daemon/repositories/task/repository.py:2063-2081` | none | Add a SECOND `AND NOT EXISTS` clause (in addition to the existing queued exclusion) using the same `admission_state IN ('done','dead')` shape as Task 1. This is required because the existing pending-only branch's `NOT EXISTS` only excludes `admission_state='queued'` — a PENDING task whose JobItem is already terminal would still be counted, leaving the deadlock partially unfixed for that path. |
| 3 | Modify `TaskRepository.has_active_non_deferred_work` running+paused branch (project_id!=None) at `daemon/repositories/task/repository.py:2088-2102` | none | Same `AND NOT EXISTS (admission_state IN ('done','dead'))` addition as Task 1, with the existing `AND i.project_id = :project_id` filter preserved. |
| 4 | Modify `TaskRepository.has_active_non_deferred_work` pending-only branch (project_id!=None) at `daemon/repositories/task/repository.py:2103-2123` | none | Same second `AND NOT EXISTS (admission_state IN ('done','dead'))` addition as Task 2, with the existing `AND i.project_id = :project_id` filter and the existing queued `NOT EXISTS` preserved. |
| 5 | Modify `TaskRepository.has_active_non_background_work` running+paused branch at `daemon/repositories/task/repository.py:2209-2221` | none | Same `AND NOT EXISTS (admission_state IN ('done','dead'))` addition as Task 1, using `is_background = :is_background_false` instead of `is_deferred`. The background gate is system-wide (project_id param is ignored) so a single branch covers all scopes. |
| 6 | Modify `TaskRepository.has_active_non_background_work` pending-only branch at `daemon/repositories/task/repository.py:2222-2240` | none | Same second `AND NOT EXISTS (admission_state IN ('done','dead'))` addition as Task 2, using `is_background = :is_background_false`. |
| 7 | Verify `JobRepository.has_active_non_deferred_work` (`daemon/repositories/job_queue/repository.py:589`, lines 652-676) is correct as-is | none | Read existing SQL; confirm it counts only `admission_state='active'` (not 'queued'); terminal JobItems (`done`/`dead`) are excluded by definition — no code changes needed |
| 8 | Verify `JobRepository.has_active_non_background_work` (`daemon/repositories/job_queue/repository.py:714`, lines 801-820) is correct as-is | none | Read existing SQL; confirm the outer `admission_state IN ('queued','active')` filter already excludes terminal JobItems; the NOT EXISTS exclusion (FIX 2B 2026-08-10) handles the queued+pending case — no code changes needed |
| 9 | Add integration test: stuck `paused` task + terminal JobItem → idle-gate returns `False` for both TaskRepository predicates | Tasks 1-6 | Test passes for both `has_active_non_deferred_work` AND `has_active_non_background_work`, covering BOTH project_id=None AND project_id!=None branches |
| 10 | Add benchmark test for NOT EXISTS subquery performance | Tasks 1-6 | Seed 10K rows in `task` + `job_queue_items`; query the predicate 100 times; assert p95 < 50ms |
| 11 | (Optional but recommended) Add composite index on `job_queue_items(job_id, admission_state, deleted_at)` if not present | Task 10 | If benchmark exceeds threshold, add migration: `CREATE INDEX IF NOT EXISTS ix_job_queue_items_work_id_admission_state ON job_queue_items (job_id, admission_state, deleted_at)` |

## Coupling

- **Tight with:** Phase 1. Both depend on the same predicate semantics — `paused` = active UNLESS the linked JobItem is terminal. Phase 2 is the defensive twin of Phase 1.
- **Loose with:** Phase 3. The migration is the safety net for any pre-existing rows that slip past Phase 2.
- **Independent of:** Finalization code paths. Phase 2 is read-only — it does not modify Task or JobItem state.

## Detailed Implementation Guidance

> **Reviewer correction C2 (2026-08-11)** — the original plan applied the
> `NOT EXISTS (admission_state IN ('done','dead'))` exclusion to the
> running+paused branch only. The pending-only branch's existing
> `NOT EXISTS (admission_state = 'queued')` exclusion does NOT cover
> terminal JobItems — a PENDING task whose linked JobItem is already
> `done`/`dead` would still be counted as active work, leaving the
> deadlock partially unfixed for that path. The corrected plan
> applies the terminal-JobItem `NOT EXISTS` to **both branches** in
> `has_active_non_deferred_work` and `has_active_non_background_work`,
> for a total of **6 SQL locations** to patch (4 for the defer
> predicate — project_id=None AND project_id!=None variants of each
> branch — and 2 for the background predicate).
>
> **Reviewer correction W4 (2026-08-11)** — the "before" SQL snippets
> below are the ACTUAL current SQL from
> `daemon/repositories/task/repository.py`, including the full WHERE
> clause (`is_deferred = :is_deferred_false` /
> `is_background = :is_background_false`) and the existing
> `NOT EXISTS (admission_state = 'queued')` exclusion. The original
> "before" snippets omitted the `is_deferred`/`is_background` filter
> and the entire pending-only branch, making the before/after
> comparison misleading.

### Task 1: `has_active_non_deferred_work` running+paused branch (project_id=None)

File: `daemon/repositories/task/repository.py` (lines 2045-2057)

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_deferred = :is_deferred_false
)
```

Bind params: `status_running=TaskStatus.RUNNING.value`, `status_paused=TaskStatus.PAUSED.value`, `is_deferred_false=False`.

**After:**
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_deferred = :is_deferred_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

### Task 2: `has_active_non_deferred_work` pending-only branch (project_id=None)

File: `daemon/repositories/task/repository.py` (lines 2063-2081)

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_deferred = :is_deferred_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: `status_pending=TaskStatus.PENDING.value`, `is_deferred_false=False`, `qi_queued=AdmissionState.QUEUED.value`.

**After:** Add a SECOND `AND NOT EXISTS` clause (in addition to the existing queued exclusion):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_deferred = :is_deferred_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

### Task 3: `has_active_non_deferred_work` running+paused branch (project_id!=None)

File: `daemon/repositories/task/repository.py` (lines 2088-2102)

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_deferred = :is_deferred_false
      AND i.project_id = :project_id
)
```

Bind params: `status_running=TaskStatus.RUNNING.value`, `status_paused=TaskStatus.PAUSED.value`, `is_deferred_false=False`, `project_id=project_id`.

**After:**
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_deferred = :is_deferred_false
      AND i.project_id = :project_id
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

### Task 4: `has_active_non_deferred_work` pending-only branch (project_id!=None)

File: `daemon/repositories/task/repository.py` (lines 2103-2123)

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_deferred = :is_deferred_false
      AND i.project_id = :project_id
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: `status_pending=TaskStatus.PENDING.value`, `is_deferred_false=False`, `project_id=project_id`, `qi_queued=AdmissionState.QUEUED.value`.

**After:** Add a SECOND `AND NOT EXISTS` clause (in addition to the existing queued exclusion):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_deferred = :is_deferred_false
      AND i.project_id = :project_id
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

### Task 5: `has_active_non_background_work` running+paused branch

File: `daemon/repositories/task/repository.py` (lines 2209-2221)

The background gate is ALWAYS system-wide per the existing code (the `project_id` parameter is `del`'d before the SQL block). A single branch covers all scopes.

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_background = :is_background_false
)
```

Bind params: `status_running=TaskStatus.RUNNING.value`, `status_paused=TaskStatus.PAUSED.value`, `is_background_false=False`.

**After:**
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status IN (:status_running, :status_paused)
      AND t.is_background = :is_background_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

### Task 6: `has_active_non_background_work` pending-only branch

File: `daemon/repositories/task/repository.py` (lines 2222-2240)

**Before** (actual current SQL):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_background = :is_background_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: `status_pending=TaskStatus.PENDING.value`, `is_background_false=False`, `qi_queued=AdmissionState.QUEUED.value`.

**After:** Add a SECOND `AND NOT EXISTS` clause (in addition to the existing queued exclusion):
```sql
SELECT EXISTS(
    SELECT 1 FROM task t
    JOIN instances i ON t.instance_id = i.instance_id
    WHERE t.status = :status_pending
      AND t.is_background = :is_background_false
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state = :qi_queued
            AND _qi.deleted_at IS NULL
      )
      AND NOT EXISTS (
          SELECT 1 FROM job_queue_items _qi
          WHERE _qi.job_id = t.work_id
            AND _qi.admission_state IN (:qi_done, :qi_dead)
            AND _qi.deleted_at IS NULL
      )
)
```

Bind params: above plus `qi_done=AdmissionState.DONE.value`, `qi_dead=AdmissionState.DEAD.value`.

**Summary of SQL locations patched:** 6 total — 4 in `has_active_non_deferred_work` (running+paused × {project_id=None, project_id!=None} + pending-only × {project_id=None, project_id!=None}) and 2 in `has_active_non_background_work` (running+paused + pending-only, each system-wide).

### Why NOT EXISTS (not LEFT JOIN)?

1. **Codebase convention**: The existing pattern in `JobRepository.has_active_non_background_work:801-820` (FIX 2B 2026-08-10) uses NOT EXISTS. Consistency matters for future readers and for query-plan predictability.
2. **Symmetric NULL handling**: LEFT JOIN with NULL check can produce different plans if the planner reorders joins. NOT EXISTS is unambiguous.
3. **Idiomatic SQL**: This pattern is the canonical way to express "no matching row exists" in both SQLite and PostgreSQL.

### Why the portable `WHERE EXISTS` works on both drivers

The pattern `WHERE NOT EXISTS (SELECT 1 FROM job_queue_items ji WHERE ji.job_id = t.work_id AND ji.admission_state IN ('done','dead') AND ji.deleted_at IS NULL)` uses only ANSI SQL operators:
- No JSONB operators (`->>`, `#>`).
- No `DROP CONSTRAINT` (PostgreSQL-only).
- No `rowid` (SQLite-only).
- `IN ('done', 'dead')` works identically on both.
- `IS NULL` is ANSI.

This was verified against the existing `JobRepository.has_active_non_background_work` pattern (which uses the same NOT EXISTS shape and is dual-driver tested).

### Tasks 7 + 8: Verification (no code changes expected)

**Task 7 — `JobRepository.has_active_non_deferred_work`:**
- File: `daemon/repositories/job_queue/repository.py:589`
- SQL lines: 652-676
- Counts only `admission_state='active'` (NOT 'queued').
- A terminal JobItem has `admission_state='done'/'dead'`, so it is excluded by the WHERE clause.
- **No changes needed.**

**Task 8 — `JobRepository.has_active_non_background_work`:**
- File: `daemon/repositories/job_queue/repository.py:714`
- SQL lines: 801-820
- Outer filter: `admission_state IN ('queued','active')` — terminal JobItems excluded by definition.
- NOT EXISTS exclusion (FIX 2B 2026-08-10): excludes queued JobItems whose linked Task is pending.
- **No changes needed.**

The verification tasks are explicit so the developer confirms the assumption via reading rather than skipping it.

### Task 11: Optional Composite Index

File: `daemon/migrations/versions/20260811_130000_add_job_queue_items_idle_gate_index.sql` (new file, only if Task 10 benchmark exceeds threshold)

```sql
-- UP
CREATE INDEX IF NOT EXISTS ix_job_queue_items_work_id_admission_state
  ON job_queue_items (job_id, admission_state, deleted_at);

-- DOWN
DROP INDEX IF EXISTS ix_job_queue_items_work_id_admission_state;
```

And add the same statement to `daemon/manager.py` startup list (PostgreSQL mirror — same dual-path pattern as Phase 3).

**Trigger condition**: only add if Task 10 benchmark exceeds 50ms p95. Otherwise skip — the existing schema may already have sufficient indexes.

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | NOT EXISTS subquery causes performance regression on the idle-gate hot path | Medium | Task 10 benchmark; Task 11 conditional index; verify EXPLAIN on both drivers |
| 2 | `paused` task with ACTIVE JobItem incorrectly excluded by NOT EXISTS | High | Subquery only matches `admission_state IN ('done','dead')`; active/queued JobItems are NOT in the subquery result set; explicit test in Task 9 (positive case: active JobItem + paused Task still counts as active) |
| 3 | Dual-driver SQL incompatibility | Medium | Use portable `WHERE EXISTS` pattern; verify on both backends in test; no banned operators |
| 4 | Subquery plan differs between SQLite and PostgreSQL (different JOIN strategies) | Low | Both drivers optimize NOT EXISTS similarly; verify with EXPLAIN on both; the index from Task 11 normalizes this |
| 5 | The pending-only branch of `has_active_non_background_work` has different semantics and is incorrectly modified | Medium | Apply the new `NOT EXISTS` to BOTH branches (running+paused AND pending-only) in both predicates — the pending-only branch's existing `NOT EXISTS (admission_state='queued')` exclusion does NOT cover terminal JobItems; a PENDING task whose JobItem is already `done`/`dead` would still be counted. C2 corrects the original plan that applied the fix only to the running+paused branch. |

## Exit Criterion

All 11 tasks complete (Task 11 conditional on benchmark). Integration tests pass:
- `paused` task + `done` JobItem → `has_active_non_deferred_work` returns `False` AND `has_active_non_background_work` returns `False`.
- `paused` task + `active` JobItem → both predicates return `True` (proves pause-first preserved).
- `running` task + `done` JobItem → both predicates return `True` (regression guard; running Task should still block even if JobItem terminal — covered by Phase 1 reconciliation transitioning to `cancelled`, but the predicate still counts `running` correctly).

Benchmark shows < 50ms p95 on 10K-row table (or Task 11 index added). Both SQLite and PostgreSQL verified.
