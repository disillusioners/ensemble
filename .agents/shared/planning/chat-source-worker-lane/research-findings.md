# Research Findings: chat-source-worker-lane

**Repo:** `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
**Branch:** `feature/chat-source-worker-lane` @ `c8855e8a`
**Date:** 2026-09-19
**Author:** planner[v2] via plan-creation worker
**Sources:** 3 explorer passes (code-verified) + spot-checked in this turn

> These findings underpin the plan. Every claim cites a file:line that was either verified by the explorer or spot-checked in this turn. The plan and decisions files build on this evidence — do not re-research, treat as load-bearing.

---

## A. Worker-pool mechanics

### A.1 Worker thread + heartbeat
- `class Worker(threading.Thread)` (`daemon/services/worker_pool.py:166`) — daemon thread (`daemon=True`, `:246`).
- Each worker owns a `TaskHeartbeat` thread that writes `task.last_heartbeat_at` every 30s (`:45-163`, `:283`, cleared on terminal `:329/:341`).
- Workers are OS threads sharing the singleton `TaskProcessor` (registered per-instance via `setup_worker_pool`).

### A.2 Claim path (the critical seam)
- `Worker.run` (`:277`) → `TaskProcessor.claim_task` (`daemon/services/task_processor.py:1268-1279`, thin passthrough) → `TaskRepository.claim_pending_task(worker_id)` (`daemon/repositories/task/repository.py:1479-1482`).
- Atomic `UPDATE…RETURNING` (`:1557`); gates folded into the inner SELECT — backoff (`:1595`), defer-idle (`:1604-1618`), background-idle (`:1646-1659`), queue-awareness (`:1686-1691`), per-instance RUNNING guard (`:1692-1756`), pause/terminal (`:1757-1770`), cross-system job-coordination (`:1810-1834`).
- ORDER BY tiers (`:1872-1875`): `process_report` first, then `created_at ASC`, `LIMIT 1`.
- **Today `claim_pending_task` takes ONLY `worker_id` — no lane/queue/pool parameter.** Any pool's workers race the same rows. **In-file precedents for adding a lane filter**: the tiered `ORDER BY CASE` literal at `:1872-1875` and the gate `EXISTS/NOT-IN` predicates at `:1686-1691`. The docstring at `:1846-1850` documents the gates-filter → order-rank → atomic-RETURNING contract.
- Claim is a global singleton — **adding a lane filter is the seam that isolates the pools**.

### A.3 Wake pulse + idle poll
- `notify_work()` (`:1300-1306`) is a `Condition.notify` token — wakes **ONE** waiter.
- Idle wait hardcoded `3.0s` timeout (`:356`); error path `1.0s` (`:364`).
- `ServicesConfig.worker_poll_interval` (`daemon/config.py:1129-1132`) is **UNWIRED/dead** — do not rely on this for chat latency.
- Pulse producers: `on_pending_task` lambda (`manager.py:6444`), watchdog carrier wakes (`manager.py:7929/:7993/:8060/:8583/:8647/:8704`), child_reports (`:4198-4204`), drift reconciler + `EligiblePendingSweepService` (approver E5 — actual class name verified via `grep "^class" daemon/services/eligible_pending_sweep.py`) (`api.py:563/:649`).
- **Under full saturation pulses are useless** — busy workers never poll.

### A.4 Pool lifecycle + citizenship for a second pool
- `start` (`:1353-1389`) — idempotent; **NOT restartable after `stop`** (`:1359-1360` raises `RuntimeError`).
- `stop(timeout=30)` (`:1391-1419`) — workers finish current task.
- Manager `shutdown_worker_pool` (`manager.py:6712-6727`) — single-pool teardown.
- Manager shutdown sequence at `manager.py:11314`.
- **Second-pool citizenship requirements** (must satisfy all):
  1. Share the single stateless `TaskProcessor`.
  2. Late-wire `set_work_resolver` / `set_watcher_repo` (`daemon/services/worker_pool.py:1457-1491`; `api.py:447-449` wires only the default pool today).
  3. Teardown entry in `shutdown_worker_pool`.
  4. **DISTINCT worker-id prefix** — ids are `f"worker-{i}"` (`:1366`), stamped into `task.worker_id` (`:1882`). Two pools collide in provenance/logs without a prefix.
  5. Do **NOT** construct a second `StaleTaskRecovery` (`:6482-6508`) — that is singleton state.
  6. Reuse `MainLoopBridge` loop (`:6435-6436`).

### A.5 Boot order (`api.py` lifespan)
- `manager.initialize` (`:342`) → `setup_worker_pool` (`:369`, calls `manager.py:6410-6412`, default `num_workers=WORKER_POOL_SIZE`; ordering doc `:6557`; `USE_WORKER_POOL` env kill-switch `:6424-6427` **kills ALL pools**) → pool late-wire (`:447-449`) → drift reconciler (`:563`) → `EligiblePendingSweepService` (`:649`) → `JobLockSweep` (`:765`) → watchdog (`:813`) → `JobProcessor` (`:1065-1075`; `poll_interval=30.0` hardcoded `api.py:1070`).
- `JobProcessor._process_loop/_process_next_job` (`daemon/services/job_processor.py:643/:682`) — single asyncio task; slow tick delays admission, **never** worker threads.
- **Boot-line conventions to match**:
  - `"JobLockSweepService started: interval=90s (default 90s)"` (`api.py:771-775`)
  - `"Waiting-children watchdog started: interval=…s, hang_threshold=…s"` (`api.py:846-850`)
  - `"Worker pool started with {n} workers (timeout=…min)"` (`manager.py:6710`)
  - `"Starting WorkerPool with N workers…"` (`daemon/services/worker_pool.py:1362`)

### A.6 Coupling A — `_invoke_semaphore` (`daemon/utils.py:554-569`)
- Lazy singleton `asyncio.Semaphore`; cap `:566` = `max(1, WORKER_POOL_SIZE - 1) = 4`.
- Acquired by `invoke_agent_and_wait` (`:588-599`, no lane param) via pure `await` (`:647`) — **NO timeout, NO reject, FIFO waiters**.
- Blocked invoke-parents **HOLD their worker** while awaiting children.
- **Deadlock analysis** (explorer-verified):
  - (i) Global cap raised to `total−1=6` is **UNSAFE** — 5 default workers could all be blocked parents; their children are default-lane; 0 free default workers; idle chat workers cannot claim default-lane tasks → deadlock.
  - (ii) Safe global cap must stay ≤ `default_pool − 1 = 4` (i.e. TODAY unchanged).
  - (iii) Split per-lane (default 4 / chat 1) requires a lane-aware selector replacing the singleton and is deadlock-safe **given children land on the default lane** (non-inheritance).
- **Decisive input:** child-lane routing (children are default-lane per the user clarification).

### A.7 Coupling B — single production engine
- Exactly ONE engine, hardcoded `pool_size=5 + max_overflow=10 = 15` (`daemon/repositories/factory.py:245-260`; does NOT read `DatabaseConfig` `:90-91`).
- All repos share `manager.engine`. Workers hold connections only for ms-scale sync ops.
- **Budget:** default 5 + chat 2 = **7 ≤ 15** — state this explicitly in the plan.
- Failure mode at exhaustion: SQLAlchemy pool `TimeoutError` (30s), not corruption.
- `ens_db_tools` repair engine is separate, maintenance-only.

### A.8 Single-pool assumption inventory (blast radius for second pool)
| Site | File:Line | Today |
|---|---|---|
| `WORKER_POOL_SIZE` constant | `daemon/constants.py:54` | `5` |
| Value pin test | `tests/unit/test_constants.py:125-127` | asserts `==5` |
| Completeness/freeze-list | `tests/unit/test_constants.py:147-159/:181` | expected set; new constants need both value test AND membership |
| `_invoke_semaphore` singleton | `daemon/utils.py:557-569` | cap = `WORKER_POOL_SIZE-1` |
| `_worker_pool` typed slot | `daemon/manager.py:1136` | single `WorkerPool \| None` |
| `on_pending_task` pulse lambda | `daemon/manager.py:6444` | `self._worker_pool.notify_work()` |
| API late-wire | `daemon/api.py:447-449` | one pool |
| Drift / sweep wake sites | `daemon/api.py:563/:649` | one pool |
| Child-reports wake | `daemon/child_reports.py:4198-4204` | one pool |
| Manager notify sites | `daemon/manager.py:7929/:7993/:8060/:8583/:8647/:8704` | one pool |
| `worker-{i}` id namespace | `daemon/services/worker_pool.py:1366` | stamped into `task.worker_id` at `:1882` |
| `USE_WORKER_POOL` kill-switch | `daemon/manager.py:6424-6427` | kills the one pool today; would kill both pools |
| `get_stats` | `daemon/services/worker_pool.py:1425-1455` | tests-only consumers; `/readyz` reads NO pool state |
| `WorkerPool` non-restartable | `daemon/services/worker_pool.py:1359-1360` | raises on re-`start` |
| `_worker_pool_size` (DEAD attribute) | `daemon/manager.py:6709` | zero readers — note only, not required cleanup |

---

## B. Source provenance & predicate surface

### B.1 `message_queue.source` is a REAL column
- Defined at `daemon/repositories/message_queue/models.py:64` (`source: str | None = Field(default=None)`).
- `instance_id` (`:61`); `message_metadata` JSONB (`:74-77`) is separate — **source is NOT in JSON**.
- **Chat prefixes are minted at exactly ONE site**: `registry.py:857` `source = f"{source_id}:{external_user_id}"` → `enqueue_message_job` (`:863-870`).
- Rows read `telegram:{user}` / `slack:{user}` / `discord:{user}` (deployed naming convention — `source_id` is free-form per `SourceCreate`; convention not enforced; `upgrade_journal.py:1047-1064`).

### B.2 NON-INHERITANCE verified (census, zero counter-examples)
- **Job-queue tool** stamps `agent:{caller_agent_id}` server-derived **UNCONDITIONALLY** (`daemon/tools/job_queue.py:712-714` `job_create`, `:1326` `job_continue`); the tool's `source` param is **DEPRECATED/IGNORED** (`:680`) — hostile source values cannot survive.
- **`internal_agent:{caller}`** (`job_queue.py:714/:2298/:2352`; `daemon/tools/instance.py:733/:2486/:2683/:3127/:3144/:3239/:3270`).
- **Scheduler** stamps exact `"scheduler"` on the inline path (`daemon/sources/adapters/scheduler.py:765`); manual triggers `_execute_immediate` `:794-807` funnel an `IncomingMessage` through the registry chokepoint minting `"scheduler:{schedule_id}"` — **BOTH shapes are default-lane**.
- **API chat route** hardcodes `source="api"` (`daemon/routers/messages.py:557`; resume fallback `"api_resume_fallback"` `:345`).
- **`system:*`** mints: `system:resume_wake` (`manager.py:9883`), `system:long-tool-nudge`, `system:report-integrity-guard`, `system:watchdog` / `:wedge` (`waiting_children_watchdog.py:171/:177`).
- **`child_report_check:`** is legacy-only (`child_reports.py:1918` — no longer minted).
- **Conclusion:** default-lane classification MUST be "NOT chat-prefix" (prefix semantics), never an exact-match allowlist — scheduler's dual shapes (`"scheduler"` and `"scheduler:{id}"`) both stay default.

### B.3 ⚠️ Forgery gap (must be addressed in the plan)
- `RESERVED_SOURCE_PREFIXES` (`daemon/constants.py:481-502`, 18 members; helper `is_reserved_source` `:505-529`) **deliberately EXCLUDES chat prefixes** (documented `:451-462`).
- `POST /api/jobs` accepts user-supplied `source` (`daemon/routers/jobs_crud.py:506`) gated only by the reserved check (`:478-495`) → **`source="telegram:fake"` is accepted TODAY** (e2e-confirmed 201; see `.agents/tester/LESSONS/2026-08-30-origin-census-reverse-scan-scheduler-gap.md`).
- A source-keyed chat lane is therefore **forgeable via the jobs API** unless the gate is extended to chat prefixes (or lane authority is stamped server-side).
- NOTE: the gate-widen is already the TOP item on the 2026-09-18 BACKLOG LEDGER — plan should scope the **minimal chat-lane-needed** extension and note coordination.
- `USER_ORIGIN_SOURCES` (`upgrade_journal.py:1076-1083`) is a **tool-layer whitelist, NOT a gate**.

### B.4 Claim-time surface
- The **task row has NO source column**.
- **Option (a) — NO schema change**: correlated `EXISTS/JOIN` on `task.message_id = message_queue.message_id` (live correlation, `predicates.py:37-43`; `processing_task_id` `:92` is DEAD for correlation `:30-35`) + prefix predicate on `message_queue.source`.
- **Option (b) — schema change**: new Task-row lane column stamped at enqueue, mirroring the `is_background` precedent (`instance_messaging.py:1594-1601` passes `is_background` into `_prepare_enqueued_message` `:1526`, which writes the atomic `MessageQueue + Task + event` trio `:1543-1562`).
- **Evaluate both; recommend one in `decisions.md`.**

### B.5 `queue_id` does NOT land on `message_queue`
- Lands on the JobItem mirror (`JobItem.queue_id`, `daemon/repositories/job_queue/models.py:338`; `enqueue_message_job` resolves `queue_id_for_job`, `instance_messaging.py:2259-2345`).
- Task links via `work_id=job_id`.
- **Mixed-provenance instances are coherent per-row**: `instance_id` `:61` vs `source` `:64` independent; no one-instance-one-source assumption anywhere; instance REUSE accepts later rows with different sources.

---

## C. Watchdogs / admission / test patterns

### C.1 All reclamation services are DB-row/heartbeat-based, pool-agnostic, SAFE with a second pool
- `JobRecoveryService` (`job_recovery_service.py` — DB pattern checks `:400`, `min_pending_age` 300s `:988/:1240-1346`); its ONLY pool touch is `notify_work` after re-enqueue (`:3609-3611`), skipped if `None` (`:3627`).
- `JobLockSweep` (`job_lock_sweep.py:27/:49-50` atomic DELETE, zero pool refs).
- `OrphanWatcherSweep` (`orphan_watcher_sweep.py:85-99`; blueprint's `instance_lifecycle._run_orphan_sweeper` no longer exists under that name).
- Waiting-children watchdog (heartbeat-based; pool refs are wake-only `:1574-1592/:1203/:1434`).
- `instance_lifecycle` resume wake `:3750-3756`.
- `job_state_machine` PURE (`:53-110`).
- **THE ONLY pool coupling across all recovery paths** = the `notify_work()` wake seam (3 sites resolving `manager._worker_pool`). **A second pool without wake fan-out degrades to its 3s poll tick** — for a REAL-TIME lane, pulse fan-out matters.

### C.2 Admission is PER-QUEUE slot locks, NOT global
- `SELECT concurrency_limit FROM job_queues WHERE queue_id` (`:3484-3492`) then for `slot in range(limit): INSERT INTO job_locks ON CONFLICT (project_id,queue_id,lock_slot) DO NOTHING` (`:3497-3538`).
- Full → `(None, False)` → caller SKIP-loops.
- `CheckConstraint` forces `concurrency_limit=1` for defer/background queues (models).
- **Options**:
  1. **UNCHANGED** single per-queue admission, lane split at claim seam only — chat jobs stay admitted on their resolved queues exactly as today.
  2. Dedicated chat queue row with its own `concurrency_limit` (first-class, queue-keyed) — adds queue routing changes.
- **Missing half either way** = the lane-less claim FIFO (see A.2) — admission alone cannot isolate lanes.

### C.3 `emit_terminal/watcher` id-mismatch defect confirmed POOL-AGNOSTIC
- Watchers key on `process_message` id, `instance.py:647-719`.
- Terminal emit resolves the terminal turn's task id, `child_reports.py:1020-1039/:415`.
- Orthogonal; second pool only mitigates latency symptom.

### C.4 Facade-forwarding guard patterns to model new seams on
- **UNIT** — `tests/unit/test_manager_enqueue_message_work_id_required.py`:
  - `__new__(InstanceManager)` skips `__init__` (`:57`)
  - `MagicMock` service + `AsyncMock` method spy (`:58-62`)
  - REAL facade invocations asserting kwarg forwarded verbatim (`:68-87`)
  - Defaults (`:89-100`)
  - Keyword-only `TypeError` (`:102-119`)
  - Neighbor kwargs (`:121-136`)
  - Pass-through identity (`:87`)
- **INTEGRATION** — `tests/integration/test_job_driven_enqueue_work_id_facade.py`:
  - Real facade → real `InstanceMessagingService` → real `_prepare_enqueued_message` over FILE-BACKED SQLite (`tmp_path + NullPool + WAL/FK`, `:74-103` — never `StaticPool/:memory:`)
  - Real job stack, `_worker_pool=None` deliberately (`:30-34`)
  - Asserts contract-error type + `Task.work_id==job_id` linkage (`:19-28`)

### C.5 Integration harness for dispatch scenarios
- Canonical wiring at `tests/integration/test_wc_wake_pure_hang.py:402-493`:
  - `setup_worker_pool(num_workers=1)` FIRST (`:411`, it wires `_task_repo`; S6 invariant `:748` "must wire the real `JobQueueService`")
  - Then `JobQueueService` + `DispatchEventBus()` with `set_event_loop` + `job_service.set_dispatch_bus` + `JobProcessor(..., dispatch_bus)` (`:481-493`)
- **Lesson (recorded):** pool alone is insufficient — `DispatchEventBus` + `JobProcessor` admission must exist before the claim guard (`repository.py:1686-1691`) accepts the linked Task.
- Supporting: `test_daemon_startup_smoke.py:34-58` (`num_workers=0`), `test_boot_report_recovery.py:187-220/:785-833` (source-window asserts on `setup_worker_pool` body).

### C.6 Constants pinning (test pattern)
- `tests/unit/test_constants.py:125-127` (value) + `TestConstantsCompleteness` expected set `:147-159` (membership) — a new `CHAT_*` constant needs **both** a value test **and** membership.

---

## D. Project conventions to honor

| Convention | Source | Implication for plan |
|---|---|---|
| Hardcoded constants, NO env resolvers | blueprint Core Architecture | Activation = rebuild+restart; boot observability lines |
| `Facade-Forwarding Discipline` | `InstanceManager` is a manual-forwarding facade over `InstanceMessagingService` | Any new kwargs need facade-forwarding check + real-dispatch integration test (guards C.4) |
| `queue_id` propagates router → service → repository → SQL | blueprint | N/A here — chat lane keys on `message_queue.source`, not `queue_id` |
| Timestamp binds use `now_utc_naive()` for naive columns | blueprint / `daemon/services/timestamps.py` | Likely minimal here — note any new timestamp touch |
| No new user-togglable `ENSEMBLE_*` flags | convention (o), `7d5285aa` | Ship always-on; tuning-only knobs OK; chat-lane size is a tunable knob |
| Tests via `uv run python -m pytest` from worktree root | caller | Test instructions in every phase |
| Plan executed by one coder per phase | caller | Each phase self-contained with its own tests |

---

## E. Architect verification addendum (2026-09-19)

**Leader-accepted amendments folded into `decisions.md` D1, D5, D8, D9, D10.1, D10.2 + `phase1-plan.md` (scope +8 tasks) + `phase2-plan.md` (wake-site list) + `phase3-plan.md` (notify-not-poll assertion + fixture-validation gate):**

- **D1 (claim seam):** PG-only `LIKE ANY` replaced with portable `sqlalchemy.or_(...)` form (A1.1); `idx_message_queue_source` index migration REJECTED — the correlated EXISTS is driven by the `message_id` PK probe, `source` is a residual filter, the index would be dead weight + violates no-migration (A1.3); claim-skew without `FOR UPDATE SKIP LOCKED` documented as PRE-EXISTING concurrency invariant, unchanged by lane predicate, explicitly out of scope (A1.2); JSONB lane-stamp alternative quantified-rejected with the ~15 internal mint-site stamping cost (A1.3 rationale strengthen).
- **D5 (wake fan-out):** canonical 19-site census replacing the original plan's partial list — CRITICAL missed site `instance_messaging.py:2086-2087` (the notify path every registry-minted chat row traverses) + 8 others (manager.py:715, eligible_pending_sweep.py:287-294 with ctor-widening, waiting_children_watchdog.py:1595-1597, job_feedback_observer.py:3400-3405/:3680-3685/:4279-4283, job_processor.py:1262-1266, long_tool_nudge.py:1184-1187) added to the existing sites. `_notify_all_pools` iterates a `self._pools: list[WorkerPool]` (NOT inline 2-tuple) for future-lane extension. Per-pool `Condition` with `notify()` (NOT `notify_all`) — the `_notification_count` discipline is verified sound. Mandatory A2.2 notify-not-poll assertion in Phase 3 Task #1.
- **D8 (invoke semaphore):** await is NOT unbounded — DOUBLE BOUND (invoke_agent_and_wait `timeout=300` inner + StaleTaskRecovery heartbeat ~5min + JobRecoveryService 300s drift reconcile outer); worst case = multi-minute tail (degrade, not deadlock).
- **D9 (engine budget):** REFRAMED to full-engine picture — 15-conn engine also serves concurrent HTTP handlers + 5 sweep/watchdog services; realistic worst ≈ 12-15 at cap under sustained API load; failure mode = 30s pool TimeoutError absorbed by TaskProcessor retry; LangGraph checkpointer uses its own asyncpg pool; PlaneSync is HTTP-only. No code change.
- **D10.1 (forgery gate):** 5-pin coupled-reservation pattern (provenance doc-bullet, exact-equality + helper-parity pins, gate-behavior pin, e2e matrix with stale-reference fix at `test/packs/origin_contract_e2e_probe_test.py:33`, constants completeness pin); ADDED registration-time source_id validator at `create_source` (`daemon/routers/sources.py:98+`) — operator vector unmitigated by HTTP gate alone; PINNED 3-vs-5 `_USER_ORIGIN_PREFIXES` vs `CHAT_SOURCE_PREFIXES` asymmetry (webhook/whatsapp deliberately excluded — interactive chat only).
- **D10.2 (chat saturation):** split into TWO cases — (i) idle-transition pickup: sub-second once D5 lands; (ii) burst > pool_size with backlog > 2: latency = next worker-return-and-continue = ONE TASK DURATION, NOT one poll cycle; no new notify-on-enqueue fast-path needed (notify path already exists, just fan out per D5); SC#6 conditional on fixture-validation (Phase 3 Task #4a) — first two chat tasks must be SHORT, else the criterion measures task duration not queueing.
- **Phase 2 (lifecycle):** A5.1 hung-worker WARNING log when a chat (or default) worker is still alive after `stop(30)` elapses (mirror StreamWatchdog pattern at `api.py:1601-1605`); worker blocked in `invoke_and_wait` is not interrupted by `_stop_event`.

**Architect risk ranking (unchanged):**
1. 🟡 Silent 3s-poll degradation on primary chat ingress if D5 wake-site list under-enumerates → closed by canonical 19-site census + A2.2 notify-not-poll assertion.
2. 🟡 Operator source_id misconfiguration → silent misroute with zero detection signal → closed by registration-time validator (Phase 1 same PR).
3. 🟢 D9 connection budget framed per-workers-only → closed by reframe documentation only.

**Confidence: HIGH** (architect fan-in 3/3, leader accepted all amendments).