# Phase 3: Integration Tests & Edge Cases

> **Revision 2 (2026-07-18)** — incorporates review items C1 (test both await points), C5 (Scenario B now expects Tier 1 child self-cleanup), C2 (paths), M2 (strict sequential).

## Objective

End-to-end test coverage exercising both registries (proc + bash) together across realistic lifecycle scenarios: two-tier cleanup (Tier 1 self + Tier 2 root sweep), child-then-root ordering, daemon shutdown, cancellation mid-bash at BOTH await points, nohup grandchildren survival, best-effort isolation, and no-op when no processes exist. This phase validates that Phase 1 + Phase 2 compose correctly with no gaps or double-kills.

## Coupling

- **Depends on:** Phase 1 **merged** AND Phase 2 **merged** (strict sequential — D12)
- **Coupling type:** **tight** — tests exercise both registries in the same flows. Cannot run until Phase 2 merges.
- **Shared files with other phases:** None (test-only phase). Tests import from `proc_tools.py`, `bash.py`, `instance.py`, `job_feedback_observer.py`, `daemon/manager.py`.
- **Why this coupling:** Integration tests by definition require both phases' code. Any bug found here may send work back to Phase 1 or Phase 2.

## Context

- Phase 1 shipped: two-tier proc cleanup (Tier 1 always + Tier 2 root-gated) + proc `cleanup_all()` on shutdown.
- Phase 2 shipped: `BashProcessRegistry`, `_make_instance_id_aware` wrapper, eager PGID capture, CancelledError leak fix (both await points), bash two-tier cleanup, bash `cleanup_all()` on shutdown.
- This phase writes **no production code** — only tests. If a test reveals a bug, file it against the relevant phase (or fix here if trivial).

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Integration: Tier 1 self-cleanup on ANY terminal instance | Child instance (parent_id != None) completes. Assert Tier 1 fires: `cleanup_instance(child_id)` called on BOTH registries. Assert Tier 2 does NOT fire (parent_id != None). Sibling procs untouched. | `tests/.../test_auto_kill_integration.py` (new) |
| 2 | Integration: Tier 2 root sweep kills descendants | Root instance completes. Assert Tier 1 cleans root's own procs, Tier 2 calls `get_tree_ids` and cleans EACH descendant (but not root again). | `tests/.../test_auto_kill_integration.py` |
| 3 | Integration: child-then-root ordering (C5 Scenario B) | Root running with 2 children, each with procs. Child 1 completes → Tier 1 cleans child 1's own procs immediately; sibling + root untouched. Then root completes → Tier 1 cleans root + Tier 2 cleans child 2 (child 1 already empty, idempotent no-op). | `tests/.../test_auto_kill_integration.py` |
| 4 | Integration: daemon shutdown kills everything | Populate both registries with processes across 3 instances. Call `manager.shutdown()`. Assert both `cleanup_all()` invoked, both registries empty, all process groups dead. | `tests/.../test_auto_kill_integration.py` |
| 5 | Integration: cancellation mid-bash at WAIT await point (C1) | Start bash with long `sleep`. Cancel the awaiting task at the `wait_for` await point. Assert `_kill_process` called, subprocess dead, `unregister` called, `task.uncancel()` invoked (3.11+), CancelledError propagated to caller. | `tests/.../test_bash_cancel.py` (new) |
| 6 | Integration: cancellation mid-bash at SPAWN await point (C1) | Cancel the task such that cancellation lands AFTER spawn returns but BEFORE the next await (or during spawn). Assert the spawned proc is killed, registry cleaned. Test both: proc assigned (spawn completed) and proc None (spawn cancelled mid-flight). | `tests/.../test_bash_cancel.py` |
| 7 | Integration: nohup/setbackgrounded grandchildren killed | bash runs `bash -c 'nohup sleep 3600 & sleep 0.1'`. Foreground exits, grandchild survives in process group. Trigger root cleanup (Tier 1 or Tier 2). Assert grandchild (in the same pgid) is killed. | `tests/.../test_auto_kill_integration.py` |
| 8 | Integration: no-op when no processes exist | Instance with no proc/bash processes completes. Assert Tier 1 runs cleanly (no exceptions, empty bucket pop), Tier 2 no-op if root. No unnecessary process-group kill attempts. | `tests/.../test_auto_kill_integration.py` |
| 9 | Integration: double-fire idempotency (terminate cascade + finalize) | Trigger `terminate_instance` on root (cascades, does proc cleanup per child at 1461), THEN trigger `_dispatch_instance_post_commit_side_effects` on root. Assert no errors from double cleanup, idempotent (atomic bucket pop). | `tests/.../test_auto_kill_integration.py` |
| 10 | Integration: best-effort failure isolation | (F1) Mock `get_tree_ids` to raise → WARNING logged, terminal completes, SSE/CompletionRegistry still fire, `tree_ids` stays []. (F2) Mock `cleanup_instance(child)` to raise → root + other children still cleaned, child failure logged WARNING, no propagation. | `tests/.../test_auto_kill_integration.py` |
| 11 | Integration: ERROR/FAILED terminal paths trigger Tier 1 + Tier 2 | Root reaches terminal via error path (`_send_error_report`) and failed path (`_finalize_instance`). Both converge on `_dispatch_instance_post_commit_side_effects`. Assert Tier 1 + Tier 2 fire for both. | `tests/.../test_auto_kill_integration.py` |
| 12 | Integration: real subprocess lifecycle (no mocks) | Use real OS subprocesses (e.g., `sleep 30`) via proc and bash tools. Verify via `os.kill(pid, 0)` that PIDs are dead after Tier 1/Tier 2/shutdown sweep. Smoke test mocks can't provide. | `tests/.../test_auto_kill_integration.py` |
| 13 | Integration: `_make_instance_id_aware` injects instance_id | Raw `bash` called with `instance_id=None` → wrapper injects `current_instance_id` from closure. Verify `@tool` args_schema does NOT expose `instance_id`. Verify `instance_id=None` fallback logs WARNING and skips registration. | `tests/.../test_instance_tools.py` |
| 14 | Regression: full pause/resume/cancel suite | Run the complete existing pause/resume/cancel test suite to confirm no regression from Phase 2's CancelledError fix. | existing tests |
| 15 | Regression: full proc tool suite | Run `test_proc_tools.py` to confirm Phase 1's `cleanup_all()` addition didn't break anything. | existing tests |

## Test Scenarios in Detail

### Scenario A — Tier 1 Self-Cleanup on Child Completion (Task 1)

```
Setup:
  root (parent_id=None)
  ├── child1 (parent_id=root) — has proc process proc_pid_c1
  └── child2 (parent_id=root) — has bash grandchild in group pgid_c2

Action: child1 reaches _dispatch_instance_post_commit_side_effects(COMPLETED, parent_id=root)

Assert:
  - parent_id != None → Tier 2 SKIPPED (get_tree_ids NOT called)
  - Tier 1 fires: cleanup_instance(child1) called on proc + bash registries
  - proc_pid_c1 dead (os.kill(proc_pid_c1, 0) raises ProcessLookupError)
  - root's procs STILL ALIVE (Tier 2 didn't run)
  - child2's bash grandchild STILL ALIVE (Tier 2 didn't run)
```

### Scenario B — Tier 2 Root Sweep (Task 2)

```
Setup:
  root (parent_id=None) — has proc process proc_pid_r
  ├── child1 — has proc process proc_pid_c1
  └── child2 — has bash grandchild in group pgid_c2

Action: root reaches _dispatch_instance_post_commit_side_effects(COMPLETED, parent_id=None)

Assert:
  - Tier 1: cleanup_instance(root) called (proc + bash) → proc_pid_r dead
  - Tier 2: get_tree_ids(root) called via to_thread → [root, child1, child2]
  - Tier 2 loop: skip root (already cleaned in Tier 1)
  - Tier 2 loop: cleanup_instance(child1) called → proc_pid_c1 dead
  - Tier 2 loop: cleanup_instance(child2) called → pgid_c2 grandchild dead
  - root NOT double-cleaned (Tier 2 `continue` on instance_id)
```

### Scenario C — Child-Then-Root Ordering (Task 3, C5 Scenario B)

```
Setup: same as Scenario B, all processes running.

Action 1: child1 reaches terminal(COMPLETED, parent_id=root)

Assert (after Action 1):
  - Tier 1 fires for child1: cleanup_instance(child1) called → proc_pid_c1 dead
  - Tier 2 SKIPPED (parent_id != None)
  - root proc (proc_pid_r) STILL ALIVE
  - child2 bash grandchild STILL ALIVE
  - KEY (C5 fix): child1's procs did NOT leak — they were cleaned immediately

Action 2: root reaches terminal(COMPLETED, parent_id=None)

Assert (after Action 2):
  - Tier 1 fires for root: cleanup_instance(root) → proc_pid_r dead
  - Tier 2: get_tree_ids(root) → [root, child1, child2]
  - Tier 2 loop: cleanup_instance(child1) → no-op (already empty, idempotent)
  - Tier 2 loop: cleanup_instance(child2) → pgid_c2 grandchild dead
```

### Scenario D — Cancellation at WAIT Await Point (Task 5, C1)

```
Setup:
  instance runs bash("sleep 3600") — subprocess proc_b, group pgid_b
  registry has entry (instance_id, proc_b.pid, pgid_b)

Action: cancel the task awaiting bash() AT the wait_for await point
        (simulate pause_instance_cascade landing during wait_for)

Assert:
  - asyncio.CancelledError raised inside bash()
  - CancelledError handler: proc is not None → enters cleanup
  - task.uncancel() called (Python 3.11+)
  - asyncio.shield(_kill_process(proc_b)) called
  - subprocess dead (os.kill(proc_b.pid, 0) raises)
  - shield(unregister(instance_id, proc_b.pid)) called → entry removed
  - CancelledError RE-RAISED to caller of bash()
  - registry has no entry for instance_id
```

### Scenario E — Cancellation at SPAWN Await Point (Task 6, C1)

```
Sub-case E1: spawn completed, cancellation lands before next await
  - proc assigned (create_subprocess_* returned)
  - cancel task
  - Assert: handler sees proc is not None → _kill_process + unregister + raise
  - proc dead, entry removed

Sub-case E2: cancellation lands DURING spawn (proc never assigned)
  - cancel task while create_subprocess_* is in flight
  - proc stays None
  - Assert: handler sees proc is None → skip _kill_process, just raise
  - No subprocess to clean (spawn didn't complete)
  - No registry entry (registration is after spawn)
```

### Scenario F — Nohup Grandchild (Task 7)

```
Setup:
  instance runs bash("bash -c 'nohup sleep 3600 & sleep 0.1'")

After bash returns (foreground exited):
  - the nohup'd grandchild is in process group pgid_b (start_new_session)
  - registry STILL has entry (we don't unregister on normal completion — D5)

Action: root reaches terminal → Tier 1 bash cleanup_instance(instance_id)

Assert:
  - os.killpg(pgid_b, SIGKILL) called
  - grandchild dead (os.kill(grandchild_pid, 0) raises)
```

### Scenario G — Double-Fire Idempotency (Task 9)

```
Setup: root with running proc process.

Action 1: terminate_instance(root)
  - cascades to children, each does cleanup_instance at 1461
  - root does cleanup_instance at 1461
  - process dead, registry empty

Action 2: _dispatch_instance_post_commit_side_effects(root, TERMINATED, parent_id=None)
  - Tier 1: cleanup_instance(root) called → bucket already empty → atomic pop returns {}
  - Tier 2: get_tree_ids → children → cleanup_instance(child) → all empty → no-ops

Assert: no exceptions, no errors logged (benign empty-pop)
```

### Scenario H — Best-Effort Isolation (Task 10)

```
Sub-case H1: get_tree_ids raises (Tier 2)
  - root reaches terminal
  - mock get_tree_ids to raise RuntimeError
  - Assert: WARNING logged, tree_ids stays [] (C4)
  - Tier 1 already ran (root's procs cleaned)
  - Tier 2 loop no-op (tree_ids empty)
  - other side-effects (SSE, CompletionRegistry, lifecycle) still fire

Sub-case H2: cleanup_instance raises for one descendant (Tier 2)
  - tree_ids = [root, child1, child2]
  - Tier 1 cleans root (success)
  - Tier 2 loop: mock cleanup_instance(child1) to raise
  - Assert: child1 failure logged WARNING
  - Assert: child2 still cleaned (loop continues)
  - no exception propagates

Sub-case H3: instance_repository missing on manager (Tier 2)
  - mock self._instance_manager to have no _instance_repository attr
  - Assert: WARNING logged, Tier 2 skipped, Tier 1 still ran
```

## Key Files

- `tests/.../test_auto_kill_integration.py` (new) — Scenarios A, B, C, F, G, H, and no-op test (task 8)
- `tests/.../test_bash_cancel.py` (new) — Scenarios D, E in full detail
- `tests/.../test_instance_tools.py` — `_make_instance_id_aware` wrapper test (task 13)
- Existing test files — regression runs (tasks 14, 15)

> **Note on test file location:** The investigation didn't pinpoint the exact test directory structure. The developer should match the existing convention (likely `tests/unit/...`, `tests/integration/...`, or `tests/services/...`). Use the same directory as `test_proc_tools.py` and `test_job_feedback_observer.py`.

## Constraints

- **No production code changes** unless a test reveals a bug. File bugs against the relevant phase.
- **Use real OS subprocesses where feasible** (tasks 1-4, 7, 12). Mocks can't verify `os.killpg` semantics. Use short-lived `sleep` commands with generous cleanup.
- **Tests must clean up after themselves.** If a test fails before cleanup, leftover processes should be harmless (short sleeps). Consider a test fixture that kills any stray `sleep` processes owned by the test run.
- **PostgreSQL primary.** If integration tests hit the DB (they may, via `_dispatch_instance_post_commit_side_effects`), run against PostgreSQL per the project's dev env convention. Mark DB-touching tests appropriately.
- **Test BOTH CancelledError await points** (tasks 5, 6). The original incomplete fix only covered the wait_for point.
- **Don't test Windows-specific paths on Unix CI.** Guard Windows fallback tests with `pytest.mark.skipif(sys.platform == 'win32')`.

## Deliverables

- [ ] Tier 1 self-cleanup fires for any terminal instance, regardless of parent_id (Scenario A)
- [ ] Tier 2 root sweep cleans descendants, skips root (Scenario B)
- [ ] Child completion does not leak — its own procs cleaned immediately (Scenario C)
- [ ] Daemon shutdown kills everything across all instances (Task 4)
- [ ] Cancellation at WAIT await point kills subprocess + unregisters (Scenario D)
- [ ] Cancellation at SPAWN await point kills subprocess (E1) / safely skips (E2) (Scenario E)
- [ ] Nohup grandchildren killed via process group (Scenario F)
- [ ] No-op when no processes exist (Task 8)
- [ ] Double-fire (terminate + finalize) is idempotent (Scenario G)
- [ ] Best-effort failures isolated (get_tree_ids raises, per-iid raises, repo missing) (Scenario H)
- [ ] ERROR/FAILED paths trigger Tier 1 + Tier 2 (Task 11)
- [ ] Real-subprocess smoke test passes (Task 12)
- [ ] `_make_instance_id_aware` wrapper injects instance_id; args_schema hides it from LLM (Task 13)
- [ ] No regressions in pause/resume/cancel or proc tool suites (Tasks 14, 15)

## Definition of Done (Feature-Level)

All three phases merged when:
- [ ] Every success criterion in `plan-overview.md` is met
- [ ] All integration tests in this phase pass
- [ ] No regressions in the full test suite
- [ ] Code reviewed and merged
- [ ] Known limitations documented (truly-detached orphans, crash recovery can't kill prior-daemon processes)
