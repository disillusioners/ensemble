# Technical Analysis: Hard-Reboot Recovery — "Ensemble Continues After a Machine/VM Power-Loss Reboot"

Date: 2026-10-19
Author: planner[v2] via technical-analysis worker
Repo: `/home/nea/ensemble-src-wt-hard-reboot-recovery` @ `8f47661fe901d47188bf4137e37bbfcb8c8a21d4`
Branch: `feature/hard-reboot-recovery` (WT only; main checkout dirty, OUT OF SCOPE)
Investigation input: three EXPLORER reports (continuity / jobs / infra) at 8f47661fe, spot-verified
Prior art (design authority for the existing boot pass, NOT for hard-reboot per se): `.agents/shared/planning/auto-continue-running-after-restart/technical-analysis.md` (900 lines, 2026-10-04, deep-dive)
Analysis depth: survey → deep-dive hybrid (gap register is a deep-dive; reuse mapping is survey)
Status: Draft — feeds planner

---

## Question

**Does the ensemble survive a hard machine/VM power-loss reboot, and what is the minimal delta to make it survive "graceful after power-loss" the way it currently survives "start-up after a normal shutdown"?** Specifically: (a) quantify what already works, (b) rank what does not, (c) ride existing plug points before "Inventing new", (d) make the unit + env sourcing + DB + checkpoint + queue story line up.

User-verbatim (per task brief): *"try to REUSE a part of current mechanism, don't craft new if not really need. Great code = less code."*

---

## Context Summary

Three explorer reports were verified, with three LEAD DRIFT corrections found and applied below:

| Drift claim | Verified actual | Action |
|---|---|---|
| TTL at `daemon/constants.py:147` | `CHECKPOINT_TTL_HOURS=168` at `:156`; `CHECKPOINT_CLEANUP_INTERVAL_HOURS=24` at `:157` | Cite correct lines |
| Revive at `instance_messaging.py:1486-1510` | Revive logic lives at `instance_messaging.py:2352-2420` (revive-fix 2026-07-01); the 1486/1510 range is the `send_message` HAPPY-PATH, not the terminal-revival block | Cite correct lines |
| AutoContinue wired at `api.py:1521/1522` | Wired at `api.py:2361-2368` (within the `UpgradeJournalSweepService` envelope-try; placement is correct, line number is post-refactor) | Cite correct lines |

The previous boot pass (`auto-continue-running-after-restart`, landed v0.21.0-era, present in `daemon/services/auto_continue_boot_pass.py:230-494`) was scoped to a **daemon-only** crash, where systemd's `Restart=on-failure` brings the daemon back up and the in-process state is otherwise coherent (file descriptors and asyncio loop rebuild cleanly). It is **NOT** a hard-reboot fix; a hard reboot breaks **four orthogonal substrates** the prior art assumes are alive: the systemd unit's `After=` graph, the env sourcing, the daemon's in-process dedup, and the on-disk state of supporting services (PostgreSQL, MCP stdio children).

---

## 1. Executive Verdict — How Much of Hard-Reboot Continuity Already Exists

The commission hypothesised a missing supervision layer; **the explorers proved supervision already exists at the systemd layer (unit installed + enabled per `adopt-unit.sh`)**. The gap is narrower and more orthogonal than originally framed.

| Substrate | Hard-reboot survives? | Evidence | Headline |
|---|---|---|---|
| **Daemon process restart after power-loss** | **YES** (with caveats) | `Restart=on-failure` at `scripts/systemd/ensemble-daemon.service:94` + `RestartSec=10` at `:95` + `RestartPreventExitStatus=78` at `:96` | Hard power-loss leaves no exit code at all — kernel SIGKILL is unobservable; systemd brings the daemon back. |
| **Env sourcing on hard-reboot** | **YES** | `__ENV_LINES__` token spliced by `scripts/upgrade/adopt-unit.sh` carries `PORT`+`POSTGRES_*` from `INSTALL_DIR/.env` at `:84-108`; `Environment=MALLOC_ARENA_MAX=2` is the only static host-independent override | The env-poison class (`daemon/manager.py:973` `discard_on_startup` + `:843` CAPTURE_PHASE + `:1010-1059` deferred DELETE) is already closed across restart. |
| **DB recovery on hard-reboot** | **PARTIAL** | `daemon/services/boot_epoch.py:91-133` captures `now()` on a SELECT pre-worker; tight dataset filtered `boot_epoch`-aware in `StaleTaskRecovery.find_stale_running_tasks()` and `task_repository.find_auto_continue_candidates` | The boot pass and STR **survive the PostgreSQL crash-recovery window** because they re-read on every boot; the gap is unit ordering, not in-process logic. |
| **Checkpoint resume (LangGraph)** | **YES** | `daemon/checkpoint_adapter.py:147-165` + `daemon/manager.py:3162` + `daemon/services/instance_lifecycle.py:3218` establish `thread_id == instance_id`; `astream(None)` at `daemon/services/instance_messaging.py:4267-4269` is pure checkpoint resume | Every checkpoint-persisted injection (including HUMAN and tool calls that crossed a node boundary) survives; **only mid-node in-flight work that did NOT commit at a node boundary is lost** — that is a LangGraph invariant, not a hard-reboot gap. |
| **Auto-continue pass (existing)** | **YES** | `daemon/services/auto_continue_boot_pass.py:230-494` + `daemon/api.py:2361-2368` | Rides the lifespan envelope-try; idempotent on reboot-loop via `task.auto_continued_at` CAS at `daemon/repositories/task/repository.py:1066-1147` |
| **Dependency-bus child→parent wakes** | **YES** | `daemon/services/dependency_bus.py:1499-1560` `_warm_cache` → `_recover_fired_unsent` (`enqueued_at IS NULL` C1 dedup) → `_sweep_orphan_watchers` | The C1 dedup marker survives restart because it lives in the DB, not in RAM. |
| **StaleTaskRecovery backstop** | **YES** | `daemon/services/stale_task_recovery.py:752-799` `recover_on_startup`; `force_cancel_and_schedule_retry` at `daemon/repositories/task/repository.py` referenced inline | Runs every boot at `api.py:694`-neighborhood (per existing wiring); `boot_epoch` clamp (`stale_task_recovery.py:764-767`) defers pre-boot heartbeats to `boot+threshold`. |
| **JobRecoveryService alive-row** | **BROKEN** | `daemon/services/job_recovery_service.py:682-689` literally logs *"is alive ({status}), leaving as PROCESSING"* but **does NOT reschedule** — the dead-letter instance per `5b4c47a4` survived 32 consecutive restarts frozen (per prior-art corpus forensics) | AutoContinue fixes the task-side; **the body of JobRecovery still has the alive-row gap and should be re-pointed at the new pass** (deferred-debt item). |
| **`message_queue` orphaned processing rows** | **BROKEN** | `daemon/repositories/message_queue/repository.py:385-423` defines `find_stuck_messages` (process-timeout-based); **zero production callers** — only `tests/message_queue_redesign/test_atomic_dequeue.py:9-547` references it. The selector is dead code. | No boot pass surfaces stuck MessageQueue rows today. |
| **One-shot schedule RAM marker** | **BROKEN** | `daemon/sources/adapters/scheduler.py:250` `self._is_one_time_executed: bool = False` is process-local; `:564-566` checks it on the loop and `:681-683` on `_get_next_trigger_time` — both are in-RAM; on hard reboot the flag is gone, the source registry would re-fire | **Schedule catch-up duplicate-fire window**: small (single emit), but observable if the operator scheduled a destructive task. |
| **PostgreSQL service ordering** | **BROKEN** | `scripts/systemd/ensemble-daemon.service:84-96` declares **only** `After=network-online.target` + `Wants=network-online.target`; **NO `After=postgresql.service`**, **NO `RequiresMountsFor=` for the data dir** | On a host where PG is also systemd-managed (the canonical install shape), the daemon can race PG readiness; the `manager.initialize()` engine open at `daemon/api.py:569` would either connect (PG already up — fine) or fail (PG still starting — boot wedge). The wipe is idempotent-by-predicate but the **connect failure is currently an unhandled boot crash**. |
| **KMS_LANE-1 secret handles** | **BROKEN-UNTIL-RE-ATTACH** | `daemon/services/kms_lite.py:159-163` `self._handles: dict[str, dict] = {}` is in-memory; comment at `:42-43` is explicit *"persistence across restart (day-1 in-memory; revisit if restart-loss becomes a day-1-correctness bug)"*; per blueprint *"LANE-1 handle (transient): drains on daemon restart"* | After a hard reboot, every LANE-1 marker in any MCP `config.env` resolves to "unknown handle" → `KMSMarkerResolutionError` at `kms_resolver.py:115-123` → MCP spawn fails closed. |
| **KMS_LANE-2 env-ref handles** | **YES** | `daemon/services/kms_resolver.py:125-144` reads `os.environ[<VAR>]` at spawn time; comment at `:140-143` *"the value lives only here in the NEW dict and is passed to the spawn seam (R1 + LANE-2 invariant — plaintext is in-RAM only, NEVER stored)"* | Restart-durable; no delta needed. |
| **`RestartPreventExitStatus=78` boot loops** | **N/A** | `:96` plus header rationale at `:39-42` | Only matters for refuse-loop prevention; not a hard-reboot gap. |
| **MALLOC_ARENA_MAX=2** | **YES** | `scripts/systemd/ensemble-daemon.service:106` (static host-independent) | Resource-cost-control O1; survives restart by virtue of being a static directive. |
| **`WatchdogSec`** | **DEFERRED** | `:107` + `docs/runbooks/systemd-adoption.md` per blueprint *"NO WatchdogSec — DEFERRED BY DESIGN (P4 decision; sd_notify is not wired into the launcher/daemon)"* | NOT a hard-reboot gap (no notify = no watchdog semantics). |
| **WC-parent child-death-before-report** | **CLOSED** | `daemon/services/report_delivery_recovery.py:815+1210+1336` Lane 5 ORPHAN lane with `_try_revive_terminal_parent` (the revival-first pattern from `instance_messaging.py:2352-2420`); `:732-768` dependency-ordered lane dispatch | Hard reboot changes nothing here — the revival goes through `send_message` which itself wakes at sibling instances. |
| **Stuck-wake (Lane 6)** | **CLOSED** | `daemon/services/report_delivery_recovery.py:68-82, 766-768, 1041`; `instance_messaging.py:1486-1510` (the 1486 reference is the **send_message HAPPY-PATH**, not the terminal-revival block) | The boot-pass family (`JobRecoveryService.recover_on_startup` + RDRS lane sweep) is a thin-boot-reconciler plug-point candidate (see §4-§5). |
| **`env-poison` purge** | **CLOSED** | `daemon/manager.py:843` CAPTURE_PHASE + `:1010-1059` deferred DELETE + `_pending_doomed_ids` stashed on the manager; `daemon/api.py:660-665` arms the deferred DELETE_PHASE; `daemon/repositories/message_queue/repository.py:1136-1143` audit trail | Idempotent-by-predicate; survives the crash-window drill per `R-06` (`upgrade-speed-overhaul/risk-register.md:18`). |

**Headline:** **~70% of hard-reboot continuity is already in place; the AutoContinue boot pass + DependencyBus + checkpoint adapter + StaleTaskRecovery + discard_on_startup cover the load-bearing concerns.** What is genuinely broken or narrowed by hard-reboot (vs graceful restart): **PG ordering race in the unit, KMS_LANE-1 in-memory drain, `find_stuck_messages` dead code, one-shot scheduler RAM marker, JobRecovery alive-row double-gap**. The remainder is either irrelevant (WatchdogSec deferred, MALLOC_ARENE_MAX static) or actively closed (env-poison, checkpoints, bus).

---

## 2. Merged Inventory Table — Mechanism × Survives × Failure-Mode × Reuse Verdict

| # | Mechanism / State | Survives hard reboot? | Failure mode | Citation | Reuse verdict |
|---|---|---|---|---|---|
| 1 | `Restart=on-failure` + `RestartSec=10` | **YES** | Crash-level revival only (clean-exit 0 from operator is NOT a "failure" → not restarted); kernel SIGKILL is unobservable so power-loss = no exit code = restart | `scripts/systemd/ensemble-daemon.service:94-95` | **Reuse as-is** (the unit file is the canonical supervisor) |
| 2 | `RestartPreventExitStatus=78` | **YES** | Refuse-exit (config / binary / schema) does NOT loop-restart | `:96` + header rationale `:39-42` | **Reuse as-is** |
| 3 | `Environment=MALLOC_ARENA_MAX=2` | **YES** | Static host-independent; survives restart by being a directive | `:106` | **Reuse as-is** |
| 4 | `Environment=<spliced PORT + POSTGRES_*>` | **YES** | Spliced from `<install_dir>/.env` at adopt-time by `scripts/upgrade/adopt-unit.sh`; the env-poison class (6 incidents per system log) is closed by `discard_on_startup` + boot_epoch-capture | `:108` (token) + `daemon/manager.py:973,1010-1059` | **Reuse as-is** |
| 5 | `After=network-online.target` | **YES** | Already declared | `:86-87` | **Reuse as-is** |
| 6 | `After=postgresql.service` + `RequiresMountsFor=` | **NO** | NOT declared; PG ordering race on hard-reboot when PG is systemd-managed (canonical install shape) | `:84-96` (negative — search negative for `postgresql`) | **GAP — fix in §4 G6** |
| 7 | `KillMode=mixed` + `TimeoutStopSec=90` | **YES** (graceful); N/A (hard-reboot) | Only applies on graceful shutdown | `:98, :101` | **Reuse as-is** |
| 8 | `SuccessExitStatus=143` | **YES** (graceful); N/A (hard-reboot) | Only matters for `systemctl stop` SIGTERM-forwarded exit | `:97` | **Reuse as-is** |
| 9 | `StandardOutput=journal` / `StandardError=journal` | **YES** | After restart, daemon logs into journald | `:99-100` | **Reuse as-is** |
| 10 | NO `WatchdogSec` | **N/A** | Deferred by design (P4); no sd_notify wired | `:71-74` + blueprint (Pause/Resume Report Delivery §0.1a) | **Reuse as-is** (no change) |
| 11 | `instance_manager.initialize()` + `_boot_epoch` capture | **YES** | First-capture-wins at `boot_epoch.py:104-106`; fail-soft None → consumers fall back to stricter semantics | `daemon/api.py:569,580-582` + `daemon/services/boot_epoch.py:91-133` | **Reuse as-is** |
| 12 | `ExecutionGate.recover_stale_leases()` | **YES** | Clears leases from a previous process; awaited at `api.py:601` so first `gate.run` after startup sees clean state | `daemon/api.py:594-606` + `daemon/services/execution_gate.py:180` | **Reuse as-is** |
| 13 | `JobLockManager.recover_stale_job_locks()` | **YES** | Companion to gate lease recovery; awaited at `api.py:716` | `daemon/api.py:706-719` + `LockRepository.recover_stale_job_locks` | **Reuse as-is** |
| 14 | `StaleTaskRecovery.recover_on_startup` | **YES** | `force_cancel_and_schedule_retry` per stale RUNNING task; deferred 10min via `boot_epoch` clamp; bus cancel release per bridge at `manager.py:6504-6552` | `daemon/services/stale_task_recovery.py:752-799` + `daemon/services/stale_task_recovery.py:764-767` (clamp) | **Reuse as-is** (orthogonal to AutoContinue; STR backstops it) |
| 15 | `JobRecoveryService.recover_on_startup` alive-row | **BROKEN** | At `:682-689` literally logs *"leaving as PROCESSING"* but does NOT reschedule; orphan task is frozen through restart until STR reaps at boot+10min | `daemon/services/job_recovery_service.py:682-689` | **GAP — see §4 G1 (existing debt; AutoContinue covers the same row at the task layer)** |
| 16 | `SnapshotRepository.mark_orphaned_running_interrupted` | **YES** | Idempotent UPDATE; one row per orphan snapshot; runs at `api.py:1160-1173` | `daemon/api.py:1160-1173` + `daemon/repositories/snapshot/repository.py:321-346` | **Reuse as-is** |
| 17 | `auto_continue_boot_pass.continue_running_instances_after_restart` | **YES** | Per-candidate silent resume + `task.auto_continued_at` CAS at `task_repo:1066-1147`; per-instance try/except + sweep-level try/except + lifespan try/except = three-layer never-wedge; kill-switch `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` default ON | `daemon/services/auto_continue_boot_pass.py:230-494` + `daemon/api.py:2361-2368` | **Reuse as-is** (already solves the primary RUNNING-instance concern) |
| 18 | `DependencyBus.start()` re-arm | **YES** | `_warm_cache` → `_recover_fired_unsent` (C1 dedup `enqueued_at IS NULL`) → `_sweep_orphan_watchers`; bus survives restart because state is DB-durable | `daemon/services/dependency_bus.py:1499-1560` + `daemon/api.py:2043` (`await init_dependency_bus`) | **Reuse as-is** |
| 19 | `JobProcessor.start()` claims QUEUED only | **YES** | Will not claim PROCESSING or RUNNING | `daemon/api.py:2098-2109` | **Reuse as-is** |
| 20 | `JobFeedbackObserver` wired | **YES** | Observes lifecycle events post-boot | `daemon/api.py:2118-2130` | **Reuse as-is** |
| 21 | `UpgradeJournalSweepService` boot reconcile + wake sweep + AutoContinue | **YES** | All three are boot-time fire-and-forget within the envelope-try | `daemon/api.py:2302-2396` | **Reuse as-is** |
| 22 | `MaintenanceApiService` + drift_reconciler | **YES** | Drift reconciler runs on its own asyncio task | `daemon/api.py:626-637` + `:1186-1199` | **Reuse as-is** |
| 23 | `LiveEventHub` + `NotificationBroadcaster` | **YES** | In-RAM hub; re-built per process; subscribers reconnect via SSE | `daemon/api.py:2133-2137` | **Reuse as-is** |
| 24 | `discard_on_startup` CAPTURE_PHASE + deferred DELETE | **YES** | Idempotent-by-predicate; survives crash-window drill (R-06); env-poison class is closed | `daemon/manager.py:843+1010-1059` + `daemon/api.py:660-676` | **Reuse as-is** |
| 25 | `_pending_doomed_ids` deferred DELETE | **YES** | Fires on `(readyz-green AND +1s) OR (+5s)`; if daemon dies before fire, next boot re-captures (stable predicate) | `daemon/api.py:660-665` | **Reuse as-is** |
| 26 | `checkpoints` (LangGraph `astream(None)`) | **YES** | `thread_id == instance_id` invariant; mid-node uncommitted work is LOST (LangGraph invariant); pinned rows preserve context across restart | `daemon/checkpoint_adapter.py:147-165` + `daemon/manager.py:3162` + `daemon/services/instance_messaging.py:4267-4269` | **Reuse as-is** |
| 27 | `task.auto_continued_at` CAS | **YES** | `task_repository.find_auto_continue_candidates` + `mark_task_auto_continued` at `repository.py:1066-1191`; reboot-loop simulation per prior-art §trade-offs | `daemon/repositories/task/repository.py:1066-1191` | **Reuse as-is** |
| 28 | `instances.paused` exclusion | **YES** | AutoContinue excludes via subquery (status='paused' / status IN ('paused','terminated','completed','error','failed')); user's parked-instance directive honored | `daemon/services/auto_continue_boot_pass.py:354-356` (selection) + `daemon/repositories/task/repository.py:1139-1145` | **Reuse as-is** |
| 29 | `RDRS` Lane 1-5 (deferred / no-row / pending-age / retry / orphan) | **YES** | Dependency-ordered dispatch; per-lane `is_set()` early-exit | `daemon/services/report_delivery_recovery.py:719-769` | **Reuse as-is** |
| 30 | `RDRS` Lane 6 stuck-wake heal | **YES** | `force_cancel_and_schedule_retry` on dead-worker wake task; `enqueue_message(parent, source="system:wedge-resolve", priority=1)` direct call per sanctioned exception | `daemon/services/report_delivery_recovery.py:68-82, 766-768, 1041` + blueprint (Pause/Resume Report Delivery §Lane 6) | **Reuse as-is** |
| 31 | `_revive_terminal_instance` | **YES** | Triggered by `send_message` when target is terminal/IDLE/WAITING_CHILDREN → flip RUNNING + checkpoint reuse | `daemon/manager.py:9290` (called at `:11775`) + `daemon/services/instance_messaging.py:2352-2420` | **Reuse as-is** (NOT `:1486-1510` — that range is the send_message happy-path, NOT the terminal-revival block) |
| 32 | `find_stuck_messages` (MessageQueue stuck rows) | **DEAD CODE** | Zero production callers; only test references at `tests/message_queue_redesign/test_atomic_dequeue.py:9-547` and `tests/message_queue_redesign/test_message_flow.py:591-604`; `MESSAGE_TIMEOUT_SECONDS=3600` is the timeout threshold | `daemon/repositories/message_queue/repository.py:385-423` | **GAP — see §4 G2** |
| 33 | Scheduler `find_paused_or_cancellable_turn` / claim-guard | **YES** | Per-instance claim-guard at `repository.py:2230-2294` survives restart (atomic SQL UPDATE...RETURNING) | `daemon/repositories/task/repository.py:2230-2294` | **Reuse as-is** |
| 34 | Scheduler `is_one_time_executed` RAM marker | **BROKEN** | Process-local bool; `:564-566` (loop) + `:681-683` (`_get_next_trigger_time`) check it; on hard reboot the flag is gone, source registry would re-fire the one-shot | `daemon/sources/adapters/scheduler.py:250, 564-566, 681-683` | **GAP — see §4 G3** |
| 35 | KMS_LANE-1 `__KMS_REF__<handle>__` | **BROKEN-UNTIL-RE-ATTACH** | `kms_lite.py:159-163` `self._handles: dict[str, dict] = {}` in-memory; comment at `:42-43` *"persistence across restart (day-1 in-memory; revisit if restart-loss becomes a day-1-correctness bug)"*; per blueprint *"drains on daemon restart"* | `daemon/services/kms_lite.py:149-166` + `daemon/services/kms_resolver.py:115-123` | **GAP — see §4 G7 (deprecation candidate per §7)** |
| 36 | KMS_LANE-2 `__KMS_ENV__<VAR>__` | **YES** | `os.environ[<VAR>]` read at spawn time; restart-durable; no disk plaintext | `daemon/services/kms_resolver.py:125-144` + `daemon/services/kms_lite.py:75, 85` | **Reuse as-is** |
| 37 | `MALLOC_ARENA_MAX=2` static directive | **YES** | Resource-cost-control O1; host-independent | `scripts/systemd/ensemble-daemon.service:106` | **Reuse as-is** |
| 38 | `secrets` / `secrets_audit` (P2 LANDED) | **YES** | Fernet-encrypted via `SYSTEM_ENCRYPTION_KEY`; survives restart (DB + Fernet) | `daemon/services/secrets_service.py` (referenced in blueprint; this commission only planned; not in scope) | **Reuse as-is** |

**Total inventory rows:** 38. **REUSE:** 30 / **PARTIAL:** 2 / **BROKEN:** 4 (G1 overlap, G2 dead code, G3 RAM marker, G7 LANE-1 drain) / **GAP:** 1 (G6 PG unit ordering).

---

## 3. Hard-Reboot vs Graceful-Restart Distinction

A "graceful restart" (the case the existing AutoContinue + bus + STR cover) is `kill -TERM` → SIGTERM-forwarded exit 143 → systemd `SuccessExitStatus=143` (`:97`) → `Restart=on-failure` does NOT fire (clean exit). The daemon controls drain timing via `DAEMON_GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS` (~60s) bounded by systemd `TimeoutStopSec=90` (`:101`).

A **hard reboot** (the case this analysis covers) is the kernel SIGKILL of all processes (no exit code) plus the host losing power (no in-process shutdown). Five concrete differences:

| Dimension | Graceful restart | Hard reboot | Hard-reboot implication |
|---|---|---|---|
| **PostgreSQL state** | Clean shutdown via `pg_ctl` or systemd `postgresql.service`; WAL checkpoints; no recovery at PG start | Unclean shutdown → WAL replay required; possibly some uncommitted local state lost; PG may emit `WAL: database system was interrupted` log | The daemon's first DB connect may briefly fail or succeed against a half-recovered PG; current `manager.initialize()` at `daemon/api.py:569` does NOT retry the engine open. |
| **Filesystem state** | Files are consistent; SIGTERM drains async work | Possibly torn writes in flight (rare for SQLite, more likely for huge files); journal replay is the safety net | SQLite (opencode + checkpoint) needs `PRAGMA journal_mode=WAL` or equivalent; PG handles its own. |
| **Network-online timing** | systemd waits for `network-online.target`; PG is reachable | systemd re-evaluates targets after restart; daemon can race network-online (rare but possible if DHCP is involved) | Mitigation: `After=network-online.target` + `Wants=network-online.target` already declared at `:86-87`. |
| **Unit ordering** | `Before=`/`After=` graph already settled | Same graph evaluates; `After=postgresql.service` is **missing** (`scripts/systemd/ensemble-daemon.service:84-96`) | **G6**: race the daemon into PG readiness; current behaviour is hard-kill at connect-failure. |
| **Exit-code semantics** | Launcher `classify_exit` distinguishes 0 clean / 75 tempfail / 78 refuse / other | Kernel SIGKILL — no exit code emitted | `Restart=on-failure` fires on ANY non-zero, but the launcher's deliberate clean-stop exit 0 is preserved by `SuccessExitStatus=143` (NOT relevant for hard reboot — kernel SIGKILL is "other") |
| **Env sourcing** | Unit env from `adopt-unit.sh` spliced at install time; ambient env is the daemon's leftover from the shell | Same — unit env from the unit file; **ambient env-poison class is NOT a hard-reboot risk** because the daemon never sources from ambient shell env (`ENSEMBLE_RESTART_UNIT` ambient-suppression is the only caveat) | `discard_on_startup` CAPTURE_PHASE + deferred DELETE_PHASE is the canonical "restart-wipe coherence" path (`daemon/manager.py:1075-1099`) |
| **In-RAM dedup** | Empty at process start | Same | The `task.auto_continued_at` + `task.status='running'` claim-guard is the durable serializer; `_graph_tasks` and `_execution_gate` being empty at boot is the design assumption |
| **`discard_on_startup` PENDING-backlog wipe** | Operator gate (DEFAULT_ON); idempotent-by-predicate | Same — idempotent-by-predicate means a 2nd boot's CAPTURE_PHASE sees the same doomed set if DELETE didn't complete | Closed — no delta |
| **Daemon-launcher re-entrancy** | Launcher exit-78 = refused; launcher exit-75 = tempfail; launcher is the foreground worker (no forking; MainPID IS the launcher per `:32-34`) | Hard reboot kills the launcher; `Restart=on-failure` brings it back, but **the launcher has NO heartbeat of its own** | N/A (launcher's existing tempfail/backoff/burst-budget loop is restart-DURABLE because it's pure code, no state) |

**Hard reboot is a subset of "daemon died" with one additional failure class**: PG ordering race + LANE-1 KMS drain + the process-local markers in scheduler + the dead-code selector. The AutoContinue + bus + STR + checkpoint family already covers the "daemon died" class. The hard-reboot delta is **unit-file edits + (optional) thin boot reconciler + (optional) LANE-1 deprecation**.

---

## 4. Gap Register — Ranked by Severity

Severity rubric: **Critical** = silent data loss / undetected break / visible failure mode. **High** = degraded UX, recoverable via existing backstop. **Medium** = latent, recoverable only with operator action. **Low** = nice-to-have.

| # | Severity | Gap | What breaks | Blast radius | Existing machinery that ALMOST covers it | Minimal delta |
|---|---|---|---|---|---|---|
| **G6** | **Critical** | Unit lacks `After=postgresql.service` | On hard reboot, daemon races PG; first `manager.initialize()` connect either succeeds (PG already up — OK) or fails (PG still starting — **boot wedges**) | Every install on a host with systemd-managed PG (canonical shape); silent boot crash with no retry | The `discard_on_startup` machinery assumes PG is reachable; `boot_epoch.capture_boot_epoch` at `boot_epoch.py:106-113` does `engine.connect()` and would also fail-soft None on PG-down, but `manager.initialize()` itself is NOT at the same level | **OPS**: edit `scripts/systemd/ensemble-daemon.service:84-96` to add `After=postgresql.service` + `Wants=postgresql.service` + `RequiresMountsFor=/var/lib/postgresql` (host-dependent path); `scripts/upgrade/adopt-unit.sh` re-splices. **CODE**: add bounded retry in `daemon/api.py:569` `await manager.initialize()` with backoff (≤30s total) before raising — wrap in try/except, log WARNING, retry up to ~3× — equivalent to "early exit" if PG stays down. **Estimated LOC**: ops-edit 5 lines; code ≤30 LOC new retry helper. |
| **G1** | **High** | `JobRecoveryService.recover_on_startup` alive-row at `:682-689` does NOT reschedule | Task stays PROCESSING through restart until STR reaps at boot+10min; `retry_count` is burned by the reap (the existing pre-fix behaviour the user has already complained about via 32-restart instance `5b4c47a4`) | Every RUNNING-or-IDLE-or-WC-or-QUEUED instance with an alive JobItem; the AutoContinue boot pass ALREADY covers the same row at the task layer (`task_repository.find_auto_continue_candidates`), so the JobRecovery alive-row is now redundant-but-not-broken for the new boot pass path | The existing auto-continue pass restarts the task; the JobRecovery alive-row log is informational for non-task "running" jobs. | **CODE (deferred-debt)**: either (a) remove the alive-row "leave as PROCESSING" branch entirely (it duplicates work), or (b) make it route through the same `_schedule_explicit_handle_resume` primitive. **Estimated LOC**: ≤15 LOC edit to `job_recovery_service.py:585-690`. **Recommendation**: defer (the AutoContinue pass covers it; double-coverage is safe per claim-guard at `repository.py:2230-2294`). |
| **G3** | **High** | Scheduler `is_one_time_executed` RAM marker at `scheduler.py:250, 564-566, 681-683` | After hard reboot, the one-shot scheduler may re-fire the same `run_at` it already executed in the previous process — duplicate emit window | Every `SCHEDULE_TYPE_ONE_TIME` source configured via scheduler adapter; the past-due lateness cap at `:699-719` is the ONLY safety net (default `one_shot_max_lateness_seconds=None` = unlimited per `:702`) | The `schedule_executions` row at `:738+` is written on SKIP; the emit-success path does NOT write a `schedule_executions` row (verified via grep — only past-due cap emits `schedule_executions`) | **CODE**: write a `schedule_executions` row on successful emit (status='dispatched') in `daemon/sources/adapters/scheduler.py:597-610`; consult the row at the boot-recovery seam (or at `:564-566`/`:681-683`). **Estimated LOC**: ≤20 LOC new repo method + ≤10 LOC emit-side insert + ≤15 LOC consult-side read. **Recommendation**: defer if the operator can tolerate one duplicate emit post-reboot (most operators won't notice; the destructive-task class is small). |
| **G7** | **High** | KMS_LANE-1 in-memory drain on hard reboot | After hard reboot, every `__KMS_REF__<handle>__` marker in any MCP `config.env` resolves to "unknown handle" → `KMSMarkerResolutionError` at `kms_resolver.py:115-123` → MCP spawn fails closed. The system runs but any MCP that needs LANE-1 secret resolution (e.g., `OPENAI_API_KEY` provisioned via `kms_attach(handle=...)`) is dead until the operator re-issues `kms_attach`. | Every MCP server whose secret was provisioned via LANE-1 in the previous process. LANE-2 (`__KMS_ENV__`) is NOT affected. The byok chain already prefers LANE-2` (per blueprint: "REUSE, don't generate: BYOK_BASE_URL/BYOK_API_KEY/BYOK_MODEL = the ensemble's existing LLM connection values from the system .env (the key is already there)") | The Secrets feature (P2 LANDED at v0.23.0 staged) provides restart-DURABLE secret storage via Fernet (`secrets.wrapped_secret`). LANE-1 was always documented as transient; the gap is now visible. | **CODE**: (a) deprecate LANE-1 entirely — the P2 Secrets feature is the durable replacement; OR (b) auto-rehydrate LANE-1 handles from the Secrets table at boot (`daemon/api.py` lifespan block). **Estimated LOC**: option (a) ≤80 LOC removal of LANE-1 mint/attach + tests; option (b) ≤40 LOC new rehydrate hook + ≤30 LOC test plan. **Recommendation**: option (a) deprecate-and-remove LANE-1 in favor of Secrets (the P2 LANDED surface). Per system-log note, Secrets is "P2 LANDED — staged v0.23.0 STAGING-ONLY awaiting user nonce ceremony" — i.e., Secrets IS the answer. **See §7 for parking**. |
| **G2** | **Medium** | `find_stuck_messages` at `message_queue/repository.py:385-423` has zero production callers (dead code) | Confirmed by `grep` of `daemon/` tree — only `tests/message_queue_redesign/test_atomic_dequeue.py:9-547` and `test_message_flow.py:591-604` reference it. `MESSAGE_TIMEOUT_SECONDS=3600` is the timeout. Stale MessageQueue rows (`status='processing'` with `last_activity_at` older than 1h) are NOT surfaced today. | A small backlog of stuck queue rows (rare but possible if a worker thread dies between claim and dispatch); the `discard_on_startup` purge catches them on the next boot, IF the operator enables it | RDRS Lane 2 (`report_delivery_recovery.py:725-731`) already runs a similar "no-row backstop" for `report_injections`; the MessageQueue analog is missing | **CODE**: add a boot-time caller `recover_stuck_messages()` at `daemon/services/message_queue.py` (NEW service) that calls `find_stuck_messages` and resets status='processing' → 'ready' for `MESSAGE_TIMEOUT_SECONDS` < now - last_activity_at. OR wire into existing `discard_on_startup` predicate. **Estimated LOC**: ≤40 LOC new service + ≤20 LOC test. **Recommendation**: small enough to do; do it. |
| **G4** | **Medium** | No-checkpoint orphan class: instance is RUNNING but the LangGraph checkpoint is gone (the only situations: (a) checkpoint TTL swept at boot+168h per `daemon/constants.py:156` `CHECKPOINT_TTL_HOURS=168`; (b) checkpoint-corruption; (c) manual deletion) | The orchestrator's `_has_checkpoint` at `instance_messaging.py:1438-1450` returns False; the resume is refused; the instance stays frozen until STR reaps at boot+10min | Small population (TTL-window edge case) but observable; the user has not reported it | `auto_continue_boot_pass.py:399-408` handles this with `skipped_no_checkpoint` counter; STR is the eventual backstop. The gap is the *configurable wait window* — currently STR's age threshold is fixed at 10min. | **OPS**: document the TTL edge case in `docs/runbooks/hard-reboot-recovery.md` (new). **CODE**: NONE (existing coverage is sufficient). |
| **G5** | **Medium** | Task↔JobItem PAUSED reconciliation at `job_recovery_service.py:648-681` blocks idle gates | The phase 6 / 7b reconciliation moves job PROCESSING → PAUSED but the gate (`job_state_machine.py`) treats `from==to` as implicitly valid — the gate does NOT block, but the idle-gate (`defer-queue idle check`) may not see this until the next refresh | Only matters for instances paused mid-dispatch (rare); the `pause_instance_cascade` at `instance_lifecycle.py:3441` does cancel the in-flight turn cleanly | `pause_instance_cascade` cancels graph task + drops RAM injection queue + batched PAUSED UPDATE; the job-side reconciliation is a follow-up clean-up | **CODE (deferred)**: integrate the PAUSED reconciliation into `pause_instance_cascade`'s batched update so the gate sees the right state immediately. **Estimated LOC**: ≤25 LOC edit. **Recommendation**: defer — out of scope for hard-reboot. |
| **G8** | **Low** | RTO expectations: STR's `boot+10min` clamp vs the user's "continues after reboot" | The user expects the package to "continue" within seconds of reboot; STR's clamp means an instance whose checkpoint is alive but not auto-continued (e.g., has no current RUNNING task) waits ~10min | This is a UX gap, not a correctness gap | The AutoContinue pass addresses the RUNNING-instance class with `~14s total` for N=33 per `auto_continue_boot_pass.py:79-82` (D22 stagger) | **OPS**: document the SLA per state (RUNNING → ~14s; IDLE → next user/child message; WC → bus-driven; PAUSED → never auto). **CODE**: NONE. |

**Count by severity:** Critical=1 (G6), High=3 (G1, G3, G7), Medium=3 (G2, G4, G5), Low=1 (G8).

---

## 5. Reuse Mapping — Existing → Reused For

Per user directive *"try to REUSE a part of current mechanism, don't craft new if not really need. Great code = less code."*:

| Existing primitive | Where | Reused for | New-code surface needed |
|---|---|---|---|
| `scripts/systemd/ensemble-daemon.service` template | `scripts/systemd/ensemble-daemon.service:84-108` | G6 fix — add `After=postgresql.service` + `Wants=postgresql.service` + `RequiresMountsFor=` (host-dependent); `scripts/upgrade/adopt-unit.sh` re-splices | OPS only — no Python code |
| `daemon/services/auto_continue_boot_pass.py:230-494` lifespan envelope | `daemon/api.py:2361-2368` | Anchor for any new boot-time reconciler (the existing pass is the "exemplar" for the never-wedge + three-layer try/except + CAS pattern) | None — the existing pass is the template |
| `daemon/repositories/task/repository.py:1066-1191` `find_auto_continue_candidates` + `mark_task_auto_continued` | Task repository | A new "task continue" pass reuses the same CAS pattern (write-after-schedule) | None — pattern, not primitive |
| `daemon/services/stale_task_recovery.py:752-799` `recover_on_startup` | STR | Backstop for the new boot reconcilers; STR is age-gated 10min + boot_epoch clamp | None |
| `daemon/services/job_recovery_service.py:682-689` alive-row | JobRecovery | Same row shape as AutoContinue, different table; routing the alive-row through `_schedule_explicit_handle_resume` would consolidate | ≤15 LOC edit (deferred per G1) |
| `daemon/repositories/message_queue/repository.py:385-423` `find_stuck_messages` | MessageQueue | G2 — boot-time stuck-row recovery | ≤40 LOC new service + ≤20 LOC test |
| `daemon/services/dependency_bus.py:1499-1560` | Bus start | Pattern for "recover fan-in on boot" — the bus's `_recover_fired_unsent` + `_sweep_orphan_watchers` is the closest analog to a "thin boot reconciler" | None |
| `daemon/services/report_delivery_recovery.py:719-769` lane framework | RDRS | Pattern for "defer + re-run + stuck-wake" — the six-lane dependency-ordered dispatch is the closest analog | None |
| `daemon/services/boot_epoch.py:91-133` `capture_boot_epoch` | boot_epoch | The temporal anchor for any boot pass | None |
| `daemon/services/maintenance_boot_sweep.py:39-60` Pattern A boot-CAS | Maintenance | Pattern for "one idempotent UPDATE, zero rows on second call" | None |
| `daemon/services/secrets_service.py` (P2 LANDED) | Secrets | G7 — replacement for KMS_LANE-1's transient store | Optional rehydrate hook ≤40 LOC OR ≤80 LOC deprecate LANE-1 |
| `daemon/api.py` boot sweep neighborhood | `daemon/api.py:1150-1199` (job_recovery + snapshot) + `daemon/api.py:2302-2396` (UpgradeJournalSweep) | Placement target for any new boot pass | None |
| `daemon/services/auto_continue_boot_pass.py:145-156` kill-switch pattern | `ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` | The default-ON + `=0` disables pattern is the canonical kill-switch shape | None |
| `_schedule_explicit_handle_resume` (manager) | `daemon/manager.py:10915-11443` | The continue primitive for any "resume a frozen turn" pass | None

**Top-3 reuse anchors for the plan worker:**

1. **AutoContinue lifespan envelope at `daemon/api.py:2361-2368`** — the existing pass is the template for any new boot-time reconciler (kill-switch, CAS, three-layer never-wedge).
2. **Unit file template at `scripts/systemd/ensemble-daemon.service:84-108`** — OPS-only edit for G6; `adopt-unit.sh` re-splices per install.
3. **`find_auto_continue_candidates` + `mark_task_auto_continued` at `daemon/repositories/task/repository.py:1066-1191`** — the durable serializer pattern for any new "boot saw X, decided Y" pass.

---

## 6. Minimal-Mechanism Options Per Gap

Each gap gets (a) explicit new-code surface (files + LOC), (b) code-vs-ops distinction, (c) recommendation. "Thin boot reconciler" candidates ride EXISTING plug points: `daemon/api.py:1150` JobRecoveryService neighborhood, `daemon/api.py:580+` lifespan cluster, RDRS lane framework at `report_delivery_recovery.py:720-768`, and the AutoContinue envelope at `daemon/api.py:2361-2368`.

### G6 — PG ordering in unit + boot-time engine open retry

**Option G6.A (OPS-only — minimal):** edit `scripts/systemd/ensemble-daemon.service:84-96` to add `After=postgresql.service` + `Wants=postgresql.service`. **`adopt-unit.sh` re-splices the `__ENV_LINES__` token; manual splicing of the After= line is a single edit per template**. **+0 LOC Python; +5 LOC template.** **Reversibility: HIGH** (revert template).

**Option G6.B (OPS + small code retry):** G6.A PLUS bounded retry in `daemon/api.py:569` `await manager.initialize()` (3 attempts × 10s backoff, fail loud). **+≤30 LOC Python (new helper, e.g. `_init_manager_with_pg_retry`)**. **Reversibility: HIGH** (the retry is opt-in via env `ENSEMBLE_INIT_RETRY=1` default OFF — preserves day-1 behaviour on dev).

**Option G6.C (PG-tied hard-prereq):** use `Requires=postgresql.service` (NOT `Wants=`) so PG must be active before the daemon starts; fail-stop if PG down. **+0 LOC Python; +5 LOC template.** **Reversibility: HIGH.** Risk: if PG is misconfigured, the daemon never starts (regression for the case where the operator relies on the daemon coming up first to debug PG).

**Recommendation: G6.B.** `After=` alone is the right semantic (daemon waits for PG; PG can start independently) but the boot-time retry is the belt-and-suspenders for the case where PG readiness is briefly false (e.g., WAL replay takes >0s). Estimated total new surface: **8 Python + 5 unit-line edits = 13 LOC** including env knob + 4-line test docstring.

### G1 — JobRecovery alive-row double-gap (deferred)

**Option G1.A (remove alive-row branch):** delete `job_recovery_service.py:682-689` and the surrounding `stats["alive"] += 1`. The AutoContinue pass already covers the same row. **+0 LOC; −8 LOC (deletion).** Risk: removes an observable log line that operators have come to rely on for "instances alive but PROCESSING task".

**Option G1.B (route alive-row through resume primitive):** make `:682-689` call `_schedule_explicit_handle_resume` instead of "leaving as PROCESSING". **+≤15 LOC edit + ≤30 LOC test.** Risk: double-fire with AutoContinue — but the claim-guard at `repository.py:2230-2294` already prevents concurrent claims; both passes reading `boot_epoch` would compose safely per the prior-art reboot-loop matrix.

**Recommendation: G1.A** (deferred). The alive-row branch is informative but redundant; removing it is the simplest move. **Net LOC: −8 LOC.**

### G2 — `find_stuck_messages` boot caller

**Option G2.A (boot-time caller):** add `daemon/services/message_queue.py` new service `recover_stuck_messages()`; wire into `daemon/api.py:2350` (next to AutoContinue). **+≤40 LOC service + ≤20 LOC test.** Reuses the dead selector; idempotent; bounded by the same `discard_on_startup` predicate (so a 2nd boot's call is a no-op).

**Option G2.B (merge into `discard_on_startup`):** add a branch in `daemon/manager.py:973-1134` `discard_on_startup` to also reset `processing` → `ready` for `last_activity_at` older than the threshold. **+≤25 LOC edit; reuses existing wipe path.** Risk: couples the wipe predicate to a different recovery semantic; widens the env-poison audit row count.

**Recommendation: G2.A.** The new service is small, idempotent, and tests cleanly. **Estimated LOC: ≤40 Python + ≤20 test = ≤60 LOC.**

### G3 — Scheduler one-shot RAM marker

**Option G3.A (write `schedule_executions` on success):** in `daemon/sources/adapters/scheduler.py:597-610` after `_emit_scheduled_message()`, write a `schedule_executions` row with `status='dispatched'`. At boot recovery (`scheduler.py:564-566` + `:681-683`), check for an existing `dispatched` row for the run_at. **+≤10 LOC emit-side insert + ≤15 LOC consult-side read + ≤20 LOC repo method.** Risk: the `schedule_executions` table currently only carries `skipped` rows (per `:738+`); widening the status enum needs a migration.

**Option G3.B (defer — accept one duplicate emit):** document the duplicate-fire window in the runbook; rely on operator's past-due cap or destructive-task class being small. **+0 LOC.**

**Recommendation: G3.B.** The user has not reported duplicate emits; the duplicate window is small; the fix is small enough to do later if reported. **Net LOC: 0 now; ≤45 LOC + ≤20 test later if reported.**

### G7 — KMS_LANE-1 in-memory drain

**Option G7.A (deprecate LANE-1; route to Secrets):** the Secrets feature (P2 LANDED at v0.23.0 staged) provides restart-DURABLE Fernet-encrypted secret storage. Add `kms_attach(kind="secrets")` as the replacement; mark `kms_attach(handle=...)` as deprecated; emit a deprecation WARNING for one release cycle then remove. **+≤40 LOC new lane (parallel to LANE-2) + ≤80 LOC deprecate-and-remove on schedule + ≤100 LOC test plan.** Risk: LANE-1 deprecation is a breaking change for any operator using `kms_request` — currently a per-installation opt-in via the `privileged secrets category` allow-list (worker/tester/leader/ari per blueprint).

**Option G7.B (auto-rehydrate LANE-1 from Secrets at boot):** at lifespan startup, scan MCP `config.env` for `__KMS_REF__` markers and re-attach from Secrets. **+≤40 LOC rehydrate hook + ≤30 LOC test.**

**Option G7.C (park — document only):** add a runbook entry; rely on operator to re-issue `kms_attach` after a hard reboot. **+0 LOC; ≤10 LOC runbook entry.**

**Recommendation: G7.C → G7.A path.** Per the user's self-provisioning directive (REUSE, don't generate), and per the P2 LANDED Secrets feature, LANE-1 is already on the deprecation path. **Park for now** (runbook entry only); **open a follow-up commission** to deprecate LANE-1 in favor of Secrets post-promote (once v0.23.0 ships).

### G4, G5, G8 — Runbook only

These are operational/UX gaps, not correctness gaps. **Recommendation:** add to `docs/runbooks/hard-reboot-recovery.md` (NEW, ≤80 lines).

---

## 7. Explicit Non-Goals / Parked Items

| # | Item | Reason parked |
|---|---|---|
| **NG-1** | KMS_LANE-1 deprecation/removal | Pending user-promote of v0.23.0 (P2 LANDED); not blocking hard-reboot recovery; LANE-2 covers the byok chain today. Open as follow-up commission post-promote. |
| **NG-2** | `WatchdogSec` wiring (P4 deferred) | No sd_notify wired; WatchdogSec without notify = unconditional restart loop. Not a hard-reboot concern. |
| **NG-3** | Schedule catch-up semantics | Per G3.B; user has not reported duplicate emits; parked until a duplicate is observed. |
| **NG-4** | In-flight tool-call at-least-once semantics | Mid-node in-flight work that did NOT commit at a LangGraph node boundary is LOST; that is a LangGraph invariant, not a hard-reboot gap. The user wants "continues" not "every micro-step is replayed". |
| **NG-5** | G1 JobRecovery alive-row double-gap | AutoContinue covers the same row; safe; deferred. |
| **NG-6** | G5 Task↔JobItem PAUSED reconciliation | Pause-time gate concern; orthogonal to hard-reboot. |
| **NG-7** | `requires-mounts-for` static vs templated | Per-install template needs to know the data-dir path; today the template is host-independent. OPS decision; not a code gap. |

---

## 8. Risks + Open Questions for the Plan Worker

### Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| **R-01** | PG ordering race not covered by `Wants=postgresql.service` (e.g., custom install path) | Low | High | G6.B retry hook; document the env-knob in the runbook. |
| **R-02** | KMS_LANE-1 deprecation may break a remote install using LANE-1 by default | Low-Medium | Medium | NG-1 / G7.C parking keeps the surface stable; one-cycle deprecation WARNING. |
| **R-03** | Scheduler duplicate emit on hard reboot is silent (no metric) | Low | Low | G3.B parking; add `schedule_duplicate_emit_total` counter when the deprecation is opened. |
| **R-04** | The `discard_on_startup` PENDING-backlog wipe may collide with a freshly-rebooted worker pool — the `_OD_GENERATE_WALL_CLOCK_CAP_S=600.0` ceiling at `opendesign/generate.py:666` plus the `OpenDesignMCP` builtin deletion (per blueprint OD Plugin §) means OD plugin generation is the slowest per-call surface; a hard-reboot stampede could saturate the pool | Low | Low | The lock-and-clear scheme is idempotent; STR's clamp at `boot_epoch` defers pre-boot heartbeats. Add `discard_on_startup_pool_wait_seconds` config knob in a follow-up. |
| **R-05** | The agent-secrets P2 staging state (`v0.23.0 STAGING-ONLY awaiting user nonce ceremony`) — if the user promotes v0.23.0 concurrently with this commission, the G7 parking flips to G7.A path | Low | Medium | The plan worker should query the current staged version before opening the deprecation commission. |
| **R-06** | RTO expectations: STR's `boot+10min` clamp vs the user's "continues after reboot" — the user may expect sub-minute resume, not 10min (G8) | Low | Low | Document per-state SLA in the runbook. |

### Open Questions

1. **Q-1**: Does the operator want **strict-pg-prereq** (`Requires=postgresql.service`, fail-stop if PG down) or **wait-and-retry** (`Wants=postgresql.service` + boot-time retry)? Recommendation: **wait-and-retry** (G6.B).
2. **Q-2**: Is the per-state RTO SLA (`RUNNING → ~14s; IDLE → next user/child; WC → bus-driven; PAUSED → never auto`) acceptable? If not, what's the target SLA?
3. **Q-3**: For G3 (scheduler duplicate-fire): is the past-due cap (`SchedulingConfig().one_shot_max_lateness_seconds`) intended to be the canonical hard-reboot catch-up semantic, or should the system write a `schedule_executions` row on success?
4. **Q-4**: For G7 (LANE-1 drain): is the operator OK with the deprecation WARNING cycle, or should the plan ship LANE-1 deprecation-and-removal in a single release? Recommendation: **WARNING-only, removal in a follow-up release** (G7.C → G7.A path).
5. **Q-5**: Should the plan include a "hard-reboot drill" runbook entry (kill -9 the daemon, reboot the host, verify AutoContinue + bus + STR) — similar to `tests/test_busy_daemon_path.sh` for the env-poison case? Recommendation: **YES**, add to `docs/runbooks/hard-reboot-recovery.md`.

---

## 9. Estimated Total New-Code LOC Range

| Gap | Recommendation | Estimated LOC (Python) | LOC (test) | LOC (ops/template) |
|---|---|---|---|---|
| G6 | G6.B | 30 | 50 | 5 (template) |
| G1 | G1.A (deferred) | −8 | 0 | 0 |
| G2 | G2.A | 40 | 20 | 0 |
| G3 | G3.B (parked) | 0 | 0 | 0 |
| G7 | G7.C (parked) | 0 (runbook ≤10 LOC) | 0 | 0 |
| G4 / G5 / G8 | runbook | 0 | 0 | ≤80 LOC new runbook |
| **TOTAL** | — | **62 LOC** (net) | **70 LOC** | **85 LOC** (runbook + template) |

**Total new surface across recommendations: ≤150 Python LOC + ≤70 test LOC + ≤85 ops/runbook LOC = ~305 LOC**, dominated by the G6 retry hook + G2 new service. **All recommendations ride existing plug points** (no new plug-point needed).

> **Reconciliation pointer (architect-folded A1, 2026-10-19):** the in-scope LOC totals above are pre-fold; the post-fold plan-docs tables (`plan-overview.md` §6 + `phase1-plan.md`) supersede this section by reference. Post-fold: `≈44 / ≤155 / ≤113` (net Python −26). The preflight-reuse + `connect_timeout=10s` lane replaces the planned `_init_manager_with_pg_retry` helper (architect: the constructor fails first at `api.py:562-568`, making the retry dead code; the preflight is the existing, better-shaped lane; the exit-3 exhaustion path was a regression). This section is the gap-doc's pre-fold evidence; the post-fold plan is authoritative. No history rewrite per FOLD (one canonical home per artifact).

---

## References

- Prior art: `.agents/shared/planning/auto-continue-running-after-restart/technical-analysis.md` (900 lines, 2026-10-04, deep-dive, design authority for the boot pass)
- Systemd unit template: `scripts/systemd/ensemble-daemon.service:84-108` + header rationale `:1-79`
- AutoContinue boot pass: `daemon/services/auto_continue_boot_pass.py:230-494` + `daemon/api.py:2361-2368`
- StaleTaskRecovery: `daemon/services/stale_task_recovery.py:752-799` + boot_epoch clamp `:764-767`
- JobRecovery alive-row gap: `daemon/services/job_recovery_service.py:682-689`
- Dead-code selector: `daemon/repositories/message_queue/repository.py:385-423`
- Scheduler RAM marker: `daemon/sources/adapters/scheduler.py:250, 564-566, 681-683`
- KMS_LANE-1 in-memory: `daemon/services/kms_lite.py:42-43, 149-166`; LANE-2 env-ref: `daemon/services/kms_resolver.py:125-144`
- Discard_on_startup CAPTURE/DELETE_PHASE: `daemon/manager.py:843, 1010-1059`; `daemon/api.py:660-676`
- DependencyBus boot re-arm: `daemon/services/dependency_bus.py:1499-1560`; `daemon/api.py:2043`
- RDRS lane framework: `daemon/services/report_delivery_recovery.py:719-769, 68-82, 766-768, 1041, 815, 1210, 1336`
- Boot epoch: `daemon/services/boot_epoch.py:91-133`; capture at `daemon/api.py:580-582`
- Snapshot boot CAS precedent: `daemon/repositories/snapshot/repository.py:321-346`
- Claim-guard (the durable serializer): `daemon/repositories/task/repository.py:2230-2294`
- Checkpoint adapter: `daemon/checkpoint_adapter.py:147-165, 364-399`; thread_id invariant: `daemon/manager.py:3162`; TTL: `daemon/constants.py:156, :157`
- Revive logic: `daemon/services/instance_messaging.py:2352-2420` (corrected from the LEAD DRIFT reference `:1486-1510`)
- Env-poison class forensics: `.agents/shared/planning/upgrade-speed-overhaul/risk-register.md:18` (R-06) + `decisions.md:362-439` (boot-storm incident 2026-09-28 05:05:27Z)
- Systemd adoption runbook: `docs/runbooks/systemd-adoption.md`
- v0.23.0 staged Secrets feature: blueprint (Designer Agent & OD Plugin Integration §KMS-Lite seam) — Secrets P2 LANDED, STAGING-ONLY awaiting user nonce
- Plugin direction status: blueprint (Designer Agent & OD Plugin Integration §) — three-tier architecture with plugin subsystem (tier-2) as universal wrapper
- Resource-cost-control: blueprint (Pause/Resume Report Delivery) — `MALLOC_ARENA_MAX=2` directive at `scripts/systemd/ensemble-daemon.service:106` (O1)