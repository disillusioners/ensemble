# Phase 3: Data Migration

## Objective

Create a one-shot migration to backfill existing stuck tasks (those with `status IN ('paused','pending')` whose linked JobItem is terminal). Mirror the UPDATE statement in `daemon/manager.py` startup list for PostgreSQL — **without this mirror, PostgreSQL databases will NOT get the fix** because the migration runner skips non-SQLite engines.

The migration is forward-only: DOWN is a no-op because reverting would re-introduce the stuck state (Tasks with terminal JobItems in `paused`/`pending`).

## Tasks

| # | Task | Depends On | Acceptance |
|---|------|------------|------------|
| 1 | Create migration file `daemon/migrations/versions/20260811_120000_reconcile_stuck_tasks_with_terminal_jobitems.sql` | none | File exists with `-- UP` and `-- DOWN` sections; portable SQL (no banned operators); idempotent |
| 2 | Add byte-identical UPDATE tuple to the `statements` list inside `_ensure_postgres_columns()` in `daemon/manager.py` (near lines 4498-4536, alongside the existing `20260810_000001_fix_idle_gate_stuck_task_flags.sql` mirror if present) | Task 1 | PostgreSQL mirror added; runs at daemon startup; matches `.sql` content byte-for-byte (minus comment headers); CI parity test (Task 7) verifies the match |
| 3 | Add migration checksum verification test | Tasks 1-2 | Migration runner discovers and applies the new file; SHA-256 checksum matches the reference test |
| 4 | Add idempotency test | Tasks 1-2 | Run migration twice in test (on both drivers); second run updates 0 rows |
| 5 | Add dual-driver test (apply on both SQLite and PostgreSQL) | Tasks 1-2 | Both backends apply the migration successfully; both backends report 0 rows on second run |
| 6 | Add production dry-run safety check (optional but recommended): add a `SELECT COUNT(*)` statement that returns the affected row count BEFORE the UPDATE, and log it | Tasks 1-2 | Migration logs row count for observability |
| 7 | Add CI parity check for dual-driver migration. Add a CI test that asserts the `.sql` file's UP section matches the Python-list tuple in `_ensure_postgres_columns()` byte-for-byte (after stripping comments/whitespace). This prevents the two paths from silently diverging — if a developer updates one but forgets the other, the CI fails | Tasks 1-2 | CI test fails on drift between `.sql` file and `_ensure_postgres_columns()` statements list; passes when both are byte-identical (after normalization) |

## Coupling

- **Loose with:** Phase 1 (Reconciliation). The migration catches pre-existing data that the code fix will prevent going forward. Phase 3 is **not** a dependency for Phase 1 — they can ship independently.
- **Loose with:** Phase 2 (Defensive Idle-Gate). Phase 2 is defense-in-depth for any rows that slip past Phase 3 (e.g., due to a race between the migration and a finalization).
- **Independent of:** Finalization code paths. The migration operates on data state via direct UPDATE.

## Detailed Implementation Guidance

### Task 1: Migration File

File: `daemon/migrations/versions/20260811_120000_reconcile_stuck_tasks_with_terminal_jobitems.sql`

```sql
-- UP
-- Reconcile tasks whose linked JobItem is already terminal.
-- Per docs/plans/task-job-reconciliation/phase3-plan.md
--
-- Root cause: When a JobItem transitions to done/dead, the linked Task
-- (via task.work_id = job_queue_items.job_id) is NEVER finalized. The
-- Task stays in 'paused' or 'pending' indefinitely, blocking the
-- defer/background idle-gate. This migration backfills the orphaned
-- rows that exist before the Phase 1 reconciliation code lands.
--
-- Idempotent: WHERE status IN ('paused', 'pending') guard means
-- re-running the migration updates 0 rows on the second pass.
UPDATE task
SET status = 'cancelled',
    updated_at = CURRENT_TIMESTAMP
WHERE status IN ('paused', 'pending')
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      WHERE ji.job_id = task.work_id
        AND ji.admission_state IN ('done', 'dead')
        AND ji.deleted_at IS NULL
  );

-- DOWN
-- No-op: this migration is a forward-only backfill. Running DOWN does
-- not restore the original (paused/pending) status because the JobItem
-- is already terminal — recreating the stuck state would re-introduce
-- the bug. Same convention as the reference migration
-- (20260810_000001_fix_idle_gate_stuck_task_flags.sql).
SELECT 1;
```

**SQL safety checklist (must pass before merge):**
- [ ] No SQLite-only syntax (`rowid`, `AUTOINCREMENT`).
- [ ] No PostgreSQL-only DDL (`DROP CONSTRAINT`).
- [ ] No JSONB operators (`->>`, `#>`, `@>`).
- [ ] `WHERE EXISTS` pattern (ANSI, dual-driver).
- [ ] `CURRENT_TIMESTAMP` supported by both drivers.
- [ ] `ji.deleted_at IS NULL` guard against soft-deleted JobItems.
- [ ] Idempotent: re-running the UPDATE affects 0 rows.

### Task 2: PostgreSQL Mirror in `daemon/manager.py` (`_ensure_postgres_columns`)

File: `daemon/manager.py` — add to the `statements` list inside `_ensure_postgres_columns()` (near lines 4498-4536)

**Important (W7):** The list is a **Python list of SQL string tuples** executed via `conn.execute(text(stmt))` at startup. There is NO constant named `_POSTGRES_STARTUP_STATEMENTS` — that placeholder name does not exist in the codebase. The actual code path is the `statements` parameter of `_ensure_postgres_columns()`. Developer MUST verify the exact list during implementation by reading `daemon/manager.py:4498-4536` directly.

```python
# Per docs/plans/task-job-reconciliation/phase3-plan.md
# MUST be byte-identical to the UP section of the .sql migration file.
# Add as a tuple inside the `statements` list in _ensure_postgres_columns().
(
    "UPDATE task "
    "SET status = 'cancelled', updated_at = CURRENT_TIMESTAMP "
    "WHERE status IN ('paused', 'pending') "
    "AND EXISTS ("
    "    SELECT 1 FROM job_queue_items ji "
    "    WHERE ji.job_id = task.work_id "
    "      AND ji.admission_state IN ('done', 'dead') "
    "      AND ji.deleted_at IS NULL"
    ");"
),
```

This MUST be added in the same code change as the `.sql` file. Without this mirror, PostgreSQL databases will NOT get the fix because the migration runner skips non-SQLite engines (`daemon/migrations/runner.py`).

**Pre-merge verification step:** `grep -n "_ensure_postgres_columns" daemon/manager.py` to confirm the function location, then locate the `statements` list (a Python list of SQL string tuples) and add the new tuple adjacent to the existing `20260810_000001_fix_idle_gate_stuck_task_flags.sql` mirror if present.

### Task 6 (Optional): Dry-Run Row Count

If observability is desired, the migration can be extended to log the affected row count:

```sql
-- UP
-- (add this BEFORE the UPDATE for observability)
SELECT COUNT(*) AS reconcile_count
FROM task t
WHERE t.status IN ('paused', 'pending')
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      WHERE ji.job_id = t.work_id
        AND ji.admission_state IN ('done', 'dead')
        AND ji.deleted_at IS NULL
  );

UPDATE task
SET status = 'cancelled',
    updated_at = CURRENT_TIMESTAMP
WHERE status IN ('paused', 'pending')
  AND EXISTS (
      SELECT 1 FROM job_queue_items ji
      WHERE ji.job_id = task.work_id
        AND ji.admission_state IN ('done', 'dead')
        AND ji.deleted_at IS NULL
  );
```

The PostgreSQL mirror must include the same `SELECT COUNT(*)` statement.

### Why Two Locations?

The migration runner (`daemon/migrations/runner.py`) discovers `.sql` files in `daemon/migrations/versions/` named `YYYYMMDD_HHMMSS_description.sql`, parses `-- UP`/`-- DOWN` sections, validates SHA-256 checksums, and applies them via SQLAlchemy.

**However**: the runner **skips non-SQLite engines** — PostgreSQL is NOT migrated by this runner. The runner was designed for SQLite-only operations.

For PostgreSQL, the equivalent statements are added to the **`statements` parameter** of `_ensure_postgres_columns()` in `daemon/manager.py` (near lines 4498-4536). The list is a Python list of SQL string tuples executed via `conn.execute(text(stmt))` at startup. There is **no `_POSTGRES_STARTUP_STATEMENTS` constant** — the actual code path is the `statements` argument of `_ensure_postgres_columns()`.

Without both locations, only one driver gets the migration.

**Reference (EXACT precedent):**
- File: `daemon/migrations/versions/20260810_000001_fix_idle_gate_stuck_task_flags.sql`
- PostgreSQL mirror: `daemon/manager.py:4498-4536` (the `statements` list inside `_ensure_postgres_columns()`)

This is the documented pattern from the project's migration system (see Phase 3 surface in the Core Architecture blueprint). Any new UPDATE-only migration MUST follow this pattern.

### Idempotency

The `WHERE status IN ('paused', 'pending')` guard makes the UPDATE idempotent:
- First run: cancels all stuck tasks.
- Second run: 0 rows match (no remaining `paused`/`pending` tasks with terminal JobItems).

The migration can safely be re-run if the checksum validation fails or if the runner is re-invoked.

### DOWN Semantics

`-- DOWN` is intentionally a no-op (`SELECT 1`). Rationale:

1. **Reverting reintroduces the bug.** The migration exists to fix Tasks that are in an inconsistent state (paused/pending with terminal JobItems). Restoring that state would re-introduce the deadlock.
2. **The bug is not data corruption.** No data was lost or malformed — the Tasks were simply orphaned. The fix is forward-only.
3. **Precedent exists.** The reference migration (`20260810_000001_fix_idle_gate_stuck_task_flags.sql`) uses the same no-op DOWN pattern.

## Risks

| # | Risk | Impact | Mitigation |
|---|------|--------|------------|
| 1 | PostgreSQL mirror missing from `manager.py` startup list → only SQLite gets the fix | Critical | Required checklist item; **both** SQLite (`.sql`) AND PostgreSQL (Python-list) MUST be updated together in the same commit/PR; tests on both backends in Task 5 |
| 2 | Migration accidentally cancels legitimate `pending` tasks | High | Idempotent `WHERE status IN ('paused', 'pending')` guard — the query only matches Tasks whose JobItem is terminal; legitimate `pending` tasks have ACTIVE JobItems; specific test cases in Task 4 |
| 3 | Migration runs before Phase 1+2 deployed → new stuck tasks form afterward | Medium | Migration is forward-compatible (cancels stuck tasks regardless of code state); Phase 1+2 prevent new occurrences; recommend shipping all 3 phases in the same release |
| 4 | `CURRENT_TIMESTAMP` semantic differs between drivers | Low | Both SQLite and PostgreSQL support `CURRENT_TIMESTAMP` returning the current timestamp; equivalent behavior. Use `CURRENT_TIMESTAMP` throughout (not `func.now()`) for dual-driver portability — both SQLite and PostgreSQL support it natively without driver-specific shims |
| 5 | `task.work_id` index missing → slow UPDATE on large tables | Medium | Verify `task.work_id` is indexed (likely already is for the JOIN in Phase 2 predicates); if not, add a separate migration to create the index |
| 6 | Migration takes a long lock on `task` table (PostgreSQL) | Low | UPDATE with WHERE clause is row-level locked, not table-level; if >100K rows affected, consider batching in a follow-up migration |
| 7 | The `.sql` file and the Python-list mirror drift (one is updated, the other isn't) | Critical | Code review checklist item: verify both files are updated in the same commit; add a CI check that asserts byte-equality (subtract comment headers) |
| 8 | The `statements` list in `_ensure_postgres_columns()` is at a different location than `manager.py:4498-4536` | Low | Developer MUST read `daemon/manager.py` directly to find the actual list location; `grep -n "_ensure_postgres_columns" daemon/manager.py` to locate the function, then inspect its `statements` parameter (a Python list of SQL string tuples). The placeholder name `_POSTGRES_STARTUP_STATEMENTS` does NOT exist in the codebase |

## Deployment Order

**Recommended Deployment Order: Phase 2 → Phase 3 → Phase 1**

1. **Phase 2 (idle-gate fix) FIRST** — provides immediate symptom relief. Even if reconciliation hasn't shipped, the defensive idle-gate unblocks stuck queues on the next predicate evaluation.
2. **Phase 3 (data migration) SECOND** — cleans up existing stuck rows. Run during a maintenance window or deploy alongside Phase 2.
3. **Phase 1 (reconciliation) THIRD** — the root-cause fix. Prevents new stuck tasks from forming.
4. **Phase 4 (visibility + cleanup) LAST** — UX layer. Depends on Phase 1's reconciliation logic concept.

All four phases SHOULD ship in the same release, but if phased rollout is needed, this order minimizes user-visible impact.

## Exit Criterion

All 7 tasks complete. Migration:
- Applies on both SQLite and PostgreSQL.
- Reports correct row count on first run (expected: matches the count of orphaned rows in the database).
- Reports 0 rows on second run (idempotency).
- DOWN section is no-op (`SELECT 1`).
- PostgreSQL mirror confirmed in `_ensure_postgres_columns()` `statements` list in `daemon/manager.py` (verify exact location during implementation; the placeholder name `_POSTGRES_STARTUP_STATEMENTS` does not exist).
- Both files pass the SQL safety checklist (no banned operators).
- Both files match byte-for-byte (minus comment headers) — verified by CI test (Task 7).

Pre-merge verification checklist:
- [ ] `.sql` file exists at `daemon/migrations/versions/20260811_120000_reconcile_stuck_tasks_with_terminal_jobitems.sql`.
- [ ] Python-list tuple exists in `_ensure_postgres_columns()` `statements` list in `daemon/manager.py` (verify exact location — placeholder name `_POSTGRES_STARTUP_STATEMENTS` does not exist).
- [ ] Migration runner discovers the new file and computes its checksum correctly.
- [ ] CI parity test (Task 7) passes: `.sql` UP section matches `_ensure_postgres_columns()` tuple byte-for-byte (after stripping comments/whitespace).
- [ ] Tests pass on SQLite.
- [ ] Tests pass on PostgreSQL.
- [ ] Code review confirms both files updated in the same commit.
