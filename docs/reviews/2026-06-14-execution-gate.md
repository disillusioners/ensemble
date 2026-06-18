# Code Review: Execution Gate

> **Review artifact (2026-06-14).** This is the code review of the initial ExecutionGate implementation. The required follow-ups (heartbeat wiring, LeaseLostError detection) were addressed in subsequent work. The gate is now required on ALL paths including resume (Phase 0 / Race #5 fix). For the current architecture, see [`../architecture/message-processing-and-correlation.md`](../architecture/message-processing-and-correlation.md).

- **Commits reviewed:** `6c11c2a` (feat), `c1aae71` (fix)
- **Reviewed at:** 2026-06-14
- **Reviewer:** kilo (claude code)
- **Verdict:** Approved with required follow-up

## Summary

The Execution Gate introduces a DB-backed per-instance lease
(`instance_execution_leases`) as the single chokepoint for
`graph.astream`. It serialises the two physical dispatchers
(`MessageJobHandler` and `ProcessMessageProcessor`) so they can no
longer call `graph.astream` concurrently for the same instance. The
fix commit (`c1aae71`) addresses review feedback on stale-lease
recovery and contention handling.

The implementation is well-structured, the SQL safety properties
(atomicity, holder-conditional release) are correctly implemented and
tested, and there is a dedicated integration test that reproduces the
original bug at the service level.

There is **one real correctness gap** that must be fixed before this
lands in production: the lease heartbeat is exposed but never called
by any caller, so a `graph.astream` call longer than the 5-minute
stale-lease threshold can be evicted by `recover_stale_leases` on
another node mid-execution. The fix for the original dual-dispatcher
race has introduced a new "long astream = lost lease" failure mode.

## Findings

### CRITICAL — Lease heartbeat is never called

**File:** `daemon/services/execution_gate.py:486-501`

`ExecutionGateService.heartbeat` exists and the recovery predicate
(`execution_lease/repository.py:247`) uses
`COALESCE(heartbeat_at, acquired_at) < :cutoff`, but **no caller
invokes `gate.heartbeat`**. The docstring at line 487-490 explicitly
admits this: "not required for correctness of acquire/release under
the current 'one call acquires, one call releases' model".

The constructor default `DEFAULT_STALE_LEASE_SECONDS = 300` (line 162)
is the wall-clock upper bound on a single `graph.astream` call. If
`graph.astream` legitimately runs for longer than 5 minutes and the
holder never calls `heartbeat` — which is the current state — the
lease is treated as stale by the next `recover_stale_leases` run on
any node. The lease row is deleted, the worker's `release` becomes a
no-op (which is fine), and a different process can `try_acquire` and
start a parallel `graph.astream` for the same instance — reintroducing
the exact race the gate is designed to prevent.

**Required fix:** wire the existing `task_heartbeat_interval_seconds`
thread (or the equivalent for the JobQueue path) to also call
`gate.heartbeat` while work is in flight. The lease heartbeat should
be at least 2-3x more frequent than the stale threshold.

### CRITICAL — Docstring promises `LeaseLostError` but it is never raised

**File:** `daemon/services/execution_gate.py:124-131, 254-257`

```python
class LeaseLostError(Exception):
    """Raised inside ``gate.run`` if the caller lost the lease mid-execution.
    This happens when ``recover_stale_leases`` (or any other code path)
    deletes the lease out from under the holder — e.g. a process crash
    recovery loop on a different node. The caller should treat this as
    a transient error and let the dispatcher decide whether to re-queue.
    """
```

```python
# In run() docstring:
# - If the lease is somehow lost mid-execution (e.g.
#   ``recover_stale_leases`` evicted the row), ``work_fn`` is
#   cancelled and ``LeaseLostError`` is raised. The
#   dispatcher treats this as a transient error.
```

`_execute_under_lease` (lines 329-359) does not check for lease loss
and does not raise `LeaseLostError`. The class is defined but has
zero call sites.

The mechanism to detect lease loss would require `heartbeat` to be
called from inside the work loop (which connects to the previous
finding). Without it, the only thing that can cause the row to
disappear mid-execution is `recover_stale_leases` on another node,
and the local process has no way to know.

**Required fix:** either implement lease-loss detection (heartbeat
before/after the work, raise `LeaseLostError` if heartbeat returns
False), or remove the `LeaseLostError` class and the corresponding
docstring claim.

### HIGH — `_find_running_task_for_instance` pass-through is dead weight

**File:** `daemon/services/message_job_handler.py:381-390`

The commit moved the SQL for the cross-dispatcher pre-flight onto
`TaskRepository.find_running_by_instance` (good), but kept a thin
pass-through wrapper on the handler "to preserve existing test-patch
surface". The 4 test files that monkeypatch the wrapper would need to
be updated to patch the repository method instead.

The justification in the commit message ("preserves existing test
patch surface") is preserving 2-line wrappers in exchange for ~30
lines of test churn. Not a great trade. The wrapper also has a
silent no-op if `self._manager._task_repo` is missing in a
misconfigured env, losing the optimisation and any diagnostics.

**Recommended fix:** delete the wrapper, patch the repository method
in the 4 test files, and add a `logger.warning` if `_task_repo` is
missing at call time.

### HIGH — `requeue_task` (no backoff) has no production caller

**File:** `daemon/repositories/task/repository.py:277-329`

`requeue_task` was added in `6c11c2a` as the unconditional version of
`requeue_task_with_backoff`. The cross-dispatcher contention path
(`daemon/services/task_processor.py:268`) uses only
`requeue_task_with_backoff`. The only callers of `requeue_task` are
the 2 unit tests added in the same commit
(`tests/unit/services/test_execution_gate.py:593, 612`).

`requeue_task_with_backoff` is strictly safer (it sets
`next_retry_at` so the worker does not re-claim the same task
immediately, which is exactly the problem the gate is solving). The
"no backoff" semantics has no documented caller.

**Recommended fix:** delete `requeue_task` and its 2 unit tests. If
a no-backoff variant is ever needed, add it then.

### MEDIUM — `LeaseHolderKind.RESUME` has no caller

**File:** `daemon/repositories/execution_lease/models.py:44-49`

```python
class LeaseHolderKind(str, enum.Enum):
    MESSAGE_JOB = "message_job"
    TASK = "task"
    RESUME = "resume"  # <-- no caller uses this
```

The Postgres CHECK constraint in
`daemon/manager.py:1462-1470` and the SQLite migration both allow
`'resume'`, but no code path produces a resume lease. If this is
planned for an upcoming feature, leave a TODO referencing the planned
use site. If not, drop the enum member and narrow the CHECK to
`('message_job', 'task')`.

### MEDIUM — `LeaseContention` is a dataclass, not a sentinel

**File:** `daemon/services/execution_gate.py:110-121`

`LeaseContention` is returned as the result of `gate.run` when the
lease is held by someone else. Dispatchers check
`isinstance(gate_outcome, LeaseContention)` to discriminate. If
`work_fn` ever returns a value that happens to be a `LeaseContention`
dataclass, the type check will misfire.

In current code, `work_fn` returns `MessageResult` (a pydantic
model), so the collision is impossible. But the contract is
fragile. The docstring on `run` should explicitly warn: "if your
work_fn returns a LeaseContention, it will be misinterpreted as
contention." Two hardening options: make `LeaseContention` a
`BaseException` subclass, or wrap results in a sum type.

### MEDIUM — `LeaseContention.holder_id` is `str` but can be `""`

**File:** `daemon/services/execution_gate.py:301-306`

The "vanishingly rare: holder released between failed acquire and
get_holder" branch returns a `LeaseContention` with `holder_id=""`,
`holder_kind=""`, `acquired_at=None`. A caller that interprets
`holder_id` as a non-empty string (e.g. to construct a log message
or a re-queue reason) may produce confusing output.

**Recommended fix:** make this an `Optional[LeaseContention]` return
or add a `holder_lost_during_contention: bool` flag so callers can
distinguish "I lost, here's who beat me" from "I lost, nobody's
there."

### MEDIUM — N+1 in `recover_stale_leases`

**File:** `daemon/services/execution_gate.py:426-435`

The recovery does `find_stale_leases` (1 query) then per-row
`clear_stale` (1 query each). For a daemon that crashed 50 instances
in flight, this is 51 round-trips. Since the threshold is 5 minutes
and the Gate is the new owner of this crash-recovery path, this will
likely fire only on a true multi-crash event. A single
`DELETE WHERE COALESCE(heartbeat_at, acquired_at) < :cutoff` with
the same predicate is preferable.

### MEDIUM — Verbose INFO log on every contention

**File:** `daemon/services/task_processor.py:256-261`

```python
logger.info(
    f"ProcessMessageProcessor: lease contention for task {task.id} "
    f"instance={task.instance_id[:8]}... "
    f"(holder_id={gate_outcome.holder_id} "
    f"holder_kind={gate_outcome.holder_kind}) — re-queuing with backoff"
)
```

If 100s/sec of tasks contend against one busy MESSAGE job, this log
could flood. Use `logger.debug` and add a per-instance summary
counter at INFO.

### LOW — `recover_stale_leases_sync` only used by tests

**File:** `daemon/services/execution_gate.py:450-483`

The only callers of the sync wrapper are 2 unit tests
(`tests/unit/services/test_execution_gate.py:560, 573`). The fix
commit moved the production caller (`api.py:182`) to the async
version. The "diagnostic scripts" use case is hypothetical.

**Recommended fix:** delete `recover_stale_leases_sync` and the 2
tests.

### LOW — `gate_raised` / `gate_outcome` dual-variable control flow

**File:** `daemon/services/message_job_handler.py:154-185`

The code stashes exceptions in `gate_raised` so the existing
`except OperationCancelledError / asyncio.CancelledError / Exception`
clauses below can still run unchanged. The intent is documented in
lines 147-153, which is good. But the resulting control flow (`if
gate_raised is None: ... else: raise gate_raised`) is harder to read
than a `try/except` around `gate.run` that translates the result.

The 2 commits deliberately preserve the existing `except` clauses —
fair trade for not touching tested paths, but worth a follow-up TODO
to refactor into a cleaner state machine.

### LOW — Postgres DDL duplicates the migration

**File:** `daemon/manager.py:1454-1478` vs
`daemon/migrations/versions/20260614_000002_create_instance_execution_leases.sql`

The table is created inline in `_ensure_postgres_columns` for
Postgres (the migration runner is SQLite-only). The two definitions
must stay in sync. The commit message in `_ensure_postgres_columns`
should cross-reference the migration file path explicitly so a
future schema change updates both.

### LOW — `Any` import in `message_job_handler.py` is now used for the gate outcome annotation

**File:** `daemon/services/message_job_handler.py:5, 154`

```python
gate_outcome: Any | LeaseContention | None = None
```

The `Any` import was added in `c1aae71` for this annotation. The
annotation could be tightened to
`MessageResult | LeaseContention | None` to make the contract more
explicit.

## What's good

- The race-scenario integration test
  (`TestCrossDispatcherRaceScenario`) is exactly the right shape —
  it reproduces the original bug at the service level with two
  coroutines and confirms the gate serialises them.
- The holder_id-conditional release invariant has dedicated tests
  with descriptive names (`test_release_with_wrong_holder_id_does_nothing`).
- The "vanishingly rare: holder released between failed acquire and
  get_holder" branch is documented and handled.
- Moving the cross-dispatcher pre-flight to `TaskRepository` is a
  good call; only the handler's pass-through remains as cleanup.
- Adding the dispatch-bus notify to `_requeue_for_contention` is a
  real fix for the 30s poll-interval latency on hot instances.
- The 5-minute stale threshold matches `StaleTaskRecovery`'s existing
  convention.
- The Postgres + SQLite dual-driver pattern is consistent with the
  rest of the codebase.
- The two-commit structure (feature first, then review fixes) is
  easy to bisect.
- The `find_running_by_instance` SQL filter in
  `find_stale_leases` (`COALESCE(heartbeat_at, acquired_at) < :cutoff`)
  is now in SQL, not Python, so it stays cheap as the table grows.

## Required follow-ups

1. **Wire `gate.heartbeat` to a real caller** so that
   `graph.astream` calls longer than 5 minutes cannot be evicted by
   `recover_stale_leases` mid-execution. (CRITICAL)
2. **Implement `LeaseLostError` detection or remove the docstring
   claim** — the class is defined but never raised. (CRITICAL)
3. **Delete `requeue_task` (no-backoff variant)** — superseded by
   `requeue_task_with_backoff`, no production caller. (HIGH)
4. **Delete `_find_running_task_for_instance` pass-through** — dead
   weight, patch the 4 test files to call the repo directly. (HIGH)

## Recommended follow-ups

5. Drop or document the planned use of `LeaseHolderKind.RESUME`.
6. Document the `LeaseContention` non-sentinel behavior in the
   `run` docstring.
7. Add a `holder_lost_during_contention` flag (or `Optional` return)
   for the "holder released between failed acquire and get_holder"
   branch.
8. Collapse `recover_stale_leases` to a single DELETE query.
9. Demote the per-contention `logger.info` to DEBUG with a periodic
   summary.
10. Delete `recover_stale_leases_sync` (only used by tests).
11. Refactor `gate_raised`/`gate_outcome` control flow into a
    cleaner state machine.
12. Cross-reference the Postgres inline DDL with the SQLite
    migration in a comment.
13. Tighten the `gate_outcome` annotation to
    `MessageResult | LeaseContention | None`.
