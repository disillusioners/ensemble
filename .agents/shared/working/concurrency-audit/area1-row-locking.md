# Area 1 — Row Locking Audit (SELECT FOR UPDATE / SKIP LOCKED / EvalPlanQual)

**Scope:** All read-then-update patterns on the same row(s) across
`daemon/repositories/` and `daemon/services/`. PostgreSQL READ COMMITTED
isolation is the worst case being modeled; SQLite's write serialization is
documented where the two behaviors diverge.

**Method:**
1. Read every `.py` file under `daemon/repositories/` recursively (10
   repositories, ~25 files).
2. Grepped for `with_for_update`, `FOR UPDATE`, `SKIP LOCKED`, `EvalPlanQual`,
   `claim_pending_task`, and `with_for_update()` across the whole daemon
   tree.
3. Cross-referenced every caller of `repo.update()`, `repo.atomic_transition`,
   and any `session.get(...)` followed by Python mutation.
4. Delegated service-layer pattern search to `@explore` for parallel coverage.

**Bottom line:** the gold-standard patterns (`claim_pending_task`,
`DeadLetterService.move_to_dlq`, `ExecutionLeaseRepository`,
`SQLModelInstanceRepository.transition_status_if`) are **correctly
implemented and verified**. However, six repositories and four services
still use SELECT-then-Python-mutate-then-UPDATE patterns **without row
locking or SQL-level status guards**. Three are CRITICAL, five are HIGH.

The `JobLockManager.acquire_queue_lock` race is the most urgent finding — it
is a **cross-process** lock table that does not use any atomic SQL, only
an in-process `asyncio.Lock`. Two concurrent worker threads can both
succeed at acquiring the "lock", silently violating the concurrency cap.

---

## 1. Verified OK — gold-standard patterns

These are correctly implemented. Listed so we have a complete picture and
so future audits can confirm we did not regress them.

### 1.1 `claim_pending_task` — EvalPlanQual recheck guard present ✅

- **Location:** `daemon/repositories/task/repository.py:153-285`
- **Pattern:** `UPDATE ... WHERE id = (SELECT ... LIMIT 1) AND status =
  :status_pending RETURNING *`
- **What makes it correct:**
  - The outer UPDATE's WHERE clause re-asserts `status = :status_pending`,
    forcing PostgreSQL's EvalPlanQual mechanism to re-check the row after
    the row lock is acquired (line 267). This eliminates the
    double-claim race that the knowledge base reports as fixed.
  - The single-statement UPDATE-RETURNING is atomic on both PostgreSQL
    and SQLite.
  - Both predicates (status guard AND retry-time guard) are in the outer
    WHERE; EvalPlanQual recheck covers both.
- **Verification source:** Two regression tests cover this exact pattern
  (`tests/message_queue_redesign/test_task_repository.py:115-148`
  smoke test + the comment at line 122-131 explicitly references the
  EvalPlanQual recheck fix).
- **Impact if regressed:** Two workers can claim the same task, driving
  `graph.astream` for the same `instance_id` concurrently → duplicate
  LLM responses and shadowed checkpointer writes.

### 1.2 `DeadLetterService` — pessimistic FOR UPDATE ✅

- **Location:** `daemon/services/dead_letter_service.py:103, 179, 259, 266`
- **Pattern:** `session.get(JobItem, job_id, with_for_update=True)` inside
  a single transaction, then validate-then-mutate-then-commit.
- **Coverage:**
  - `move_to_dlq` (line 103) — `JobItem` row is locked before status
    validation; DLQ insert happens in the same transaction.
  - `move_to_dlq_standalone` (line 179) — same.
  - `replay_from_dlq` (line 259) — `DeadLetterItem` row is locked first
    to prevent concurrent replay; the linked `JobItem` (line 266) is
    also locked to prevent concurrent modification between DLQ-deletion
    and job-reset.
- **Impact if regressed:** Concurrent `move_to_dlq` calls could both see
  `status='failed'` and both create DLQ items for the same job.

### 1.3 `MessageQueueRepository.dequeue` — `with_for_update()` ✅

- **Location:** `daemon/repositories/message_queue/repository.py:118`
- **Pattern:** `stmt = stmt.with_for_update()` on the READY-message SELECT,
  then mutate the same row in Python inside the same session.
- **Note:** This is one of the only repositories that uses
  `with_for_update()` for the dequeue. `MessageQueueRepository.complete`,
  `fail`, and `retry` (see Finding 5.3) do NOT use it.

### 1.4 `ExecutionLeaseRepository` — atomic SQL throughout ✅

- **Location:** `daemon/repositories/execution_lease/repository.py`
- **Patterns:**
  - `try_acquire` (line 56-135): `INSERT ... ON CONFLICT DO NOTHING`
    (PostgreSQL) / `INSERT OR IGNORE` (SQLite) — atomic by dialect.
  - `release` (line 137-160): `DELETE WHERE instance_id = :id AND
    holder_id = :hid` — conditional, safe against stale losers.
  - `heartbeat` (line 166-196): `UPDATE ... WHERE instance_id = :id
    AND holder_id = :hid` — conditional.
- **Impact if regressed:** Stale losers could steal/overwrite fresh
  winners' leases; `graph.astream` could be driven by two dispatchers
  concurrently for the same instance.

### 1.5 `transition_status_if` — atomic status transition ✅

- **Location:** `daemon/repositories/instance/repository.py:578-613`
- **Pattern:** `UPDATE instance SET status = :new WHERE instance_id =
  :id AND status IN :allowed_from RETURNING *` (via SQLAlchemy
  `update()`).
- **Used correctly** in `message_job_handler.py:228-248` (pre-pickup
  WAITING_CHILDREN/IDLE → RUNNING transition) with explicit comments
  noting it is the safer replacement for the previous
  read-then-unconditional-update TOCTOU pattern.
- **Should be used** for status transitions in pause/resume/terminate
  (see Findings 5.1, 5.2, 5.6) — they currently use the unguarded
  `repo.update()`.

### 1.6 `waiting_for` counter — atomic SQL ✅

- **Locations:**
  - `daemon/services/child_reports.py:509-523` (decrement)
  - `daemon/services/error_reporting.py:205-221` (decrement)
  - `daemon/tools/instance.py:580-590` (increment)
- **Pattern:** `UPDATE instances SET waiting_for = CASE WHEN waiting_for
  > 0 THEN waiting_for - 1 ELSE 0 END WHERE instance_id = :id RETURNING
  waiting_for` — atomic, no Python read-modify-write.
- **Note:** The atomic SQL is correct. The READ-THEN-DECIDE layer above
  this counter has additional races (covered in
  `area3-waiting-for.md`).

### 1.7 `task` heartbeat / requeue / retry — atomic UPDATE-WHERE-guard ✅

- **Locations:**
  - `daemon/repositories/task/repository.py:359-395` `update_heartbeat`
    — `UPDATE ... WHERE id = :id AND status = 'running'`
  - `daemon/repositories/task/repository.py:287-357` `requeue_task_with_backoff`
    — `UPDATE ... WHERE id = :task_id AND status = 'running'`
  - `daemon/repositories/task/repository.py:857-891` `request_cancel`
    — `UPDATE ... WHERE id = :id AND status = 'running' AND
    cancel_requested = false AND retry_scheduled = false`
- **What makes it correct:** All three include the precondition in the
  SQL WHERE clause, so a concurrent state transition (cancel, complete,
  retry-schedule) cannot be silently overwritten. SQLite's write
  serialization plus PostgreSQL's EvalPlanQual recheck both apply.

### 1.8 `correlation_manager` — in-memory per-parent lock + W1 fix ✅

- **Location:** `daemon/services/correlation_manager.py:148-162, 181-214,
  216-325`
- **Pattern:** Per-parent `asyncio.Lock` (lazily created on the main
  event loop) serializes all `register_message_send` /
  `resolve_response` calls for the same parent.
- **Critical fixes verified:**
  - N2 (line 267-268): `had_error` is set BEFORE popping the entry, so
    the error flag is preserved even if the entry is later re-queried.
  - W1 (line 293-300): `completion_callback` is invoked AFTER the
    per-parent lock is released, so Phase 2 cascade work that re-enters
    the CM for the same parent cannot deadlock.
  - W2 (line 404-412, 459-466): `_pending` is cleared before rebuild
    and the count comparison happens UNDER the per-parent lock.
- **Impact if regressed:** Race #1 (JobFeedbackObserver TOCTOU) and
  Race CM-2 (W1 callback window) re-emerge.

---

## 2. CRITICAL Findings

### 2.1 `JobLockManager.acquire_queue_lock` — SELECT count → INSERT, not atomic

- **Location:** `daemon/services/job_lock_manager.py:41-80` and the
  repository helper it calls at `daemon/repositories/job_queue/lock_repository.py:23-29`.
- **Risk Level:** **CRITICAL** (cross-process lock violation).
- **Description:** The `JobLockManager.acquire_queue_lock` method claims
  to "atomically check capacity and acquire lock in database", but the
  implementation is:
  1. `asyncio.Lock` (in-process only — two different worker threads
     both go through this lock, so it works intra-process, but **two
     concurrent worker pools or threads still serialize on it**).
  2. `SELECT COUNT(*)` from `job_locks`.
  3. If `count < limit`, `INSERT` a new lock row.

  ```python
  # daemon/services/job_lock_manager.py:63-79
  async with self._lock:
      # Check current lock count
      current_count = await asyncio.to_thread(
          self._lock_repo.get_lock_count, project_id, queue_id
      )
      if current_count >= concurrency_limit:
          return False
      # Acquire lock in database
      db_lock = JobLock(project_id=project_id, queue_id=queue_id,
                         job_id=job_id, instance_id=instance_id)
      await asyncio.to_thread(self._lock_repo.acquire, db_lock)
      return True
  ```

  The `asyncio.Lock` only serializes coroutines on the SAME event loop.
  Two paths can bypass it:
  - A worker thread calling `acquire_queue_lock` via `asyncio.to_thread`
    holds `self._lock` only briefly, but the two SQL round-trips (count
    + insert) happen across the lock acquisition window. Two such
    threads can BOTH read `count = 0`, BOTH pass the check, and BOTH
    insert.
  - `lock_repository.acquire` (line 23-29) is just `session.add(lock)` —
    no UNIQUE constraint on `(project_id, queue_id, job_id)`, no
    `ON CONFLICT` clause, no row-level guard.

  The same flaw exists in the project-level fallback
  `acquire()` (line 204-230) — it just calls `acquire_queue_lock` with
  `concurrency_limit=1`.

- **Current code (lock_repository.acquire):**
  ```python
  # daemon/repositories/job_queue/lock_repository.py:23-29
  def acquire(self, lock: JobLock) -> JobLock:
      """Persist a lock record."""
      with SQLModelSession(self.engine) as session:
          session.add(lock)
          session.commit()
          session.refresh(lock)
          return lock
  ```

- **Recommended fix:** Replace the count-then-insert with a single
  atomic SQL using PostgreSQL's `INSERT ... SELECT ... WHERE
  (SELECT COUNT(*) FROM job_locks WHERE ...) < :limit RETURNING *`
  or a conditional INSERT with a CTE. SQLite has the equivalent
  `INSERT OR IGNORE` combined with a `SELECT` returning rowcount. A
  less invasive fix: add a UNIQUE constraint on `(project_id, queue_id,
  job_id)` and use `INSERT ... ON CONFLICT DO NOTHING` — but that only
  prevents duplicate-job locks, not duplicate concurrent locks for
  different jobs.
- **Impact:** A queue configured with `concurrency_limit=1` can have
  multiple workers process jobs concurrently. The per-queue
  serialization guarantee advertised by the lock table is violated.
  LLM rate-limit / sequencing assumptions break.

### 2.2 `SQLModelInstanceRepository.update` — SELECT → Python mutate → UPDATE, no lock

- **Location:** `daemon/repositories/instance/repository.py:554-572`.
- **Risk Level:** **CRITICAL** (most heavily used update path in the
  codebase; called by pause/resume/terminate paths and many others).
- **Description:** The generic `update(**updates)` method does an
  unconditional SELECT, Python-mutates the SQLModel object, then commits.
  There is no `with_for_update` and no WHERE-clause precondition. Any
  caller passing `status=...` is exposed to:
  - Concurrent writers reading the same `status`, both passing their
    Python-side checks, both writing — the second clobbers the first.
  - A concurrent `transition_status_if` write that succeeds between
    this method's SELECT and its UPDATE — the unconditional UPDATE
    clobbers the `transition_status_if` row.

  ```python
  # daemon/repositories/instance/repository.py:554-572
  def update(self, instance_id: str, **updates) -> Instance | None:
      """Update an instance's fields."""
      with SQLModelSession(self.engine) as db_session:
          instance = db_session.get(Instance, instance_id)   # SELECT, no lock
          if instance is None:
              return None
          if 'status' in updates and not InstanceStatus.is_valid(updates['status']):
              raise ValueError(f"Invalid status: {updates['status']}")
          for key, value in updates.items():
              if hasattr(instance, key):
                  setattr(instance, key, value)               # Python mutation
          instance.updated_at = datetime.now(timezone.utc).isoformat()
          db_session.commit()                                  # unconditional UPDATE
          db_session.refresh(instance)
          return self._enrich_instance(db_session, instance)
  ```
- **Callers affected (status writes via this method):**
  - `daemon/services/instance_lifecycle.py:545-547` (`terminate_instance`)
  - `daemon/services/instance_lifecycle.py:793-798, 800-804` (`_pause_single`)
  - `daemon/services/instance_lifecycle.py:902-907` (`resume_instance_cascade`)
  - `daemon/services/instance_lifecycle.py:625` (`update_waiting_for`)
  - `daemon/services/instance_messaging.py` (multiple call sites for
    `status`, `waiting_for`, `last_activity_at` writes — all are
    SELECT-then-mutate-via-`update`).
- **Recommended fix:**
  - For status-field updates, route through `transition_status_if`
    (line 578-613, the existing atomic primitive).
  - For non-status field updates, add `WHERE instance_id = :id AND
    version = :old_version` (optimistic concurrency) — `version` is
    already a column on `Instance`.
  - For callers that need both status and metadata changes in one
    shot, add an atomic `UPDATE instance SET status=:new, ... WHERE
    instance_id=:id AND status IN :allowed_from RETURNING *` method.
- **Impact:** Pause/resume/terminate races with each other and with
  message-job status writes; lost resume writes; `waiting_for=0` reset
  on terminate can be clobbered by a concurrent in-flight `waiting_for`
  decrement from a child-completion handler.

### 2.3 `JobRepository.atomic_transition` — NOT atomic despite the name

- **Location:** `daemon/repositories/job_queue/repository.py:430-489`.
- **Risk Level:** **CRITICAL** (misnamed; widely called by job
  lifecycle services).
- **Description:** The method is named `atomic_transition` but its
  implementation is a SELECT-then-Python-status-check-then-UPDATE
  pattern, identical to `SQLModelInstanceRepository.update`. There is
  no row lock and the SQL UPDATE has no status precondition. Two
  concurrent callers can both read `job.status == "processing"`, both
  pass the Python check, and both write `job.status == "completed"` —
  the second clobbers the first's `result_summary`, `completed_at`,
  and any other extra fields.

  ```python
  # daemon/repositories/job_queue/repository.py:430-489
  def atomic_transition(self, job_id: str, from_status: str | None,
                         to_status: str, **extra_updates) -> JobItem | None:
      ...
      with SQLModelSession(self.engine) as session:
          job = session.get(JobItem, job_id)            # SELECT, no lock
          if job is None:
              return None
          if job.status != from_status:                  # Python status check
              raise InvalidTransitionError(...)
          job_state_machine.validate_transition(from_status, to_status)
          job.status = to_status                         # MUTATE
          for key, value in extra_updates.items():
              setattr(job, key, value)
          session.commit()                                # unconditional UPDATE
  ```

- **Callers affected (10+):**
  - `daemon/services/job_feedback_observer.py:598, 641, 695` — terminal
    status transitions on the JOB row. The Race #1 fix relies on these
    not racing with each other; the race-window between SELECT and
    UPDATE reintroduces Race #1 for the JOB-side terminal write even if
    the INSTANCE-side is fixed by the CM.
  - `daemon/services/job_queue_service.py:536, 576, 590` — start_job /
    complete_job / fail_job wrappers.
  - `daemon/services/job_recovery_service.py:182` — recovery sweep.
  - `daemon/services/message_job_handler.py:627` — `_requeue_for_contention`.
- **Recommended fix:** Replace with a single-statement atomic UPDATE:
  ```python
  UPDATE job_queue_items
  SET status = :to, <extra_updates>
  WHERE job_id = :id
    AND status = :from
  RETURNING *
  ```
  Use `rowcount == 0` to detect no-op and raise
  `InvalidTransitionError` (preserving the current exception contract).
  This is the same pattern as `transition_status_if` in the instance
  repository.
- **Impact:** Double-completion of a job (two concurrent
  `complete_job()` calls both succeed; the second overwrites the first's
  result_summary). Worse, this affects the JOB-side terminal write
  even though the CM has fixed the INSTANCE-side — the daemon's SSE
  events and watcher notifications can desync.

---

## 3. HIGH Findings

### 3.1 `pause_instance_cascade._pause_single` — uses unguarded `repo.update`

- **Location:** `daemon/services/instance_lifecycle.py:728-813`
  (specifically the call to `repo.update` at lines 793-798 and 800-804).
- **Risk Level:** **HIGH** (covers the user-facing pause API).
- **Description:** Reads `meta = repo.get(target_id)` (no lock), checks
  `if meta.status == PAUSED` in Python, then calls
  `repo.update(target_id, status=PAUSED, ...)` (no precondition in
  SQL). A concurrent `resume_instance_cascade` for the same instance
  can both read `meta.status == PAUSED` (from the resume side) or
  `meta.status == RUNNING` (from the pause side), both pass their
  Python checks, and the resume write clobbers the pause write (or
  vice-versa).

  ```python
  # daemon/services/instance_lifecycle.py:771-804
  paused_at = datetime.now(timezone.utc).isoformat()
  cm = get_correlation_manager()
  if cm is not None:
      has_pending_children = cm.get_pending_count(target_id) > 0
  else:
      has_pending_children = bool(
          getattr(meta, "waiting_for", None) and meta.waiting_for > 0
      )
  if has_pending_children:
      repo.update(
          target_id,
          status=InstanceStatus.PAUSED.value,
          waiting_for=0,
          paused_at=paused_at,
      )
  else:
      repo.update(
          target_id,
          status=InstanceStatus.PAUSED.value,
          paused_at=paused_at,
      )
  ```
- **Recommended fix:** Route through `transition_status_if` with
  `allowed_from=(RUNNING, IDLE, WAITING_CHILDREN)` (the existing atomic
  primitive in `instance/repository.py:578`). The
  `paused_at=paused_at` and `waiting_for=0` writes are idempotent, but
  the `status=PAUSED` write MUST be guarded. Also: the loop at line
  815-829 iterates `tree_ids` calling `_pause_single` sequentially,
  but if any node is concurrently paused/terminated, the unguarded
  update can clobber.
- **Impact:** Pause operation appears to succeed while another path
  silently writes RUNNING afterward. UI shows inconsistent state.
  Resume races can corrupt `paused_at`/`waiting_for` cache.

### 3.2 `resume_instance_cascade` — uses unguarded `repo.update`

- **Location:** `daemon/services/instance_lifecycle.py:870-907`.
- **Risk Level:** **HIGH**.
- **Description:** Same pattern as 3.1 — reads `meta.status`, checks
  `!= PAUSED`, then calls `repo.update(... status=RUNNING ...)` without
  any SQL precondition. Two concurrent `resume_instance_cascade` calls
  (e.g., user clicks resume twice, or resume races pause) can both
  pass the Python check and both write RUNNING — the second clobbers
  the first's `paused_at=None` write.

  ```python
  # daemon/services/instance_lifecycle.py:870-907
  for node_id in tree_ids:
      try:
          meta = repo.get(node_id)
          if meta is None: ...
          if meta.status != InstanceStatus.PAUSED.value: ...  # Python check
          ...
          repo.update(                                       # UNGUARDED
              node_id,
              status=InstanceStatus.RUNNING.value,
              paused_at=None,
              waiting_for=waiting_for_value,
          )
  ```
- **Recommended fix:** Same as 3.1 — `transition_status_if` with
  `allowed_from=(PAUSED,)`.
- **Impact:** Resume state corruption; `waiting_for=1` (for ancestors)
  may be overwritten by a concurrent pause/resume to `waiting_for=0`.

### 3.3 `terminate_instance` — uses unguarded `repo.update`

- **Location:** `daemon/services/instance_lifecycle.py:544-547`.
- **Risk Level:** **HIGH** (less common but irreversible).
- **Description:**
  ```python
  # daemon/services/instance_lifecycle.py:544-547
  if hasattr(self._manager, '_instance_repository') and self._manager._instance_repository:
      self._manager._instance_repository.update(
          instance_id, status="terminated", waiting_for=0
      )
  ```
  No row lock, no SQL precondition. Two concurrent terminate calls
  (e.g., user + cron) both read status=RUNNING, both pass the
  re-entrancy check at line 454 (`if meta.status == TERMINATED`), and
  both write status=TERMINATED. The waiting_for=0 reset is preserved
  but other in-flight writers (a child-completion waiting_for
  decrement at the same moment) can clobber the terminate.
- **Recommended fix:** `transition_status_if` with `allowed_from=(*ALL
  NON-TERMINAL*)`. Since terminate should be idempotent and must
  always win over a concurrent non-terminal transition, allow from any
  non-terminal state. The re-entrancy check at line 454 then becomes
  unnecessary because `transition_status_if` already returns `None` on
  a row already in TERMINATED.
- **Impact:** Same pattern as 3.1/3.2; additionally, the
  `waiting_for=0` reset documented as crash-recovery-consistent at
  line 540-543 can be clobbered by a child-completion decrement that
  sneaks in between the SELECT and the UPDATE.

### 3.4 `JobRetryEngine.maybe_retry` — SELECT → Python check → UPDATE, no lock

- **Location:** `daemon/services/job_retry_engine.py:201-238`.
- **Risk Level:** **HIGH** (duplicate retry entries on concurrent
  sweeps).
- **Description:**
  ```python
  # daemon/services/job_retry_engine.py:201-238
  with SQLModelSession(self._job_repo.engine) as session:
      job = session.get(JobItem, job_id)
      if job is None: return None
      if job.status != "failed": return None        # Python guard
      if self.should_retry(job, queue, config):
          ...
          job.status = "pending"
          job.retry_count += 1                       # LOST UPDATE HAZARD
          job.next_retry_at = next_retry_at
          ...
          session.commit()
  ```
  Two concurrent `maybe_retry` calls (e.g., the retry sweep from
  `retry_scheduler` and the explicit retry triggered by
  `job_feedback_observer`) can both read `retry_count = 2`, both
  compute `new_retry_count = 3`, and both write `retry_count = 3` —
  one retry is silently dropped, AND the job is moved to PENDING twice.
- **Recommended fix:** Replace the in-memory `job.retry_count += 1`
  with an atomic SQL UPDATE that returns the new value:
  ```sql
  UPDATE job_queue_items
  SET status = 'pending',
      retry_count = retry_count + 1,
      next_retry_at = :next_retry_at,
      failed_at = NULL,
      error_message = NULL
  WHERE job_id = :id
    AND status = 'failed'
    AND retry_count < :max_retries
  RETURNING *
  ```
  Use `rowcount == 0` to detect "not in FAILED" or "exhausted retries"
  cases (route to DLQ on the latter).
- **Impact:** Lost retries (silent skip of an attempt), premature DLQ
  moves (because `retry_count` is under-counted), and double-enqueue
  to PENDING.

### 3.5 `JobLockRepository.acquire` — plain INSERT, no ON CONFLICT

- **Location:** `daemon/repositories/job_queue/lock_repository.py:23-29`
  (and callers in `job_lock_manager.py`).
- **Risk Level:** **HIGH** (a building block of 2.1; documented
  separately because the repository-level fix is independent of the
  service-level fix).
- **Description:**
  ```python
  # daemon/repositories/job_queue/lock_repository.py:23-29
  def acquire(self, lock: JobLock) -> JobLock:
      with SQLModelSession(self.engine) as session:
          session.add(lock)
          session.commit()
          session.refresh(lock)
          return lock
  ```
  No UNIQUE constraint check, no `INSERT OR IGNORE` / `ON CONFLICT
  DO NOTHING`. If two callers happen to build `JobLock` with identical
  `(project_id, queue_id, job_id)`, both inserts succeed (no PK
  because the table does not declare one), and `release_by_job`
  silently deletes only one of them — the second row leaks forever.
- **Recommended fix:** Add a UNIQUE constraint on
  `(project_id, queue_id, job_id)` to the migration, and use
  `INSERT ... ON CONFLICT DO NOTHING` (PostgreSQL) / `INSERT OR IGNORE`
  (SQLite) to make the acquire idempotent for the same `(job_id)`.
  Pair with the count-then-insert fix from 2.1 for full atomicity.
- **Impact:** Duplicate lock rows accumulate; release deletes only one;
  `get_lock_count` reports inflated values, blocking legitimate
  acquires even when the queue is logically empty.

### 3.6 `LockRepository.release*` — SELECT → DELETE, no row lock

- **Location:** `daemon/repositories/job_queue/lock_repository.py:31-65`.
- **Risk Level:** **HIGH**.
- **Description:**
  ```python
  # daemon/repositories/job_queue/lock_repository.py:31-39
  def release(self, lock_id: str) -> bool:
      with SQLModelSession(self.engine) as session:
          lock = session.get(JobLock, lock_id)
          if lock is None:
              return False
          session.delete(lock)
          session.commit()
          return True

  # daemon/repositories/job_queue/lock_repository.py:41-54
  def release_by_job(self, project_id: str, queue_id: str, job_id: str) -> bool:
      with SQLModelSession(self.engine) as session:
          stmt = select(JobLock).where(
              JobLock.project_id == project_id,
              JobLock.queue_id == queue_id,
              JobLock.job_id == job_id,
          )
          lock = session.exec(stmt).first()
          if lock is None:
              return False
          session.delete(lock)
          session.commit()
          return True
  ```
  Same SELECT-then-DELETE pattern. Two concurrent releases can both
  SELECT the same row, both decide to delete, and both succeed
  (returning True twice even though only one row was actually deleted).
  Worse, on PostgreSQL READ COMMITTED, the SELECT-then-DELETE window
  is non-trivial; another release can DELETE the row between our
  SELECT and our DELETE, and our `session.delete` raises
  `InvalidRequestError` on a stale instance.
- **Recommended fix:** Replace with a single-statement conditional
  DELETE:
  ```sql
  DELETE FROM job_locks
  WHERE lock_id = :lock_id
  RETURNING 1
  ```
  Use `rowcount == 1` as success.
- **Impact:** False-positive release responses; rare
  `InvalidRequestError` under contention (errors are likely caught at
  the call site but the lock state becomes inconsistent).

### 3.7 `MessageQueueRepository.complete / fail / retry` — SELECT → mutate, no row lock

- **Location:** `daemon/repositories/message_queue/repository.py:271-337`.
- **Risk Level:** **HIGH** (the dequeue side is locked, but the
  terminal-side writes are not).
- **Description:**
  ```python
  # daemon/repositories/message_queue/repository.py:271-284
  def complete(self, message_id: str) -> MessageQueue | None:
      with Session(self.engine) as session:
          message = session.get(MessageQueue, message_id)
          if message is None:
              return None
          message.status = MessageStatus.COMPLETED.value
          message.completed_at = datetime.now(timezone.utc)
          session.commit()
          session.refresh(message)
          return message

  # lines 286-300 (fail) — same pattern
  # lines 302-337 (retry) — same pattern, also increments retry_count
  ```
  `dequeue` (line 89-136) correctly uses `with_for_update()`, but
  `complete` / `fail` / `retry` do NOT. A concurrent terminal-status
  write can race: two workers both call `complete(message_id)` after
  the message was dequeued by one of them; both succeed; the second
  clobbers the first's `completed_at`.
  `retry` is worse — it does `message.retry_count += 1` in Python
  (lost-update hazard: two concurrent retries both increment from the
  same base value, one increment is silently lost).
- **Recommended fix:** Add `with_for_update()` to the `session.get`
  calls at lines 274, 289, 313. Or use atomic UPDATEs:
  ```python
  UPDATE message_queue
  SET status = :new, completed_at = :now
  WHERE message_id = :id AND status = :expected
  RETURNING *
  ```
- **Impact:** Lost retry attempts; clobbered `completed_at` timestamps;
  in rare cases, status fields that contradict each other
  (e.g., COMPLETED set by worker A and FAILED set by a stuck-recovery
  sweep that wins the second write).

### 3.8 `instance_lifecycle.py:370-389` — parent.children list update

- **Location:** `daemon/services/instance_lifecycle.py:374-389` (in
  the spawn_child path).
- **Risk Level:** **HIGH** (concurrent spawn for same parent can lose
  a child ID).
- **Description:**
  ```python
  # daemon/services/instance_lifecycle.py:374-389
  with WriteGuardSession(Session(self._manager.engine), self._manager.write_guard) as session:
      parent = session.get(Instance, parent_id)
      if parent:
          children_list = json.loads(parent.children) if parent.children else []
          if instance_id not in children_list:
              children_list.append(instance_id)
              parent.children = json.dumps(children_list)
          session.commit()
  ```
  Two concurrent `spawn_child` calls for the same parent (e.g., two
  LLM-driven fan-outs) can both read `parent.children = '[A]'`, both
  append B, both write `'[A, B]'` — the second append is lost.
- **Recommended fix:** Use an atomic JSON-array append:
  - PostgreSQL: `UPDATE instance SET children = children || :json
    WHERE instance_id = :id AND NOT (children @> :json_id)` (uses
    JSONB containment check to make it idempotent).
  - SQLite: requires a different pattern — extract the children list
    inside a CTE and re-assemble, or use a separate child-link table.
  - Or simpler: route through a per-parent `asyncio.Lock` since
    `spawn_child` is async.
- **Impact:** Lost child ID — the child exists in `instances` but is
  not in the parent's denormalized `children` list, so cascade
  pause/resume/terminate does not find it.

---

## 4. MEDIUM Findings

### 4.1 `task_repository.force_cancel_and_schedule_retry` — UPDATE without WHERE guard

- **Location:** `daemon/repositories/task/repository.py:983-1080`.
- **Risk Level:** **MEDIUM** (single-transaction but no status guard).
- **Description:** The SELECT-then-Python-check-then-UPDATE pattern at
  line 1017-1056 reads `retry_scheduled` and `retry_count` from the
  parent row in Python, then unconditionally writes:
  ```python
  # daemon/repositories/task/repository.py:1037-1056
  conn.execute(
      text("""
          UPDATE task SET
              status = :status_cancelled,
              ...
              retry_scheduled = :retry_scheduled
          WHERE id = :id           # <-- NO retry_scheduled/retry_count guard
      """),
      ...
  )
  ```
  Two concurrent calls to `force_cancel_and_schedule_retry` (e.g.,
  stale-task-recovery sweep racing the regular retry engine) can both
  pass the Python guard `if parent.get("retry_scheduled", False): pass`,
  both transition to CANCELLED, both create a retry child task →
  double-retry. The retry child tasks are then claimed in any order,
  leading to two parallel workers processing the same work item.
- **Recommended fix:** Add the guard to the SQL WHERE clause:
  ```sql
  UPDATE task SET status = :cancelled, ..., retry_scheduled = true
  WHERE id = :id
    AND retry_scheduled = false
    AND retry_count < :max_retries
  RETURNING *
  ```
  Then `INSERT INTO task (...)` for the retry child only if the UPDATE
  returned a row.
- **Impact:** Double retry chains; the same task claimed and processed
  twice in parallel.

### 4.2 `task_repository.schedule_retry` — same pattern as 4.1

- **Location:** `daemon/repositories/task/repository.py:738-847`.
- **Risk Level:** **MEDIUM**.
- **Description:** Same SELECT-then-Python-check pattern (lines 759-779)
  followed by unconditional UPDATE (line 795-813) and INSERT (line 818-839).
  Two concurrent calls can both pass the Python guard and both create
  retry child tasks.
- **Recommended fix:** Same as 4.1 — add `retry_scheduled = false AND
  retry_count < :max_retries` to the UPDATE WHERE clause.

### 4.3 `_prepare_enqueued_message` status transition — unguarded

- **Location:** `daemon/services/instance_messaging.py:832-876`
  (specifically the status mutation at line 841-842).
- **Risk Level:** **MEDIUM**.
- **Description:**
  ```python
  # daemon/services/instance_messaging.py (approximate lines)
  instance = session.get(Instance, instance_id)
  if instance:
      if instance.status in (
          InstanceStatus.IDLE.value,
          InstanceStatus.WAITING_CHILDREN.value,
          InstanceStatus.COMPLETED.value,
      ):
          instance.status = InstanceStatus.RUNNING.value
      ...
  session.commit()
  ```
  Concurrent enqueue_message calls for the same instance can both
  pass the Python check and both write RUNNING. More importantly, a
  concurrent `terminate_instance` (line 545-547 of instance_lifecycle)
  writes status=TERMINATED; if terminate wins the race and commits
  first, the enqueue's RUNNING write clobbers TERMINATED.
  The `version = (instance.version or 1) + 1` bump at line 874 is
  also computed in Python (lost-update vulnerable), but version is
  only used for SSE display, so impact is low.
- **Recommended fix:** Use `transition_status_if` with
  `allowed_from=(IDLE, WAITING_CHILDREN, COMPLETED)`.

### 4.4 `job_feedback_observer._finalize_instance_db_sync` — terminal write unguarded

- **Location:** `daemon/services/job_feedback_observer.py:909-980`
  (specifically the write at line 973).
- **Risk Level:** **MEDIUM**.
- **Description:** Reads `instance.status`, checks
  `if instance.status in _TERMINAL_INSTANCE_STATUSES:` (idempotency
  guard), then writes `instance.status = new_status` without any SQL
  precondition. A concurrent non-terminal `transition_status_if` call
  (e.g., `message_job_handler.py:228` pre-pickup) can be clobbered if
  it lands between the SELECT and the UPDATE.
- **Recommended fix:** Use `transition_status_if` with
  `allowed_from=set(non-terminal statuses)` (the pre-existing list
  `IDLE`, `RUNNING`, `WAITING_CHILDREN`).

### 4.5 `child_reports` and `error_reporting` cascade status writes — CM-disabled path

- **Location:** `daemon/services/child_reports.py:675, 714, 1342, 1368`
  and `daemon/services/error_reporting.py:339, 369`.
- **Risk Level:** **MEDIUM** (CM-disabled legacy path; CM-enabled path
  is correct).
- **Description:** When `cm is None` (graceful-degradation fallback),
  the cascade writes `parent.status = COMPLETED` (or `ERROR`) directly
  via `repo.update()` after the atomic `waiting_for` decrement. A
  concurrent `terminate_instance` (line 545-547) can land between the
  decrement and the status write, with the cascade clobbering
  TERMINATED with COMPLETED.
- **Recommended fix:** Route the cascade status write through
  `transition_status_if` with
  `allowed_from=(RUNNING, WAITING_CHILDREN, IDLE)`.

### 4.6 `infra` `update_asset` and `register_type` — SELECT-mutate, no row lock

- **Location:**
  - `daemon/repositories/infra/repository.py:649-814` (`update_asset`)
  - `daemon/repositories/infra/repository.py:988-1047` (`register_type`)
- **Risk Level:** **MEDIUM** (low due to DB-level backstops).
- **Description:** Both use SELECT → Python mutate → UPDATE without
  row locking. However:
  - `update_asset` has a UNIQUE constraint on `(project_id, type, name)`
    that protects against the worst-case rename race — a concurrent
    rename will raise `IntegrityError`, which the repository
    correctly classifies and re-raises as `ValueError`.
  - `register_type` keys on the `name` column (the PK), so concurrent
    registers for the same type are serialized by the PK index.
- **Recommended fix:** Not strictly required — the DB constraints
  backstop the worst cases. But add `with_for_update` on `session.get`
  for `update_asset` to prevent lost-update on non-unique fields
  (e.g., two concurrent updates to `attributes` clobbering each
  other).

---

## 5. LOW Findings

### 5.1 `infra.repository.update_asset` — see 4.6

Already covered above; the DB UNIQUE constraint is a sufficient
backstop for the rename race, but non-unique concurrent updates to
`attributes` / `relationships` JSON columns can still be lost.

### 5.2 `infra.repository.register_type` — see 4.6

Already covered above; the PK on `name` is a sufficient backstop. This
is also a startup-only path so contention is extremely rare.

### 5.3 `watcher_repository.add_watch` — SELECT-then-UPDATE

- **Location:** `daemon/repositories/job_queue/watcher_repository.py:46-73`.
- **Risk Level:** **LOW** (in-memory fallback; duplicates are benign).
- **Description:**
  ```python
  # daemon/repositories/job_queue/watcher_repository.py:46-73
  with SQLModelSession(self.engine) as db_session:
      stmt = select(JobWatcher).where(
          JobWatcher.job_id == job_id,
          JobWatcher.instance_id == instance_id,
      )
      existing = db_session.exec(stmt).first()
      if existing:
          existing.watch_events = events
          db_session.add(existing)
          db_session.commit()
          ...
          return existing
      watch = JobWatcher(...)
      db_session.add(watch)
      db_session.commit()
      ...
  ```
  Two concurrent `add_watch` calls for the same `(job_id,
  instance_id)` pair can both see `existing is None` and both INSERT
  → duplicate watcher rows. The unique constraint at the table level
  (if present) catches this; without one, both rows persist and
  notifications are duplicated.
- **Recommended fix:** Add a UNIQUE constraint on `(job_id,
  instance_id)` and use `INSERT ... ON CONFLICT DO NOTHING` /
  `INSERT OR IGNORE` for atomicity, or add `with_for_update` to the
  SELECT to serialize concurrent calls.

### 5.4 `dead_letter_repository.delete` and `delete_by_job_id` — SELECT-DELETE

- **Location:** `daemon/repositories/job_queue/dead_letter_repository.py:117-150`.
- **Risk Level:** **LOW** (idempotent delete; races only affect
  count return value).
- **Description:**
  ```python
  # daemon/repositories/job_queue/dead_letter_repository.py:117-132
  def delete(self, dlq_id: str) -> bool:
      with SQLModelSession(self.engine) as session:
          item = session.get(DeadLetterItem, dlq_id)
          if item is None:
              return False
          session.delete(item)
          session.commit()
          return True
  ```
  Two concurrent deletes both return True even though only one row
  is actually removed. Caller treats the second as a no-op success.
  Impact is purely cosmetic (return value inconsistency).
- **Recommended fix:** Replace with `DELETE FROM dead_letter_items
  WHERE dlq_id = :id RETURNING 1` and use `rowcount`.

### 5.5 `message_queue.complete / fail` — see 3.7

Covered above.

---

## 6. SKIP LOCKED and EvalPlanQual — codebase policy verified

- **SKIP LOCKED:** Not used in any production code. The design
  documents (`docs/architecture/message-queue-redesign.md`, plan files
  in `.agents/shared/planning/message-queue-redesign/`) explicitly
  rejected `FOR UPDATE SKIP LOCKED` because SQLite silently ignores
  `FOR UPDATE` clauses. The codebase uses the UPDATE-RETURNING
  pattern instead, which is correct for both backends. **No change
  recommended.**
- **EvalPlanQual:** Triggered correctly by the outer WHERE clause
  recheck in `claim_pending_task` (line 267: `AND status =
  :status_pending`). All other atomic UPDATE-WHERE-guard patterns
  (1.7 above) also benefit from EvalPlanQual on PostgreSQL. **No
  change recommended.**

---

## 7. Audit-Scope Files — Status Summary

| File | Status |
|------|--------|
| `daemon/repositories/task/repository.py` | ✅ EvalPlanQual guard correct on `claim_pending_task`; ⚠️ 4.1, 4.2 medium-risk SELECT-mutate patterns in retry scheduling |
| `daemon/repositories/message_queue/repository.py` | ✅ `dequeue` correctly locked; ❌ `complete` / `fail` / `retry` (3.7) unguarded |
| `daemon/repositories/job_queue/repository.py` | ❌ `atomic_transition` (2.3) — name is misleading; not atomic. `update` (used by 3.1-3.3) is vulnerable |
| `daemon/repositories/job_queue/lock_repository.py` | ❌ `acquire` (3.5), `release*` (3.6) all unguarded |
| `daemon/repositories/job_queue/dead_letter_repository.py` | ✅ `enqueue`, `get`, `list` are fine; ⚠️ `delete` (5.4) low-risk |
| `daemon/repositories/job_queue/queue_repository.py` | ✅ All operations are either single-table CRUD or use atomic UPDATE |
| `daemon/repositories/job_queue/watcher_repository.py` | ⚠️ `add_watch` (5.3) low-risk duplicate insert hazard |
| `daemon/repositories/instance/repository.py` | ❌ `update` (2.2) critical; ✅ `transition_status_if` (1.5) correct |
| `daemon/repositories/execution_lease/repository.py` | ✅ Atomic SQL throughout (1.4) |
| `daemon/repositories/infra/repository.py` | ⚠️ `update_asset` (4.6) — DB UNIQUE backstop; medium risk for JSON fields |
| `daemon/repositories/event`, `project`, `source`, `mcp_server` | ✅ No row-locking requirements identified (read-heavy or single-writer paths) |
| `daemon/services/dead_letter_service.py` | ✅ FOR UPDATE throughout (1.2) |
| `daemon/services/correlation_manager.py` | ✅ In-memory per-parent lock + W1 fix (1.8) |
| `daemon/services/job_lock_manager.py` | ❌ CRITICAL race (2.1) — `asyncio.Lock` does not protect cross-thread |
| `daemon/services/job_retry_engine.py` | ❌ `maybe_retry` SELECT-mutate (3.4) |
| `daemon/services/instance_lifecycle.py` | ❌ `terminate_instance` (3.3), `pause_instance_cascade` (3.1), `resume_instance_cascade` (3.2), `spawn_child.children` list (3.8) |
| `daemon/services/message_job_handler.py` | ✅ `transition_status_if` correctly used at line 228-248; ⚠️ `atomic_transition` calls at line 627 inherit 2.3 |
| `daemon/services/instance_messaging.py` | ⚠️ `_prepare_enqueued_message` (4.3) unguarded status transition |
| `daemon/services/job_feedback_observer.py` | ⚠️ `_finalize_instance_db_sync` (4.4) unguarded terminal write; relies on `atomic_transition` for job-side (2.3) |
| `daemon/services/child_reports.py`, `error_reporting.py` | ✅ waiting_for atomic decrement (1.6); ⚠️ CM-disabled cascade status writes (4.5) |

---

## 8. Recommended Fix Priority

### Must-fix (correctness)

1. **2.1** `JobLockManager.acquire_queue_lock` — atomic INSERT-or-count.
2. **2.2** `SQLModelInstanceRepository.update` — guard with
   version/transition_status_if, OR refactor callers to use
   `transition_status_if`.
3. **2.3** `JobRepository.atomic_transition` — replace with atomic
   UPDATE-WHERE-guard.

### Should-fix (frequent races)

4. **3.1** pause_instance_cascade — `transition_status_if`.
5. **3.2** resume_instance_cascade — `transition_status_if`.
6. **3.3** terminate_instance — `transition_status_if`.
7. **3.4** `JobRetryEngine.maybe_retry` — atomic UPDATE-WHERE-guard.
8. **3.5** `JobLockRepository.acquire` — `INSERT ... ON CONFLICT`.
9. **3.6** `JobLockRepository.release*` — atomic DELETE-WHERE.
10. **3.7** `MessageQueueRepository.complete/fail/retry` — add
    `with_for_update()`.
11. **3.8** `spawn_child.children` list — atomic JSON append or
    per-parent lock.

### Nice-to-have (rare races / cosmetic)

12. **4.1, 4.2** `task_repository.{schedule_retry,
    force_cancel_and_schedule_retry}` — add WHERE guard.
13. **4.3** `_prepare_enqueued_message` — `transition_status_if`.
14. **4.4** `_finalize_instance_db_sync` — `transition_status_if`.
15. **4.5** child_reports / error_reporting CM-disabled cascade —
    `transition_status_if`.
16. **4.6** infra `update_asset` / `register_type` — `with_for_update`
    for JSON field protection.
17. **5.x** watcher / dead_letter / message_queue `delete` —
    atomic DELETE-WHERE.

---

## 9. Tests That Would Have Caught These

- **`tests/test_observer_race1.py`** exists and covers Race #1 (CM
  fix). It does NOT cover:
  - `JobLockManager.acquire_queue_lock` concurrency (2.1)
  - `instance_lifecycle.pause_instance_cascade` race (3.1)
  - `instance_lifecycle.resume_instance_cascade` race (3.2)
  - `instance_lifecycle.terminate_instance` race (3.3)
  - `JobRepository.atomic_transition` SELECT-mutate race (2.3)
  - `MessageQueueRepository.complete/fail/retry` unguarded writes (3.7)
  - `instance_messaging._prepare_enqueued_message` race (4.3)

**Recommendation:** Add a PostgreSQL-only concurrent-thread test pack
that hammers these methods under contention with two threads on the
same row; assert the post-condition matches what a serial schedule
would produce.

---

## 10. Cross-Reference to Other Audit Areas

- Area 2 (`transaction-boundary`): `WriteGuardSession` patterns
  verified at `daemon/services/correlation_manager.py`,
  `child_reports.py`, `error_reporting.py`. The transaction boundary
  itself is correct; the row-locking within the transaction is the
  gap (covered here).
- Area 3 (`waiting-for`): covered by `area3-waiting-for.md`. The
  counter itself is atomic (verified 1.6); the read-then-decide layer
  above is the gap (covered there).
- Area 4 (`jsonb-field-update`): `infra/repository.py` JSON path
  handling is correct (4.6 covers the only outstanding gap).

---

## Final Verdict

The codebase's core locking primitives (`claim_pending_task`,
`DeadLetterService`, `ExecutionLeaseRepository`,
`transition_status_if`) are **correctly implemented and verified**.
The CorrelationManager migration's W1, W2, N2 fixes are also in place.

However, **three CRITICAL and five HIGH-impact SELECT-then-UPDATE
patterns remain** that bypass the repository layer's atomic primitives
or use unguarded repository methods. The most urgent is 2.1
(`JobLockManager`) — a **cross-process lock table that does not use
any atomic SQL**, only an in-process `asyncio.Lock`. This violates the
concurrency cap advertised by the lock table and is the highest-impact
finding in this audit area.

All other findings are localized to single call sites and have
straightforward fixes via the existing `transition_status_if` primitive
or atomic UPDATE-WHERE-guard patterns.
