# Lessons: ensure-validation for Turn Reconciler Increment 4 (FINAL)

**Date:** 2026-08-01
**Branch:** `latest` @ `6564b15e`
**Author:** ensure-validation executor (Worker)

## Lessons Captured

### 1. `tests/message_queue_redesign/` flakiness is a pre-existing concern, NOT an Inc 4 regression

**Symptom:** Three concurrent-claim tests intermittently fail with
`sqlite3.OperationalError: cannot commit transaction - SQL statements in progress`:

- `test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_concurrent_only_one_worker_wins`
- `test_atomic_dequeue.py::TestDequeueAtomicClaim::test_dequeue_with_instance_filter_under_concurrency`
- `test_atomic_status_transitions.py::TestCompleteAtomic::test_complete_concurrent_double_call_only_one_succeeds`

**Repro:** On macOS Apple Silicon (M-series), running the full `tests/message_queue_redesign/` directory 5 times yields 2-3 transient failures of the above 3 tests. The failures reproduce under `pytest` with default `pytest-randomly` order. The 3 tests pass in isolation 100% of the time.

**Why it matters:** The validator's first full-directory run reported `3 failed, 416 passed`, raising a false-alarm "is this a new Inc 4 regression?" concern. Only after running the same suite 5 times and confirming the same tests fail in the same code locations (with no Inc 4 changes in the message queue path) did it become clear the issue is pre-existing flake, not Inc 4 regression.

**Root cause (inferred):** Real-thread + StaticPool + SQLite contention. The `concurrency_atomic_unit_test` pack runs cleanly because its concurrency tests are gated behind `pytestmark` markers; the mq_redesign tests don't gate themselves. Worker pool + Atomic claim + double-call race all run on the same SQLite connection under load.

**Mitigation options (none applied at this commit):**
- Add `pytest.mark.concurrency` marker and run mq_redesign with `-p no:randomly` in CI.
- Add to `QUARANTINE.md` if the 3 tests fail again in Inc 5.
- Refactor the 3 tests to use threading primitives with explicit joins and `engine.dispose()` cleanup.

**Lesson:** Always run a pack 3-5× before flagging a "new failure" — single runs of concurrency-heavy packs are not deterministic. The Inc 3 results file (`RESULTS/2026-08-01-inc3-turn-reconciler-full-regression.md`) reported `tests/message_queue_redesign/` as 419/419 PASS in a single run; that single run was not flake-detected.

### 2. The "in progress" label on quick fixes can be stale by the time the brief is read

**Symptom:** Worker's brief said "Inc 4 new tests (schema/transitions/pause_resume_root) → 56 passed, 30 failures (quick fix in progress)". The "in progress" framing is misleading: by the time the validator reads the brief, the quick fix has usually landed.

**What happened here:** `6564b15e` and `4e82c8c9` (the two quick fixes the worker mentioned) were both already on `latest` HEAD. Re-running the Inc 4 test files at HEAD yields **64/64 PASS in 5.25s** — no 30 failures.

**Lesson:** When a brief says "X failures, quick fix in progress", first re-run the named test files at HEAD to confirm the fix landed. Don't take "in progress" at face value. The fix may have been committed between brief-write and brief-read.

### 3. Static check for `asyncio.to_thread` should be scoped to the repository, not the caller

**Symptom:** The brief's Req 3 grep is:
```
grep -rn "asyncio.to_thread" daemon/repositories/task/repository.py | grep -i "find_paused_or_cancellable_turn|find_suspended_turn_for_answer|reconcile_turn_mirror"
```

This grep returns ZERO matches. A naive reader would think "no threading wrapping = bug". The reality is the opposite: the repository methods are sync SQLAlchemy, and the threading wrapping lives in `daemon/manager.py` (the async caller). The grep should be:

```
grep -rn "asyncio.to_thread" daemon/manager.py daemon/services/ | grep -E "find_suspended_turn_for_answer|find_paused_or_cancellable_turn|reconcile_turn_mirror"
```

**Lesson:** The static check's grep target was wrong. The repository.py grep will always return zero for these methods (they're the lowest layer, by design). Threading wrapping is one layer up.

**Suggested ensure.md rewrite for Req 3 (if user wants to keep it static):**
> "Validation: `grep -rn 'asyncio.to_thread' daemon/manager.py daemon/services/ | grep -E 'find_suspended_turn_for_answer|find_paused_or_cancellable_turn'` shows ≥1 wrapping per selector. Plus: thread-identity tests in `tests/test_deadlock_fix.py` PASS."

### 4. WORKER BRIEF: "1 E2E ×5 failure (quick fix in progress)" needs explicit resolution before merge

**Symptom:** Brief states "E2E ×5 flakiness → 49/50 pass, 1 failure (quick fix in progress)" but the validator cannot re-run E2E (no running daemon). The 1 failure is documented in the brief but not yet resolved on HEAD.

**Lesson:** When a brief mentions an unresolved failure that the next-step executor (validator) cannot re-run, the validator should explicitly call out "this failure is unconfirmed/pending" in the report — not silently include it in the pass count. The final report must surface this as a "pre-merge recommendation: confirm or quarantine before merging".

### 5. Docstring/comment references to deleted primitives are expected and OK

**Symptom:** `find_paused_or_running_by_instance` and `find_resume_root_candidate_by_active_job` still appear in `daemon/manager.py` and `daemon/repositories/task/repository.py`. At first glance, that looks like dead code was not actually deleted.

**Reality:** Every remaining reference is inside a docstring or comment that explains the migration ("Replaces the inference-based `find_paused_or_running_by_instance` lookup with an authoritative handle lookup"). The new selector's docstring cites the old name as the migration target.

**Lesson:** When grepping for "dead code" references, **filter out docstring/comment lines first**. The brief's grep `grep -v "#" | grep -v '"""' | grep -v "docstring"` does this; it just requires careful reading of the remaining matches to confirm they're all doc-only.
