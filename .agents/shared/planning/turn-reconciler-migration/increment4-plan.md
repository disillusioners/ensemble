# Increment 4 Implementation Plan: Turn Handles and Explicit Resume Routing

Date: 2026-08-01
Author: planner[v2] via plan-creation worker
Status: Ready for Review (revised per Council Review — B2, B3, C2, C4, R1 applied)
Parent design: `docs/plans/turn-reconciler-named-transitions.md` — Phase 3
Dependency: Increment 3 — Named Transitions (`SUSPEND_TURN`, `RESUME_TURN`)

> **Revision history (2026-08-01, Council Review).** Section 6 (Schema Migration) now requires: (B2) a legacy-paused backfill `UPDATE` step on both SQLite and PostgreSQL; (B3) a guarded/idempotent SQLite migration that does not break fresh-DB `create_all()` ordering; (C4) a triple-registered composite index `idx_task_resume_target` on `(resume_target_turn_id, suspension_reason)` with an EXPLAIN verification test; (C2) a new §11.4.1 full-chain E2E test that proves Increments 1, 3, and 4 integrate. Section 2 line citations corrected (R1). Rollback §16 Stage B flagged as destructive for the backfilled columns (B2).

## 1. Objective

Add an explicit, persisted suspension handle to each turn and rewrite resume routing so an answer resumes a known turn by authoritative `task.work_id`, rather than inferring root-versus-child behavior from a set of task statuses.

Increment 4 is complete when `SUSPEND_TURN` records why and which turn is suspended, `RESUME_TURN` targets that recorded turn, answer-gate resumes reuse an existing turn instead of creating a cascade-resume Task without a JobItem, and pause-during-`process_report` resumes the report branch without leaving the original turn's JobItem active.

## 2. Problem Statement: The Routing Gap

### Current behavior

`DaemonManager.resume_processing_job` currently asks `TaskRepository.find_paused_or_running_by_instance(instance_id)` for the newest `PROCESS_MESSAGE` Task whose status is `paused`, `running`, or `cancelled` (`daemon/manager.py:4821-5023` resume body; the actual call site is at `daemon/manager.py:4880`; `daemon/repositories/task/repository.py:171-244`). That lookup is being used as an indirect classifier:

- a matching `PROCESS_MESSAGE` Task means “root/checkpoint resume”; and
- no matching Task means “child/cascade resume,” which enqueues a fresh message through `enqueue_message(source="cascade_resume")` (`daemon/manager.py:4990-4996`, surrounded by the branch at lines 4961-5003).

That inference fails when a pause occurs during a `process_report` turn. The original `process_message` Task may already be terminal, while the currently interrupted turn is a `process_report` Task. The lookup therefore returns no normal root candidate. The current point-fix compensates with `find_resume_root_candidate_by_active_job`, which detects a terminal `PROCESS_MESSAGE` Task plus an active JobItem (`daemon/repositories/task/repository.py:246-370`; `daemon/manager.py:4884-4919` for the manager fallback), but it still reconstructs intent from mirror state after the fact.

### Structural defect

Task status and task type do not encode *why* a turn is suspended or *which turn* an answer should resume. As a result, routing behavior depends on a heuristic over historical rows rather than a durable handle declared at suspension time. The heuristic can miss a report turn, route into the child branch, create a fresh Task with no JobItem, and leave the original JobItem active.

### Target behavior

`SUSPEND_TURN` records suspension intent on the authoritative Task row. Answer handling then finds a turn with `suspension_reason='awaiting_answer'` and a non-null `resume_target_turn_id`, and `RESUME_TURN` operates on that recorded `work_id`. If no answer-gate suspension exists, the answer path does not fabricate one from statuses. Pause-during-report follows the report-resume path, and the reconciler remains responsible for mirror cleanup after `RESUME_TURN`.

## 3. Scope

### In Scope

1. Add two nullable Task columns:
   - `suspension_reason`: `null`, `awaiting_answer`, `awaiting_children`, or `paused_external`.
   - `resume_target_turn_id`: nullable UUID string referencing the target turn's authoritative `task.work_id` by convention.
2. Register both columns in all three required schema paths:
   - SQLModel declaration for fresh databases and ORM reads/writes.
   - SQLite `.sql` migration for existing SQLite databases (idempotent — see §6 B3 note).
   - PostgreSQL `_ensure_postgres_columns()` ALTER statements for existing PostgreSQL databases.
3. Add a composite index `idx_task_resume_target` on `(resume_target_turn_id, suspension_reason)` triple-registered across SQLModel, SQLite migration, and `_ensure_postgres_columns()` (§6 C4 note).
4. Backfill in-flight legacy `paused` Tasks whose `suspension_reason` is NULL at migration time so the new routing code can identify them as suspended (§6 B2 note). This must run on both SQLite and PostgreSQL.
5. Extend Increment 3's `SUSPEND_TURN` to persist both handle fields atomically with suspension.
6. Extend Increment 3's `RESUME_TURN` to resolve and target the recorded `resume_target_turn_id` by `work_id`.
7. Add `TaskRepository.find_suspended_turn_for_answer(answer_message_id)` for answer-gate routing.
8. Add `TaskRepository.find_paused_or_cancellable_turn(instance_id)` for pause-cascade selection.
9. Rewrite `DaemonManager.resume_processing_job` to route by explicit suspension intent instead of `PROCESS_MESSAGE` status inference.
10. Delete `find_paused_or_running_by_instance` and replace all production/test callers.
11. Delete the point-fix `find_resume_root_candidate_by_active_job`, since explicit turn handles supersede it.
12. Remove the answer-gate cascade-resume fresh-Task/no-JobItem path while preserving legitimate internal child orchestration that is not an answer-gate resume.
13. Add repository, transition, routing, migration, and end-to-end coverage for both answer-gate and pause-during-report behavior.
14. Add a full-chain E2E test that exercises the entire post-Increment-4 lifecycle (claim → pause → resume → answer → complete) to validate Increments 1, 3, and 4 working together with no orphan mirrors, no deadlock, and correct answer delivery (§11.4 C2 note).
15. Verify the full existing 404-test baseline remains green, with PostgreSQL as the primary integration target and SQLite migration parity covered.

### Out of Scope

- Changing the eight-table reconciliation rules; Increment 4 changes the selected turn, not mirror projection.
- Simplifying cross-system claim guard carve-outs; that is a later design-doc phase.
- Altering DependencyBus semantics or parent/child completion authority.
- Merging Task, JobItem, and MessageQueue tables.
- Introducing a hard database foreign key from `resume_target_turn_id` to `task.work_id`; this increment uses FK-style application validation because self-referential lifecycle and rollback behavior need separate review.
- Reworking public Job-as-Front-Primitive entry points.
- Adding or removing suspension reasons beyond the three specified non-null values.

## 4. Exact Files and Functions Touched

| File | Planned change |
|---|---|
| `daemon/repositories/task/models.py` | Add a `SuspensionReason` string enum (or equivalent centrally validated constants); add nullable `suspension_reason` and `resume_target_turn_id` fields to `Task`; add the composite `Index("idx_task_resume_target", "resume_target_turn_id", "suspension_reason")` to `Task.__table_args__` (§6 C4); include both fields in `Task.to_dict()` if Task serialization is part of transition diagnostics/tests. |
| `daemon/migrations/versions/20260801_000001_task_turn_handles.sql` | New SQLite migration adding both nullable `TEXT` columns, the composite index `idx_task_resume_target`, AND the legacy-paused backfill `UPDATE` (§6 B2/B3/C4). The migration must be guarded/idempotent — see §6 B3 note. Use the repository's timestamp/sequence convention and adjust the filename if another migration has claimed the slot before implementation. |
| `daemon/manager.py::_ensure_postgres_columns()` (starts near line 2992) | Add idempotent PostgreSQL `ALTER TABLE IF EXISTS task ADD COLUMN IF NOT EXISTS ... VARCHAR` statements for both columns, the composite `CREATE INDEX IF NOT EXISTS idx_task_resume_target ...` statement, AND the legacy-paused backfill (`UPDATE task SET ... WHERE status='paused' AND suspension_reason IS NULL`). Update the method's schema inventory documentation. Follow the existing project guidance at `manager.py:3091` — never issue raw `ALTER TABLE` without `IF NOT EXISTS`. |
| Increment 3 transition module, expected `daemon/services/turn_transitions.py` | Modify `SUSPEND_TURN` and `RESUME_TURN`; use the actual Increment 3 location if review selected repository anchoring instead. |
| `daemon/repositories/task/repository.py` | Add `find_suspended_turn_for_answer`; add `find_paused_or_cancellable_turn`; delete `find_paused_or_running_by_instance` (currently around lines 171-244); delete `find_resume_root_candidate_by_active_job` (currently around lines 246-370); retain `work_id` as the only turn correlation key. |
| `daemon/manager.py::resume_processing_job` (currently around lines 4821-5023; active-orphan fallback at lines 4884-4919; cascade-resume enqueue at lines 4961-5003) | Replace status/type inference and active-orphan fallback with explicit answer-suspension resolution and named `RESUME_TURN`; remove answer-gate fresh enqueue behavior; update route logging and return values to report target `work_id`. |
| Question answer/dismiss orchestration call sites | Ensure the answer's `message_id` is available to `find_suspended_turn_for_answer` and the returned target `work_id` is passed into `RESUME_TURN`/resume processing. Exact call sites must be confirmed against Increment 3's merged interface before coding. |
| Pause cascade call sites in `daemon/services/instance_lifecycle.py` (e.g., `_pause_cascade_db_sync` near line 3039) or their Increment 3 replacement | Replace `find_paused_or_running_by_instance` usage with `find_paused_or_cancellable_turn`; pass an explicit suspension reason into `SUSPEND_TURN`. |
| Tests currently covering pause/resume, terminal-orphan routing, questions, and migrations | Rewrite obsolete heuristic assertions, add explicit-handle tests, add the backfill-migration test (§11.1 B2), add the fresh-DB SQLite idempotency test (§11.1 B3), add the composite-index verification (§11.1 C4), add the full-chain E2E test (§11.4 C2), and add the missing end-to-end scenarios described in §10/§11. |

### Implementation-time search gate

Before editing, run a repository-wide search for both deleted method names and for direct calls to `resume_processing_job`. Every production caller and test fixture must be classified. The increment cannot be considered complete while either deleted symbol remains outside historical documentation or intentionally retained migration notes.

## 5. Dependency on Increment 3

Increment 4 must not begin until Increment 3 has landed and its named-transition contract is stable.

### Required Increment 3 capabilities

- `SUSPEND_TURN(work_id, ...)` exists and owns the authoritative status transition into suspension.
- `RESUME_TURN(work_id, ...)` exists and owns the authoritative transition out of suspension plus reconciler invocation.
- Both execute inside the transition transaction/`WriteGuardSession` established by Increment 3.
- `TransitionResult` can carry post-commit resume scheduling data without scheduling graph work before commit.
- The reconciler is invoked after `RESUME_TURN` so mirror cleanup remains centralized.

### Contract extension in Increment 4

`SUSPEND_TURN` should accept a required reason at all semantically known suspension sites and an optional target turn ID:

```text
SUSPEND_TURN(
    work_id=<turn being suspended>,
    suspension_reason=<awaiting_answer | awaiting_children | paused_external>,
    resume_target_turn_id=<authoritative task.work_id or null>,
)
```

`RESUME_TURN` should accept the explicit target `work_id`, validate the handle, apply the named transition, and clear or consume the suspension handle in the same transaction:

```text
RESUME_TURN(work_id=<resolved resume_target_turn_id>, ...)
```

The precise status result remains governed by Increment 3's approved transition table. Increment 4 must not reopen named-transition semantics; it only adds selection and handle persistence.

### Entry gate

Do not implement temporary direct SQL setters in manager/question code if Increment 3 is absent. Such setters would recreate the hand-written transition problem this migration is eliminating. If Increment 3's API differs from this plan, update this plan at review time before implementation.

## 6. Schema Migration: Mandatory Triple Registration

This is a hard, four-part acceptance gate. A code review checklist must have a separate checked item for each registration AND for the legacy-paused backfill AND for the composite index.

### Registration 1: SQLModel

**File:** `daemon/repositories/task/models.py`

Add nullable fields to `Task`:

```python
suspension_reason: str | None = Field(default=None, index=False)
resume_target_turn_id: str | None = Field(default=None, index=False)
```

And register the composite index in `Task.__table_args__` (see §6 C4 below):

```python
__table_args__ = (
    Index("idx_task_resume_target", "resume_target_turn_id", "suspension_reason"),
    ... # existing indexes
)
```

Implementation decisions:

- Persist strings rather than a database-native enum so SQLite and PostgreSQL behave consistently and enum evolution does not require engine-specific DDL.
- Define `SuspensionReason(str, enum.Enum)` or validated constants containing exactly `awaiting_answer`, `awaiting_children`, and `paused_external`.
- Validate reason values at the named-transition boundary. Legacy/backfilled rows remain null OR hold the safe `paused_external` value assigned by the B2 backfill.
- Do **not** declare single-column `index=True` on either field; the composite index in §6 C4 covers the answer-resume lookup pattern. Single-column indexes are rejected by this plan to avoid duplicate index maintenance.
- Include both fields in `to_dict()` if transition events, API diagnostics, or tests serialize Task rows. Avoid exposing them through public API schemas unless already intended by Increment 3.

### Registration 2: SQLite migration (idempotent / guarded)

**File:** `daemon/migrations/versions/20260801_000001_task_turn_handles.sql` (proposed name)

**§ REVISION NOTE (Council Review — B3):** SQLite's `ALTER TABLE ADD COLUMN` is NOT idempotent — it raises a "duplicate column name" error if the column already exists. On a FRESH database, `SQLModel.metadata.create_all()` already creates the table with every column declared in the model, so the migration runs *after* the columns exist and a plain `ALTER TABLE ADD COLUMN` would fail. SQLite's `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` clause is supported on engines ≥ 3.35.0, but is unreliable on older versions and the project has not standardized on it.

Therefore the SQLite migration must use one of the two following guard patterns — it must NOT be a plain `ALTER TABLE ADD COLUMN`:

- **(a) PRAGMA column-existence detection** (preferred — same pattern as `manager.py:3736`): before issuing each `ALTER TABLE ADD COLUMN`, query `PRAGMA table_info(task)` and skip the ALTER if the column already exists. Implementation can wrap this in a tiny `IF NOT EXISTS`-style helper at the top of the migration file.
- **(b) Try/except "duplicate column name"**: wrap each `ALTER TABLE ADD COLUMN` in a try/except block that catches the SQLite "duplicate column name" error and ignores it. Mirrors the existing "does not exist" error suppression at `manager.py:3736`.

`CREATE INDEX IF NOT EXISTS` is already idempotent in SQLite and does not need a guard.

**Required UP statements** (sketch; actual implementation must use one of the guards above for the two `ALTER TABLE` lines):

```sql
-- Guarded / idempotent column adds (B3)
ALTER TABLE task ADD COLUMN suspension_reason TEXT;
ALTER TABLE task ADD COLUMN resume_target_turn_id TEXT;

-- Composite index (C4)
CREATE INDEX IF NOT EXISTS idx_task_resume_target
    ON task (resume_target_turn_id, suspension_reason);

-- Legacy-paused backfill (B2)
UPDATE task
   SET suspension_reason     = 'paused_external',
       resume_target_turn_id = work_id
 WHERE status = 'paused'
   AND suspension_reason IS NULL;
```

Migration requirements:

- Both columns are nullable; the backfill assigns safe defaults to legacy paused rows so they remain routable by the new explicit-handle code.
- The backfill `UPDATE` is safe to re-run: the `WHERE suspension_reason IS NULL` predicate is naturally idempotent.
- Document that the PostgreSQL equivalent (column adds, index, backfill) lives in `_ensure_postgres_columns()` because the `.sql` runner does not execute for PostgreSQL.
- Follow current SQLite rollback convention. If SQLite cannot safely drop columns in supported versions, document the additive rollback and restore code compatibility by reverting application reads/writes first.

### Registration 3: PostgreSQL ALTER (idempotent, includes backfill)

**File:** `daemon/manager.py`, `_ensure_postgres_columns()` near line 2992

Required idempotent statements (must follow the existing `IF NOT EXISTS` discipline documented at `manager.py:3091` — never issue raw `ALTER TABLE` without `IF NOT EXISTS, as that breaks re-runs on databases that already have the column`):

```sql
-- Columns (already idempotent via IF NOT EXISTS)
ALTER TABLE task ADD COLUMN IF NOT EXISTS suspension_reason     VARCHAR;
ALTER TABLE task ADD COLUMN IF NOT EXISTS resume_target_turn_id VARCHAR;

-- Composite index (C4)
CREATE INDEX IF NOT EXISTS idx_task_resume_target
    ON task (resume_target_turn_id, suspension_reason);

-- Legacy-paused backfill (B2) — wrapped so failure on legacy rows
-- does not block startup. The UPDATE is naturally idempotent because
-- of the WHERE clause; we additionally guard against running on a
-- schema where the columns were just added in the same call (the
-- ALTER above is the same statement, but PostgreSQL evaluates all
-- statements within the connection sequentially, so the UPDATE sees
-- the columns). Use a soft-fail try/except that ignores "column does
-- not exist" errors during the very first invocation on a database
-- pre-Increment-4 only IF the migration order cannot guarantee the
-- ALTER runs first; in practice the ALTER is always first in this
-- list, so the UPDATE will see the columns.
UPDATE task
   SET suspension_reason     = 'paused_external',
       resume_target_turn_id = work_id
 WHERE status = 'paused'
   AND suspension_reason IS NULL;
```

Migration requirements:

- Preserve fail-loud startup behavior if ALTER execution fails. The `_ensure_postgres_columns()` method documents that exceptions propagate.
- Add the composite index with `CREATE INDEX IF NOT EXISTS`.
- Update `_ensure_postgres_columns()` documentation to list both turn-handle fields, the composite index, and the backfill, and point to the SQLite migration for parity.
- Verify the method still runs only on PostgreSQL and remains safe on repeated startup.

### Registration 4: Composite index `idx_task_resume_target` (§ C4)

**Files:**
- SQLModel: `daemon/repositories/task/models.py` — `Index("idx_task_resume_target", "resume_target_turn_id", "suspension_reason")` in `Task.__table_args__`.
- SQLite: `CREATE INDEX IF NOT EXISTS idx_task_resume_target ON task (resume_target_turn_id, suspension_reason);` in the `.sql` migration (Registration 2).
- PostgreSQL: `CREATE INDEX IF NOT EXISTS idx_task_resume_target ON task (resume_target_turn_id, suspension_reason);` in `_ensure_postgres_columns()` (Registration 3).

**Why composite (not two single-column indexes):** `find_suspended_turn_for_answer` queries `WHERE suspension_reason='awaiting_answer' AND resume_target_turn_id IS NOT NULL`. A composite index on `(resume_target_turn_id, suspension_reason)` lets PostgreSQL serve the lookup with a single index scan and lets SQLite use the same index. Two single-column indexes would force the planner to combine them, which is unnecessary overhead at this row count and complicates the schema-parity tests.

### Schema parity verification

Add tests/inspection that independently prove:

1. a fresh SQLModel-created schema contains both columns AND the composite index `idx_task_resume_target`;
2. an existing SQLite schema gains both columns and the composite index through the guarded `.sql` migration;
3. **a fresh SQLite database** (where columns already exist via `SQLModel.metadata.create_all()`) runs the migration successfully without "duplicate column name" errors — this is the B3 idempotency test;
4. the PostgreSQL startup evolution path executes both `ADD COLUMN IF NOT EXISTS` statements and the composite index `CREATE INDEX IF NOT EXISTS` and is idempotent across multiple startups;
5. **legacy paused Task backfill** (B2): inserting a Task with `status='paused'`, `suspension_reason=NULL`, `resume_target_turn_id=NULL`, then running the backfill, leaves the row with `suspension_reason='paused_external'`, `resume_target_turn_id=<work_id>` on both SQLite and PostgreSQL;
6. nullable legacy rows (genuinely null reason) can be read without validation errors;
7. a Task written with each allowed reason and a target UUID round-trips identically on PostgreSQL and SQLite; and
8. a freshly migrated database can locate an awaiting-answer Task via `find_suspended_turn_for_answer` without a full table scan (PostgreSQL `EXPLAIN` and SQLite `EXPLAIN QUERY PLAN` both report index usage on `idx_task_resume_target`).

The implementation PR must not merge if only ORM tests pass. That proves registration 1 but not registrations 2 or 3, and omits the composite-index and backfill proof entirely.

## 7. Turn-Handle Semantics and Invariants

### Column semantics

| Field | Meaning | Write owner | Read owner | Clear/consume point |
|---|---|---|---|---|
| `suspension_reason` | Why this turn is suspended | `SUSPEND_TURN` only | resume router and `RESUME_TURN` | Atomically during successful `RESUME_TURN`; terminal transitions should also clear stale handles. |
| `resume_target_turn_id` | Authoritative `task.work_id` of the turn to resume | `SUSPEND_TURN` only | `find_suspended_turn_for_answer`, `RESUME_TURN` | Atomically during successful `RESUME_TURN`; terminal transitions should clear stale handles. |

### Required invariants

1. `work_id` is the authoritative correlation axis; never store the integer `task.id` or `message_id` in `resume_target_turn_id`.
2. `suspension_reason='awaiting_answer'` requires a non-null `resume_target_turn_id`.
3. The target must resolve through `TaskRepository.get_by_work_id()` before graph resume is scheduled.
4. `resume_target_turn_id` must refer to the intended in-flight/checkpoint-bearing turn. If self-targeting is the normal answer-gate shape, permit and document it; if a gate Task points at a different parent turn, verify both rows belong to the expected instance/turn relationship.
5. No target match, ambiguous match, invalid reason, cross-instance target, or terminal/non-resumable target may silently fall back to child enqueue. Return/log an explicit invalid-transition or not-found result.
6. Handle writes, status mutation, and transition version checks occur in one transaction.
7. Successful resume consumes the handle exactly once. A duplicate answer must be idempotent and must not schedule a second graph resume.
8. Non-answer reasons (`awaiting_children`, `paused_external`) must not be selected by `find_suspended_turn_for_answer`.
9. Terminalization clears stale suspension metadata so a completed historical Task cannot be selected later.

### Concurrency rule

The answer selector and `RESUME_TURN` must protect against two answers racing. Prefer Increment 3's guarded transition/version mechanism: select the candidate, then update only if its expected suspension reason/target/status still match. A zero-row guarded update means another caller won and should produce an idempotent “already resumed/consumed” result, not enqueue new work.

## 8. Repository Function Changes

### 8.1 Add `find_suspended_turn_for_answer(answer_message_id)`

**Location:** `daemon/repositories/task/repository.py`

Purpose: locate an explicitly declared answer-gate suspension and return the suspended Task row whose stored target will drive resume.

Required predicate:

- `suspension_reason = 'awaiting_answer'`;
- `resume_target_turn_id IS NOT NULL`;
- answer correlation matches `answer_message_id` through the answer-gate relation established by Increment 3/question persistence; and
- the row has not already consumed its handle or reached an incompatible terminal state.

The supplied requirement names `answer_message_id` but only states the two handle predicates. Implementation must make the correlation explicit rather than returning an arbitrary awaiting-answer row. Before coding, confirm how the question record or answer payload links `answer_message_id` to the suspended turn. Use a direct indexed relation where available; do not infer by “most recent Task.” If the existing question schema maps answer message to target instance rather than turn, combine that mapping with the handle and assert uniqueness.

Return behavior:

- one valid suspended row: return it;
- none: return `None` and allow the caller to use the non-answer/report route appropriate to the current operation;
- multiple rows for one answer: fail loudly/log an invariant violation; never choose by recency.

### 8.2 Add `find_paused_or_cancellable_turn(instance_id)`

Purpose: preserve the pause-cascade concern after deleting the overloaded status-inference method.

Required behavior:

- Select only turns that the named pause/cancel transition may legally act upon.
- Use explicit task status/transition eligibility appropriate to cascade mutation, not as a resume classifier.
- Include `PROCESS_REPORT` where a report turn is the active checkpoint-bearing turn; do not hard-code `PROCESS_MESSAGE` unless Increment 3 explicitly constrains the transition.
- Return a deterministic candidate or an invariant error if more than one concurrently eligible turn exists, consistent with the one-running-turn-per-instance invariant.
- Keep the method narrowly named and documented: it exists for pause cascade, not answer routing.

### 8.3 Delete `find_paused_or_running_by_instance`

- Remove implementation and all callers.
- Replace pause-cascade calls with `find_paused_or_cancellable_turn`.
- Replace resume calls with `find_suspended_turn_for_answer` plus explicit target resolution.
- Rewrite/delete tests that assert the old `PROCESS_MESSAGE` status set.
- Search production code, fixtures, mocks, docs, and test names to ensure no runtime reference remains.

### 8.4 Delete `find_resume_root_candidate_by_active_job`

This is a point-fix for the exact inference gap that turn handles remove. Delete:

- the repository query and its long incident-specific documentation;
- the manager fallback call;
- `root_active_orphan` branch discrimination that exists solely for this fallback; and
- terminal-orphan matrix tests whose only assertion is that the heuristic returns a candidate.

Preserve the production scenario as the new end-to-end regression test. The behavior remains required; only the heuristic implementation is removed.

## 9. Resume Routing Rewrite

### 9.1 Answer-gate flow

1. The question tool/answer persistence creates or receives the answer message and preserves its `message_id`.
2. `resume_processing_job` (or a thin answer-specific caller established by Increment 3) calls `find_suspended_turn_for_answer(answer.message_id)`.
3. The repository returns the explicitly suspended answer-gate row. The router reads its non-null `resume_target_turn_id`.
4. Resolve the target through `task.work_id`. Validate the target, instance relationship, suspension eligibility, and checkpoint/resume preconditions.
5. Write the answer payload onto `message_queue` for the existing target turn using the established queue/message contract. Do not create a new logical turn or public JobItem.
6. Call `RESUME_TURN(target_work_id, answer_payload/context)`.
7. In its transaction, `RESUME_TURN` consumes the suspension handle and applies the named status/mirror transition.
8. After commit, schedule `_resume_processing_background`/`graph.astream` for the same target turn and inject the answer from its queue/checkpoint context.
9. The reconciler runs after `RESUME_TURN`, bringing JobItem, MessageQueue, lock, watcher, report-injection, and instance mirrors into the state prescribed by Increment 1.
10. Duplicate delivery sees the consumed handle and becomes idempotent; it does not enqueue a fresh Task.

### 9.2 Pause-during-`process_report` flow

1. The original `process_message` Task is already completed; a `process_report` Task is the current checkpoint-bearing turn.
2. External pause/cascade invokes `SUSPEND_TURN` on that report turn with `suspension_reason='paused_external'` (not `awaiting_answer`).
3. When resume is requested, `find_suspended_turn_for_answer(answer.message_id)` returns `None` because no answer-gate turn exists.
4. The router does not reinterpret that absence as proof of a child instance and does not enqueue the answer/cascade payload as a fresh Task.
5. The appropriate report-resume path resolves the suspended/current report turn and calls `RESUME_TURN` with the report turn's `work_id`.
6. Graph execution resumes from the report turn's checkpoint.
7. The reconciler finalizes or repairs the stale active JobItem and related mirrors using the authoritative turn status/work ID.
8. The instance progresses; no active JobItem or processing MessageQueue row remains orphaned, and no Task-with-no-JobItem artifact is created.

### 9.3 Manager branch structure

Refactor manager routing into semantically named outcomes, for example:

- `answer_gate_existing_turn` — explicit `awaiting_answer` handle resolved;
- `report_or_external_resume` — explicit non-answer suspended/current turn resolved by the named transition path;
- `internal_child_noop` — legitimate silent child cascade behavior, if still required;
- `invalid_or_missing_handle` — fail/log; never fabricate an answer-gate Task.

Remove route reasons based on task terminality (`root_existing_task`, `root_active_orphan`, `child`) where those names encode the obsolete inference. Structured logs should include `answer_message_id`, selected suspension reason, selected/target `work_id`, transition result, and whether a handle was consumed; avoid logging answer contents.

### 9.4 Remove the fresh-Task artifact narrowly

The code at current `daemon/manager.py:4961-5003` uses `enqueue_message(source="cascade_resume")` when no inferred root Task exists. Remove this behavior for the answer-gate path. Do not accidentally remove unrelated child messaging required for parent orchestration. Tests must prove:

- answer-gate resume never calls `enqueue_message` and never creates a new Task/JobItem;
- legitimate non-answer child orchestration still follows its approved internal path; and
- missing/corrupt answer handles surface an error rather than falling back to enqueue.

## 10. Implementation Phases and Tasks

### Phase 1: Lock the Increment 3 contract and map consumers

**Objective:** establish exact transition signatures and every affected caller before schema or routing edits.

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Confirm Increment 3 is merged and record actual `SUSPEND_TURN`, `RESUME_TURN`, `TransitionResult`, transaction, and reconciler APIs. | Increment 3 | No direct-manager SQL workaround is needed; interface differences are reflected in this plan/PR description. |
| 2 | Search all references to the two deletion targets and to `resume_processing_job`; classify each as answer-gate, pause cascade, external resume, or child orchestration. | 1 | A caller checklist accounts for every production and test reference. |
| 3 | Trace answer-message correlation from `ask_questions` persistence through resume invocation. | 1 | The join/filter that binds `answer_message_id` to one suspended turn is documented and testable; no “latest row” heuristic is required. |
| 4 | Confirm checkpoint ownership for `process_report` and the target `work_id` expected by graph resume. | 1 | Pause-during-report can name the exact Task row to resume. |

**Exit criterion:** transition APIs, answer correlation, and report-turn checkpoint ownership are unambiguous.

### Phase 2: Add turn-handle schema with triple registration

**Objective:** make the two nullable fields available consistently on fresh and existing SQLite/PostgreSQL databases.

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add reason constants/enum and both fields to `Task`; update serialization if required. | Phase 1 | ORM creates, reads, and round-trips null and valid values. |
| 2 | Add the SQLite `.sql` migration with both ALTER statements and selected indexes. | 1 | Migrating an old SQLite task schema exposes both columns without data loss. |
| 3 | Add both idempotent PostgreSQL ALTER statements and matching indexes to `_ensure_postgres_columns()`. | 1 | Existing PostgreSQL schema upgrades on startup and a second startup is a no-op. |
| 4 | Add schema parity tests for fresh SQLModel, SQLite migration, and PostgreSQL startup evolution. | 1-3 | All three registration paths fail independently if their declaration is removed. |

**Exit criterion:** all three schema registrations are present and independently verified.

### Phase 3: Extend named transitions and repository selectors

**Objective:** make handle lifecycle transactional and replace overloaded repository primitives.

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Extend `SUSPEND_TURN` to validate and persist reason/target atomically with suspension. | Phase 2 | Valid handles commit with suspension; invalid reason/target rolls back the whole transition. |
| 2 | Extend `RESUME_TURN` to target a `work_id`, guard against duplicate consumption, clear the handle, and invoke reconciler behavior from Increment 3. | 1 | Resume is idempotent and cannot resume a different/missing turn. |
| 3 | Add `find_suspended_turn_for_answer(answer_message_id)` with explicit answer correlation and uniqueness handling. | Phase 1, Phase 2 | Only `awaiting_answer` rows with valid targets match; unrelated reasons/answers do not. |
| 4 | Add `find_paused_or_cancellable_turn(instance_id)` for pause cascade. | Phase 1 | Eligible message and report turns are selected according to transition legality, not resume inference. |
| 5 | Add transition/repository unit and PostgreSQL integration tests, including race/duplicate cases. | 1-4 | Transaction rollback, uniqueness, and consumption semantics are proven. |

**Exit criterion:** all suspension/resume intent can be represented and consumed without manager-side inference.

### Phase 4: Rewrite routing and remove superseded artifacts

**Objective:** route answer and report resumes to explicit existing turns and delete heuristic code.

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Rewrite answer-gate routing in `resume_processing_job` to query the suspension handle and invoke `RESUME_TURN` with target `work_id`. | Phase 3 | Answer payload attaches to the existing target; no fresh Task or JobItem is created. |
| 2 | Route pause-during-report through the report turn's explicit `work_id`, not the terminal original message Task. | 1 | Report checkpoint resumes and reconciler cleans mirrors. |
| 3 | Remove answer-gate use of `enqueue_message(source="cascade_resume")`; preserve only explicitly approved non-answer child behavior. | 1 | Tests distinguish answer resume from child orchestration and forbid silent fallback. |
| 4 | Delete `find_paused_or_running_by_instance` and replace pause-cascade consumers. | Phase 3 | Repository-wide runtime search returns zero references. |
| 5 | Delete `find_resume_root_candidate_by_active_job`, active-orphan manager fallback, and obsolete route logging. | 2 | The incident is covered by explicit-handle E2E tests, not heuristic unit tests. |
| 6 | Add structured route logging keyed by `work_id` and reason. | 1-5 | Operators can identify selected turn and outcome without answer payload exposure. |

**Exit criterion:** no answer routing decision depends on PROCESS_MESSAGE status inference or active-orphan reconstruction.

### Phase 5: Regression and dual-database verification

**Objective:** demonstrate the missing production sequence, normal answer flow, and existing behavior are all safe.

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add E2E `test_pause_during_report_turn_then_resume`. | Phase 4 | Report turn resumes; instance completes/progresses; no stale active JobItem, processing message, lock, or no-JobItem Task remains. |
| 2 | Add answer-gate E2E/integration flow. | Phase 4 | Answer reuses the stored target `work_id`; graph receives answer once; Task/JobItem counts do not increase due to resume. |
| 3 | Add corruption and race tests. | Phase 4 | Missing target, cross-instance target, duplicate answers, duplicate awaiting rows, and consumed handles fail/idempotently resolve without enqueue fallback. |
| 4 | Run focused repository/transition/question/pause/terminal-orphan suites on PostgreSQL. | 1-3 | All focused tests pass on the primary DB. |
| 5 | Run SQLite migration/parity tests, including the B3 fresh-DB idempotency test and the B2 backfill test. | Phase 2 | Existing SQLite schema upgrades and behavior matches PostgreSQL for turn handles; the migration is safe on fresh databases. |
| 6 | Run all 404 existing tests plus newly added tests. | 1-5 | The original 404 remain green and all Increment 4 tests pass; record any changed total explicitly. |
| 7 | Add a full-chain E2E test (C2) that exercises the entire post-Increment-4 lifecycle on PostgreSQL: claim a task → process → user pauses mid-processing → resume → user answers an `ask_questions` → answer delivered → task completes. Validate that Increments 1 (reconciler), 3 (named transitions), and 4 (turn handles) cooperate with no orphan mirrors, no deadlock, correct answer delivery, and all 8 mirror tables consistent at every step. | 1-6 | The full-chain E2E passes against PostgreSQL; removing any one of Increments 1/3/4 from the runtime breaks the test, proving the chain is genuinely integrated. |

**Exit criterion:** both target scenarios pass, both database paths are verified, the full-chain integration is proven, and no regression remains in the original 404-test baseline.

## 11. Test Strategy

### 11.1 Schema tests

- SQLModel metadata contains both nullable columns AND the composite index `idx_task_resume_target` on a new database.
- SQLite migration adds both columns and the composite index to a pre-Increment-4 schema and preserves existing Task rows.
- **B3 — Fresh-DB SQLite idempotency:** a fresh SQLite database (where `SQLModel.metadata.create_all()` already created the columns) runs the guarded migration successfully without "duplicate column name" errors. Verified by running the migration immediately after `create_all()` and asserting no exception.
- **B2 — Legacy paused backfill:** insert a Task with `status='paused'`, `suspension_reason=NULL`, `resume_target_turn_id=NULL`, then run the backfill migration, and assert the row now has `suspension_reason='paused_external'` and `resume_target_turn_id=<work_id>`. Repeat on PostgreSQL using `_ensure_postgres_columns()`. The backfill is naturally idempotent: running it twice does not error and does not change the result.
- PostgreSQL `_ensure_postgres_columns()` adds both columns and the composite index to an existing table.
- **C4 — Composite index verification:** inspect `PRAGMA index_list(task)` (SQLite) and `pg_indexes` (PostgreSQL) on a freshly migrated database and assert `idx_task_resume_target` exists with the exact column list `(resume_target_turn_id, suspension_reason)`. Run `EXPLAIN` / `EXPLAIN QUERY PLAN` against `find_suspended_turn_for_answer`'s predicate and assert the planner reports index usage on `idx_task_resume_target` (not a full scan).
- PostgreSQL evolution is idempotent across two invocations/startups.
- Valid reason values and UUID strings round-trip; null legacy rows remain readable.
- Genuinely-null legacy rows (rows that were never paused) remain null after the backfill (the backfill `WHERE` predicate excludes them).

### 11.2 Repository tests

For `find_suspended_turn_for_answer`:

- returns the one matching `awaiting_answer` turn with a non-null target;
- ignores `awaiting_children`, `paused_external`, null reasons, and null targets;
- does not match a different answer message;
- never chooses the newest unrelated awaiting turn;
- rejects/reports duplicate candidates rather than selecting arbitrarily;
- returns `None` after handle consumption;
- resolves UUID `work_id`, never integer `id` or `message_id` as the resume target;
- **resolves backfilled legacy paused Tasks** (B2): after the backfill migration, a `paused` Task with `suspension_reason='paused_external'` and `resume_target_turn_id=<work_id>` is correctly selected only by `find_paused_or_cancellable_turn` (pause-cascade route) and never by `find_suspended_turn_for_answer` (which filters for `awaiting_answer`). The composite index serves both predicates.

For `find_paused_or_cancellable_turn`:

- selects a legally suspendable/cancellable `process_message` turn;
- selects a legally suspendable/cancellable `process_report` turn;
- excludes terminal/non-eligible rows;
- handles multiple eligible rows as an invariant breach, not recency-based ambiguity;
- **correctly resolves backfilled legacy paused Tasks** (B2): a `paused` Task with `suspension_reason='paused_external'` is selected when its instance is paused again, proving the backfill preserved routability.

### 11.3 Transition tests

- `SUSPEND_TURN` writes status, reason, and target in one transaction.
- Invalid reason, missing required answer target, missing target Task, or cross-instance target causes a complete rollback.
- `RESUME_TURN` uses `resume_target_turn_id`, consumes/clears the handle, and invokes reconciler semantics once.
- Concurrent duplicate resume attempts produce one successful transition and one idempotent loser.
- `COMPLETE_TURN`/`ABORT_TURN` clear stale handles as required.
- Transition result schedules graph work only after commit.

### 11.4 Answer-gate integration/E2E test

Seed or drive a real `ask_questions` flow:

1. start a message turn;
2. suspend it with `awaiting_answer` and its target `work_id`;
3. submit an answer message;
4. assert `find_suspended_turn_for_answer(answer.message_id)` identifies the expected row;
5. assert `RESUME_TURN` is called for the stored target `work_id`;
6. assert the answer is written to the existing turn's message/checkpoint input;
7. assert `enqueue_message(source="cascade_resume")` is not called;
8. assert no new Task or JobItem was created for the answer resume;
9. assert graph execution receives the answer once; and
10. assert mirrors reconcile and the handle is consumed.

Include dismissal/answer variants if they share the same resume primitive, preserving existing `test_question_dismiss.py` and `test_question_untested_paths.py` behavior.

### 11.4.1 Full-chain E2E test (§ C2 — post-Increment-4 lifecycle)

This test exercises the complete lifecycle after all 4 increments to prove they integrate rather than merely coexist. It must run against PostgreSQL because the active-JobItem/JobLock constraint is PostgreSQL-enforced.

Drive the following sequence:

1. Claim a Task via the worker pool; the reconciler (Increment 1) sets JobItem, MessageQueue, lock, watcher, report-injection, and instance mirrors to their expected states.
2. Begin processing the Task; the named transition (Increment 3) creates a `paused` Task with `suspension_reason='paused_external'` and `resume_target_turn_id=<self work_id>` when the user pauses mid-processing.
3. Resume from the external pause; assert the manager routes by the recorded handle (Increment 4) — no `find_paused_or_running_by_instance` heuristic is consulted, no `enqueue_message` fallback fires.
4. The LLM emits an `ask_questions` call; Increment 4 records `suspension_reason='awaiting_answer'` and a non-null `resume_target_turn_id` on the awaiting Task.
5. Submit the user's answer message; `find_suspended_turn_for_answer(answer.message_id)` returns the awaiting row; `RESUME_TURN` consumes the handle and schedules the graph against the original target `work_id`.
6. The graph receives the answer once, completes the turn, and the reconciler finalizes the instance.
7. Task reaches a terminal state.

Assertions:

- No orphan mirrors at any point in the sequence. After every transition (pause, resume, answer-resume, complete), all 8 mirror tables (Task, JobItem, MessageQueue, JobLock, JobWatcher, ReportInjection, Instance state, CriticalNotes/Vote state) are in a consistent state per the Increment 1 reconciler.
- No deadlock. The instance reaches the expected terminal state.
- Answer delivered correctly. The graph receives the answer exactly once; `enqueue_message(source="cascade_resume")` was never called during the answer path.
- All 8 mirror tables consistent at every checkpoint.
- Backfilled legacy paused Tasks (B2): insert a `paused` Task with `suspension_reason=NULL` BEFORE the migration runs, run the migration/backfill, and then drive a pause-cascade resume against that task. The task is correctly resolved by `find_paused_or_cancellable_turn` and resumed.

This test fails if any of Increments 1, 3, or 4 is reverted — proving the chain is genuinely integrated rather than four independent layers.

### 11.5 Required missing E2E: `pause_during_report_turn_then_resume`

Drive the exact production sequence rather than mocking only the repository lookup:

1. create/admit the original message work with a JobItem keyed by its `work_id`;
2. complete the original `process_message` Task;
3. start a `process_report` Task on the same instance and pause while it is actually in flight;
4. assert suspension records `paused_external` on the report turn and does not fabricate an answer-gate handle;
5. resume;
6. assert the manager does not select the terminal original message Task and does not enqueue a cascade-resume Task;
7. assert `RESUME_TURN` targets the report Task's `work_id` and graph processing re-enters from the report checkpoint;
8. assert the reconciler transitions stale mirrors to their required state;
9. assert the original JobItem is no longer `active`, MessageQueue has no stale `processing` row, and no orphan lock remains;
10. assert no new Task-with-no-JobItem artifact exists; and
11. assert the instance reaches its expected post-report state without deadlock.

Run this test against PostgreSQL because the active-JobItem/JobLock constraint is PostgreSQL-enforced and production-relevant. A SQLite variant may supplement it but cannot replace it.

### 11.6 Regression tests for deletions

- Replace `tests/test_terminal_orphan_matrix.py` assertions tied to `find_resume_root_candidate_by_active_job` with explicit-handle routing and the E2E incident scenario.
- Update pause/terminate matrix tests that mock `find_paused_or_running_by_instance` to mock/assert `find_paused_or_cancellable_turn` or named transitions.
- Add a source-level assertion or CI grep that deleted runtime method names are absent from production code.
- Preserve legitimate silent child cascade tests and prove they are not conflated with answer-gate behavior.

### 11.7 Full-suite gate

The release gate is:

- all 404 pre-existing tests pass;
- every new Increment 4 test passes;
- focused transition/routing suites pass on PostgreSQL;
- migration/schema parity tests pass on SQLite and PostgreSQL; and
- no known test is skipped merely because PostgreSQL is unavailable in the primary test job.

## 12. Coupling and Dependencies

### Hard dependency

| Dependency | Why required | Gate |
|---|---|---|
| Increment 3 Named Transitions | `SUSPEND_TURN` must own handle writes and `RESUME_TURN` must own handle consumption/reconciler invocation. | Increment 3 merged, APIs stable, transition tests green. |

### Internal coupling

| Component | Coupling | Risk control |
|---|---|---|
| Task model ↔ SQLite migration ↔ PostgreSQL startup ALTER | Tight: three representations of the same columns. | Triple-registration review checklist and independent parity tests. |
| `SUSPEND_TURN` ↔ question/pause call sites | Tight: every suspension must declare reason/target correctly. | Exhaustive reason tests and no direct field writers outside transition module. |
| `find_suspended_turn_for_answer` ↔ answer persistence schema | Tight: answer message must map uniquely to a suspension row. | Trace correlation before implementation; reject ambiguity. |
| `RESUME_TURN` ↔ graph resume scheduler | Tight: commit must precede checkpoint re-entry. | Post-commit `TransitionResult` and race tests. |
| Manager routing ↔ child orchestration | Medium: removing answer fallback must not remove legitimate child flows. | Separate route outcomes and dedicated tests. |
| Resume routing ↔ reconciler | Tight but unchanged contract: correct target must be reconciled after resume. | Assert target `work_id` and all mirror postconditions in E2E. |

## 13. Risks and Mitigations

| # | Risk | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| 1 | One schema registration is omitted, silently breaking an engine. | High | Medium | Three explicit PR checklist items; independent fresh-schema, SQLite-upgrade, and PostgreSQL-upgrade tests. |
| 2 | `answer_message_id` is not directly correlated to the suspended row, causing another recency heuristic. | High | Medium | Resolve the actual question/answer relation in Phase 1; reject implementation until a unique indexed relation is identified. |
| 3 | The target field accidentally stores `task.id` or `message_id` instead of `work_id`. | High | Medium | Name/document the invariant, resolve exclusively through `get_by_work_id`, and add negative tests. |
| 4 | Two answers race and resume the graph twice. | High | Medium | Guarded, transactional handle consumption plus duplicate-answer concurrency test. |
| 5 | Removing cascade-resume enqueue breaks legitimate child orchestration. | High | Medium | Remove it only for answer-gate routing; inventory callers first and retain dedicated child-path regression tests. |
| 6 | Pause-during-report still targets the completed message turn rather than report checkpoint. | High | Medium | Record reason on the actual active report turn and assert report `work_id` in E2E. |
| 7 | Stale handle remains on a terminal Task and is selected later. | High | Low | Clear handles in resume and all terminal named transitions; filter/validate status and test historical rows. |
| 8 | PostgreSQL-specific behavior passes SQLite-only CI but fails production. | High | Medium | Make PostgreSQL the required focused/E2E gate; SQLite supplements migration parity only. |
| 9 | Existing point-fix tests are deleted without preserving incident coverage. | High | Low | Replace heuristic unit coverage with the exact production-sequence E2E before deleting the methods. |
| 10 | Index choice diverges across fresh, SQLite, and PostgreSQL schemas. | Medium | Medium | Either defer indexes with measured justification or triple-register and inspect them in parity tests. |
| 11 | Increment 3 transition API changes after this plan. | Medium | Medium | Hard entry gate and implementation-time plan update; no compatibility shim in manager. |
| 12 | Additive columns are rolled back from code while old binaries/new rows coexist. | Medium | Low | Roll back routing first, retain nullable columns during stabilization, and only drop columns in a separately approved destructive migration. |
| 13 | **(B2)** Legacy paused Tasks with NULL `suspension_reason` are stranded after `find_paused_or_running_by_instance` is deleted: `find_suspended_turn_for_answer` filters for `suspension_reason='awaiting_answer'` and returns `None` for these rows, leaving them unresumable. | High | High | Add the backfill migration step (B2): `UPDATE task SET suspension_reason='paused_external', resume_target_turn_id=work_id WHERE status='paused' AND suspension_reason IS NULL` runs after the columns are added on both SQLite (in the `.sql` migration) and PostgreSQL (in `_ensure_postgres_columns()`). Backfill is naturally idempotent. Add a migration test that inserts a NULL-handle paused Task and verifies the backfill assigns `paused_external` + self-target. |
| 14 | **(B3)** SQLite migration fails on a fresh database because `SQLModel.metadata.create_all()` already created both columns; a plain `ALTER TABLE ADD COLUMN` raises "duplicate column name". | High | High | Use the guard pattern: either (a) `PRAGMA table_info(task)` existence check before the ALTER, or (b) try/except "duplicate column name" suppression. Mirrors the existing "does not exist" pattern at `manager.py:3736`. Add a fresh-DB SQLite migration test that proves the migration is idempotent against a `create_all()`-populated schema. |
| 15 | **(C4)** Without a composite index, `find_suspended_turn_for_answer` is a full table scan on every resume; at scale this is the most-frequent repository read in the answer-gate path. | Medium | Medium | Triple-register `idx_task_resume_target` on `(resume_target_turn_id, suspension_reason)`: SQLModel `Index(...)` in `Task.__table_args__`, `CREATE INDEX IF NOT EXISTS` in the SQLite migration, `CREATE INDEX IF NOT EXISTS` in `_ensure_postgres_columns()`. Add an EXPLAIN/EXPLAIN QUERY PLAN test that asserts index usage on the answer-resume predicate. |
| 16 | **(C2)** Increments 1, 3, and 4 each have their own test suites, but no test proves the chain works end-to-end; one of the three layers could silently regress without any single test failing. | High | Medium | Add the §11.4.1 full-chain E2E test that exercises claim → pause → resume → answer → complete and asserts all 8 mirror tables stay consistent. The test must run against PostgreSQL. Document it as the gate that proves the chain integrates rather than merely coexists. |
| 17 | **(B2 follow-on)** Stage B schema rollback (`DROP COLUMN`) overwrites the backfilled values and re-nulls the columns; legacy paused Tasks would need re-backfill after rollback. | Medium | Low | Document in §16 that Stage B is destructive for these columns. The default rollback is Stage A with additive columns retained. If Stage B must proceed, re-run the B2 backfill after re-adding the columns. |

## 14. Success Criteria

| # | Criterion | How to Measure | Pass Threshold |
|---|---|---|---|
| 1 | Both turn-handle columns exist through all schema paths. | Inspect SQLModel metadata, migrated SQLite schema, and upgraded PostgreSQL schema. | 3/3 registrations present and tests independently pass. |
| 2 | Suspension intent is declared transactionally. | Transition tests inspect committed and rolled-back rows. | Every successful suspension has a valid reason; every answer suspension has a non-null valid target; invalid writes commit nothing. |
| 3 | Answer-gate resumes an existing turn. | Compare Task/JobItem counts and target `work_id` before/after E2E answer. | Same target turn is resumed; zero resume-created Tasks and zero resume-created JobItems. |
| 4 | Routing gap is structurally impossible. | Run pause-during-report E2E and inspect route calls/artifacts. | Report turn `work_id` is resumed; no status-inference or enqueue fallback is exercised; no deadlock/orphan remains. |
| 5 | Obsolete inference functions are deleted. | Repository-wide runtime search. | Zero production definitions/calls for `find_paused_or_running_by_instance` and `find_resume_root_candidate_by_active_job`. |
| 6 | Answer selector is reason-specific and unique. | Repository matrix tests. | Only matching `awaiting_answer` rows resolve; unrelated, missing, or ambiguous rows never route silently. |
| 7 | Resume is idempotent under duplicate answers. | Concurrent integration test. | Exactly one transition/graph resume; subsequent attempt reports consumed/already resumed without enqueue. |
| 8 | Mirror cleanup remains correct. | Assert all eight-table relevant postconditions after `RESUME_TURN`. | No active JobItem backed by terminal work, no stale processing MessageQueue row, no orphan lock; other reconciler invariants remain green. |
| 9 | Existing behavior does not regress. | Run the full existing suite plus new tests. | All 404 pre-existing tests and all new tests pass. |
| 10 | PostgreSQL-primary behavior is verified. | Run focused migrations, transitions, and E2E against PostgreSQL. | Required PostgreSQL job passes with no SQLite-only substitutions or skips. |

## 15. Rollout and Observability

1. Land schema additions first or in the same deploy as nullable-reading code; both columns are additive and null-compatible.
2. Deploy transition writers and routing readers atomically if possible. During a staged deploy, readers must treat null as “legacy/no handle” but must not use null to recreate the deleted answer-gate inference once the cutover is enabled.
3. Consider a short-lived feature flag only if mixed-version daemon processes can run concurrently. The flag must switch the entire write+read contract, not independently enable writers and readers.
4. Emit structured counters/logs for:
   - suspension reason written;
   - answer handle found/not found/ambiguous;
   - target resolution failure;
   - duplicate handle consumption;
   - resume route by semantic outcome; and
   - reconciler corrections after resume.
5. During canary, alert on any answer-gate attempt that lacks a handle, any cross-instance target, any resume-created Task, or repeated reconciler correction for the same `work_id`.
6. After one stable release, remove temporary compatibility metrics/flag code but retain invariant-level error logging.

## 16. Rollback Plan

Rollback is operationally two-stage because code rollback is reversible, while dropping columns is destructive and requires explicit approval.

### Stage A: Safe application rollback

1. Stop/canary-disable new answer routing if invariant errors rise.
2. Revert manager/question routing and named-transition handle reads/writes to the last known-good release as one code change.
3. Restore the prior repository functions and tests only if needed to run that release.
4. Leave both nullable columns in place. Older code ignores them, so schema retention is safe and avoids destructive DDL during incident response.
5. Verify suspended instances can be handled by the prior operational recovery procedure and that no mixed-version writer remains.

### Stage B: Schema rollback, separately approved

After all new binaries are drained and backups are verified:

```sql
ALTER TABLE task DROP COLUMN suspension_reason;
ALTER TABLE task DROP COLUMN resume_target_turn_id;
```

- PostgreSQL can execute guarded `DROP COLUMN IF EXISTS` in a dedicated rollback migration/change window.
- SQLite may require a table rebuild depending on the supported SQLite version; do not improvise it in the forward `.sql` migration.
- Remove the SQLModel fields, the composite `idx_task_resume_target` index, and PostgreSQL `_ensure_postgres_columns()` statements in the same rollback release so startup does not re-add the columns.
- **§ REVISION NOTE (Council Review — B2):** the DROP COLUMN statements above are **destructive for the backfilled handle metadata**. In-flight Tasks that were paused before Increment 4 had their `suspension_reason` set to `'paused_external'` and `resume_target_turn_id` set to their own `work_id` by the B2 backfill. Dropping the columns erases that routing information, and after rollback these Tasks will need to be re-backfilled (or recovered through the legacy `find_paused_or_running_by_instance` heuristic, which this increment removes). The default rollback is therefore Stage A with additive columns retained. If Stage B must proceed, document the loss of legacy-paused routability in the rollback PR description, gate it behind SemiAuto/maintainer approval, and accept that any in-flight paused Tasks at rollback time become stranded unless they complete before the DROP runs.
- Re-run schema parity and full regression tests.

Because column removal overwrites persisted suspension metadata and is destructive, Stage B requires explicit SemiAuto/maintainer approval. The default rollback is Stage A with additive columns retained.

## 17. Assumptions and Open Questions

### Assumptions

- Increment 3's named transitions and reconciler are merged before implementation.
- `task.work_id` remains globally unique and is the authoritative cross-system turn identifier.
- The active `process_report` Task owns or can identify the checkpoint needed for report resume.
- The answer pipeline retains `answer.message_id` at the routing call site.
- The stated 404-test count is the pre-Increment-4 baseline; the post-change suite total will be higher.

### Open questions requiring implementation review

1. What exact persisted relation maps `answer_message_id` to the answer-gate suspension row? This must be resolved before finalizing the repository query.
2. Is `resume_target_turn_id` normally self-referential (suspended Task targets its own `work_id`) or does an answer-gate helper Task point to a parent turn? Tests and validation must encode the selected model.
3. Should the database enforce allowed `suspension_reason` values with a CHECK constraint, or is transition-boundary validation preferred for cross-engine migration simplicity?
4. Is a composite index on `(suspension_reason, resume_target_turn_id)` sufficient, or must answer correlation add another indexed field/join?
5. Does Increment 3 transition `RESUME_TURN` leave Task status as `cancelled` before graph re-entry, as proposed in the design doc, or has its approved status contract changed? Increment 4 must follow the landed contract.
6. Which non-answer child cascade cases still legitimately use `enqueue_message(source="cascade_resume")`? The caller inventory must explicitly preserve or retire each one.
7. Should handle fields be exposed through public Task API serialization, or remain internal transition metadata?

## 18. Definition of Done Checklist

- [ ] Increment 3 dependency verified and APIs recorded.
- [ ] SQLModel fields added.
- [ ] **§ REVISION (B2)** Backfill migration added: `UPDATE task SET suspension_reason='paused_external', resume_target_turn_id=work_id WHERE status='paused' AND suspension_reason IS NULL` runs on both SQLite (in the `.sql` migration) and PostgreSQL (in `_ensure_postgres_columns()`).
- [ ] **§ REVISION (B2)** Backfill migration test added and passing on both engines: a Task inserted with `status='paused'`, `suspension_reason=NULL`, `resume_target_turn_id=NULL` is correctly backfilled.
- [ ] **§ REVISION (B3)** SQLite migration is guarded/idempotent: PRAGMA column-existence detection OR try/except "duplicate column name" suppression. Plain `ALTER TABLE ADD COLUMN` is NOT acceptable.
- [ ] **§ REVISION (B3)** Fresh-DB SQLite migration test added and passing: the guarded migration runs successfully against a `create_all()`-populated schema with no error.
- [ ] **§ REVISION (C4)** Composite index `idx_task_resume_target` on `(resume_target_turn_id, suspension_reason)` triple-registered in SQLModel, SQLite migration, and `_ensure_postgres_columns()`.
- [ ] **§ REVISION (C4)** EXPLAIN/EXPLAIN QUERY PLAN test asserts index usage on the answer-resume predicate.
- [ ] SQLite migration added.
- [ ] PostgreSQL `_ensure_postgres_columns()` ALTERs added.
- [ ] All selected indexes triple-registered or consciously deferred.
- [ ] Schema parity verified on fresh DB, existing SQLite, and existing PostgreSQL.
- [ ] `SUSPEND_TURN` writes reason and target transactionally.
- [ ] `RESUME_TURN` resolves/consumes target by `work_id` and remains idempotent.
- [ ] `find_suspended_turn_for_answer` added with explicit answer correlation.
- [ ] `find_paused_or_cancellable_turn` added for pause cascade only.
- [ ] `find_paused_or_running_by_instance` deleted with zero runtime references.
- [ ] `find_resume_root_candidate_by_active_job` deleted with zero runtime references.
- [ ] Answer-gate fresh Task/no-JobItem artifact removed.
- [ ] Legitimate child orchestration still passes its dedicated tests.
- [ ] Answer-gate E2E proves existing-turn reuse.
- [ ] `pause_during_report_turn_then_resume` E2E proves report-turn targeting and mirror cleanup.
- [ ] Duplicate, corrupt, missing, and cross-instance handles are covered.
- [ ] **§ REVISION (C2)** Full-chain E2E test (§11.4.1) added and passing on PostgreSQL: exercises claim → pause → resume → answer → complete; asserts all 8 mirror tables consistent at every checkpoint; proves Increments 1, 3, and 4 integrate rather than merely coexist.
- [ ] PostgreSQL-focused tests pass.
- [ ] SQLite migration/parity tests pass, including the B3 fresh-DB idempotency and B2 backfill tests.
- [ ] All 404 pre-existing tests plus new Increment 4 tests pass.
- [ ] Rollout metrics and rollback steps are documented in the implementation PR.
