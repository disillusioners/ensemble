# REPORT 1c — Memory-only inventory: watch → `[JOB_EVENT]` notification path

- **Base:** `a1130e257a3c3bb3929455aec28cacafcbfaa1a7` (`docs/jwr-memory`, worktree `/home/nea/ensemble-src-wt-jwr-memory`)
- **Date:** 2026-11-23
- **Commission:** job-watch-restart-persistence, deliverable 1(c)+(d) — READ-ONLY, Emperor decision input.
- **Method:** `grep -rnI` + `read_file` against the base tree only. No daemon boot, no pytest, no DB, no knowledge calls, no code/config/migration edits.
- **Anchor policy:** brief-supplied anchors re-anchored at base. Several were stale (noted inline). Anything not re-verified is labelled "Could not verify".

Facts first; open questions at the end.

---

## 1. Briefed item (i) — the "job_inject RAM-only FIFO queue": CONFIRMED + CORRECTED/EXTENDED

**Correction to framing.** There is no structure *named* `job_inject` queue. `job_inject` is an **agent tool** (`daemon/tools/job_queue.py:3545`) that is one of *four* writers into a single manager-owned RAM FIFO:

- **Actual structure + owner:** `InstanceManager._pending_injections: dict[str, list[dict[str, str]]]` — `daemon/manager.py:1782` (owner: `InstanceManager`). Declared RAM-only, no DB persistence (comment `:1773-1781`).
- **API:** `set_injection` append (`:5233-5340`), `get_injection` peek (`:5342-5363`), `get_injection_count` (`:5365-5380`), `clear_injection` pop (`:5382-5400`), `requeue_injections` prepend (`:5402-5452`, the D2 get/clear-race repair). TTL sweep constant `_INJECTION_TTL_SECONDS = 3600` (`:5231`).
- **Graph-side handle:** `InjectionSlot` (`daemon/graph.py:314-357`, `get`/`clear` delegate to the manager); drained in `create_agent_node` at `daemon/graph.py:9161` (peek) and `:9235` (clear). Mutable state lives only on the manager; the slot holds no state.
- **Writers:** HTTP `POST /messages` (`daemon/routers/messages.py`), agent-tool `send_message` (`daemon/tools/instance.py`), `job_inject` (`daemon/tools/job_queue.py`), chat-source ingest (`daemon/sources/registry.py`) — enumerated in `daemon/constants.py:940-944`. Eligibility set `INJECTION_ELIGIBLE_STATUSES = {"running"}` (`daemon/constants.py:960-961`); `WAITING_CHILDREN` was **removed** (`:948-958`, wc-wake-report-integrity T2) — so the `messages.py:64-65` prose claiming the slot "survives until the next agent turn resumes" for a parked WC parent is **stale**; a WC parent must be woken by durable `enqueue_message`.
- **Restart semantics:** the injected `HumanMessage` is checkpoint-persisted only **after** the drain (C2, `manager.py:5227-5229`, `graph.py:9196-9216`). Entries still queued at restart are lost. This is the single most direct RAM-loss gap in the path.
- **Anchor drift:** brief hints `manager.py:5286-5316` land on `set_injection` docstring (correct area); `graph.py:9204`/`:9309` are the drain-loop comments (correct area); `messages.py:56` is the eligibility-set comment (correct area); `constants.py:918-947` is the rationale block (correct). One *internal* drift: `constants.py:934-935` still cites drain at `graph.py:2871` and report-injection at `:3021`; actual sites are `graph.py:~9161` and `~9480`.

**Extension — the sibling, DB-backed report-injection queue.** `ReportInjectionSlot` (`graph.py:697-772`) is explicitly separate; its `drain()` calls `ReportInjectionRepository.claim_for_injection` (DB, atomic). It has a **RAM-only fast-path hint** `InstanceManager._report_injection_pending: set[str]` (`manager.py:1549`; bumped in `child_reports._dispatch_post_commit_side_effects`, checked at `graph.py:746-748`, discarded at `:770-771`). Loss of this hint on restart is NOT data loss (DB claim is source of truth) but causes `drain()` to early-return `[]` for every instance until a fresh enqueue re-bumps the set → first-turn-after-restart delivery latency, backed by the PROCESS_REPORT fallback (`graph.py:755-766`).

## 2. Briefed item (ii) — in-memory busy/counter locks cleared on restart: CONFIRMED + CORRECTED

- **CORRECTION (important):** the **per-instance busy guards are NOT RAM-only**. `TaskRepository.has_instance_busy` (`daemon/repositories/task/repository.py:1620-1641`) and `has_inflight_task` (`:1545`) are DB queries over the `task` table (PENDING/RUNNING/PAUSED). They **survive restart**. So do `claim_pending_task`'s NOT-EXISTS guards and the JobItem/queue predicates. The restart-persistence risk here is therefore *not* the busy guard itself.
- **DependencyBus** (`daemon/services/dependency_bus.py`) is the in-memory authority that does NOT survive restart — all of it is RAM, DB is watcher source of truth:
  - `_pending` watcher cache `:534` (DB-backed source; re-warmed).
  - `_locks` / `_parent_locks` + guards + `_lock_touch` / `_parent_lock_touch` `:547,551,561,565,572,573` (per-task/per-parent asyncio serialization; reaped by `reap_stale_locks` `:2373+`).
  - `generation: dict[str,int]` `:592` — in-memory monotonic signal. POK `predicate-discriminator-01-durable-key` applies: it cannot be a row-level match discriminator; `source_task_id` terminality is the durable key.
  - `_parent_errored` `:611`, `_parent_error_message` `:629`, `_unrecovered_child_errors` `:651`, `_error_state_guard` `:663`. The code itself documents the crash edge at `:623-628`: a crash between child-error and finalize **loses the error text → parent finalizes COMPLETED instead of ERROR**. Revive-then-clean retraction state is lost too.
- **worker_pool in-memory release:** `_cancel_bus_watchers_for_task` (`daemon/services/worker_pool.py:695-728`, called at `:867` and `:1033`). The comment at `:1027-1032` is explicit: "F6 covers the DB `job_watchers`; this covers the in-memory dependency bus." Post-restart only the DB half remains.
- **worker_pool RAM-only:** `TaskHeartbeat` `_current_task_id`/`_lock`/`_stop_event`/`_missed_heartbeats` (`:91-94`, thread recreated on boot); `_stats`+`_stats_lock` counters incl. `notifications_sent` (`:1385,1389`). Liveness is DB (`last_heartbeat_at`); counters are telemetry only.
- **job_processor RAM-only:** `_last_in_progress` / `_in_progress_since` (`daemon/services/job_processor.py:157-158`) throttle/dedup `in_progress` emits and hold the stuck-child escape-hatch clock (`:580-620`). Restart resets the throttle (possible extra `in_progress` emit) and restarts the force-fail clock.

## 3. Full inventory table — every memory-only / restart-sensitive item found in this path

| # | State | Location (file:line, at base) | Survives restart? | Effect of loss on watch delivery |
|---|---|---|---|---|
| 1 | `_pending_injections` RAM FIFO (user/agent/`job_inject` messages) | `manager.py:1782`; helpers `:5233-5452`; drain `graph.py:9161,9235` | **NO** | Undrained queued messages vanish; no wake to a parked parent. Direct loss. |
| 2 | `_report_injection_pending` fast-path hint set | `manager.py:1549`; `graph.py:746-748,770-771` | **NO** | DB report injection skipped for one/more LLM calls until re-bump; PROCESS_REPORT fallback. Latency, not loss. |
| 3 | DependencyBus `_pending` watcher cache | `dependency_bus.py:534` | **NO** (re-warmed) | None on delivery — DB `dependency_watchers` is authoritative. |
| 4 | DependencyBus `_locks`/`_parent_locks`+guards+touch clocks | `dependency_bus.py:547,551,561,565,572,573`; reap `:2373+` | **NO** | Serialization resets; safe in-process. No delivery effect. |
| 5 | DependencyBus `generation` counter | `dependency_bus.py:592` | **NO** | Orphan-race finalize re-arm loses in-flight signal; durable key is `source_task_id` terminality. |
| 6 | DependencyBus `_parent_errored`/`_parent_error_message`/`_unrecovered_child_errors` | `dependency_bus.py:611,629,651` | **NO** | Documented: crash between child-error and finalize → parent finalizes COMPLETED not ERROR; retraction set lost. |
| 7 | In-memory bus watcher release (timeout/usage-limit retry) | `worker_pool.py:695-728`, comment `:1027-1033` | **NO** (DB half via F6 survives) | If only RAM half existed, parent strands in WAITING_CHILDREN (incident 2026-06-26). |
| 8 | Task heartbeat thread state | `worker_pool.py:91-94` | **NO** | Recreated at boot; liveness via DB `last_heartbeat_at`. |
| 9 | Worker `_stats` counters | `worker_pool.py:1385,1389` | **NO** | Telemetry only (`notifications_sent` resets). |
| 10 | `in_progress` throttle + stuck-child escape-hatch clocks | `job_processor.py:157-158`, used `:580-620` | **NO** | Possible duplicate `in_progress` emit; force-fail clock restarts. |
| 11 | PP1 zero-watcher WARN dedup set (`_PP1_ZERO_WATCHER_WARN_SEEN`, cap 4096) | `work_notifier.py:236-237`; `_pp1_zero_watcher_should_warn` `:240-253` | **NO** | WARN re-fires once per `work_id` post-restart. Log hygiene only. |
| 12 | Observer outbox PP1 WARN (UNdeduped second site) | `job_feedback_observer.py:2254-2284` | N/A (no state) | Fires every zero-watcher terminal; observability only, no re-delivery. |
| 13 | Claimed `[JOB_EVENT]` in-flight envelope (local var between CAS-claim and enqueue) | `work_notifier.py:1031-1045` (claim), `:1097-1180` (envelope+enqueue) | **NO** (local) | Crash after CAS-DELETE of watcher row but before `enqueue_message` = lost notification AND lost watcher row. S15 compensation (`:1169-1180`) covers exceptions, **not process death**. Real restart gap. |
| 14 | Delivered `[JOB_EVENT]` message itself | `instance_messaging.py:2515` (`MessageQueue` row), `:2944,3523` (`enqueue_message`→`JobQueueService.enqueue`) | **YES** (DB) | Durable — the claim→enqueue hand-off is the only RAM window. |
| 15 | LiveEventHub SSE subscribers `_connections`/`_queue_keys`/`_room_channels` | `live_event_hub.py:35,47,56,59` (broadcast `:371,256`) | **NO** | All SSE clients gone; events are fire-and-forget (no buffering) → stream events during downtime dropped. (Distinct from the global `/api/notifications/stream` defect.) |
| 16 | `evaluate_mission_live` / mission_resolver caches | `mission_live_guard.py:327` (stateless; local dicts `:762`); `mission_resolver.py:558,560,970` (locals) | **N/A — no persistent cache** | **NEGATIVE finding:** re-resolves from DB each call; `freshest_activity` computed fresh (`:797`). No restart-sensitive cache here. |
| 17 | delta-arm (`watch_mission` per-receipt arm) | `job_queue.py:4383-4398`; rows in `JobWatcher` table (`watcher_repository.py:170-215`) | **YES** (DB rows) | Not RAM. Design limit: receipts minted after the call need a re-call — not a restart loss. |
| 18 | `_zombie_close_streaks` (A1/CF-3 escalate-WARN streak) | `job_recovery_service.py:562` (comment `:556-561`, "restart-reset acceptable (D-5)") | **NO** | Escalation WARN threshold resets; no delivery effect. |
| 19 | `_strand_heal_inflight` (coalescing/recursion-bound set) | `child_reports.py:308` (comment `:300-307`) | **NO** | In-flight scheduled heal dies with process; a fresh hold can schedule a new one. |
| 20 | WC-watchdog cooldowns `_notified`/`_wedge_notified`/`_nudge_counts`/`_escalation_notified`/`_release_notified` | `waiting_children_watchdog.py:731,742,761,766,771` (per-process note `:57-62`) | **NO** | Cooldowns reset → possible duplicate wedge/hang notices after restart. |
| 21 | Sweep timer liveness (WatchReconcileSweep, MissionStrandHealer) | `watch_reconcile_sweep.py:202,424`; `mission_strand_healer.py:359,386`; started `api.py:2187,2240` | **NO** (re-created at boot) | No sweeps during downtime; recovery deferred to next boot tick / boot recovery pass. |
| 22 | RDRS one-shot degrade-log flag | `report_delivery_recovery.py:865` (`_mission_lane_degrade_logged`) | **NO** | Degrade WARN re-logs once after restart. |

**Top restart-persistence risks in this path (ranked):** (1) `#13` claim→enqueue crash window (lost notification + lost watcher row); (2) `#1` undrained RAM FIFO; (3) `#6` lost child-error state → wrong terminal status; (4) `#2` report-injection hint loss (latency); (5) `#20/#11/#18` dedup/cooldown resets (duplicate notices only).

## 4. PP1 "zero-watcher terminal-fire" — related or orthogonal?

**Verdict: mostly ORTHOGONAL to restart persistence, but a first cousin with the same symptom and the same durable recovery anchor.** PP1 (report-delivery-bug-family, 2026-10-03) is a **DB watcher-row lifecycle** defect, not a RAM loss: in the v0.26.4 running-zombie incident the root's `JobWatcher` row was **deleted inside a DELETE window** (06:38:42→06:44:21, actor unverified), producing a zero-watcher terminal fire at 06:44:21 ("terminal report NOT being delivered") with **no `job_completed` event row for the root** (only the f2-synthetic one), so the (otherwise live, job-lock-holding) watcher was never woken. The fix was explicitly **routed elsewhere**, not shipped in that branch (`.agents/shared/planning/running-zombie-fix/design-decision.md:1210-1216`); it is listed as sibling strand #4 by `db-pool-idle-txn/wi-78967dba-verdict.md:325-327`, and `missed-finalize-zombie-active/architecture-recommendation.md:147` records `work_notifier` PP1 WARN as a pass-through consumer requiring no code change. The two WARN sites are `work_notifier.py:559-574` (RAM-deduped, pinned literal `PP1 zero-watcher terminal fire`, test `tests/job_queue/test_report_delivery_bug_family_pins.py::TestPP1ZeroWatcherWarn` and `tests/unit/services/test_log_noise_demotions.py::TestPP1ZeroWatcherDedupe`) and `job_feedback_observer.py:2254-2284` (undeduped). **Why it matters to this commission anyway:** both PP1 and the restart case converge on the same durable recovery anchor — the `job_completed` events row carries `result_summary`/`error` and is the re-fire source the WARN text points at; and both are "watcher row gone at terminal time" shapes. But the mechanisms differ (DB DELETE vs process death), so a fix for one does not close the other. Note also `#13` above is a *genuine* RAM-adjacent zero-delivery window that PP1's WARN would surface if it fired there.

---

## 5. Open questions (facts above; these need Emperor/developer ruling or a follow-up lane)

1. **Claim→enqueue atomicity (`#13`):** is the CAS-claim (DELETE watcher row) intentionally allowed to precede `enqueue_message`, accepting a crash-loss window? S15 handles exceptions only. Should the durable `job_completed` event row be the claim source so a crash can re-fire, or should enqueue precede claim (at-least-once + dedup)?
2. **RAM FIFO (`#1`):** is losing undrained `_pending_injections` on restart an accepted v1 limitation (like the revive-once counter), or should the queue be DB-backed / re-derived from the message queue? Note the injected message is only checkpointed *after* drain.
3. **Report-injection hint (`#2`):** should `ReportInjectionSlot.drain` consult the DB directly when the hint is cold (post-restart), rather than early-return `[]`? The one-LLM-call latency may be acceptable — needs a ruling.
4. **Child-error state (`#6`):** the "parent finalizes COMPLETED instead of ERROR" crash edge is documented as an accepted known limitation. Does the restart-persistence commission intend to close it (e.g. derive from a durable event row) or explicitly re-affirm acceptance?
5. **Bus in-memory half (`#4-7`):** confirm that DB `job_watchers` + `dependency_watchers` fully cover post-restart watch semantics, so the loss of `_locks`/`generation`/error-state is provably non-blocking for watch delivery (the code asserts it; no live proof in this READ-ONLY lane).
6. **SSE (`#15`):** is fire-and-forget SSE loss across restart acceptable, given the separate known global-stream defect? (Out of scope here unless the commission wants stream continuity.)
7. **PP1 scope:** confirm PP1 stays a separate commission (DB watcher-row lifecycle) and that this commission's restart work should only ensure the durable `job_completed` re-fire anchor survives — i.e. do not fold PP1's fix into the restart lane.
8. **Anchor drift:** `constants.py:934-935` cites `graph.py:2871`/`:3021` (stale); `messages.py:64-65` WC-survival prose is stale vs `constants.py:948-961`. Worth a docs-only cleanup lane, not this report.
