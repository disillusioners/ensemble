# Architecture Recommendation: Hard-Reboot Recovery — Plan Enrichment

Date: 2026-10-19
Author: architect (controller) — synthesis of 3 dispatched analyses, worktree-verified at `8f47661fe` + planning commit `694b135b5`
Mode: Standard Design (area fan-out; council not triggered — reversible ≤70 LOC, kill-switched, single subsystem, approaches pre-adjudicated by gap doc)
Analysts: Worker A `642fa1ab` (resilience-design — boot ordering + systemd), Worker B `1a19ff52` (resilience-design — crash-loop/idempotency), Worker C `8b4f2e8b` (data-flow-design — G2 seam + D1 edges)
Mandate honored: *"try to REUSE a part of current mechanism, don't craft new if not really need. Great code = less code."* — this review's headline finding is a reuse: **the PG-wait lane the plan builds already exists** (`_boot_db_preflight` + launcher exit-75 track).

> ⚠️ Line-anchor convention: all `file:line` cites are as-verified on this worktree at analysis time; project convention is grep-verify at implementation time (anchors drift across refactors — two such drifts were caught inside this review alone: the terminal-revive block and `_schedule_explicit_handle_resume`).

---

## 0. Executive Verdict

The plan's ~70%-reuse thesis is **confirmed**, its phasing order **stands**, but **both new code pieces as specified miss their target seam** and one of them would have actively **regressed** launcher behavior. The amended plan is ~25 Python LOC *smaller* than the original and strictly more correct.

| Focus area | Verdict | One-line essence |
|---|---|---|
| 1. Boot-sequence ordering | **AGREE w/ AMEND** | Order correct; MQ pass moves from `:2350` neighborhood to the post-boot-epoch / pre-worker-pool slot; both suspected hazards (duplicate wake, lease lapse) are non-events — with evidence |
| 2. Crash-loop / idempotency | **AMEND (critical ×2)** | Planned retry wraps the wrong call (constructor fails first); planned "idempotent selector" is select-only and the reset has no consumer; replace retry with existing preflight lane |
| 3. G2 seam choice | **AGREE-with-AMEND** | Standalone service (G2.A) is the RIGHT seam — but the 3600s age predicate is boot-invalid; must become boot-epoch-relative; never ride RDRS |
| 4. systemd dependency semantics | **AMEND** | Keep `After=`, drop `Wants=`; local `postgresql.service` on this host is a meta-unit no-op (real PG = remote 10.44.0.2); exhaustion must exit 75 (tempfail), never re-raise → exit 3; template edits don't reach existing installs (adopt-unit refuses clobber) |
| 5. D1 auto-resume edge cases | **AGREE ×3 + GAP-NAMED** | All three probed edges are covered by verified mechanisms; the task-less RUNNING-instance class is invisible to every sweep → named non-goal D13 |
| 6. Phasing verdict | **AMEND content, keep order** | P1-first stands but P1 shrinks to ~4 LOC (preflight reuse + `connect_timeout`); P2 re-scoped to hygiene + RDRS-lane-6 enablement; P3/P4 grow new entries |

---

## 1. Focus 1 — Boot-Sequence Ordering: AGREE w/ AMEND

### 1.1 Verified canonical boot order (as-built, `daemon/api.py`)

| # | Step | Anchor | Note |
|---|---|---|---|
| 0 | Entry preflight: PG `SELECT 1`, exit 75/78 | `daemon/__main__.py:143-247`; called `run_app.py:70` (frozen) + `__main__.py:467` (`python -m daemon`) | **The existing PG-wait lane** (see §4) |
| 1 | Config load + DB-settings overrides | `api.py:382-411` | throwaway engine, fail-open |
| 2 | `InstanceManager(...)` construction | `api.py:562-568` | **first DB I/O** — engine, `create_all`, migrations, column-ensures, discard CAPTURE all in `__init__` (`manager.py:657,751,755-757,773,973-1166`) |
| 3 | `await manager.initialize()` | `api.py:569` | checkpointer (2nd DB touch, `manager.py:3259`), background tasks, MaintenanceService |
| 4 | `capture_boot_epoch` | `api.py:580-582` | process-global, first-capture-wins, never persisted (`boot_epoch.py:91-133`) |
| 5 | `recover_stale_leases()` | `api.py:594-606` | **no-op** — ExecutionGate is `asyncio.Lock` (`execution_gate.py:180-193`) |
| 6 | `setup_worker_pool()` → STR boot + RDRS boot + WorkerPool.start | `api.py:609` | claims begin (`pool_orchestrator.py:305-358,433-465`) |
| 7 | Deferred DELETE_PHASE arm (discard_on_startup) | `api.py:660-676` | fires post-readyz +1..5s |
| 8 | `recover_stale_job_locks` | `api.py:706-719` | predicate DELETE, no TTL |
| 9 | JobRecoveryService + snapshot sweep | `api.py:1150-1170` | |
| 10 | DependencyBus `start()` (`_warm_cache` → `_recover_fired_unsent` → `_sweep_orphan_watchers`) | `api.py:2043` | **before** AutoContinue |
| 11 | JobProcessor.start; sources start | `api.py:2098-2153` | |
| 12 | UpgradeJournalSweep envelope → wake sweep → **AutoContinue** | `api.py:2312-2396`, wiring `:2361-2368` | last of the recovery passes |

### 1.2 Where the NEW pieces sit — amendment

- **PG retry (P1)**: the plan's placement at `:569` is **wrong by one call** — the canonical PG-down failure raises inside `InstanceManager.__init__` at `:562-568`, before the helper can fire (Workers A+B independently). Amendment in §4: the retry moves OUT of the lifespan entirely (entry preflight reuse), making both `__init__` and `initialize()` single-shot.
- **MQ boot recovery (P2)**: **AMEND placement** — wire after `capture_boot_epoch` (`:582`) and **before** `setup_worker_pool` (`:609`), not beside AutoContinue at `:2350`. Rationale: (a) the corrected predicate is boot-epoch-relative (§3), so it must follow epoch capture; (b) it completes before any claimer/RDRS lane starts, eliminating even the theoretical race with live claimers; (c) on `QUEUE_DISCARD_ON_STARTUP=true` installs the deferred wipe (step 7) later removes the *doomed subset* of the same rows — pass-then-wipe is idempotent, wipe-then-pass is also safe (predicate re-checks status).

### 1.3 The two suspected hazards — both non-events

| Suspected hazard | Adjudication | Evidence |
|---|---|---|
| MQ recovery vs AutoContinue re-arm → duplicate wakes | **Non-event (as amended)** | The corrected G2 action is reset-only with **no re-dispatch** (§2.4); a `ready` row has no dispatch consumer; rows with a live RUNNING task are excluded — AutoContinue's resume path already completes their MQ rows (step-1 cleanup in `_schedule_explicit_handle_resume`, `manager.py:12040-12440` neighborhood; `reconcile_turn_mirror` terminalizes MQ rows on task-terminal transitions, `task/repository.py:1856-2140`) |
| PG retry delaying lease clears → wrongly-lapsed leases | **Non-event** | ExecutionGate leases are `asyncio.Lock` (RAM) — `recover_stale_leases()` returns 0 (`execution_gate.py:180-193`); JobLockManager recovery is a predicate DELETE with no wall-clock TTL (`lock_repository.py:267-302`). Nothing can lapse during a 30s init delay |

---

## 2. Focus 2 — Crash-Loop / Idempotency: AMEND (critical ×2)

### 2.1 🔴 The planned retry helper never fires (seam defect)

`_init_manager_with_pg_retry` as specified (phase1-plan tasks 1.2–1.3) wraps `await manager.initialize()` at `api.py:569`. With PG down, `InstanceManager.__init__` raises first at `api.py:562-568` (`create_postgres_engine` → `create_all` → migrations → `_ensure_postgres_columns` → discard CAPTURE — `manager.py:657,751,755-757,773,973-1166`), an **unwrapped** call. The retry loop is dead code in the canonical PG-down boot; the boot instead crash-loops at ~12–16s period via the constructor path.

**R-02's premise is also wrong** (conclusion accidentally holds): CAPTURE_PHASE lives in `__init__`, not `initialize()` — the plan's review checklist pins the wrong method. The real re-entrancy hazard: `initialize()` re-invocation after a *late* failure duplicates 3 unguarded background tasks (`manager.py:3267-3284`) and constructs a second MaintenanceService/checkpointer (`:3318+, :3548`). **Amendment**: no in-lifespan retry at all (see §4); if one is ever reintroduced, it must wrap construction+init as a fresh-factory per attempt (~12 LOC), never re-invoke on a partially-built manager.

### 2.2 Reboot-loop math — corrected

- **As planned**: cycle ≈ 12–16s (boot 2–6s + constructor fail <1s + `RestartSec=10`), ~19–25 cycles per 5-min PG outage. The 3×10s=30s figure only occurs in the rare constructor-succeeds-checkpointer-fails flap. `StartLimitBurst`/`IntervalSec` absent from the unit; systemd defaults (5/10s, verified `systemctl show`) never trip at ≥10s pacing — **systemd never gives up**; the loop is sustained and benign *if* the exit lane is right (§4.3).
- **Persistent accumulation per cycle: zero.** `create_all`/column-ensures are idempotent DDL (and nothing connects when PG is down); `boot_epoch` is a process-global, first-capture-wins, never persisted; `_pending_doomed_ids` is RAM; CAPTURE's deferred DELETE fires only post-readyz (never reached). Cost = log spam + process churn.
- **STR boot+10min amnesty: intact.** STR never runs during the loop (death at `:562` precedes all recovery passes); the first successful boot captures a fresh epoch and the clamp applies as designed (`stale_task_recovery.py:752-799`).

### 2.3 🔴 R-03's "atomic transition" citation is FALSE — the selector is select-only

`find_stuck_messages` (`message_queue/repository.py:385-423`) is a `SELECT` only (Session + select, `:414-420`). The guarded-CAS shape the plan attributes to it belongs to `dequeue()` (`:302-313`). The nearest "action" primitives are unusable as-is: `schedule_retry_for_stuck` (`:485-513`) burns a retry and sets `RETRYING` — whose consumer `move_retry_ready_to_ready` (`:440-458`) is unguarded **and has zero production callers** (rows stranded forever); `fail_stuck_message` (`:460-483`) is unguarded (`session.get` → mutate → commit). **The whole stuck-recovery family is dead code, not just the selector.** The caller must ADD a new guarded UPDATE (§3.4).

### 2.4 Idempotency of the corrected pass — and the no-re-dispatch rule

- **Crash mid-loop**: with a per-row guarded UPDATE (`WHERE status='processing'`), a committed row leaves the predicate; the second boot skips it. Batched-then-crash rolls back — also safe. Idempotent across reboot-loops **in the corrected shape**.
- **At-least-once exposure if anyone ever re-dispatches**: report-family rows have layered but *terminal-state-incomplete* dedup (obligation triple covers only PENDING/DEFERRED — re-delivery of an already-delivered report is NOT blocked); jobs dedup only when callers pass `idempotency_key`; **regular instance messages have no dedup at all** — re-dispatch mints a new visible turn. Worse, `send_message` to a TERMINAL instance **revives it** (revive block `instance_messaging.py:2352-2420` neighborhood; note `:1486-1510` is the happy-path cite — the plan's own drift table flags this). **Rule: the boot pass is reset-only; re-dispatch is permanently out of scope.**
- **MQ pass × RDRS lanes**: disjoint at read time by `mq.status` (`processing` vs lane-6's required `ready`, `report_injection/repository.py:1974+`). They **compose**: a wake row claimed-then-killed (stuck `processing`, invisible to lane 6) becomes lane-6-healable after the reset; lane-6's task-retry then delivers **once** under the obligation triple. This composition — not "delivery recovery" — is G2's real second benefit.

---

## 3. Focus 3 — G2 Seam Choice: AGREE-with-AMEND

### 3.1 Standalone service (G2.A) is the right seam — confirmed

RDRS is `report_injections`-rooted with wake terminal-actions; G2 is `message_queue`-rooted with a status-reset action. No shared predicate, no shared obligation table, no shared primitive — a "G2 lane" would be a seventh lane riding a framework whose value-add (dependency ordering, write-once obligation, wake) does not apply. Riding RDRS also inherits: the loop-thread hazard already fixed three times (`pool_orchestrator.py:360-430`), and a *third* kill-switch idiom (RDRS lanes use pydantic config fields, `config.py:1891-1936`; D9 specifies env-direct).

### 3.2 ID-semantics split hazard — immune by construction, must be fenced

G2.A never joins tables, so `mq.message_id ≠ report_injection.child_message_id` cannot bite (`report_injection/repository.py:2127-2131` colon-anchored join is RDRS-only). But the hazard is latent: `mq.processing_task_id` is documented dead code (`message_queue/predicates.py:245-247`) and is the tempting-wrong key; any future "unify with RDRS" refactor re-imports the bug class. **Amendment (0 LOC)**: G2 docstring must state — single-table by design; correlate via `task.message_id` (`task/repository.py:650-661`) if ever needed; do not ride RDRS.

### 3.3 🔴 The 3600s predicate is boot-invalid — the decisive defect

`MESSAGE_TIMEOUT_SECONDS=3600` (`repository.py:67`) filters on `last_activity_at` **age**. At boot, every `processing` row predates the boot by construction — age is the wrong axis:

| Pre-boot row shape | Selector returns? | Correct? |
|---|---|---|
| `processing`, `last_activity_at` NULL | yes | correct, but rare — `claim_specific`/`dequeue` stamp `last_activity_at=now` on claim (`:298-300,:357-359`), so NULL only for claim-then-die-before-first-tick |
| `processing`, minutes-old (the COMMON hard-kill shape — `update_activity` ticks on every LLM token) | **NO** | **WRONG — missed** |
| `processing`, >1h old | yes | correct |

**The fresh-but-dead class — the most likely hard-reboot shape — is exactly the class the selector misses.** The Phase-4 E2E as planned would likely pass on a NULL-`last_activity_at` fixture and mask this. **Amendment**: boot caller uses `status='processing' AND (last_activity_at IS NULL OR last_activity_at < :boot_epoch)` — frame-clean (epoch is naive-UTC-normalized, `boot_epoch.py:88,114,136`, matching the column). Add a param or sibling method (`find_stuck_messages_before(boot_epoch)`); **do not modify the selector body** — it carries a pinned regression suite (`test_atomic_dequeue.py:428-521`) and a live steady-state semantic.

### 3.4 The reset has no downstream consumer — re-scope G2's value proposition

Delivery is **task-driven**, not queue-driven: `dequeue()`/`list_ready()`/`dequeue_by_instance()` have zero production callers; the only production READY→PROCESSING writer is `claim_specific` (`message_processing_pipeline.py:678`) for *known* message_ids with Task rows; enqueue mints MessageQueue+Task in one txn (`instance_messaging.py:2001-2340`). The repository's own warning (`repository.py:138-150`): *"delivery is task-driven only — a task-less insert sits at READY forever."* Therefore:

- Rows with a live RUNNING task → **AutoContinue owns them** (resume step-1 cleanup completes PENDING/PROCESSING/RETRYING MQ rows) — G2 must exclude them.
- Task-less rows → reset-to-`ready` is inert for delivery (and on `QUEUE_DISCARD_ON_STARTUP=true` installs the discard wipe removes the doomed subset post-readyz; shipped `config.yaml:306` defaults `false`, live `.env` overrides `true`).
- **G2's actual value = (a) queue-accounting hygiene** — stale `processing` rows are counted by `get_stats()` (`repository.py:1415-1480`) and trip the `send_message` busy-guard (`daemon/tools/instance.py:4037`: `pending_count>0 or processing_count>0`), making a *revived* instance look permanently busy to agent-to-agent sends — **a real, user-visible wedge**; **(b) lane-6 enablement** for report-family wake rows (§2.4).

**Amended action**: one new guarded repo method — `UPDATE message_queue SET status='ready', processing_started_at=NULL WHERE message_id=:id AND status='processing'` (mirrors `dequeue`'s CAS shape) — applied per-row, skip rows whose instance is terminal (revive hazard), skip rows with a RUNNING/PAUSED task (AutoContinue owns them). **Acceptance re-specified**: assert `get_stats()["processing_count"] == 0` post-boot and the busy-guard clears — NOT "the message was re-delivered."

---

## 4. Focus 4 — systemd Dependency Semantics: AMEND

### 4.1 Host reality — the dependency as templated is mostly a no-op here

Live install reads `POSTGRES_HOST=10.44.0.2:5432` (**remote**); local `postgresql.service` on this host is a Debian meta-unit (`Type=oneshot`, `ExecStart=/bin/true`, `RemainAfterExit`) — zero readiness value. For remote/tunnel PG the real dependency is `network-online.target`, **already declared**. On a host with local PG installed but unused, `Wants=postgresql.service` would **start the unused local PG at every boot** (wasted RAM on a memory-constrained box — this host was OOM-killed on 2026-10-17).

**Amendment**: keep `After=postgresql.service` (correct + harmless on local-PG hosts, inert on remote-PG hosts), **drop `Wants=`**, add 2 template comment lines stating the host-dependent intent and pointing to the runbook.

### 4.2 🔴 Exit-code analysis — the planned exhaustion path is a regression

Lifespan failure → uvicorn `sys.exit(STARTUP_FAILURE=3)` (`uvicorn/main.py:43,614`) → launcher `classify_exit(3)` = **crash lane**: backoff 10→20→40→80→160→300s under burst budget >5/600s → **abort exit 1 after ~5 cycles (~5–8 min)**. A transient PG outage (WAL replay) would leave the daemon **permanently down**. The plan's re-raise-on-exhaustion is exactly the wrong lane. (Exhaustion exiting 78 is also wrong: 78 = "restarting cannot fix" = permanent refusal for a transient condition.)

### 4.3 The reuse that replaces the helper — `_boot_db_preflight` + exit-75 track

**Already shipped**: `daemon/__main__.py:143-247` does `SELECT 1` with `connect_timeout=BOOT_DB_TIMEOUT_S=10` (`constants.py:70`); exits **75** (EX_TEMPFAIL) on unreachable PG, **78** on auth refusal; called on both entry paths (`run_app.py:70` frozen binary; `__main__.py:467` `python -m daemon`). Launcher maps 75 → 5–60s capped backoff, **budget-exempt, infinite** (`launcher.sh:1340-1346,1576-1590`) — the canonical "wait for a transient dependency" lane. A 5-min WAL replay resolves with the daemon starting cleanly the moment PG accepts connections; no crash-budget burn; systemd never sees a failure.

**Amendment (P1 rewrite)**: delete the in-lifespan retry helper; (i) verify preflight coverage is on the canonical boot path (it is, both entry modes); (ii) **+1 LOC: add `connect_timeout` to `create_postgres_engine`** (`daemon/repositories/factory.py:232-284` currently sets only `pool_pre_ping` — a firewall-DROP hangs 75–130s per attempt, making every retry budget fiction); (iii) template comments per §4.1. `ENSEMBLE_INIT_RETRY` knob: **eliminated** (no new knob; the preflight is the existing, better-shaped lane). Kill-switch count stays at one (`ENSEMBLE_HARD_REBOOT_BOOT_PASS`).

### 4.4 Template edits do not reach existing installs

`adopt-unit.sh:537` refuses to clobber ("ONE-TIME migration"); verified live `/etc/systemd/system/ensemble-main.service` has no `After=postgresql.service`. **Amendment (doc-only)**: runbook ships a 5-line drop-in example (`/etc/systemd/system/ensemble-main.service.d/10-pg-order.conf`) for existing installs; template edit serves new installs only.

### 4.5 StartLimit — non-event, documented

Defaults verified 5 starts/10s; unreachable at `RestartSec=10` pacing → systemd never rate-limits this loop. No template change needed; note in runbook.

---

## 5. Focus 5 — D1 Auto-Resume Edge Cases: AGREE ×3 + GAP-NAMED

### 5.i Mid-tool-call kill -9 — **AGREE (covered), one disclosure**

Three verified layers: checkpoints commit at superstep boundaries; on silent resume (`graph_input=None`, `instance_messaging.py:4680` → `astream(None)` `:4910`) the poisoned tail (AIMessage with unanswered `tool_call`) is healed by `_ensure_full_history_pairing` (`graph.py:468+`, invoked `:9065-9078` proactive / `:9111` reactive) which synthesizes a placeholder `ToolMessage` with deterministic id `pairing-synth-{tc_id}` (idempotent across re-heals via `add_messages` dedup); the O(1) tail helper `_ensure_tool_result_pairing` (`graph.py:342-465`) covers trailing-block injection cases. Without it the history is API-invalid — a permanent brick; with it, integrity holds. **Consequence to disclose**: the tool is NOT re-executed; the model is told the result is unavailable and may re-issue — non-idempotent tools (external sends/writes) can double-execute. This is NG-4, correctly parked; the runbook must state it in operator language (0 LOC). 🟢 Out-of-scope: placeholder text doesn't name the tool (`graph.py:336-339`) — slow triage, but that's a sealed hot path.

### 5.ii WAITING_CHILDREN vs RUNNING children — **AGREE (safe both directions)**

Bus starts at `api.py:2043`, AutoContinue at `:2365` — bus first, materially. Safe because: (a) child-completed-before-crash → watcher FIRED + `enqueued_at IS NULL` → `_recover_fired_unsent` surfaces it (`dependency_bus.py:1712-1733`) → durable finalize; (b) child-completes-after-boot → natural terminal emit enqueues the parent's report Task (durable, one txn); (c) AutoContinue **excludes** `waiting_children` parents (`task/repository.py:1155-1169` `not_in` subquery) — the bus owns them, exactly D1's split, enforced in code. The parent's wake is a durable enqueue that auto-resumes `waiting_children → RUNNING` (`instance_messaging.py:2377-2379` neighborhood). 🟡 Runbook: one-line bus-start check (`journalctl | grep "DependencyBus started"`) — a hard reboot (PG WAL replay) is the most likely bus-start failure moment, and downstream bus-`None` is a hard error with no fallback (`child_reports.py:401-410`).

### 5.iii Pause-cancel vs hard-kill — **AGREE (uniform treatment is correct AND forced)**

`CancellationReason` is RAM-only (plain Enum, `cancellation.py:10-17`; zero DB columns — grep-verified) and the resume path never consults it. The only durable discriminator is `instances.status`, which AutoContinue already keys on. Torn-pause (graph task cancelled, DB write uncommitted — the pause is one transaction, `instance_lifecycle.py:5391-5546`) → instance stays RUNNING → resumes: correct under D1's "continue" verb, since the pause never landed. Discriminating would require a new durable column = migration = the scope creep D10 forbids. 🟡 Runbook: pause-durability window disclosure ("my pause didn't stick" triage).

### 5.iv Task-less RUNNING instance class — **GAP-NAMED (must be D13)**

`find_auto_continue_candidates` roots its SELECT on `task` (`task/repository.py:1128-1131`) — a RUNNING instance with no task row cannot appear. Cross-check of every sweep: STR roots on Task; all 6 RDRS lanes root on `report_injections`; bus sweeps root on `dependency_watchers`; JobRecovery roots on `job_queue_items` (and its alive-row branch explicitly does nothing — the G1 debt); `reconcile_turn_mirror` *detects* the class (`drift_flags["instance_running_without_inflight_task"]`, `task/repository.py:2255-2281`) but logs at DEBUG and is not a boot sweep. **Both 2026-10-19 RCAs (leader turn `897011bb`; rcc mission `6b1630e7` task-less ghost governor) are this class.** An honest fix (instance-rooted selector + checkpoint probe per candidate + resume-vs-park-vs-terminate decision ≈ 45–60 LOC) exceeds this plan's ceiling and risks false-resuming design-legal orphans — worse than a frozen instance. **Required (0 Python LOC)**: D13 non-goal entry citing both RCAs; out-of-scope bullet in plan-overview §2; runbook triage query (`SELECT ... FROM instances i WHERE i.status='running' AND NOT EXISTS (SELECT 1 FROM task t WHERE t.instance_id=i.id AND t.status IN ('pending','running','paused'))`); inventory row in technical-analysis §2.

---

## 6. Focus 6 — Phasing Verdict: AMEND content, keep order

**P1-first stands** — but for a corrected reason. Failure-frequency reality: on the canonical host (remote PG), the binding boot risk is not the unit ordering (no-op) but the **launcher exit-3 crash-budget abort** on any lifespan-phase PG failure — which the preflight-reuse amendment eliminates *by deletion*. P1 becomes the smallest phase (~4 LOC) and still gates P2's envelope discipline. **P2 remains second** — its re-scoped value (busy-guard wedge clearance + lane-6 enablement) is real but lower-frequency than boot survival. **P3 grows** (six new runbook entries, §7) and stays before P4. **P4's E2E must be amended** — add the fresh-dead fixture (row claimed 60s pre-power-loss IS recovered) and the `processing_count == 0` assertion; the NULL-branch fixture alone would mask §3.3.

---

## 7. Consolidated Amended LOC Estimate

| Phase | Plan (py/test/ops) | Amended (py/test/ops) | Delta drivers |
|---|---|---|---|
| 0 Worktree gate | 0 / ≤10 / 0 | 0 / ≤10 / 0 | unchanged |
| 1 PG ordering + wait | ≤30 / ≤50 / 5 | **≈4 / ≤15 / 8** | −30 helper deleted (preflight reuse); +1 `connect_timeout`; +2 template comments, −1 `Wants=` dropped; +drop-in example |
| 2 MQ boot recovery | ≤40 / ≤20 / 0 | **≤40 / ≤30 / 0** | +6 guarded reset; +4..8 boot-epoch predicate variant; +3 doc fence; +3 skip-guards; placement move 0; tests +fresh-dead fixture +busy-guard assertion |
| 3 Runbook | 0 / ≤10 / ≤80 | 0 / ≤10 / **≤105** | +NG-4 disclosure, pause-durability window, D13 triage query, bus-start check, PG-shape/drop-in, discard-on-startup interplay |
| 4 Demo E2E | 0 / ≤80 / 0 | 0 / ≤90 / 0 | +fresh-dead fixture, `processing_count` assertion |
| **Total** | **≤70 / ≤160 / ≤85** | **≈44 / ≤155 / ≤113** | **Net Python −26**; total code surface shrinks while coverage grows |

All amendments fit the plan's ≤315 total ceiling; the ≤70 Python ceiling is *tightened* to ≈44. No migrations, no schema change, no agent-prompt files (no seal re-pin), one kill-switch (down from two).

---

## 8. Architectural Risk List (final)

- 🔴 **R-A1** *(resolved by amendment — kept as record)*: plan-as-written ships two dead mechanisms — retry helper that never fires (constructor fails first, `api.py:562`) + MQ reset with no consumer — AND regresses launcher behavior (exhaustion → exit 3 → crash-budget abort ≈5–8 min into a transient PG outage). Triple-verified (A+B+C).
- 🔴 **R-A2** *(resolved by amendment)*: 3600s age predicate misses the fresh-dead class — the most likely hard-reboot shape; the planned E2E fixture (NULL branch) would pass and mask it.
- 🟡 **R-A3**: Task-less RUNNING instance class remains unowned post-plan (D13) — both 2026-10-19 RCAs are this shape; a third occurrence is plausible before a follow-up commission lands.
- 🟡 **R-A4**: Unit-template edit reaches new installs only (`adopt-unit.sh:537` refuses clobber; live unit verified without the ordering) — without the runbook drop-in, G6.A is illusory on the existing fleet.
- 🟡 **R-A5**: `Wants=postgresql.service` on remote-PG hosts with local PG installed-but-unused would start it every boot (RAM cost — this host has an OOM history). Dropped by amendment.
- 🟡 **R-A6**: `manager.initialize()` is not re-entrant (duplicate background tasks / MaintenanceService / checkpointer leak on late-failure re-entry). Moot under the amendment; re-flag if any in-lifespan retry is ever reintroduced — must be fresh-factory-per-attempt.
- 🟡 **R-A7**: Operator-facing disclosures live or die with Phase 3: NG-4 double-side-effect window, pause-durability window, terminal-revive hazard on manual MQ row edits.
- 🟢 **R-A8**: Placeholder ToolMessage text lacks the tool name — triage friction, sealed hot path, out of scope.
- 🟢 **R-A9**: Bus-start failure coupling is pre-existing (bus-`None` = hard error, no fallback); runbook line added.

---

## 9. Decisions Pending (leader)

1. **Ratify P1 rewrite**: delete `_init_manager_with_pg_retry`; adopt preflight-reuse + `connect_timeout` (net −26 Python LOC). Alternative if entry-path coverage is judged insufficient on some install shape: fresh-factory construction+init retry (~12 LOC, exit-75 mapped on exhaustion) — not recommended.
2. **Ratify `Wants=` drop** (keep `After=` only) — or consciously accept the local-PG start side effect on mixed hosts.
3. **Ratify G2 action = guarded `processing→ready` reset** (lane-6-enabling, evidence-preserved) over the `complete()`/`fail` alternative. Implementation-time check: one unit test asserting a flipped report-family wake row enters lane-6's predicate and heals exactly once.
4. **Ratify D13** (task-less ghost class as explicit non-goal + follow-up commission + runbook triage).

## 10. Open Questions

- Prevalence of the fresh-dead `processing` shape on real data — one `SELECT` during Phase-0 env audit would quantify it.
- Whether every install shape's ExecStart chain reaches the launcher (and thus the exit-75 track) — verified for the live `ensemble-main.service` shape; spot-check demo/dev shapes during P4.
- `QUEUE_DISCARD_ON_STARTUP` default divergence (shipped `config.yaml:306` `false` vs live `.env` `true`) — decide whether G2's hygiene value justifies flipping the shipped default in a future commission.

## 11. Evidence Index

| Finding | Workers | Key anchors (grep-verify at impl time) |
|---|---|---|
| Constructor-first DB I/O defeats retry seam | A, B | `api.py:562-568`; `manager.py:657,751,755-757,773,973-1166` |
| Preflight + exit-75 lane exists | A | `daemon/__main__.py:143-247`; `run_app.py:70`; `launcher.sh:1340-1346,1576-1590`; `constants.py:70` |
| Exit-3 crash-budget abort | A | `uvicorn/main.py:43,614`; launcher crash lane |
| Missing `connect_timeout` | A | `daemon/repositories/factory.py:232-284` |
| Selector select-only; R-03 citation false | B, C | `repository.py:385-423` vs `dequeue():302-313` |
| 3600s predicate boot-invalid | A, B, C | `repository.py:67,413-423`; claim stamps `:298-300,357-359` |
| No READY consumer; task-driven dispatch | A, B, C | `repository.py:138-150,257-322`; `message_processing_pipeline.py:678`; `instance_messaging.py:2001-2340` |
| Busy-guard wedge | C | `repository.py:1415-1480`; `daemon/tools/instance.py:4037` |
| Lane-6 composition | B, C | `report_injection/repository.py:1974+`, `:2127-2131`; obligation triple `:47,180,197` |
| AutoContinue owns RUNNING-task rows | A, C | `manager.py` resume step-1 cleanup (12040-12440 nbhd); `task/repository.py:1128-1131,1155-1169` |
| Pairing heal on silent resume | C | `graph.py:336-339,342-465,468+,9065-9111`; `instance_messaging.py:4680,4910` |
| CancellationReason RAM-only | C | `cancellation.py:10-17,36`; zero repo columns (grep) |
| Bus-before-AutoContinue safety | A, C | `api.py:2043` vs `:2365`; `dependency_bus.py:1712-1733`; `instance_messaging.py:2377-2379` nbhd |
| Leases cannot lapse | A | `execution_gate.py:180-193`; `lock_repository.py:267-302` |
| Remote-PG host shape | A | live `.env` `POSTGRES_HOST=10.44.0.2`; local unit meta-unit; `adopt-unit.sh:537` |
| Task-less ghost class | C | both 2026-10-19 RCAs; `task/repository.py:2255-2281` (DEBUG-only detection) |
