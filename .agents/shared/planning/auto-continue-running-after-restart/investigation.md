# Investigation: auto-continue RUNNING instances after daemon restart

- **Date:** 2026-10-04
- **Repo / branch:** `/home/nea/ensemble-src` @ `feature/auto-continue-running-after-restart` = `cf8efbef` (= `latest`, post arm-notify merge)
- **Investigator:** Wanderer (read-only; 5 delegated code/forensics sub-investigations + independent spot-verification of every load-bearing claim)
- **Status:** UNCOMMITTED — left in the working tree for the planner, per commission.

---

# EXECUTIVE SUMMARY

## 1. Reuse inventory (function → what it does → reusable as boot primitive?)

| Mechanism | What it does | Boot-primitive verdict |
|---|---|---|
| `_schedule_explicit_handle_resume` (`daemon/manager.py:10915`) | Dedup gate (`_graph_tasks`) → cleans stale message_queue rows → `asyncio.create_task(_resume_processing_background(...))` | ✅ **YES — directly callable** by a boot sweep with `route_outcome="report_or_external_resume"`, `silent=True`, `target_work_id=<dormant task.work_id>` |
| `_resume_processing_background` (`daemon/manager.py:11445`) | Gate-locked `_process_message_with_tracking(is_retry=True, silent=True, message_source="cascade_resume")` → JobFeedbackObserver resume-finalize | ✅ **YES (private) — the minimal "continue prior turn without injecting text" primitive.** With `silent=True` → `graph_input=None` → `astream(None)` continues from the LangGraph checkpoint (`instance_messaging.py:4267-4269`) |
| `find_paused_or_cancellable_turn` (`daemon/repositories/task/repository.py:743`) | Selector matching `task.status IN ('paused','running')` AND `task_type IN ('process_message','process_report')`; one-running-turn-per-instance invariant | ✅ **YES — the canonical boot-sweep selector** for dormant RUNNING tasks |
| `enqueue_message` (manager `:7935` → service `instance_messaging.py:2108`) | One-transaction MessageQueue+Task write; wakes pools; status-gated (IDLE/WC→RUNNING; terminal→revive; RUNNING→append-behind) | ⚠️ **with-adaptation** — requires non-empty message; WC-wake precedent synthesizes `"continue"` with `source="system:resume_wake", priority=0` (`manager.py:10837-10848`) |
| `resume_instance_cascade` (`manager.py:10477` → `instance_lifecycle.py:3879`) | DB-only `PAUSED→RUNNING` (instances) + `PAUSED→PENDING` (tasks via `ResumeTurn`); no dispatch | ⚠️ **with-adaptation** — no-op for already-RUNNING instances (SQL guard `status='paused'`); correct ONLY for the PAUSED leg the feature must NOT touch |
| `ResumeTurn` (`turn_transitions.py:261`) | Guarded UPDATE `paused→pending` + clear handle columns | ❌ wrong primitive for RUNNING (guarded on `status='paused'`) |
| `job_continue` (`daemon/tools/job_queue.py:1768`) | Terminal-job continuation → `enqueue_message_job` | ❌ **REQUIRES terminal status** (`is_terminal` at `:1827`) — refuses still-RUNNING instances |
| `StaleTaskRecovery.recover_on_startup` (`stale_task_recovery.py:752`) | Force-cancel stale RUNNING task + schedule RETRY task (`origin="startup_stale_running"`) | ❌ different goal: age-gated (10 min + boot-epoch amnesty), burns `max_retries=3`; retry task DOES checkpoint-resume but only after the threshold |
| WC-wake branch in `resume_processing_job` (`manager.py:10837`) | `enqueue_message("continue", source="system:resume_wake", priority=0)` for WAITING_CHILDREN parents | ⚠️ **the pattern to mirror** — only resume-path site that creates a fresh Task without a suspension handle; currently gates on `status==WAITING_CHILDREN` only |
| `sweep_wake_records` boot pass (`upgrade_journal_sweep.py:1055`, invoked `api.py:1521`) | Delivers arming-instance wakes via `manager.enqueue_message(priority=2)`; journal-file idempotency (structural pop) | ✅ **the architectural template** for a new boot pass (never-raises, fire-and-forget, durable consumption marker) |

**Minimal call to continue ONE dormant-RUNNING instance today** (composed from existing primitives; no new mechanism):
1. (skip if already RUNNING — the normal crash case) if PAUSED: `resume_instance_cascade(instance_id)`;
2. `dormant = task_repo.find_paused_or_cancellable_turn(instance_id)` (matches RUNNING);
3. `_schedule_explicit_handle_resume(instance_id, ..., silent=True, target_work_id=dormant.work_id)` → background `_process_message_with_tracking(is_retry=True, silent=True)` → `graph_input=None` → checkpoint continuation.
Guard with `_has_checkpoint(instance_id)` (`instance_messaging.py:1438`) before step 3.

## 2. Empirical gap verdict

**🚨 CONFIRMED (code + logs): NO boot pass re-dispatches an interrupted RUNNING instance's turn.** Frozen instances recover only via (a) StaleTaskRecovery at boot+10 min default (checkpoint-resuming retry task, budget-limited), (b) an external message/child-report/watchdog nudge, or (c) eventual Pattern-f1 DEAD-finalization / operator cleanup. Log forensics across the LIVE install (Sep 27 → Oct 4): **33 `is alive (running)` and 171 `is alive (waiting_children)` recovery lines, ZERO auto-resumes**; `is_retry` appears **0 times** in the daemon runtime path; instance `5b4c47a4` survived **32 consecutive daemon restarts** (Sep 30 12:36→13:17) frozen each time, DEAD-finalized only at 13:22:42 by Pattern f1, manually deleted at 14:16:37. The `JobRecoveryService` alive-branch (`job_recovery_service.py:683-689`) literally logs "leave as PROCESSING, the observer will resume pickup" — but the observer is event-driven and a dead process emits no events.

## 3. WAITING_CHILDREN answer

**The child→parent report path is DB-DURABLE and DOES correctly wake a parked parent after restart — no new mechanism needed.** Wake = three atomic DB rows (`dependency_watchers` PENDING→FIRED via guarded UPDATE; `MessageQueue` READY `internal_report:{child}:`; `Task` PROCESS_REPORT) + `report_injections` drained inside the parent's next graph turn. `DependencyBus.start()` (`dependency_bus.py:1499-1560`) re-arms at boot: `_warm_cache` → `_recover_fired_unsent` (crash window; `enqueued_at` = C1 dedup marker) → `_sweep_orphan_watchers`. Ordering (a) child-completed-before-death: recovered idempotently at boot. Ordering (b) child-completes-after-reboot: normal durable path fires. **Caveat:** `instances.status=WAITING_CHILDREN` is cosmetic/deprecated — the authority is `bus.count_pending_for_target_sync(parent)` (`child_reports.py:2366` etc.); a boot continue-pass must use the bus count, not the status string. Known RAM-only loss: parent error-message memory (`_parent_errored`) dies with the process → crash between child-error and finalize can finalize parent as COMPLETED instead of ERROR (`dependency_bus.py:466-471`).

## 4. Top risks for the design

1. **Double-fire with pending_wakes: NOT REAL today** — `claim_pending_task`'s per-instance SQL guard (`task.instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')`, `repository.py:2230-2294`) blocks the wake's new PENDING task while the orphan RUNNING task exists. **BUT** a continue-pass that force-reaps/terminalizes the orphan task at boot OPENS the claim window for the wake → the arming instance could get wake-message + continued-turn near-simultaneously. Sequencing must respect the guard (continue-in-place keeps the row RUNNING; the wake lands behind it — matching arm-notify's intended UX where the wake reports AFTER the turn finishes).
2. **In-process idempotency is empty at boot**: `_graph_tasks` (`manager.py:506`) and `_execution_gate._locks` (`execution_gate.py:108`) are process-local — a reboot loop (boot→continue→crash→boot) is only stopped by the durable claim-guard + a durable "already-continued" marker. Adopt Pattern A (boot CAS, `maintenance_boot_sweep.py:39-60` / snapshot `mark_orphaned_running_interrupted` `api.py:707-717`) or a row-scoped instance column (e.g. `auto_continued_at`) — NOT an in-memory set.
3. **PAUSED stays parked is already encoded**: the stale-task predicate excludes PAUSED/TERMINATED instances' tasks ("recovery must not auto-resume such tasks", `repository.py:3164-3181`); `enqueue_message` also excludes PAUSED (`instance_messaging.py:1953-1954`). The new pass must preserve both carve-outs.
4. **Never wedge boot**: mirror `sweep_wake_records`' double try/except + fire-and-forget (`api.py:1510-1531`); boot placement after `job_processor.start()` (`api.py:1371`) and coordinated with the wake sweep (`api.py:1521`).
5. **Status-vocabulary scatter**: `no_active_job` is synthesized at 10+ router/tool sites; a new boot-side status string would need threading through `routers/instances.py`, `answer_helper.py`, `messages.py`, `tools/job_queue.py`, `tools/instance.py`.
6. **Retry-budget + latency**: reusing StaleTaskRecovery as the continue mechanism inherits boot+10 min latency and `max_retries=3` burn-down; the explicit `_schedule_explicit_handle_resume(silent=True)` route avoids both.
7. **Residual unknowns** (below, §Unverified): JobFeedbackObserver keying on force-cancel+retry; PROCESS_REPORT claim's cosmetic status flip; attestation-gate interaction with a boot-continue carrier.

---

# A. What exists today — the reuse inventory

## A1. Resume/continuation machinery (`instance_messaging.py` / `manager.py`)

Result-status strings and their emitters (all verified):

| Status | Emit site | Semantics | Boot-reusable? |
|---|---|---|---|
| `resuming` | `manager.py:11442` (`_schedule_explicit_handle_resume` late return) | Background resume scheduled (`asyncio.create_task(_resume_processing_background)` at `:11424`); synchronous ack only | The underlying background fn — yes |
| `silent_resume` | `manager.py:10896` (`resume_processing_job`) | **No-op return** for silent+non-WC instances (`route_outcome=internal_child_noop`); no DB write | No (explicit no-op) |
| `wake_enqueued` | `manager.py:10859` | WC-wake: `enqueue_message("continue", source="system:resume_wake", priority=0)` → fresh MessageQueue+Task + pool notify | **Pattern to mirror** (only handle-free Task-creating site) |
| `wake_failed` | `manager.py:10876` | Error sentinel when the WC-wake enqueue raised | No (sentinel) |
| `already_resuming` | `manager.py:10997` (+ `:11829` cleanup) | Dedup gate on `_graph_tasks[instance_id]` not done; finally-block always pops | In-process guard only |
| `deferred_report_recovery` | `manager.py:10792` | Recovers DEFERRED `report_injections` rows (revive terminal parent, transition, re-enter child-completion notify) | No (report lane, not own-turn) |
| `no_active_job` | routers only — `routers/messages.py:460`, `routers/answer_helper.py:525-534`, `routers/instances.py:840,865,1285,1324,1339`, `tools/job_queue.py:3768,3787`, `tools/instance.py:4899,4914` | Synthesized when `resume_processing_job` returns `None` (no handle found) | No (sentinel) |

Key mechanics:
- **Pure checkpoint resume**: `silent=True` + `is_retry=True` → `graph_input = None` → `astream(None)` continues the checkpoint with **no HumanMessage injected** (`instance_messaging.py:4267-4269`; branch structure at `:3681-3685` + `_has_checkpoint` gate `:1438`). *(Spot-verified verbatim.)*
- `is_retry` computation: `task_processor.py:521-527` (`is_retry = task.retry_count > 0 or original_resume_mode`).
- `enqueue_message` requires a non-empty message; `enqueue_message_job` (`instance_messaging.py:2284`) additionally mirrors a JobItem and accepts `idempotency_key` (partial-UNIQUE dedup, `:2357-2365`) — available if the design routes through it.

## A2. Pause/resume cascade (`instance_lifecycle.py`)

- `resume_instance_cascade` (`manager.py:10477` → `instance_lifecycle.py:3879-4124`): DB-only. Tree walk → batched `instances: paused→running` (`_resume_cascade_db_sync` `:5765`) → per-task `ResumeTurn` (`turn_transitions.py:261`, guarded `paused→pending` + clears `suspension_reason`/`resume_target_turn_id` + `reconcile_turn_mirror`) → post-commit watcher re-arm (`:3735-3847`, env `ENSEMBLE_WATCHER_REARM_ON_RESUME`, default ON) + pool notify. **No graph dispatch.**
- **There is NO single existing entry point "re-dispatch an interrupted RUNNING instance's turn."** The composite minimal sequence is EXEC-SUMMARY §1.
- `find_paused_or_cancellable_turn` (`repository.py:743-855`) accepts `status IN ('paused','running')`, `task_type IN ('process_message','process_report')` — **the boot-sweep selector**. *(Spot-verified.)*

## A3. StaleTaskRecovery (`stale_task_recovery.py`)

- **REAP-then-retry, not continue-in-place.** `recover_on_startup` (`:752`) + 60 s loop (`:200`): `find_stale_running_tasks(threshold_minutes=10 default, boot_epoch)` → per task `force_cancel_and_schedule_retry` (`:780-787`) = atomic CANCELLED + fresh PENDING retry Task (`origin="startup_stale_running"`); `max_retries=3` then permanent fail + parent error report (`:805-849`).
- **Never instant**: boot-epoch amnesty — young daemon short-circuits to `[]` (`repository.py:3161-3162`); predicate `COALESCE(last_heartbeat_at, started_at) < threshold`; **excludes instances in PAUSED/TERMINATED** with the explicit comment "recovery must not auto-resume such tasks" (`:3164-3181`). *(Spot-verified.)* If `capture_boot_epoch` failed (`_boot_epoch=None`) the amnesty degrades to legacy stricter behavior (pre-epoch beats eligible immediately).
- The retry Task **does resume the same turn from checkpoint** (retry_count>0 → `is_retry=True` → checkpoint branch; `resume_target_turn_id` wiring at `manager.py:11311`, `repository.py:746-863`). ⚠️ Note: one sub-report editorialized "loses mid-turn LLM state" — **contradicted by the mechanism chain above; treat checkpoint continuity as confirmed (🟢), latency + retry-budget as the real costs.** The instance row's status is never mutated by StaleTaskRecovery.
- Phase B `find_orphaned_cancelled_tasks` (crash-between-cancel-and-retry) and Phase C watchover-marker sweep also live here.

## A4. Boot sequence (numbered; `api.py` lifespan `:205` onward)

1. `InstanceManager.initialize()` (`api.py:399`) — engine, repos, blueprint backfill
2. `capture_boot_epoch(engine)` (`api.py:412` → `boot_epoch.py:91`) — DB-clock sentinel; must precede any heartbeat write
3. critical-notes boot probe (`api.py:422`)
4. `recover_stale_leases` (`api.py:431`, awaited)
5. `setup_worker_pool` (`api.py:439` → `pool_orchestrator.py:163`): 5a heartbeat backfill `:217-252`; 5b StaleTaskRecovery ctor `:264-288`; 5c **`StaleTaskRecovery.recover_on_startup()`** `:292`; 5d background loop start `:294`; 5e-f ReportDeliveryRecovery boot sweep + start `:308-448`; 5g-h worker/chat pools start `:496,542` (claim PENDING only)
6-10. MigrationWorker/MaintenanceApi ctors; job repo; `recover_stale_job_locks` (`api.py:507`, awaited); RetryScheduler (optional `api.py:647`)
11. **`JobRecoveryService.recover_on_startup()`** (`api.py:694` → `job_recovery_service.py:522`) — message-job-with-missing-Task → `reset_active_to_queued` (`:552-619`); terminal-instance → `_fail_orphaned_job` (`:621-647`); PAUSED instance → `processing→paused` (`:648-682`); **alive branch (`:683-689`): leave PROCESSING, no dispatch** *(spot-verified verbatim)*
12. snapshot boot sweep `mark_orphaned_running_interrupted` (`api.py:707-717`)
13. drift-reconcile periodic 300 s (`api.py:730-780`; patterns a/f/f1 — f1 DEADs orphan-ACTIVE JobItems)
14-21. OrphanWatcherSweep 90 s (`:883`); JobLockSweep (`:927`); WatchReconcileSweep 300 s (`:976`); TmpImage (`:1009`); PlaneSync (`:1054`); **WaitingChildrenWatchdog 3600 s (`:1135` — WC-scoped only, wakes hung-children parents via `enqueue_message(source="system:watchdog")`)**; LongToolNudge (`:1201`); LLMStreamWatchdog (`:1246`)
22-26. `reconcile_terminal_watches` (`:1263`); JobFeedbackObserver (`:1298`); `init_dependency_bus` (`:1306` — cache warm + fired-unsent recovery + orphan sweep); queue auto-provision (`:1314`); **`job_processor.start()` (`:1371`, claims QUEUED only)**
27. **UpgradeJournalSweepService** boot `reconcile_pending_op` + `gc_pending_actions` + **`sweep_wake_records()`** (`api.py:1510-1531`), periodic `start()` (`:1538`) — the only deliverable-bearing boot pass today
28. HTTP listener up.

`clear_all(preserve_in_flight=True)` (messages `manager.py:768-776`, tasks `:866-873`) preserves RUNNING/PAUSED — gated by `QUEUE_DISCARD_ON_STARTUP`; empirical warning exists that wiping can strand ACTIVE JobItems on alive instances (log.1:64732, job `c264aa8a`). *(Spot-verified.)*

## A5. Dependency watcher / work_notifier (child→parent)

See EXEC-SUMMARY §3. Registration: `tools/instance.py:883-894` writes `dependency_watchers` (PENDING) keyed on `source_task_id`. Fire: `dependency_bus.py:600-755` guarded `UPDATE ... WHERE state='PENDING'`. Delivery: `child_reports.py:4435-4598` → MessageQueue (`:3755-3770`, `source=internal_report:{child}:{msg_id}`) + PROCESS_REPORT Task + `report_injections` claim inside parent's graph (`graph.py:464-501`). Blueprint's "re-registers on revival" = message-driven terminal→RUNNING revival (`instance_messaging.py:1958-1967`) — watchers are status-independent DB rows, no re-registration needed; **boot re-arm happens via `bus.start()`**. Watcher re-arm on pause→resume is a separate path (`instance_lifecycle.py:3735-3847`).

## A6. Heartbeat / boot-epoch / inflight_turns

- `boot_epoch.py:91-133`: one DB-clock capture per process (`api.py:412`), best-effort (None → legacy stricter semantics). **Only two consumer families**: readiness `queue_freshness` MAX() filter (`readiness.py:117-150,490-540`) and stale/cancellable task clamps (`repository.py:3115-3182`, `:4469-4526`). It is **not** a re-dispatch gate — a continue pass can ignore it.
- Heartbeats stop at process death; pre-epoch beats invisible to readiness. `inflight_turns` is `/readyz`-payload advisory only (`readiness.py:198,229,290`) — not a gate.
- WaitingChildrenWatchdog cooldown set is per-process (reset on restart — "accepted v1 limitation", `waiting_children_watchdog.py:54-62`).

---

# B. The gap — empirical verdict

## B7. Code-path walk (definitive)

Every boot pass examined; **none re-dispatches a RUNNING instance's interrupted turn**:

| Pass | Re-dispatches RUNNING? | Evidence |
|---|---|---|
| clear_all(preserve_in_flight=True) | ❌ preserves RUNNING/PAUSED | `manager.py:768-776, 866-873` |
| capture_boot_epoch / leases / job-locks | ❌ sentinel/row cleanup only | `api.py:412,431,507` |
| StaleTaskRecovery | ⚠️ age-gated (10 min + amnesty) retry task, checkpoint-resuming; never touches instance status | `stale_task_recovery.py:752-849`, `repository.py:3115-3181` |
| JobRecoveryService alive-branch | ❌ "leave as PROCESSING" — no dispatch/notify/status flip | `job_recovery_service.py:683-689` *(verbatim)* |
| JobRecoveryService special-case | ✅ but only message-JobItem-with-missing-Task (crash window) | `job_recovery_service.py:552-619` |
| snapshot / drift (a,f,f1) / orphan-watcher / watch-reconcile / WC-watchdog / job-locks / report-recovery / wake-sweep / job_processor | ❌ each scoped to its own substrate; job_processor claims QUEUED only | see A4 |

The only automatic path back to a running instance is StaleTaskRecovery's retry — **boot+10 min floor** (amnesty clamp) and **retry-budget-consuming**.

## B8. Log forensics (LIVE install Sep 27 → Oct 4; demo = no workload, 0 alive everywhere)

| Restart event | Boot (UTC) | RUNNING at boot | Outcome |
|---|---|---|---|
| Sep 30 adoption + test boots | 09:06-09:08 | 1 (job `2e2d3699`/`ea387458`) | "Completed" ONLY via delayed LLM response from the pre-crash process (incidental, `ensemble.log.2:460`); not auto-resume |
| Sep 30 09:00 / 10:53 / 11:1x | multiple | 2 incl. `513c4421`(running), `1df28545`(WC) | Frozen; later activity only from fresh Discord messages (manual revive) |
| **Sep 30 12:36→13:17 crash loop** | 8+ restarts | `5b4c47a4` alive ×32 (running→WC drift), `5c9ed369` ×~40, `8d14f292` ×40 | **Frozen through 32 restarts**; f1 DEAD-finalized 13:22:42 (`ensemble.log.2:63162` *(verbatim)*); operator DELETE 14:16:37 |
| Oct 1 v0.16.9 promote | 17:46 | 0 alive | backlog clear only |
| Oct 2 v0.16.10 | 18:54 | 1 WC (`a2ab126f`) | Frozen + stranded-JobItem WARNING (`ensemble.log.1:64732` *(verbatim)*) |
| Sep 30 17:05/17:07 v0.16.5/v0.16.3 | 2 boots | 1 running (`1034286a`) | Same job "alive" across both boots — frozen |
| Oct 3 v0.16.11 / v0.16.12 | 04:36 / 15:56 | 0 alive | backlog clear only |

Corpus totals (recounted): 33 `is alive (running)`, 171 `is alive (waiting_children)`, **0** auto-resumes; `is_retry` **0 hits** in the daemon runtime path.

## B9. WAITING_CHILDREN across restart

Answered in EXEC-SUMMARY §3: durable, boot-recovered via `bus.start()`; both orderings safe; status string cosmetic (bus count authoritative); RAM-only error-message loss is the one known defect (parent may finalize COMPLETED instead of ERROR after a crash in the error window — pre-existing, `dependency_bus.py:466-471`, own ticket recommended).

---

# C. Coexistence + constraints

## C10. pending_wakes sweep + double-fire

- Storage: `pending_wakes` dict inside `releases/state.json` under the journal envelope (`upgrade_journal.py:782-892`); lifecycle `pending → delivering(CAS) → delivered(structural pop :1082 *(verbatim)*) | abandoned`; grace 600 s; coalesce cap 16.
- Boot order: after `job_processor.start()` (`api.py:1371`) and source wiring, boot sweep at `api.py:1521` before periodic start `:1538`. Kill-switch `ENSEMBLE_POST_RESTART_ARM_NOTIFY=0`.
- **Arming instance CAN be mid-turn RUNNING at daemon death** — the arm write happens inside the tool call (`upgrade_tools.py:2328-2335`, `:2978-2985`), tool returns, executor fires at turn-end. So yes: the wake target and a hypothetical auto-continue target can be the SAME instance.
- **Double-fire verdict: NOT REAL today.** The wake's new PENDING Task cannot be claimed while the orphan RUNNING Task exists — `claim_pending_task` per-instance guard `instance_id NOT IN (SELECT instance_id FROM task WHERE status='running')` (`repository.py:2230-2294` *(verbatim, incl. anti-starvation comment)*) — atomic in the same UPDATE...RETURNING as the pause/defer gates. enqueue_message for a RUNNING target does NOT flip status (gated at `instance_messaging.py:1953-1964`). Consequence: wake lands FIFO-behind the continued turn (the arm-notify UX: outcome report after turn finishes). **Design constraint:** a continue-pass must NOT terminalize the orphan task before the wake is sequenced, or it opens the claim window early.

## C11. Idempotency surfaces (reboot-loop safety)

| Surface | file:line | Reboot-loop-safe? |
|---|---|---|
| `_graph_tasks` + `already_resuming` | `manager.py:506, 10988-10998` | 🟡 No — process-local, empty at boot |
| `_execution_gate` asyncio locks | `execution_gate.py:108-144` | 🟡 No — process-local |
| **`claim_pending_task` per-instance running-guard** | `repository.py:2230-2294` | 🟢 **Yes — the load-bearing durable gate** |
| `uq_job_locks_slot` UNIQUE + LockManager | `manager.py:6093-6102` | 🟢 Yes (JobItems; N/A for raw enqueue path) |
| MessageQueue+Task one-transaction write | `instance_messaging.py:1769-1924` | 🟢 atomicity only — no dedup of repeat calls |
| `idempotency_key` (enqueue_message_job) | `instance_messaging.py:2357-2365` | 🟢 Yes when supplied (wake sweep doesn't supply one) |
| `has_instance_busy` (PENDING+RUNNING+PAUSED) | `repository.py:930-1051` | 🟡 Python-side pre-check, not a claim arbiter |
| wake CAS `pending→delivering` under journal lock | `upgrade_journal.py:994-1040` | 🟢 Yes |
| wake delivered = structural pop | `upgrade_journal.py:1082` | 🟢 Yes — canonical marker pattern |

**Durable-marker recommendation (precedents):** Pattern A boot CAS (`maintenance_boot_sweep.py:39-60` called from `manager.initialize()` pre-service-start; snapshot `api.py:707-717`) or a row-scoped instance column (mirrors `attestation_denied_count`). A reboot loop must respect: (1) the claim-guard (never create a second claimable task while one is RUNNING); (2) a durable already-continued marker read at boot; (3) enqueue-rollback semantics like `mark_wake_pending` (`upgrade_journal_sweep.py:1391-1417`).

## C12. Config flags / env knobs

Governing recovery today (none govern auto-continue — the feature is new):

| Knob | Default | Gates |
|---|---|---|
| `QUEUE_DISCARD_ON_STARTUP` | None/False | clear_all(preserve_in_flight) wipe (`config.py:674`) |
| `SERVICES_STALE_TASK_RECOVERY_INTERVAL` | 60 s | recovery loop cadence (`config.py:1196-1199`) |
| `SERVICES_STALE_TASK_RECOVERY_THRESHOLD_MINUTES` | 10 | reap threshold (`config.py:1271-1283`) |
| `SERVICES_TASK_HEARTBEAT_INTERVAL_SECONDS` | 30 s | beat cadence (`config.py:1493-1505`) |
| `SERVICES_MAX_TASK_RETRIES` | 3 | retry budget (`config.py:1211-1214`) |
| `SERVICES_DRIFT_RECONCILE_*` | 300/300/900 s | drift patterns (`config.py:1290-1414`) |
| `SERVICES_WAITING_CHILDREN_WATCHDOG_*` | on / 3600 / 3600 | WC watchdog (`config.py:1451-1492`) |
| `SERVICES_ORPHAN_WATCHER_SWEEP_*` | 90 / 30 s | orphan sweep (`config.py:1537-1569`) |
| `SERVICES_WATCH_RECONCILE_SWEEP_INTERVAL_SECONDS` | 300 s | mission-terminal backstop (`config.py:1748-1765`) |
| `SERVICES_REPORT_DELIVERY_RECOVERY_*` | on / 300 s | report lane (`config.py:1302-1318`) |
| `JOB_RETRY_SCHEDULER_ENABLED` | None/off | retry scheduler (`config.py:1979`) |
| `ENSEMBLE_POST_RESTART_ARM_NOTIFY` | on | wake sweep kill-switch (`upgrade_journal_sweep.py:1123`) |
| `ENSEMBLE_WATCHER_REARM_ON_RESUME` | on | pause→resume watcher re-arm (`instance_lifecycle.py:107-159`) |
| `BOOT_DB_TIMEOUT_S` | env-only | PG preflight (`daemon/__main__.py`) |

---

# Unverified items (carry into design review)

1. **JobFeedbackObserver keying** on StaleTaskRecovery force-cancel+retry (job_id-persisted vs task_id-changed) — static analysis only (`job_recovery_service` / observer); affects whether the retry's completion settles the original JobItem.
2. **PROCESS_REPORT claim's cosmetic status flip**: whether claiming the child-report Task flips a WAITING_CHILDREN parent row to RUNNING (bus is authoritative either way; `child_reports.py:3755+` does not itself set it; `send_message` revival does at `instance_messaging.py:1958-1967`). Worth one follow-up hop before pinning the boot-pass contract.
3. **Attestation-gate interaction** with a boot-continue carrier on gated leaders (`attestation_gate.py:1009-1017`) — not traced.
4. `dispatch_event_bus` wake behavior after `reset_active_to_queued` — assumed per the code's own reliance (`job_recovery_service.py:585-619`), not directly observed.
5. Boot-time concurrency of N parallel `_schedule_explicit_handle_resume` calls (one gate per instance expected; untested).
6. Disposition choice for a RUNNING task whose instance row is TERMINATED/missing (dead-letter vs skip) — existing precedent at `instance_messaging.py:1486-1510`; decision needed in design.

# Verification appendix (Wanderer spot-checks, all verbatim-confirmed)

- `job_recovery_service.py:683-689` alive-branch comment + log line — confirmed.
- `repository.py:3155-3181` boot-epoch amnesty clamp + PAUSED/TERMINATED exclusion comment — confirmed.
- `instance_messaging.py:4267-4269` "Pure checkpoint resume ... graph_input = None" — confirmed.
- `repository.py:743+` selector filters `status IN ('paused','running')`, `task_type IN ('process_message','process_report')` — confirmed.
- `manager.py:10833-10850` WC-wake `"continue"` / `system:resume_wake` / `priority=0` — confirmed.
- `repository.py:2270-2294` claim-guard SQL + anti-starvation comment — confirmed.
- `upgrade_journal.py:1078-1086` structural `pending.pop(run_id)` + `journal_write` — confirmed.
- Logs: Pattern-f1 DEAD line (`ensemble.log.2:63162`), stranded-JobItem WARNING (`ensemble.log.1:64732`; sub-report cited :64720 — 12-line offset, content identical), alive-line recounts 33/171 vs reported 31/172 (counting drift only).
- One sub-report conflict resolved against evidence: StaleTaskRecovery retry DOES checkpoint-resume (see A3).

*Method note: 5 delegated read-only investigations (resume machinery; boot passes + gap; child→parent wake + epochs; log forensics; coexistence + idempotency), each independently spot-verified by the Wanderer. No files outside this planning artifact were written; nothing committed.*
