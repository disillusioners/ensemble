# Phase 2: Manager/API Wiring + Facade Forwarding

## Objective

Construct the chat `WorkerPool` inside `setup_worker_pool`, plumb shutdown teardown, fan out wake pulses across both pools, widen the API late-wire, and add the boot-line observability. **At exit, the second pool is live in production boot, both pools coexist correctly, and the facade-forwarding guards are in place for any new seam introduced.**

---

## Tasks

| # | Task | Depends On | Acceptance |
|---|---|---|---|
| 1 | Add `self._chat_worker_pool: WorkerPool \| None = None` slot to `InstanceManager.__init__` next to `self._worker_pool` at `daemon/manager.py:1136`. **E3 — boot-window guard:** also initialize `self._pools: list[WorkerPool] = []` in `InstanceManager.__init__` (or earlier in the lifecycle) so any early wake/wiring that touches `_pools` before `setup_worker_pool` runs cannot crash on a missing attribute (matches the None-guard mandate philosophy — early HTTP `/api/messages` traffic can fire in the lifespan window between pool construction and source start). | Phase 1 | Slot exists, type-annotated, None-initialized; `self._pools` initialized as `[]` early in the lifecycle. |
| 2 | Inside `setup_worker_pool` (`daemon/manager.py:6410-6726`), AFTER the default pool constructs and starts (around line 6710 where the default-pool boot line lives), construct `self._chat_worker_pool = WorkerPool(num_workers=CHAT_WORKER_POOL_SIZE, worker_id_prefix="chat-worker-", task_processor=self._task_processor, ...)`. Start it. **B1 — flipping strictness ON:** immediately after `self._chat_worker_pool.start()`, set `self._chat_lane_active: bool = True` so the default-lane claim predicate's `NOT EXISTS` chat-prefix clause activates (per phase1 Task #6). Respect the `USE_WORKER_POOL` kill-switch (`:6424-6427`) — if disabled, both pools are skipped AND `self._chat_lane_active` stays `False`. | Phase 1, Task 1 | `manager._chat_worker_pool` is non-None when env flag is unset; both pools start together; if env flag is disabled, both remain None AND `manager._chat_lane_active == False`. |
| 3 | Add `manager._pools: list[WorkerPool] = []` slot and `manager._notify_all_pools()` helper method (D5 list-shape — NOT inline 2-tuple). The helper iterates `self._pools` and calls `.notify_work()` on each non-None entry. **Replace ALL 19 canonical wake sites** per D5 census (architect amendment A2.1):<br>• `daemon/manager.py:715` (discard_on_startup lambda)<br>• `daemon/manager.py:6444` (on_pending_task lambda)<br>• `daemon/manager.py:7929/:7993/:8060/:8583/:8647/:8704` (6 child-report carrier wakes)<br>• `daemon/services/child_reports.py:4198-4204`<br>• `daemon/services/job_recovery_service.py:3609-3611` (None-safe already per `:3627`)<br>• `daemon/services/instance_lifecycle.py:3750-3756`<br>• **`daemon/services/instance_messaging.py:2086-2087`** — CRITICAL (every registry-minted chat row traverses this path)<br>• `daemon/services/eligible_pending_sweep.py:287-294` — **ctor-widening required** (singleton-attribute reach via `self._worker_pool`; widen ctor to take manager reference, then route through `manager._notify_all_pools()`)<br>• `daemon/services/waiting_children_watchdog.py:1595-1597` (already holds `manager` ref)<br>• `daemon/services/job_feedback_observer.py:3400-3405, :3680-3685, :4279-4283` (already hold `self._instance_manager`)<br>• `daemon/services/job_processor.py:1262-1266` (defense-in-depth)<br>• `daemon/services/long_tool_nudge.py:1184-1187` (defense-in-depth)<br>**Reviewer F12 — the `decisions.md` D5 census table is AUTHORITATIVE** (this Task #3 list is a convenience copy). On any divergence (e.g., a newly added wake site), the D5 table in `decisions.md` wins; update both in the same edit.<br>Populate `self._pools` in `setup_worker_pool` AFTER both pools construct. Each site changes from `self._worker_pool.notify_work() if self._worker_pool else None` (or singleton-attribute reach) to `self._notify_all_pools()`. **MANDATE (reviewer recommendation 4 — wake-site None-guard): every widened wake site MUST None-guard `_chat_worker_pool` exactly as `daemon/services/instance_messaging.py:2086-2087` guards the default pool today** — because early HTTP `/api/messages` traffic CAN fire in the lifespan window between pool construction (`api.py:369`) and source start (`~api.py:1119`); a wake firing during that window must not crash on a None pool. **The `_notify_all_pools` helper's per-entry `if pool is not None: pool.notify_work()` None check satisfies this mandate site-wide** — say so explicitly in the helper's docstring and as a hard Phase 2 requirement on every site listed in Task 3. | Phase 1 (lane parameter available) | Existing wake semantics preserved when only the default pool exists; both pools wake when both are running. The helper handles the `None` cases (chat pool disabled, default pool disabled). Phase 3 task #1's notify-not-poll assertion exercises the fan-out end-to-end. |
| 4 | Widen `daemon/api.py:447-449` late-wire — call `self._worker_pool.set_work_resolver(...)` AND `self._chat_worker_pool.set_work_resolver(...)`; same for `set_watcher_repo`. Wrap each call in `if pool is not None` to handle the `USE_WORKER_POOL=false` case. | Task 2 | API lifespan succeeds with both pools running; both pools see the late-wired resolvers. |
| 5 | Add teardown entry to `shutdown_worker_pool` (`daemon/manager.py:6712-6727`). **B2 ordering — snapshot BEFORE stop/None; iterate snapshot AFTER both stops:** the prescribed sequence is `pools_snapshot = [p for p in (self._worker_pool, self._chat_worker_pool) if p is not None]` taken FIRST, then iterate `pools_snapshot` and for each pool: if `pool is self._chat_worker_pool` then `self._chat_lane_active = False` (B1 fail-open restore) THEN `pool.stop()` THEN `pool is None`; otherwise just `pool.stop()` then `None`. **E1 — chat-FIRST iteration is mandatory:** the snapshot iteration MUST visit the chat pool BEFORE the default pool so the flag flips BEFORE the default pool stops (default pool continues to claim chat rows during its own stop window = fail-open restored, no stranding). The code-shape is explicit: `for pool in (self._pools_snapshot_chat_first or pools_snapshot): ...` where `_pools_snapshot_chat_first` is built as `[p for p in (self._chat_worker_pool, self._worker_pool) if p is not None]` (chat pool first), OR `reversed(pools_snapshot)` if `_chat_worker_pool` was appended after `_worker_pool` in the canonical list-order. Implementer chooses ONE — both are equivalent; the requirement is that chat pool's `stop()` runs BEFORE default pool's `stop()`. **Hung-worker WARNING loop (architect amendment A5.1, reviewer N4 — REQUIRED accessor):** iterate `pools_snapshot` and for each worker that is still `is_alive()` after `stop(30)` elapsed, emit a WARNING log naming the worker_id (so the operator can identify whether default or chat hung). Mirror the StreamWatchdog pattern at `daemon/api.py:1601-1605`. **N4 — choose ONE in-PR (no defer):** (i) document the exception explicitly in the implementation ("this WARNING loop accesses the private `_workers` list; encapsulated per pool lifecycle — acceptable trade-off for the observability gain"); OR (ii) add an `is_any_worker_alive() -> bool` accessor on `WorkerPool` and use it. The implementer MUST pick one in the same PR (no deferred follow-up). Rationale: a worker blocked in `invoke_and_wait` is not interrupted by `_stop_event` (fires only inside `wait_for_work`, `daemon/services/worker_pool.py:1339-1340`); the daemon thread dies with the process — no deadlock, but a possible 30s hung join, and the WARNING makes it observable (vs. silent on monitoring). | Task 2 | `manager._chat_worker_pool` is `None` after `shutdown_worker_pool`; log line `"Chat worker pool stopped"` present; WARNING log fires iff a snapshotted worker is still alive after its `stop(30)`; `manager._chat_lane_active == False` after shutdown (fail-open restored); chat pool's `stop()` ran BEFORE default pool's `stop()` (E1 ordering). |
| 6 | Add the boot-line log after the chat pool starts in `setup_worker_pool`. Format: `logger.info(f"ChatSourceWorkerPool started: workers={CHAT_WORKER_POOL_SIZE}, prefixes={','.join(CHAT_SOURCE_PREFIXES)}")`. (Resolved answer to open question #1: dynamic from tuple.) | Task 2 | Log line appears on every successful boot when chat pool is enabled. |
| 7 | (Conditional — only if any new kwarg is added to a facade method) Apply the `Facade-Forwarding Discipline` guard pattern. Today the only facade seams touched are internal (no new manager.method() kwargs in this plan) — but if Phase 2 widens `manager.enqueue_message_job` or any neighbor to thread `lane=`, add unit + integration guards modeled on `tests/unit/test_manager_enqueue_message_work_id_required.py` + `tests/integration/test_job_driven_enqueue_work_id_facade.py`. | Conditional | If no new facade kwarg added, skip (note in commit message). If added, both guard files exist and pass. |
| 8 | Verify the existing `_worker_pool_size` (DEAD attribute at `manager.py:6709`) is unused — if any reader exists in the codebase, document it. Optional: remove if confirmed unused. | none | `git grep -n "_worker_pool_size" daemon/ tests/` shows only the assignment at `:6709`; safe to remove if desired (P3 follow-up note in PR). |

---

## Coupling

- **Tight with:** Phase 1 (consumes `WorkerPool(worker_id_prefix=...)`, `TaskProcessor(..., lane=...)`, `claim_pending_task(lane=...)`, `CHAT_*` constants); Phase 3 (boot-line assertion, shutdown teardown test, late-wire test).
- **Loose with:** None.
- **Independent of:** Phase 3 (this phase ships the wiring; Phase 3 ships the integration tests that exercise it).

**Shared contracts (consumed from Phase 1):**
- `WorkerPool(num_workers, worker_id_prefix, task_processor, ...)` — constructor shape.
- `CHAT_WORKER_POOL_SIZE: int`, `CHAT_SOURCE_PREFIXES: tuple[str, ...]` — values.
- `claim_pending_task(worker_id, lane)` — Phase 2's chat pool threads `lane="chat"` at the call site (in `Worker.run` via `TaskProcessor.claim_task(worker_id, lane)`).

**Shared contracts (consumed by Phase 3):**
- `manager._chat_worker_pool: WorkerPool | None` — for shutdown and stats assertions.
- `manager._notify_all_pools()` — for wake-pulse tests.
- Boot-line string — for log-capture assertions.
- API late-wire ordering — for harness setup.

---

## Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Construction order: chat pool is constructed AFTER default pool but BEFORE late-wire — if late-wire fires before chat pool starts, chat workers miss the resolver. | High | Task #2 places chat-pool construction AFTER default-pool start AND BEFORE late-wire. Task #4 explicit guard: `if self._chat_worker_pool is not None: ...set_work_resolver(...)`. |
| `_notify_all_pools` introduces a method-call vs direct attribute-access overhead — minor but measurable in hot loops (child_reports wake fires per child report). | Low | The helper is a single attribute check + method call; negligible vs the network/file I/O of the wake itself. If measured impact > 1%, cache the pool list at construction time (P3 follow-up). |
| `USE_WORKER_POOL=false` disables both pools — but the gate at `manager.py:6424-6427` is checked ONCE at the top of `setup_worker_pool` and returns early. If the chat-pool construction is inside the same function AFTER the gate check, it's also disabled (correct). | Low | Phase 2 explicitly places chat-pool construction INSIDE the same function body so the gate covers both. Test: boot with `USE_WORKER_POOL=false` and assert both `manager._worker_pool` and `manager._chat_worker_pool` are None. |
| `child_reports.py:4198-4204` is in a separate module — Phase 2 must import or expose `manager._notify_all_pools`. Today the code reaches `manager._worker_pool` via a closure or argument; Phase 2 must mirror that path for `_notify_all_pools`. | Medium | Phase 1 spot-check: `child_reports.py:4198-4204` likely holds a reference to `manager` (the pattern in cross-module wake sites). Phase 2 verifies the reference is the manager instance (not stale); widens to call `_notify_all_pools` on the manager reference. If the reference is a local closure binding, update that closure. |
| Boot-line change adds a log-line test fragility risk — if format changes, tests break. | Low | Phase 3 log-capture test matches a substring (e.g., `ChatSourceWorkerPool started`), not the exact format. |
| `_worker_pool_size` removal (Task #8) could break a hidden reader. | Very Low | Confirmed via `git grep` in Task #8 itself before removal; if any reader exists, defer the removal and document. |
| Adding `lane` kwarg to `TaskProcessor.claim_task` (Phase 1 Task #8) cascades to `Worker.run` — if Phase 2 doesn't thread the lane at the call site, chat workers always claim as `lane="default"`. **Reviewer F3 MANDATE: the per-pool-TaskProcessor option is STRUCK.** `TaskProcessor` MUST remain a single shared singleton (it is the seam to the same `TaskRepository`; the late-wire second consumer at `api.py:450-452` consumes the same processor instance via `set_work_resolver` / `set_watcher_repo`). The lane flows Worker → shared processor → repository as a CLAIM ARGUMENT, not as processor state. Phase 1 Task #8 lands BOTH `WorkerPool.__init__` ctor kwargs (`worker_id_prefix` + `lane`) — they are one seam, one PR. | High | Phase 1 Task #8 widens `WorkerPool.__init__` to accept `lane="default"` kwarg + stores it on `self._lane`. `Worker.__init__` takes `lane` and stores `self._lane`. `Worker.run` calls `self._task_processor.claim_task(self._worker_id, self._lane)`. `TaskProcessor.claim_task(worker_id, lane)` forwards to `TaskRepository.claim_pending_task(worker_id, lane)`. Phase 3 integration test asserts the lane is correctly threaded. |
| Wake-site widening misses a site — original plan missed 9; architect found them via grep. | High | Phase 2 Task #3 enumerates the canonical 19-site list with citations; Phase 3 Task #1's notify-not-poll assertion (A2.2) proves the fan-out works end-to-end. If a future site is added, the helper is the one place to call. |
| `eligible_pending_sweep.py:287-294` ctor-widening — the singleton-attribute reach `self._worker_pool` is read at construction time, so widening the ctor is a constructor signature change at every call site (likely 1-2: `EligiblePendingSweepService(...)` (approver E5 — actual class name verified via `grep "^class" daemon/services/eligible_pending_sweep.py`) in `daemon/api.py:649` or wherever constructed). | Medium | Ctor-widening to `(manager)` (instead of `(worker_pool,)`) is a 1-line signature change; pass `manager._notify_all_pools` (a bound method) instead of `worker_pool.notify_work` (a bound method). Behavior identical from the sweep's perspective. |

---

## Exit Criterion

**Phase 2 is complete when:**

1. `uv run python -m pytest tests/integration/test_chat_source_pool_wiring.py -v` passes (new integration test that boots 2 pools via `setup_worker_pool(num_workers=WORKER_POOL_SIZE)`; the chat pool is constructed with `CHAT_WORKER_POOL_SIZE=2` per the F3-corrected seam; asserts both `manager._worker_pool` and `manager._chat_worker_pool` are non-None; asserts workers are correctly named — see Implementer Note (e)).
2. `uv run python -m pytest tests/integration/test_chat_source_shutdown.py -v` passes (shutdown test).
3. `uv run python -m pytest tests/integration/test_chat_source_late_wire.py -v` passes (late-wire test; uses FileBackedSQLite harness per C.5).
4. Manual smoke: `./dev.sh` boots cleanly; `data/logs/ensemble.log` shows the boot-line `"ChatSourceWorkerPool started: workers=2, prefixes=telegram:,slack:,discord:"` within first 60s.
5. Manual smoke: `curl -X POST /api/jobs -d '{"source":"telegram:fake", ...}'` returns 422 with `JobValidationError` envelope (D10.1 forged-source gate).
6. Manual smoke: `./dev.sh` shutdown cleanly; no zombie threads (process exits within 30s).
7. No code outside `daemon/manager.py`, `daemon/api.py`, `daemon/services/child_reports.py`, `daemon/services/job_recovery_service.py`, `daemon/services/instance_lifecycle.py`, `daemon/services/instance_messaging.py`, `daemon/services/eligible_pending_sweep.py`, `daemon/services/waiting_children_watchdog.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/job_processor.py`, `daemon/services/long_tool_nudge.py`, `daemon/services/worker_pool.py` (Task #8 only if removing DEAD attribute), `tests/integration/test_chat_source_*.py`, has been modified by Phase 2. (This is the full D5 census of files touched — F4 normalized paths — any deviation from this list is a review comment.)
8. If any new facade kwarg was introduced (Task #7 conditional), both facade-forwarding guards pass.
9. **Wake-site None-guard mandate verified:** every widened wake site None-guards `_chat_worker_pool` (the `_notify_all_pools` helper's per-entry None check satisfies this — confirm in code review).
10. **B1 conditional fail-open verified end-to-end (approver S4 — home = Phase 2 wiring test):** the fail-open test homes in `tests/integration/test_chat_source_pool_wiring.py` as an explicit case. The test runs the wiring test in two configurations: **(a) chat pool ABSENT** — skip the chat-pool construction step; assert that a chat-prefixed row enqueued in the same harness is claimed by the default pool (fail-open = today's behavior, `_chat_lane_active == False`); **(b) chat pool PRESENT** — full wiring; assert strict two-way (default pool does NOT claim chat rows; cross-ref `tests/integration/test_chat_source_two_way_isolation.py`). Both cases pin the flag's three states (P1 alone / P1+P2 normal / P2 shutdown — the latter exercised by the existing `tests/integration/test_chat_source_shutdown.py`). Phase 3 receives a one-line cross-reference pointing at this phase2 home (no duplicate home). Test name: `test_b1_conditional_fail_open_chat_pool_absent_then_present` in `tests/integration/test_chat_source_pool_wiring.py`.

---

## Test Gate (phase-level)

A new commit on `feature/chat-source-worker-lane` containing ONLY Phase 2 changes (cumulative with Phase 1) must pass:

```bash
uv run python -m pytest \
  tests/unit/test_constants.py \
  tests/unit/test_repository_claim_lane.py \
  tests/unit/test_jobs_crud_chat_source_gate.py \
  tests/unit/test_worker_pool_prefix.py \
  tests/integration/test_chat_source_pool_wiring.py \
  tests/integration/test_chat_source_shutdown.py \
  tests/integration/test_chat_source_late_wire.py \
  -v
```

**New test files to create:**
- `tests/integration/test_chat_source_pool_wiring.py` — uses `setup_worker_pool(num_workers=WORKER_POOL_SIZE)` (approver N9 — fixed from `num_workers=1`; this test MUST use the production default-pool size to instantiate SC#3's 5-job saturation later) + manually constructs the chat pool (mirroring `test_wc_wake_pure_hang.py:402-493`); asserts `manager._chat_worker_pool` exists, has 2 workers, all worker_ids start with `chat-worker-`. Then invokes `shutdown_worker_pool` and asserts both slots are None.
- `tests/integration/test_chat_source_shutdown.py` — boots 2 pools; calls `manager.shutdown_worker_pool()`; asserts no exceptions; checks threads are joined within timeout.
- `tests/integration/test_chat_source_late_wire.py` — uses FileBackedSQLite harness (per C.5); constructs both pools; calls the late-wire path; asserts `_work_resolver` and `_watcher_repo` are set on both pools' workers (introspect via the worker's private attr or a stats hook).

---

## Out-of-phase notes for Phase 3

- **Phase 3** will: (a) write the saturation-isolation integration test (default pool FULLY saturated by 5 long jobs → chat message picked up ≤3.5s) WITH the mandatory A2.2 notify-not-poll assertion (no `wait_for_work(3.0s)` timeout-expiry wake observed during the chat-claim window — proves the notify path, not the poll fallback, delivered the claim); (b) write the non-inheritance e2e test (slack → ari → spawn child → default lane); (c) write the mixed-provenance test; (d) write the chat-saturation test with the A4.1 fixture-validation gate (first two chat tasks must be SHORT, else SC#6 measures task duration not queueing); (e) pin the watchdog regression suites.
- Phase 2 MUST leave the boot-line log in a stable format so Phase 3 can match it (substring match is fine; full-string match is fragile).
- Phase 2 MUST not change `manager.enqueue_message_job` or other facade methods without applying the facade-forwarding guard pattern (Task #7 conditional).

---

## Implementation hint: lane threading through Worker

The cleanest threading strategy (Phase 1 Task #7 + Phase 2):

```python
# daemon/services/worker_pool.py — Worker.__init__
class Worker(threading.Thread):
    def __init__(self, ..., worker_id, lane, ...):
        self._lane = lane
        ...

# daemon/services/worker_pool.py — Worker.run
def run(self):
    ...
    task = self._task_processor.claim_task(self._worker_id, self._lane)
    ...

# daemon/services/worker_pool.py — WorkerPool.__init__ (extended)
def __init__(self, ..., num_workers, worker_id_prefix="worker-", lane="default", ...):
    self._lane = lane
    ...
    for i in range(num_workers):
        worker = Worker(worker_id=f"{worker_id_prefix}{i}", lane=self._lane, ...)
```

This keeps the lane a property of the pool, not threaded at every call site.

---

## Implementer Notes (reviewer findings 13-22 — deferred, NOT design fixes)

These are notes for the executing coder. They are NOT changes to the plan's design text — read them BEFORE coding Phase 2 so the implementation matches the intent.

- **(e) num_workers truth — `WORKER_POOL_SIZE=5` (default) / `CHAT_WORKER_POOL_SIZE=2` (chat); see Phase 2 Exit Criterion #1 (approver N9 — fixed):** the gating exit text now uses `setup_worker_pool(num_workers=WORKER_POOL_SIZE)` for the default pool and `CHAT_WORKER_POOL_SIZE=2` for the chat pool (constructed via a separate call inside `setup_worker_pool`). **Do NOT** pass `CHAT_WORKER_POOL_SIZE=2` as the `num_workers` arg of `setup_worker_pool` — that argument is the DEFAULT pool size and would boot the default pool at 2 workers, which is wrong. The Phase 3 saturation-isolation test (Task #1) must construct the default pool at 5 workers; using `num_workers=1` (the `test_wc_wake_pure_hang.py:411` harness pattern) CANNOT instantiate SC#3's 5-job default saturation — see Implementer Note (i). Plan-overview.md Risk #8 has the same confusion and is corrected to spell out the size split.
- **(f) Exit criteria renumbered sequentially** (reviewer noted items jumped 1, 3, 4, 5, 6, 7, 8, 9 → now 1-9 sequentially). No semantic change; cosmetic only.
- **(g) EligiblePendingSweepService ctor site** — pin the construction site for the ctor-widening (Task #3 #13 ⭐). The construction call is at `~daemon/api.py:649` (with `EligiblePendingSweepService(worker_pool=...)` today; approver E5 — verified `grep "^class" daemon/services/eligible_pending_sweep.py`; widen to `EligiblePendingSweepService(manager=...)` or pass both pools). After widening, confirm shutdown tolerance: the sweep must stop cleanly when the manager's `_chat_worker_pool` is set to `None` in `shutdown_worker_pool` (Task #5). If the sweep holds a strong reference to the chat pool, set the sweep's reference to `None` in the shutdown sequence before the pool teardown — otherwise the chat pool's stop timeout may not converge.
- **(h) Hung-worker WARNING private attr** — Task #5 iterates `pool._workers` to detect hung workers, but `_workers` is a private attribute of `WorkerPool` (no public accessor). Two implementation options: **(i)** document the exception explicitly in the implementation ("this WARNING loop accesses the private `_workers` list; encapsulated per pool lifecycle — acceptable trade-off for the observability gain"); **(ii)** add an `is_any_worker_alive() -> bool` accessor on `WorkerPool` and use it. The implementer may choose either; if (ii) is chosen, the accessor becomes part of the `WorkerPool` public surface and is a tiny Phase 2 API addition (mention in commit message). Recommend (i) for minimal-surface-area reasons — the private-attr access is bounded by the lifecycle scope (the manager owns the pools, the manager is the only caller).