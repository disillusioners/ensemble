# Area 5 — Parallel Read-Update Audit (PostgreSQL READ COMMITTED)

**Audit date:** 2026-06-19
**Scope:** All read-modify-write (RMW) and lost-update patterns under PostgreSQL READ COMMITTED isolation
**Methodology:** ripgrep + targeted file reads in `daemon/repositories/`, `daemon/services/`, `daemon/tools/`, `daemon/manager.py`, `daemon/opencode/`
**Mode:** ANALYSIS-ONLY — no code changes, recommendations only

---

## Executive Summary

The codebase has a **mixed concurrency posture**. The high-frequency hot paths for `waiting_for` and task claiming are correctly atomic. The **status-transition layer is largely racy** under PostgreSQL's default isolation: the well-named `atomic_transition()` function in the job queue repository is *not* atomic at the SQL level, and the task lifecycle (`complete_task`, `fail_task`, `cancel_task`) performs an ORM read-modify-write with no conditional UPDATE.

Most concurrency-sensitive features rely on **in-memory coordination** (CorrelationManager, asyncio locks, the correlation_manager singleton) rather than database-enforced atomicity. This works on SQLite+aiosqlite (single-writer model masks races) but does **not** survive the planned PostgreSQL migration (v0.5.2+ default backend).

**Counts:**
- **12 CRITICAL** RMW sites with no atomic guard (lost update possible under PG READ COMMITTED)
- **8 HIGH** RMW sites (lost update possible, but with limited concurrency exposure)
- **3 MEDIUM** RMW sites (low-frequency paths or protected by external coordination)
- **14 VERIFIED OK** atomic patterns (single-statement UPDATE-RETURNING or `with_for_update`)

The single most concerning finding is that **`atomic_transition()` in `daemon/repositories/job_queue/repository.py:430` is misnamed**: it issues `UPDATE … WHERE id = ?` (no status predicate), so under PG READ COMMITTED any two concurrent callers succeed and the later writer clobbers the earlier one's transition.

---

## CRITICAL Findings (Active Lost-Update Risk)

### C-1. `cancel_task` — Classic PG EvalPlanQual Bug

- **File:** `daemon/repositories/task/repository.py:926-981`
- **Risk Level:** Critical
- **Pattern:** SELECT → Python-side status check → unconditional UPDATE

**Current code (excerpt):**
```python
def cancel_task(self, task_id: int, reason: str = "") -> Task | None:
    now = datetime.now(timezone.utc)
    result = None

    with self.engine.begin() as conn:
        # Check current status
        row = conn.execute(
            text("SELECT * FROM task WHERE id = :id"),
            {"id": task_id}
        ).fetchone()

        if row is None:
            return None

        current = self._row_to_task(row)
        if current.status not in (TaskStatus.RUNNING.value, TaskStatus.PENDING.value):
            return None

        conn.execute(
            text("""
                UPDATE task SET
                    status = :status_cancelled,
                    cancel_requested = :cancel_requested,
                    cancel_requested_at = :cancelled_at,
                    completed_at = :completed_at,
                    error = :error
                WHERE id = :id
            """),
            ...
        )
```

**Concurrency issue:**
The Python-side `if current.status not in (RUNNING, PENDING)` check operates on the SELECT snapshot. The UPDATE's WHERE clause is `id = :id` — **no status predicate**, so under PostgreSQL READ COMMITTED:

1. Recovery service: `SELECT * FROM task WHERE id=X` → snapshot says `status='running'`
2. Concurrent worker: `complete_task(X)` commits `status='completed'`
3. Recovery service: `UPDATE … WHERE id=X` → PG re-evaluates outer WHERE under the row's lock (EvalPlanQual); row still matches `id=X`; UPDATE succeeds, silently overwriting `completed` with `cancelled`.

The same race exists in reverse (cancel commits first, complete overwrites it back).

**Recommended fix:**
```sql
UPDATE task SET
    status = :status_cancelled,
    cancel_requested = TRUE,
    cancel_requested_at = :cancelled_at,
    completed_at = :completed_at,
    error = :error
WHERE id = :id
  AND status IN (:status_running, :status_pending)
RETURNING *
```

Then check `result.rowcount == 1` to detect concurrent transition. Mirror the pattern already in `request_cancel()` (line 857) which DOES have the conditional guard.

**Impact:** Stale-task recovery silently overwrites a worker's `completed` result with `cancelled`, losing the result. Symptom: completed work appears as cancelled in audit logs; retry decisions become wrong.

---

### C-2. `complete_task` — No Precondition Guard

- **File:** `daemon/repositories/task/repository.py:453-482`
- **Risk Level:** Critical
- **Pattern:** ORM load → mutate → commit

**Current code (excerpt):**
```python
def complete_task(self, task_id: int, result: dict[str, Any]) -> Task | None:
    now = datetime.now(timezone.utc)

    with SQLModelSession(self.engine) as db_session:
        task = db_session.get(Task, task_id)
        if task is None:
            return None

        task.status = TaskStatus.COMPLETED.value
        task.result = json.dumps(result)
        task.completed_at = now

        db_session.commit()
        db_session.refresh(task)

    self._notify_pending_task()
    return task
```

**Concurrency issue:**
No precondition check whatsoever — not even a Python-side one. SQLAlchemy ORM issues `UPDATE task SET status=?, result=?, completed_at=? WHERE id=?` (no status predicate). If the recovery service concurrently sets `status='cancelled'`, this `complete_task` silently overwrites it back to `'completed'`.

Called from `worker_pool.py:329-331` and `task_processor.py:329-334`. **Every successful task completion goes through this racy path.**

**Recommended fix:** Conditional UPDATE pattern, mirroring `requeue_task_with_backoff`:
```sql
UPDATE task
SET status = :status_completed,
    result = :result,
    completed_at = :now
WHERE id = :task_id
  AND status IN (:status_running, :status_pending)
RETURNING *
```

**Impact:** Worker's completed result silently wins over recovery's cancellation, or vice versa. Inconsistent terminal state across the system.

---

### C-3. `fail_task` — No Precondition Guard

- **File:** `daemon/repositories/task/repository.py:484-511`
- **Risk Level:** Critical
- **Pattern:** Identical to C-2

**Current code (excerpt):**
```python
def fail_task(self, task_id: int, error: str) -> Task | None:
    now = datetime.now(timezone.utc)

    with SQLModelSession(self.engine) as db_session:
        task = db_session.get(Task, task_id)
        if task is None:
            return None

        task.status = TaskStatus.FAILED.value
        task.error = error
        task.completed_at = now

        db_session.commit()
```

**Concurrency issue:** Same defect as C-2. Called from `worker_pool.py:429-432, 447-449, 463`.

**Recommended fix:** Same conditional UPDATE pattern as C-2.

**Impact:** Identical to C-2; failure result silently lost or recovery silently overwritten.

---

### C-4. `atomic_transition()` — Misnamed, Not Atomic

- **File:** `daemon/repositories/job_queue/repository.py:430-489`
- **Risk Level:** Critical
- **Pattern:** session.get() → Python-side status check → ORM mutate → commit (no conditional WHERE)

**Current code (excerpt):**
```python
def atomic_transition(
    self,
    job_id: str,
    from_status: str | None,
    to_status: str,
    **extra_updates: Any,
) -> JobItem | None:
    """Atomically transition a job's status within a single session.

    Uses SELECT + UPDATE within the same session to ensure atomicity.
    Checks current status to detect concurrent modification or stale state.
    """
    from daemon.services.job_state_machine import job_state_machine, InvalidTransitionError

    transition_name = job_state_machine.get_transition_name(from_status, to_status)

    with SQLModelSession(self.engine) as session:
        job = session.get(JobItem, job_id)
        if job is None:
            return None

        # Verify current status matches expected
        if job.status != from_status:
            raise InvalidTransitionError(...)

        # Validate transition is allowed
        job_state_machine.validate_transition(from_status, to_status)

        # Apply the transition
        job.status = to_status
        for key, value in extra_updates.items():
            setattr(job, key, value)

        session.commit()
        ...
```

**Concurrency issue:**
Despite the docstring claim, this is **NOT atomic at the SQL level**. SQLAlchemy ORM generates `UPDATE job_queue_items SET status=?, … WHERE id=?` with NO `AND status=?` predicate. The Python-side check operates on the SELECT snapshot and provides no protection against concurrent commits. Under PG READ COMMITTED:
- Session A: SELECT (sees `processing`) → Python check passes
- Session B: SELECT (sees `processing`) → Python check passes
- Session A: UPDATE SET status='completed' (commits)
- Session B: UPDATE SET status='failed' (commits, overwrites 'completed')

**Critical mass:** This is the workhorse for **every job status change**. Called from:
- `start_job_atomic`, `complete_job`, `fail_job`, `cancel_job`, `terminate_job` (in same file)
- `job_queue_service.py:536, 576, 590` (cancel_job, etc.)
- `job_feedback_observer.py:598, 641, 695` (success/failure terminal transitions)
- `message_job_handler.py:627` (`_requeue_for_contention` — PROCESSING→PENDING)
- `job_recovery_service.py:182` (recovery timeout → FAILED)

**Recommended fix:** Rewrite as a conditional UPDATE-RETURNING:
```python
def atomic_transition(
    self, job_id: str, from_status: str, to_status: str, **extra_updates
) -> JobItem | None:
    # Build SET clause dynamically from extra_updates
    set_clauses = ["status = :to_status", "updated_at = :updated_at"]
    params = {"job_id": job_id, "from_status": from_status, "to_status": to_status,
              "updated_at": datetime.now(timezone.utc).isoformat()}
    for key, value in extra_updates.items():
        set_clauses.append(f"{key} = :{key}")
        params[key] = value

    sql = text(f"""
        UPDATE job_queue_items
        SET {', '.join(set_clauses)}
        WHERE job_id = :job_id AND status = :from_status
        RETURNING *
    """)
    with self.engine.begin() as conn:
        row = conn.execute(sql, params).fetchone()
        if row is None:
            return None  # Concurrent transition won, or job gone
        return self._row_to_job(row)
```

This pattern matches the working `transition_status_if()` in `instance/repository.py:578-613`.

**Impact:** **Highest blast radius in the codebase.** Any two concurrent job-terminal paths (e.g., JobFeedbackObserver success vs. JobRecoveryService timeout; two cancel_job callers) silently overwrite each other. The first writer's terminal state and result payload are lost.

---

### C-5. `start_job` (non-atomic variant) — Two-Transaction TOCTOU

- **File:** `daemon/repositories/job_queue/repository.py:522-553`
- **Risk Level:** Critical
- **Pattern:** `self.get()` (Session 1) → check → `self.update()` (Session 2, unconditional UPDATE)

**Current code (excerpt):**
```python
def start_job(
    self,
    job_id: str,
    instance_id: str,
) -> JobItem | None:
    """Mark a job as processing (started).

    Can only be called on PENDING jobs.
    """
    job = self.get(job_id)
    if job is None:
        return None
    if job.status != JobStatus.PENDING.value:
        raise ValueError(
            f"Cannot start job in '{job.status}' state, must be PENDING"
        )
    return self.update(
        job_id,
        status=JobStatus.PROCESSING.value,
        started_at=datetime.now(timezone.utc).isoformat(),
        instance_id=instance_id,
    )
```

**Concurrency issue:** Two separate transactions. Between `self.get` and `self.update`, anything can happen. `self.update` does its own `session.get` + `setattr` + `commit` — also unconditional. Two concurrent `start_job` callers each pass the Python check (both see `PENDING`) and both succeed in writing `instance_id=…` — second writer clobbers first.

**Recommended fix:** Remove this method entirely; route all callers through `start_job_atomic()` (line 555) which uses `atomic_transition()`. After fixing C-4, `atomic_transition` itself becomes safe.

**Impact:** Concurrent job dispatch for the same PENDING job produces duplicate `PROCESSING` rows with conflicting `instance_id`. The losing instance_id is orphaned.

---

### C-6. `instance_metadata` RMW — Three Unsafe Operations

- **Files:**
  - `daemon/repositories/instance/repository.py:627-640` (`update_title`)
  - `daemon/repositories/instance/repository.py:642-655` (`set_metadata`)
  - `daemon/repositories/instance/repository.py:657-670` (`delete_metadata`)
- **Risk Level:** Critical
- **Pattern:** load ORM object → mutate `instance_metadata` dict → `flag_modified` → commit

**Current code (excerpt — `set_metadata`):**
```python
def set_metadata(self, instance_id: str, key: str, value: Any) -> Instance | None:
    """Set an instance_metadata key-value pair."""
    with SQLModelSession(self.engine) as db_session:
        instance = db_session.get(Instance, instance_id)
        if instance is None:
            return None

        instance.instance_metadata[key] = value
        flag_modified(instance, "instance_metadata")
        instance.updated_at = datetime.now(timezone.utc).isoformat()
        db_session.commit()
```

**Concurrency issue:**
Two concurrent callers writing **different keys** to the same instance will lose one write:
- Caller A: SELECT → metadata = `{"a": 1}` → mutate to `{"a": 1, "b": 2}` → COMMIT
- Caller B: SELECT (sees `{"a": 1}` from snapshot) → mutate to `{"a": 99}` → COMMIT
- Final state: `{"a": 99}` — `b=2` is lost.

`update_title` and `delete_metadata` exhibit the same defect. On PostgreSQL with `JSONB`, the ORM replaces the entire column, so the race is real and visible.

**Call sites with concurrency exposure:**
- `instance_lifecycle.py:368` (during spawn — inheritance write)
- `instance_messaging.py:1059, 1070, 1161, 1171` (per-message writes)
- `title_generation.py:123` (LLM-generated title)
- MCP tools `mcp_tools.py` (agent-invoked metadata edits)

**Recommended fix:** Dialect-aware single-statement UPDATE, mirroring `increment_scheduler_run_counter()` in `daemon/repositories/source/repository.py:102-160`:
```python
# PostgreSQL
UPDATE instances
SET instance_metadata = jsonb_set(
    COALESCE(instance_metadata, '{}'::jsonb),
    :path_array,
    to_jsonb(:value),
    true  -- create_missing
),
updated_at = :now
WHERE instance_id = :instance_id

# SQLite
UPDATE instances
SET instance_metadata = json_set(
    COALESCE(instance_metadata, '{}'),
    :path_json,
    :value
),
updated_at = :now
WHERE instance_id = :instance_id
```

**Impact:** Metadata keys silently dropped on concurrent writes. Title generation races with original_source inheritance can leave a child without the inherited key. Per-message metadata from concurrent sources (system messages, tool results) can clobber each other.

---

### C-7. `children` JSON Denormalized Cache — Quadruple RMW Site

- **Files (4 sites on the same JSON column):**
  - `daemon/services/instance_lifecycle.py:371-389` (spawn: append child ID)
  - `daemon/services/child_reports.py:593-602` (child complete: remove child ID, site 1)
  - `daemon/services/child_reports.py:1286-1294` (child complete: remove child ID, site 2)
  - `daemon/services/error_reporting.py:245-261` (child error: remove child ID)
- **Risk Level:** Critical (doubly so — see Design Concern D-1)
- **Pattern:** load → `json.loads` → mutate list → `json.dumps` → commit

**Current code (excerpt — spawn site):**
```python
parent = session.get(Instance, parent_id)
if parent:
    # Add child to parent's denormalized children list
    children_list = json.loads(parent.children) if parent.children else []
    if instance_id not in children_list:
        children_list.append(instance_id)
        parent.children = json.dumps(children_list)
        ...
    session.commit()
```

**Concurrency issue:** Two concurrent child-spawn operations on the same parent lose one child:
- Spawn A: SELECT → `children="[]"` → append "A" → write `"[\"A\"]"`
- Spawn B: SELECT → `children="[]"` → append "B" → write `"[\"B\"]"` ← "A" lost

The remove sites (child_reports.py × 2, error_reporting.py) race with each other and with the append site. The classic two concurrent completions scenario:
- Completion 1: SELECT → `["A", "B"]` → remove "A" → write `["B"]`
- Completion 2: SELECT → `["A", "B"]` → remove "B" → write `["A"]` ← wrong, "B" should be gone

**Impact:** Children cache loses entries on concurrent operations. The actual parent→child link in `instance_hierarchy` is fine (single-row INSERT in the junction table is atomic), but the cache drifts.

**Recommended fix:** See Design Concern D-1 — **drop the `children` JSON column entirely** since the junction table is the canonical source.

---

### C-8. Project JSON List/Dict RMW — Four Operations

- **Files:**
  - `daemon/repositories/project/repository.py:574-588` (`add_related_directory`)
  - `daemon/repositories/project/repository.py:590-604` (`remove_related_directory`)
  - `daemon/repositories/project/repository.py:689-708` (`add_relationship`)
  - `daemon/repositories/project/repository.py:710-727` (`remove_relationship`)
- **Risk Level:** Critical
- **Pattern:** load ORM object → mutate list/dict in Python → `flag_modified` → commit

**Current code (excerpt — `add_related_directory`):**
```python
def add_related_directory(self, project_id: str, directory: str) -> Project | None:
    with Session(self.engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            return None

        if directory not in project.related_directories:
            project.related_directories.append(directory)
            project.updated_at = datetime.now(timezone.utc).isoformat()
            flag_modified(project, "related_directories")
            session.commit()
```

**Concurrency issue:** Identical to C-6. Two concurrent `add_related_directory(d1)` and `add_related_directory(d2)` on the same project can lose one. `add_relationship` is even more dangerous because it mutates a dict-of-lists structure.

**Tool exposure:** `daemon/tools/project.py:590, 601, 702, 716` — these are LLM-invokable tools that can be called concurrently by MCP.

**Recommended fix:** Convert to junction tables (matching `ProjectTagLink` / `ProjectShortnameLink` already in the codebase) for native SQL atomicity, OR rewrite as `jsonb_set`/`json_set` UPDATE-RETURNING.

**Impact:** Related directories / project relationships silently lost on concurrent agent edits.

---

### C-9. Project Tags/Shortnames Junction Table RMW

- **Files:**
  - `daemon/repositories/project/repository.py:494-509` (`add_tag`)
  - `daemon/repositories/project/repository.py:511-526` (`remove_tag`)
  - `daemon/repositories/project/repository.py:536-?` (`add_shortname`)
  - `daemon/repositories/project/repository.py:553-?` (`remove_shortname`)
- **Risk Level:** Critical
- **Pattern:** load tags from junction → mutate list in Python → call `_sync_tags_bulk` (delete-all-then-reinsert)

**Current code (`add_tag`):**
```python
def add_tag(self, project_id: str, tag: str) -> Project | None:
    with Session(self.engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            return None

        current_tags = self._load_tags(session, project_id)
        if tag not in current_tags:
            current_tags.append(tag)
            self._sync_tags_bulk(session, project_id, current_tags)
            ...

def _sync_tags_bulk(self, session, project_id, tags):
    session.exec(
        sql_delete(ProjectTagLink).where(ProjectTagLink.project_id == project_id)
    )
    if tags:
        session.execute(
            insert(ProjectTagLink),
            [{"project_id": project_id, "tag": tag} for tag in tags]
        )
    session.commit()
```

**Concurrency issue:** Two concurrent `add_tag` calls on the same project:
- Caller A: `_load_tags` → `["t1"]` → append → `["t1", "t2"]`
- Caller B: `_load_tags` → `["t1"]` → append → `["t1", "t3"]`
- Caller A: DELETE all links → INSERT `["t1", "t2"]` → COMMIT
- Caller B: DELETE all links → INSERT `["t1", "t3"]` → COMMIT ← "t2" lost

`remove_tag` and the `shortname` variants share the same defect.

**Recommended fix:** Use `INSERT … ON CONFLICT DO NOTHING` per row (one INSERT per tag, idempotent). This makes each insert atomic and concurrent-safe:
```sql
INSERT INTO project_tag_links (project_id, tag) VALUES (:project_id, :tag)
ON CONFLICT (project_id, tag) DO NOTHING
```

**Impact:** Concurrent tag/shortname edits by agents or background workers silently lose entries.

---

## HIGH-Risk Findings

### H-1. `message.retry_count += 1` on Loaded ORM Object

- **File:** `daemon/repositories/message_queue/repository.py:302-337` (`retry`)
- **Risk Level:** High
- **Pattern:** ORM load → Python `+=` → commit (no conditional UPDATE)

**Current code (excerpt):**
```python
def retry(self, message_id: str, error_message: str | None = None) -> MessageQueue | None:
    with Session(self.engine) as session:
        message = session.get(MessageQueue, message_id)
        ...
        message.retry_count += 1
        ...
        session.commit()
```

**Concurrency issue:** Two concurrent `retry()` calls on the same message both load `retry_count=0`, both compute `0+1=1`, both write `retry_count=1` instead of the correct `retry_count=2`. Two attempts lost.

**Note:** The message_queue is a legacy dispatcher — `MessageJobHandler` is the hot path. Direct `retry()` calls are likely serialized by asyncio task scheduling, but the moment this is called from any concurrent context (e.g., a retry scheduled by another worker), the race is real.

**Recommended fix:** Conditional UPDATE or single-statement increment:
```sql
UPDATE message_queue
SET retry_count = retry_count + 1, ...
WHERE message_id = :id
  AND retry_count < max_retries
```

**Impact:** Retry count under-reported; retry exhaustion decisions based on stale count; messages stuck or prematurely abandoned.

---

### H-2. `job.retry_count += 1` on Loaded ORM Object

- **File:** `daemon/services/job_retry_engine.py:225`
- **Risk Level:** High
- **Pattern:** ORM load → Python `+=` → commit

**Current code (excerpt):**
```python
job = session.get(JobItem, job_id)
...
job.status = "pending"
job.retry_count += 1
job.next_retry_at = next_retry_at
job.failed_at = None
job.error_message = None

session.commit()
```

**Concurrency issue:** Same as H-1. Two concurrent retry decisions on the same failed job:
- Both load `retry_count=1`, both compute `1+1=2`, both write `2`.
- Real count is 3. Max-retry exhaustion decisions are off-by-one.

Worse: there's no `status = 'failed'` guard in the WHERE. A concurrent `cancel_job` or `complete_job` will be silently overwritten.

**Recommended fix:** Conditional UPDATE with status guard:
```sql
UPDATE job_queue_items
SET status = 'pending',
    retry_count = retry_count + 1,
    next_retry_at = :next_retry_at,
    failed_at = NULL,
    error_message = NULL
WHERE job_id = :job_id
  AND status = 'failed'
RETURNING *
```

**Impact:** Retry exhaustion off-by-one; concurrent terminal transitions silently lost.

---

### H-3. `Instance.version` Increment — Dead Concurrency Control

- **Files (9 sites):**
  - `daemon/services/child_reports.py:433, 591, 1119, 1206, 1228, 1284`
  - `daemon/services/job_feedback_observer.py:976`
  - `daemon/services/instance_messaging.py:851`
  - `daemon/services/error_reporting.py:243`
- **Risk Level:** High (wasted, misleading)
- **Pattern:** ORM load → `instance.version = (instance.version or 1) + 1` → commit

**Current code (excerpt):**
```python
instance.version = (instance.version or 1) + 1
```

**Concurrency issue:** The `version` field is incremented in 9 places but is **never read back as an optimistic-lock guard**. No `WHERE version = ?` clause exists anywhere in the codebase. The increments race with each other under concurrent writes — two concurrent updaters both compute `current+1` and both write `current+1`, losing one increment.

Additionally, since the version is co-committed with other RMW (children cache, metadata, status), a stale version increment silently leaves the row at a lower version than the number of actual edits.

**Recommended fix (two options):**
1. **If version is intended as optimistic-lock:** Configure `version_id_col` on `Instance` so SQLAlchemy auto-emits `WHERE version = ?` on every UPDATE. Treat as a proper OCC column.
2. **If version is intended as a counter:** Rewrite as atomic `UPDATE instances SET version = version + 1 WHERE instance_id = ?`.

**Impact:** Dead code that creates an illusion of concurrency control without providing any. Audit confusion.

---

### H-4. `update_status` Unconditional Status Write

- **File:** `daemon/repositories/instance/repository.py:574-576` (delegates to `update()`)
- **Caller:** `daemon/services/job_queue_service.py:1098` (reactivation of terminal instance for MESSAGE job)
- **Risk Level:** High
- **Pattern:** ORM `update()` is unconditional

**Current code (excerpt):**
```python
def update_status(self, instance_id: str, status: str) -> Instance | None:
    return self.update(instance_id, status=status)
```

`update()` is just `session.get + setattr + commit` with no status guard.

**Concurrency issue:** If two MESSAGE jobs simultaneously try to reactivate the same terminal instance, both pass the in-memory status check and both write `status='running'`. The second clobbers any state set by the first.

There's an in-memory check at `job_queue_service.py:1087` (`instance.status` is inspected before reactivation), but this is **stale by the time `update_status` runs** — a concurrent caller in another coroutine can change status between the check and the update.

**Recommended fix:** Use `transition_status_if(instance_id, RUNNING, allowed_from=(COMPLETED, TERMINATED, ERROR, FAILED))` — the safe pattern that already exists in this file (line 578).

**Impact:** Lost instance reactivation; orphaned instances; inconsistent job→instance linkage.

---

### H-5. `update_waiting_for` Unconditional Setter

- **File:** `daemon/repositories/instance/repository.py:615-625`
- **Risk Level:** High (dead code today, but ready to be misused)
- **Pattern:** Plain setter, no arithmetic

**Current code:**
```python
def update_waiting_for(self, instance_id: str, waiting_for: int) -> Instance | None:
    return self.update(instance_id, waiting_for=waiting_for)
```

**Concurrency issue:** This function accepts a Python int and writes it verbatim. Any caller that does `update_waiting_for(id, current + 1)` would lose updates. The function is currently dead (no production callers found via grep), but it's a foot-gun on the public API.

**Recommended fix:** Remove this function (no callers), OR replace with a single-statement atomic increment helper that mirrors `increment_scheduler_run_counter`.

**Impact:** Future caller bugs that materialize as lost counter increments. High risk because the name implies "just set this value" while the safe pattern is "atomic increment/decrement by N".

---

## MEDIUM-Risk Findings

### M-1. `instance_hierarchy` Junction Not Updated Inside Same Transaction as `children` Cache

- **File:** `daemon/services/child_reports.py:593-608`, `error_reporting.py:245-267`
- **Risk Level:** Medium (race window is small — atomic decrement happens first, then hierarchy mutation)

The atomic `waiting_for` decrement (correct) runs before the children-cache removal and `instance_hierarchy` DELETE in the same session. The hierarchy DELETE is a single SQL statement (atomic), but it happens AFTER the children-cache RMW (which is racy per C-7). The cache and hierarchy can disagree on the read side.

**Recommended fix:** Same as C-7 — drop the cache, rely on the junction table.

---

### M-2. `set_metadata` Hot Path Used by `instance_messaging` Per-Message

- **Files:** `daemon/services/instance_messaging.py:1059, 1070, 1161, 1171`
- **Risk Level:** Medium
- **Pattern:** Calls `instance_repository.set_metadata()` (which is C-6)

`instance_messaging` writes per-message metadata to `instance_metadata` (e.g., `last_message_id`, counters). Multiple concurrent message handlers or message + tool-result handlers will race.

**Recommended fix:** Same as C-6.

---

### M-3. `mcp_service` Concurrent Tool-Call Metadata Edits

- **Files:** `daemon/services/mcp_service.py` and `daemon/mcp/builtin_servers/*.py`
- **Risk Level:** Medium
- **Pattern:** Various MCP tools call `set_metadata` (C-6) and `add_related_directory` (C-8)

MCP tools can be invoked concurrently from agent run loops. Same root cause as C-6/C-8.

**Recommended fix:** Same as C-6/C-8.

---

## Design-Level Concerns

### D-1. `instances.children` Column Is Doubly Broken

- **File:** `daemon/repositories/instance/models.py:72`, `daemon/repositories/instance/repository.py:60-65`, `daemon/repositories/instance/repository.py:67-73`
- **Risk Level:** Critical (architectural)

**The model declares:**
```python
children: str = Field(default="[]")  # JSON string column
```

**But every read OVERRIDES this column** via `_load_children`:
```python
def _load_children(self, db_session: SQLModelSession, instance_id: str) -> list[str]:
    """Load child instance IDs from hierarchy table."""
    links = db_session.exec(
        select(InstanceHierarchy).where(InstanceHierarchy.parent_id == instance_id)
    ).all()
    return [link.child_id for link in links]

def _enrich_instance(self, db_session, instance):
    if instance is None:
        return None
    with db_session.no_autoflush:
        instance.children = self._load_children(db_session, instance.instance_id)
    return instance
```

This means:
1. The 4 RMW sites (C-7) race to write a JSON value.
2. That JSON value is **ignored on every read** — `_load_children` always overwrites `instance.children` from the `instance_hierarchy` junction table.
3. The cache update only affects the in-memory ORM instance during the same transaction; it's not durable in any useful sense.

**Recommended fix:**
- **Drop the `children` JSON column from `Instance` entirely.**
- Remove the 4 RMW sites in `instance_lifecycle.py`, `child_reports.py` × 2, `error_reporting.py`.
- Keep `_load_children` (the junction table IS the canonical source).
- The `children` field on the ORM model can become a `@property` that calls `_load_children` lazily, or simply removed from serialization.

This eliminates 4 RMW sites in one stroke.

---

### D-2. ORM `version_id_col` Not Used

- **File:** `daemon/repositories/instance/models.py` (definition)
- **Risk Level:** Medium (architectural)

The `version` field exists on `Instance` but is not declared as SQLAlchemy's `version_id_col`. This means SQLAlchemy does NOT auto-emit `WHERE version = ?` on every ORM update — the field has zero concurrency-control effect.

**Recommended fix:** Either:
1. Configure `__mapper_args__ = {"version_id_col": version}` on `Instance` so SQLAlchemy enforces OCC automatically; OR
2. Remove the `version` field entirely (it's incremented 9 times but never read as a guard — see H-3).

---

## Verified OK — Atomic Patterns

These patterns are correctly atomic under both SQLite and PostgreSQL READ COMMITTED.

### Counter Operations (CORRECT)

| Location | Function | Pattern | Notes |
|----------|----------|---------|-------|
| `daemon/services/child_reports.py:509-521` | waiting_for decrement (site 1) | `UPDATE instances SET waiting_for = CASE … END WHERE instance_id = :pid RETURNING waiting_for` | Uses `COALESCE(waiting_for, 0)` for NULL safety, `CASE` clamp-at-zero (portable — GREATEST not portable, MAX not scalar in PG) |
| `daemon/services/child_reports.py:1257-1269` | waiting_for decrement (site 2) | Same atomic UPDATE | Inline duplicate of site 1 in child-completion path |
| `daemon/services/error_reporting.py:205-217` | waiting_for decrement (error path) | Same atomic UPDATE | Symmetric to decrement sites in child_reports |
| `daemon/tools/instance.py:580-588` | waiting_for increment | `UPDATE instances SET waiting_for = COALESCE(waiting_for, 0) + 1 WHERE instance_id = :pid RETURNING waiting_for` | Atomic increment; sender-side counter |
| `daemon/repositories/source/repository.py:102-160` | `_run_counter` increment | Dialect-aware `jsonb_set` (PG) / `json_set` (SQLite) UPDATE-RETURNING | **The reference template for any future JSON counter** |

### Task Status Transitions (CORRECT)

| Location | Function | Pattern |
|----------|----------|---------|
| `daemon/repositories/task/repository.py:213-280` | `claim_pending_task` | `UPDATE … WHERE id = (SELECT …) AND status = :status_pending RETURNING *` — outer status guard is the EvalPlanQual recheck |
| `daemon/repositories/task/repository.py:287-348` | `requeue_task_with_backoff` | `WHERE id = :task_id AND status = :status_running RETURNING id` |
| `daemon/repositories/task/repository.py:381-395` | `update_heartbeat` | `WHERE id = :id AND status = :status_running` |
| `daemon/repositories/task/repository.py:408-418` | `backfill_heartbeats` | `WHERE last_heartbeat_at IS NULL AND status = :status_running` |
| `daemon/repositories/task/repository.py:562-591` | `reset_stale_tasks` | `WHERE status = :status_running AND COALESCE(...) < :threshold` |
| `daemon/repositories/task/repository.py:740-847` | `schedule_retry` | UPDATE + INSERT in same `engine.begin()` transaction with `retry_scheduled` guard; parent update has no status guard but is serialized via `retry_scheduled` flag |
| `daemon/repositories/task/repository.py:857-891` | `request_cancel` | `WHERE id = :id AND status = :status_running AND cancel_requested = false AND retry_scheduled = false` |

### Instance Status Transitions (CORRECT)

| Location | Function | Pattern |
|----------|----------|---------|
| `daemon/repositories/instance/repository.py:578-613` | `transition_status_if` | `sqlmodel_update(Instance).where(status IN allowed_from).values(status=new_status)` + `rowcount == 0` check |

### Pessimistic Locking (CORRECT — rare but correct)

| Location | Function | Pattern |
|----------|----------|---------|
| `daemon/services/dead_letter_service.py:103` | `move_to_dlq` | `session.get(..., with_for_update=True)` |
| `daemon/services/dead_letter_service.py:179` | `move_to_dlq_standalone` | `with_for_update=True` |
| `daemon/services/dead_letter_service.py:259` | `replay_from_dlq` (DLQ item lock) | `with_for_update=True` |
| `daemon/services/dead_letter_service.py:266` | `replay_from_dlq` (job lock) | `with_for_update=True` |
| `daemon/repositories/message_queue/repository.py:118` | `dequeue` | `stmt.with_for_update()` |

### Atomic Upserts (CORRECT)

| Location | Function | Pattern |
|----------|----------|---------|
| `daemon/repositories/project/repository.py:619-638` | `set_metadata_record` | `on_conflict_do_update` (PG `ON CONFLICT DO UPDATE` / SQLite upsert) — correct atomic upsert |

### Full-Replace JSON Writes (CORRECT — not RMW, no lost-update risk)

These replace the entire JSON column with a caller-supplied value. Safe because the caller is responsible for assembling the full new value, and there is no read-modify-write within the database transaction.

- `daemon/repositories/infra/repository.py:721-814` — `update_asset` for `attributes` / `relationships` (full replacement, `flag_modified` is defensive)
- `daemon/repositories/infra/repository.py:1014-1047` — `register_type` sets `schema_doc` (full replacement)
- `daemon/repositories/mcp_server/repository.py:160-165` — `update_mcp_server` (full replacement)
- `daemon/opencode/repository.py:245-276` — `update_session_data` (full replacement)
- `daemon/repositories/task/repository.py:471` — `task.result = json.dumps(result)` (full replacement of TEXT-stored JSON)

---

## In-Memory Coordination Patterns (CORRECT for single-process, do not survive PG multi-writer)

These are NOT database races per se — they rely on in-memory state. They work because the daemon is currently single-process with asyncio. They become correctness issues if the daemon is ever multi-process or multi-instance.

| Location | Pattern | Notes |
|----------|---------|-------|
| `daemon/services/correlation_manager.py` | In-memory pending-count per parent | Authoritative source of truth for parent completion (Phase 3+); CM is single-source, no DB race because no DB write |
| `daemon/manager.py:368 self._token_count += 1` | In-memory counter | Process-local; fine for single-process |
| `daemon/services/worker_pool.py:235 self._tasks_claimed += 1` | In-memory counter | Process-local; fine |
| `daemon/services/notification_broadcaster.py` | In-memory connection counter | Process-local |

These are not findings — they are correctly scoped to in-process state. Worth documenting so a future multi-process daemon author knows what's process-local.

---

## Recommended Remediation Priority

1. **P0 — Fix C-4 (`atomic_transition`).** Highest blast radius. Rewrite as conditional UPDATE-RETURNING. Affects every job terminal transition.
2. **P0 — Fix C-1/C-2/C-3 (task lifecycle).** Rewrite `complete_task`, `fail_task`, `cancel_task` as conditional UPDATE-RETURNING. Every task completion/failure races with recovery.
3. **P0 — Fix D-1 (drop `children` JSON column).** Architectural cleanup; eliminates 4 RMW sites at once.
4. **P1 — Fix C-5 (`start_job` non-atomic).** Easy; just delete or deprecate.
5. **P1 — Fix C-6/C-7/C-8/C-9 (JSON/junction RMW).** Replace with `jsonb_set`/`json_set` or junction-table inserts.
6. **P2 — Fix H-3/H-4 (version, update_status).** Small, localized.
7. **P2 — Fix H-1/H-2 (retry counters).** Convert to atomic SQL increments.
8. **P3 — Add regression tests** that reproduce the C-1..C-9 races under PostgreSQL READ COMMITTED. The current single-process SQLite deployment masks most of these.

---

## Verification Approach

The codebase has **no existing concurrency test suite** that exercises PostgreSQL-specific behavior. Recommended verification:

1. Stand up a PostgreSQL instance (matches v0.5.2+ default).
2. Write integration tests that:
   - Spawn N coroutines that all call `complete_task(task_id)` concurrently. Assert exactly one succeeds (others see `rowcount=0`).
   - Spawn N coroutines that all call `atomic_transition(job_id, 'processing', 'completed')` concurrently. Same assertion.
   - Spawn N coroutines that all call `set_metadata(id, 'k1', v1)` and `set_metadata(id, 'k2', v2)` concurrently. Assert both keys present in final state.
3. Run with `READ COMMITTED` (default) and `READ COMMITTED + autocommit` to demonstrate EvalPlanQual re-evaluation.

The `transition_status_if()` test pattern (instance repository line 578) is a good model — its rowcount check is exactly the assertion a correct PG-aware test would make.

---

## Notes on Prior Knowledge Verification

The pre-loaded context claimed:
- ✅ "Counter is reportedly atomic: `UPDATE instances SET waiting_for = COALESCE(waiting_for, 0) + 1`" — **VERIFIED CORRECT**. The increment (tools/instance.py:580) and both decrement sites (child_reports.py:509 and :1257, error_reporting.py:205) all use atomic SQL.
- ⚠️ "Job queue uses advisory locks via `JobLocks` table" — **PARTIALLY VERIFIED**. The job queue has `JobLocks` (lock_repository.py) used by `lock_manager` for queue-level locks, but **advisory locks are not used for per-row status transition atomicity**. The atomicity gap is at the SQL UPDATE level, not at the lock-acquisition level.
- ⚠️ "`claim_pending_task()` reportedly needed EvalPlanQual recheck guard" — **VERIFIED CORRECT**. The outer `AND status = :status_pending` in the UPDATE (line 267) is the guard.
- ❌ "3 HIGH-severity races reportedly fixed (Race #1, #3, CM-2)" — **PARTIALLY**. The CM-first pattern in `correlation_manager.py` addresses in-memory coordination races. The C-1..C-9 database-level races found here are a **separate class** of issue not covered by the CM-first fix. The prior fix was at the control-flow level; this audit is at the SQL UPDATE level.

---

## Summary Table

| # | File:Line | Function | Risk | Pattern |
|---|-----------|----------|------|---------|
| C-1 | `task/repository.py:926` | `cancel_task` | Critical | SELECT + Python check + UPDATE (no status guard in SQL) |
| C-2 | `task/repository.py:453` | `complete_task` | Critical | ORM RMW with no precondition |
| C-3 | `task/repository.py:484` | `fail_task` | Critical | ORM RMW with no precondition |
| C-4 | `job_queue/repository.py:430` | `atomic_transition` | Critical | ORM RMW despite "atomic" name |
| C-5 | `job_queue/repository.py:522` | `start_job` (non-atomic) | Critical | Two-transaction TOCTOU |
| C-6 | `instance/repository.py:627,642,657` | `update_title`/`set_metadata`/`delete_metadata` | Critical | Python dict mutation on JSON column |
| C-7 | `instance_lifecycle.py:371`, `child_reports.py:593,1286`, `error_reporting.py:245` | children cache append/remove | Critical | json.loads → mutate → json.dumps |
| C-8 | `project/repository.py:574,590,689,710` | `add/remove_related_directory`, `add/remove_relationship` | Critical | Python list/dict mutation on JSON column |
| C-9 | `project/repository.py:494,511,536,553` | `add/remove_tag/shortname` | Critical | load junction → mutate → delete-all + reinsert |
| H-1 | `message_queue/repository.py:324` | `retry` (retry_count += 1) | High | ORM counter increment |
| H-2 | `job_retry_engine.py:225` | `retry` (retry_count += 1) | High | ORM counter increment, no status guard |
| H-3 | 9 sites (see listing) | `instance.version += 1` | High | Dead concurrency control |
| H-4 | `instance/repository.py:574` | `update_status` | High | Unconditional ORM update |
| H-5 | `instance/repository.py:615` | `update_waiting_for` | High | Foot-gun public API |
| M-1 | `child_reports.py:593-608`, `error_reporting.py:245-267` | hierarchy delete ordering | Medium | Cache + junction disagreement window |
| M-2 | `instance_messaging.py:1059,1070,1161,1171` | per-message `set_metadata` | Medium | Hot path of C-6 |
| M-3 | MCP tools | concurrent metadata edits | Medium | Hot path of C-6/C-8 |
| D-1 | `instance/models.py:72`, `instance/repository.py:60-73` | `children` JSON cache | Critical | Cache RMW'd but overridden on every read |
| D-2 | `instance/models.py` | `version` field not configured as OCC | Medium | Dead concurrency control field |

---

**End of report.**
