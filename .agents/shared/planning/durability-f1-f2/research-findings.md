# Research Findings: Durability F-1 + F-2 (Worktree-Evidence Base)

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Authors: Explorer A (F-1 lane), Explorer B (F-2 lane), dispatcher synthesis

> **Re-anchor notice (binding).** All line numbers below are verified on the
> worktree at `18827dbd` (cut from `latest` after the auto-continue merge
> `86ea8d69`). Anchors pre-dating the durability worktree — including the
> 86ea8d69-era explorer table in `.agents/tester/RESULTS/2026-10-04-auto-continue-merge-gate.md`
> and the `wc-wake-report-integrity` plan folder on `main` — are SUPERSEDED by
> the corrected tables below where drift is noted. Implementation occurs on
> `feature/durability-f1-f2`; the boot-pass call sites in particular differ
> from 86ea8d69's original shape (call signature now passes `boot_epoch`).
>
> **Read-only note.** `/home/nea/ensemble-src` (main workdir) holds a sibling
> commission's in-flight branch. The R18 dev-E2E boot recipe referenced
> below lives ONLY there at
> `.agents/tester/LESSONS/2026-10-04-r18-dev-e2e-boot-recipe-and-pairing-gotchas.md`
> and is read-only to this commission. All other evidence is from this
> worktree.

---

## 1. Explorer A Report — F-1 Lane (Verdict: HIGH confidence)

### Q1 — F-1 fix sites: ONLY the two DependencyBus gates

Anchor verification (`daemon/services/dependency_bus.py`):

* **Gate `:671` — `emit_terminal`.** The condition is bare
  `if outcome.status == "error":` with no truthiness on `outcome.error`.
  The fall-through block at `:689-694` unconditionally stamps
  `self._parent_error_message[tgt] = outcome.error` when truthy and
  `setdefault(tgt, "child agent error")` otherwise. Phase-1 (2026-06-24)
  captured the LAST child error per parent.
* **Mirror `:859-868` — `emit_terminal_for_child_instance`.** Byte-identical
  pattern: `if outcome.status == "error":` at `:859`, `_parent_errored`
  flip at `:860`, truthiness consulted inside at `:861-868`, fallback
  `"child agent error"` at `:866-868`.
* **Observer read path UNCHANGED.** `job_feedback_observer.py:171-174`
  (inside `_resolve_finalize_status`, defined at `:125`) consults
  `bus.parent_error_message(...) or CHILD_AGENT_ERROR_FALLBACK` (constant
  defined at `:122`). The observer's own fallback means the
  truthy-error fix at the bus gates is safe and additive: tightening
  the bus producer does not change the observer's read contract.
* **STR off the wedge path.** `daemon/services/stale_task_recovery.py:805-810`
  calls `TaskRepository.fail_task` with explicit non-None error text
  (forced-fail of stale RUNNING rows only). `TaskRepository.find_stale_running_tasks`
  at `repository.py:3329-3395` filters `status == 'running'` (`:3380`),
  so a row already terminal at boot N+1 is excluded; `fail_task` no-ops
  non-running rows at `repository.py:3196-3201`. A bus-side truthy gate
  incidentally shields the STR → `ErrorReportingService._send_error_report`
  → `child_reports._emit_terminal_via_bus(status="error")` chain.

### Q2 — StaleTaskRecovery lifecycle (for the ownership documentation)

* `PoolOrchestrator.__init__` at `pool_orchestrator.py:264-288`,
  `recover_on_startup()` called synchronously at `:292` during
  `manager.setup_worker_pool()` (`api.py:439`); periodic `start()` at `:294`.
* `find_stale_running_tasks(threshold_minutes, boot_epoch=get_boot_epoch())`
  at `stale_task_recovery.py:764-767` → `repository.py:3329-3395` (predicate
  `status == 'running' AND COALESCE(last_heartbeat_at, started_at) <
  now-threshold AND instance not PAUSED/TERMINATED`; boot-epoch amnesty at
  `:3375-3376`).
* Orphaned-cancelled Phase B at `stale_task_recovery.py:855`; watchover
  Phase C at `:924`.

### Q3 — Preserve predicates (current behaviour, basis for Option-2)

* **`TaskRepository.clear_all` def at `repository.py:4282`.** Preserve
  DELETE at `:4380-4394`:
  `Task.status.notin_(['running','paused'])`
  `AND NOT EXISTS (SELECT 1 FROM job_queue_items jqi
   WHERE jqi.job_id = task.work_id
   AND jqi.admission_state IN ('active','queued')
   AND jqi.deleted_at IS NULL)`.
  Preserved rows = exactly RUNNING + PAUSED + any-status rows anchoring
  an active/queued non-deleted JobItem. **NO heartbeat / age /
  boot-epoch condition today.**
* **`MessageQueueRepository.clear_all` def at `repository.py:936`.**
  Preserve DELETE at `:1002-1008`:
  `DELETE FROM message_queue WHERE message_id NOT IN
   (SELECT message_id FROM task WHERE status IN ('running','paused'))`.
  Status-only via `task.message_id` linkage; **NO JobItem-anchor clause
  (asymmetric vs. task-side).** Comment `:998-999` notes the task table
  is intact when this runs (queue cleared BEFORE tasks in the wipe
  block — `manager.py:771-873`).

### Q4 — Option-2 ingredients (all present on this branch)

* **`boot_epoch`.** `daemon/services/boot_epoch.capture_boot_epoch(engine)`
  at `api.py:410-412` — DB-clock, first-wins per process
  (`boot_epoch.py:103-105`), process-global naive-UTC, best-effort → `None`
  on failure (`:115-130`). `get_boot_epoch()` at `:70-76`.
* **`auto_continued_at` CAS marker.** Model at `daemon/models.py:294`;
  column-ensure at `manager.py:6034`; migration
  `20261004_000001_add_task_auto_continued_at.sql:37`; CAS candidate
  filter at `repository.py:962-968`
  (`auto_continued_at IS NULL OR auto_continued_at < boot_epoch`); stamp
  UPDATE at `:1046-1050`; sole writer at `:930`. Terminalizer gate at
  `manager.py:11696-11708` accepts only when
  `boot_task.auto_continued_at is not None` (`:11700-11702`);
  call-site gating at `:11673-11683`. Complete-task guard at
  `repository.py:2859-2869`; terminalizer failure non-escaping at
  `manager.py:11709-11719`.
* **`last_heartbeat_at`.** Model at `daemon/models.py:284` (indexed);
  partial index `WHERE status='running'` at `manager.py:6020-6024`.
  Writers: `update_heartbeat` (`repository.py:2905`) ← worker heartbeat
  loop at `worker_pool.py:64,152`; `backfill_heartbeats` (`repository.py:2945`)
  ← `pool_orchestrator.py:243`; `create_one_shot_heartbeat` at `:344`.

### Q5 — Evidence file reference

`.agents/tester/RESULTS/2026-10-04-auto-continue-merge-gate.md` (tracked,
125 lines). F-1 finding line 78: at boot 3 the child's task row left the
running-set (DependencyBus None-error path set `status='failed' 10:01:35`),
and `discard_on_startup=backlog-clear` deleted it before the boot pass →
`candidates=0` → child orphaned, parent wedged. Disclosed gaps: T5.4c
PARTIAL; raw evidence under `/tmp/ac-e2e/*` and `/tmp/ac-gate/*` (volatile
— copy into repo post-merge if durable copies wanted); boot logs were
`~/ac-e2e-demo/logs/boot{1-retry,2,3,4,5}.log`. §9 item 3 commissions
this F-1/F-2 work (line 122).

### Q6 — Surprise scan: no other boot-time error=None → terminal-error site

* `status == "error"` consumers: only `:671` / `:859` of `dependency_bus.py`
  (+ comment `:1215`, docstrings, `plane_sync_watchdog_service.py:363`
  unrelated).
* `Outcome(status="error")` producers:
  * `error_reporting.py:703-707, 768-777` — explicit text by intent.
  * `child_reports.py:420, :653` (`_emit_terminal_via_bus` /
    `_emit_terminal_for_child_instance_via_bus`) — OPTIONAL error params
    (the **None-capable producers**).
  * `instance_lifecycle.py:256` (terminated) — not error-typed.
* `error=None` sites all live in completed/terminated lanes.
* Other `fail_task` calls: `stale_task_recovery.py:537, 605, 750, 887` +
  `worker_pool.py:865, 1042, 1076` — all explicit text, not boot-time.

### Reconciliation: only `:671` + `:859` need the truthy-error fix.

`stale_task_recovery.py:805-810` does NOT need the None-gate (different
mechanism, explicit non-None error text, off the wedge path because STR
scans only `status='running'` rows). `decision.md §1` records this.

---

## 2. Explorer B Report — F-2 Lane (Verdict: HIGH confidence)

### Corrected anchor table

* `_root_completion_gate` at `child_reports.py:2304` (bus-pending leg
  `:2366-2368`, FP4 tree-liveness `:2370+`).
* `_gate_wedge_resolver` at `:1874` (freshness `:1930`, L2b declared-wait
  discharge `:1935-1990`).
* Empty-final-turn closure `:1836-1843` inside `_assistant_message_fresh`
  `:1801-1872` (note: `:1822` is docstring — ~14-line drift from the
  86ea8d69-era docs).
* Dead-letter / stale-readable `:1997-2009` (paths documented
  `:1890-1907`).
* Publish `:4825-4854` (completed `:4829`, FAILED branch `:4836-4844`,
  fail-open `:4845-4854`, SSE `:4856-4864`, `CompletionRegistry`
  `:4865-4866`).
* Wake `MessageQueue` row `:3758-3769`
  (`source = f"internal_report:{instance.instance_id}:{completed_message_id}"` at `:3762`,
  `COMPLETION_REPORT`, `READY`).
* Wake `Task` row **drifted +27** to `:3862-3872` —
  `Task(task_type=PROCESS_REPORT, instance_id=parent_id,
  message_id=report_message_id, status=PENDING)` — **minted with NO
  `work_id`**.
* `:3834-3855` is the SKIP branch (marker / db_paused / dead_parent — the
  dead-parent case marks the message FAILED at `:3845`).
* `_process_child_completion_db_sync` at `:2674` (WriteGuardSession `:2709`;
  via `asyncio.to_thread`).
* Parent lock `bus._get_parent_lock` at `:2560-2574` (lock at `:2563`,
  `to_thread` `:2569-2574`, rationale comment `:2532-2559`).
* `manager.py` terminalizer at `:11660-11721` (gate conjunct
  `:11700-11702`).
* `message_queue.clear_all` at `:936` / DELETE `:1002-1008` / JOURNAL
  `:978-994`.
* `task.clear_all` at `:4282` / JOURNAL `:4337-4364` / preserve DELETE
  `:4380-4394` / commit `:4409-4410` (function ends `:4411`, not
  `:4450` as some pre-cutover docs state).

**KEY STRUCTURAL FACT:** wake Tasks minted at `:3864-3871` carry
**NO `work_id`** (NULL default) → the FP1 JobItem-anchor preserve clause
(`:4385-4394`) can never match them, and
`find_work_ids_on_active_jobs_with_alive_instances` explicitly skips
NULL-work-id rows at `:1316-1322`. A PENDING wake task is in the delete
set today **purely by its status** (`status == 'pending'` and not
`'running'` or `'paused'`).

### Idempotency guards (verified)

1. **Source-diff dedup** at `child_reports.py:3498-3510` (regular-child
   branch; async twin `:949-960`): the existing_report query is
   `instance_id == parent_id AND source ==
   "internal_report:{child}:{completed_message_id}" AND status IN
   ('ready', 'processing', 'completed')` → `idempotency_skip` at
   `:3508-3510`. **CAVEAT:** exact equality on
   `(child_id, completed_message_id)`, NOT a LIKE prefix; DB-side LIKE
   precedent exists in `report_injection/repository.py:1105`.
2. **TOCTOU:** terminal short-circuit at `:2723-2756`
   (COMPLETED / ERROR → `idempotency_skip`; PAUSED → `deferred_pause` +
   `ensure_deferred`); atomic conditional UPDATE at `:3718-3753`
   (`UPDATE instance SET status=COMPLETED WHERE instance_id = ? AND
   status NOT IN ('paused', 'completed', 'error')`; `rowcount == 0` →
   rollback + `idempotency_skip` at `:3735-3753`).
3. **Root-gate fresh-assistant:** `_gate_wedge_resolver → _assistant_message_fresh`
   at `:1801-1872` (uses `get_last_assistant_timestamp` `:1839`,
   empty-turn-aware); terminal-allow at `:1997-2009`; L2b `:1935-1990`
   → L6 escalate-and-HOLD at `:4799-4823`.
4. **Content-keyed idempotency does NOT exist** (grep-verified — no
   `hashlib` / `sha256` / `content-key` / `source-LIKE` in
   `child_reports.py`). Gate fail-open caller wrap docstring at
   `:4133-4143`.

**Net:** sweep re-run safety is grounded in (1)+(2)+(3) — **NOT** in
an assumed content-hash idempotency. The plan specifies how the sweep
derives `completed_message_id` (or why status-level guards suffice when
it cannot).

### Boot sequence (straight-line `lifespan()` at `api.py:204`; no hook registry)

1. RAG auto-test `:380-385`; `CredentialManager` `:390`.
2. **`InstanceManager` constructed `:393-398` → CONSTRUCTOR runs
   `discard_on_startup` wipe at `manager.py:771-873`:**
   `queue.clear_all(preserve_in_flight=True)` at `:772`; stranded-JobItem
   probe `:806-820` (+ WARN `:821-829`); integrity probe `:836-851`;
   `task.clear_all` in the same block (summary log `:872-873`).
   **WIPE RUNS BEFORE `boot_epoch` EXISTS** (epoch capture is step 4).
3. `await manager.initialize()` `:399`.
4. `capture_boot_epoch` `:412` (FIRST time `boot_epoch` is in scope).
5. Critical-notes probe `:422`; execution-gate stale-lease recovery
   `:431-436`.
6. `manager.setup_worker_pool()` `:439` → `PoolOrchestrator.setup` at
   `manager.py:7645-7652` → STR: heartbeat backfill at
   `pool_orchestrator.py:243`, construct `:264-288`,
   `recover_on_startup()` `:292`, periodic `start()` `:294`.
7. Routers / wiring `~:469-1300` (STR wiring `:671-677`).
8. `await init_dependency_bus(app, manager)` `:1306` →
   `DependencyBus(watcher_repo)` `:2420`, `bus.start()` `:2421`; bus
   internal order at `dependency_bus.py:1499-1560`: `_warm_cache`
   `:1540` (def `:1804`) → `_recover_fired_unsent` `:1541` (def `:1839`;
   FIRED + `enqueued_at IS NULL` `:1880-1894`) → `_sweep_orphan_watchers`
   `:1553` (def `:1896`; `DEFAULT_ORPHAN_SWEEP_GRACE_SECONDS=30` at
   `:131`; single atomic conditional UPDATE, W2 anti-TOCTOU
   `:1914-1923`; CANCELS PENDING watchers whose source task is gone).
9. Recovery loop `:2459+` is **finalization-only** (per-target:
   `has_instance_busy` → stamp+defer, else `_finalize_job` directly;
   docstring `:2433-2458`; C4 paused-preserve `:2464-2479`; never
   re-drives wake delivery).
10. `UpgradeJournalSweepService` `:1498-1507`;
    `sweep_wake_records()` `:1521` inside `if upgrade_install_dir is
    not None:` `:1509`.
11. **Auto-continue boot pass** at `:1547-1564`:
    `continue_running_instances_after_restart(manager, boot_epoch=get_boot_epoch())`
    at `:1551-1554`; envelope-try BODY level, sibling of the `if` (dev
    boots run it); comment `:1532-1546`. **Selection excludes
    WAITING_CHILDREN** (auto-continue selection predicate at
    `repository.py:915-921`: `status NOT IN paused/terminated/completed/error/failed/waiting_children`).
12. `upgrade_journal_sweep.start()` `:1571` +
    `manager.set_upgrade_journal_sweep` `:1573`.
13. `ServiceReconciliationService` awaited `sweep_once()` `:1615-1684`;
    VS Code `:1692+`.

**NEW WC SWEEP SLOTS:** after `:1564`, **inside the same envelope-try**
(except `:1565-1570`), before `:1571`. The sweep reads post-wipe state
**by design** (parent's own wipe ran earlier in the ctor).

### Parent-history read (verified, see §3 below)

`get_instance_messages(checkpointer, instance_id, manager=None)` at
`daemon/persistence.py:312-371`. Returns dicts with `role / content /
thinking / tool_calls` and — since
`daemon/utils.py:264-266` (`source = additional_kwargs.get("source");
if source: serialized["source"] = source`) — also surfaces
`additional_kwargs.source` whenever it is truthy. This was UNVERIFIED in
the dispatch synthesis; **verified on this worktree** (see
"verification gap closure" in §3).

**Marker stamping site:** `daemon/graph.py:8518-8534` (source `:8526-8529`;
FIFO-drain convention `:8479`); enqueue-lane twin via
`_stamped_additional_kwargs` at `instance_messaging.py:525`.

**REUSABLE scan precedent:** `daemon/services/attestation_resolver_activation.py` —
`_INTERNAL_REPORT_SOURCE_PREFIX` `:472`, source-shape doc `:476`,
`_is_child_report_message` `:543-564` (HumanMessage + `source.startswith("internal_report:")`),
`_extract_child_id_from_source` `:567-575` (regex group(1) = child uuid).

### Content extraction (verified)

`completion_content.get_last_assistant_message(checkpointer, instance_id)`
at `daemon/services/completion_content.py:22-56` — returns
`(content | None, created_at | None)`. Reads via `get_instance_messages`
`:46`; last non-empty assistant scanning reversed `:49-56`. Companion
`get_last_assistant_timestamp` at `:59-90` (any assistant incl. empty).
Callers: `ChildReportsService._get_last_assistant_message` and
`JobFeedbackObserver` (`result_summary`).

### Kill-switch convention (verified)

`ENSEMBLE_AUTO_CONTINUE_RUNNING_ON_RESTART` has NO `config.py` field —
env-direct:

* Module constant `AUTO_CONTINUE_KILL_SWITCH_ENV` at
  `auto_continue_boot_pass.py:145`.
* `_auto_continue_enabled()` at `:148-156` =
  `os.environ.get(ENV, "1") != "0"`.
* Read per boot, NOT cached.
* Deliberate convention (comments `:83-90`; typed config for
  intervals, `config.py:1633-1634`).
* Mirrors `ENSEMBLE_POST_RESTART_ARM_NOTIFY` at
  `upgrade_journal.py:886` (per `:142`).
* Consumed at `:245-248` → `skipped_kill_switch=1` in `ContinueResult`.

**NEW SWITCH PATTERN:** module-level `ENSEMBLE_<FEATURE_NAME>` constant
+ `_feature_enabled()` helper, default ON, read per boot at pass entry,
**NOT piggybacked** on the auto-continue switch.

### Keep-green inventory (verified)

* `tests/unit/services/test_auto_continue_boot_pass.py` (660 lines; mock
  manager + mock `TaskRepository`, no real DB; `_TaskRow` `:45-60`;
  `_MockTaskRepo` `:63+`; imports module + kill-switch constant
  `:29-37`; structural grep-proofs `:9-11` — zero `enqueue_message`
  calls, zero `datetime.now` in pass module, no reaper).
* `test_auto_continue_interleaving.py` (AC4 ordering matrix).
* `test_auto_continue_terminalizer.py` (Δ1 call-site gate, M18).
* `tests/unit/repositories/test_auto_continue_candidates.py` (selection
  + CAS SQL, M1 / M2 / M3 / M17).
* Pack format (`auto_continue_boot_pass_unit_test.sh`, 62 lines):
  `set -u`; `cd` to repo root `:29-33`; `PACK_NAME` `:35`; inner
  watchdog `timeout 110s .venv/bin/pytest <explicit file list>
  --tb=short -q` `:44-48` (Layer-2 110s; Layer-1 dispatcher timeout
  300 `:20-22`); exit-code mapping `0=PASS / 1=FAIL / 124=TIMEOUT` +
  trailing `RESULT:` line `:52-62`; transparent wrapper — no
  deselection `:24-26` (mirrors `post_restart_arm_notify_sweep` pack).
* **No `PACKS.md` under `test/` in this worktree** — packs discovered
  by directory listing. New packs must follow the same wrapper shape.

### E2E harness (R18 dev-E2E boot recipe)

Recipe exists ONLY in `/home/nea/ensemble-src` (main workdir, read-only
to this commission) at
`.agents/tester/LESSONS/2026-10-04-r18-dev-e2e-boot-recipe-and-pairing-gotchas.md`.

Key points referenced by Phase 3 (full file remains outside the
worktree; not vendored here):

* Data dir OUTSIDE worktree
  (`/home/nea/dev-daemon-8079-<tag>/data/ensemble.json`, copied verbatim
  from prior tag).
* `/tmp/<tag>-boot.sh` wrapper scrubbing `POSTGRES_*` /
  `ENSEMBLE_*` / `PORT` / `HOST` / `SSL_CERT_*`, sourcing
  `/home/nea/dev-daemon-8079-v0.16.11/boot.env`
  (`ENSEMBLE_SELF_ENV=dev`, PG `ensemble_dev@127.0.0.1`, `PORT=8079`,
  `QUEUE_DISCARD_ON_STARTUP=true` — **directly exercises the F-2
  wipe lane**), exports `ENSEMBLE_DATA_DIR`, `cd <worktree>`,
  `exec .venv/bin/python -m daemon`.
* 4-point isolation evidence (factory engine line, `/livez` version,
  `/readyz` components, `/proc/<pid>/environ`).
* Revert = kill `ss`-verified pid → `bash boot.sh` → livez.

Gotchas: branch-advance mid-commission; full `tests/unit/tools/` sweep
exceeds 300s; pre-existing 5-test failure family on base.

### Design (a) claim-side facts (verified where reachable)

* Wake Tasks minted with NULL `work_id` at
  `child_reports.py:3862-3872` → the JobItem-anchor preserve clause
  at `repository.py:4385-4394` can never match them; a PENDING wake
  task is deleted **purely by status** today.
* **Predicate extension = disjunction restructure of BOTH clear_all
  predicates** (task-side `:4380-4394` + JOURNAL mirror
  `:4337-4364`; queue-side `:1002-1008` + JOURNAL `:978-994`) — the
  keep-set gains `source LIKE 'internal_report:%'`, which needs no
  join since `source` lives on `message_queue`.
* **Claim-side gap:** a preserved wake task whose parent is
  PAUSED / TERMINATED is NEVER claimable (pause gate inside
  `claim_pending_task` WHERE, `repository.py:2148-2243`) and sits
  silently. Creation-time dead-parent guard already in place
  (`:3812-3855`): parent `None` / TERMINATED → no Task /
  `report_injection` INSERT; MessageQueue retained but FAILED `:3845`.
* COMPLETED / ERROR / FAILED parents: claim proceeds; delivery lane
  has terminal-revival semantics (A1 carve-out at
  `instance_messaging.py:1841-1874`; `_prepare_enqueued_message`
  auto-resume of IDLE / WAITING_CHILDREN / COMPLETED at `:1642-1643`;
  `send_message` revive at `:1486-1510`).
* **Residual unknown (NOT line-walked in this commission):** the full
  PROCESS_REPORT claim → task_processor → message-processing → revive
  path. If design (a) is recommended in-scope, include a verification
  task; if deferred, record the unknown.

---

## 3. Verification Gap Closure (this plan author, post-dispatch)

The dispatcher synthesis flagged: "Whether `get_instance_messages`
surfaces `additional_kwargs.source` is UNVERIFIED." This plan author
verified the question by reading `daemon/utils.py:252-266` on the
worktree:

```python
additional_kwargs = getattr(msg, "additional_kwargs", None) or {}
injected_message = additional_kwargs.get("injected_message")
if injected_message is not None:
    serialized["injected_message"] = injected_message
context_kind = additional_kwargs.get("context_kind")
if context_kind:
    serialized["context_kind"] = context_kind
source = additional_kwargs.get("source")
if source:
    serialized["source"] = source
```

**Conclusion:** the F-2 sweep's parent-history ledger check CAN rely on
`get_instance_messages` returning a dict that includes the `"source"`
key whenever the source is truthy on the underlying `HumanMessage`. The
attestation_resolver_activation precedent (`_is_child_report_message`
at `:543-564`) confirms the marker shape. No additional verification
task is required for the ledger check itself.

The implementation must still follow the FIFO-drain convention noted
in `daemon/graph.py:8479` — messages stamped with
`source="internal_report:<child_iid>"` after the marker scheme
migration carry the field; pre-migration parents do not, and the
sweep's "already-reported" check must degrade gracefully (treat
absence as "not yet reported", as it does today in the post-commit
side-effects path).

---

## 4. Dispatcher Synthesis Items (binding constraints carried into the plan)

1. **F-1 reconciliation settled** (HIGH confidence): only the two
   DependencyBus gates `:671` and `:859` need the truthy-error fix.
   `stale_task_recovery.py:805-810` is OFF the wedge path (different
   mechanism, runs only on `status='running'` rows that the F-1
   wedge never produces because the row is already terminal at boot
   N+1). Recorded in `decisions.md §1`.

2. **⚠️ NEW CONSTRAINT — epoch is not available at wipe time**
   (cross-finding A+B): the backlog-clear wipe runs in the
   `InstanceManager` CONSTRUCTOR (`api.py:393-398` →
   `manager.py:771-873`; queue `clear_all` at `:772` first, task
   `clear_all` after) which executes BEFORE
   `capture_boot_epoch(manager.engine)` at `api.py:412`. The
   commission's Option-2 condition `last_heartbeat_at >=
   boot_epoch` therefore has NO epoch to compare against at wipe
   time in the current boot order. **Phase 1 must resolve this
   explicitly** — options include (i) capturing the epoch (DB-clock,
   first-wins — reuse `daemon/services/boot_epoch.py` mechanics)
   before the wipe / inside the wipe path, (ii) reordering so epoch
   capture precedes manager construction, or (iii) relying on
   `auto_continued_at IS NOT NULL` alone for this release and
   dropping the heartbeat arm. Analyze each against the boot-sequence
   ownership contract and pick one in `decisions.md §2`. **NOTE:**
   `boot_epoch` is process-global naive-UTC, best-effort (None on
   failure) — plan the None case.

3. **F-2 sweep placement pinned:** boot is straight-line
   `lifespan()` (`api.py:204`) with NO hook registry. The WC wedge
   sweep slots immediately AFTER the auto-continue pass block
   (`api.py:1547-1564`), INSIDE the same envelope-try (except at
   `:1565-1570`), BEFORE `upgrade_journal_sweep.start()` (`:1571`).
   It must wait for: `bus.start` + its finalization-only recovery
   loop (`:1306`, `:2459+`), the wake sweep (`:1521`), and the
   auto-continue pass. The sweep reads post-wipe state **by design**
   (parent's own wipe ran earlier in the ctor).

4. **Idempotency-guard inventory for the sweep:** see §2 above. The
   plan must ground sweep re-run safety in (a)+(b)+(c) — **NOT** in
   an assumed content-hash idempotency — and specify how the sweep
   derives `completed_message_id` (or why the status-level guards
   suffice when it cannot). The `get_instance_messages` source-
   surfacing question is RESOLVED in §3 above.

5. **Design (a) feasibility facts:** see §2 above. The
   recommendation in-scope vs. deferred is decided in `decisions.md
   §3` with rationale citing the claim-side gap and the residual
   PROCESS_REPORT claim-path unknown.

6. **Kill-switch convention:** see §2 above. The sweep gets its OWN
   var following this pattern; explicitly not piggybacked.

7. **Keep-green inventory:** see §2 above. The Phase 3 pack creation
   must follow the same transparent wrapper shape.

8. **Demo E2E harness:** see §2 above. Phase 3's optional demo E2E
   references the R18 recipe but does not vendor it; the dev boot.env
   in the recipe uses `QUEUE_DISCARD_ON_STARTUP=true` which directly
   exercises the wipe lane.

---

## 5. Repo Gotchas (binding constraints)

* `.venv` is CPython 3.13 while code targets 3.14 — no module-level
  string forward-ref annotations in new code (or
  `from __future__ import annotations`).
* Multi-edit batches need grep / read-back verification (known
  silent-failure mode).
* SQLModel `select` import convention (`child_reports.py` mixes both
  — live trap; new code must use ONE consistent form).
* New terminal status tokens must NEVER be added without the
  observer accepted-set update (wedge hazard).

---

## 6. Discrepancies vs. older docs (do NOT trust without spot-check)

| Topic | Older doc anchor | This worktree | Reason |
|---|---|---|---|
| `_assistant_message_fresh` def line | `~:1822` (86ea8d69-era docs) | `:1801-1872` (def line `:1801`) | drift from later edits |
| Wake Task row mint | `~:3835` (86ea8d69-era docs) | `:3862-3872` | +27 line drift |
| `task.clear_all` end | `~:4450` (some pre-cutover docs) | `:4411` | function ends at the commit |
| `_recover_fired_unsent` DEF | `~:1840` | `:1839` (def); call site `:1541` | read-only loader |
| `get_instance_messages` source-surfacing | "UNVERIFIED" (dispatch synthesis) | `daemon/utils.py:264-266` — YES, truthy | verified by this author |

Implementation must spot-check any line number that did not appear in
this worktree-anchored report.

---

# REVISION CYCLE 2 RESEARCH (W-3, W-1/W-5, W-4 / C-1 / C-2)

Date: 2026-10-04
Worktree: `/home/nea/ensemble-src-wt-durability` @ `18827dbd` (branch `feature/durability-f1-f2`)
Authors: Explorer A (W-3 arm-3 lifecycle bound + W-1/W-5 verifications), Explorer B (W-4 RDRS evaluation), Explorer C (C-1 line-walk + W-2 seams)

> **Re-anchor notice (binding).** All anchors below are
> verified on the worktree at `18827dbd`. The prior cycle's
> drift table (this file's §6) is SUPERSEDED only where the
> REVISION CYCLE 2 anchors differ (none in this cycle;
> cycle 1 drift was `get_instance_messages` source-
> surfacing, now verified). Implementation occurs in this
> worktree; the main workdir `/home/nea/ensemble-src` is
> occupied by a sibling commission and is read-only to
> this one.

---

## Explorer A Report — W-3 ARM-3 LIFECYCLE + W-1/W-5 VERIFICATIONS (verbatim, with anchors)

**VERDICT: A Task row with `auto_continued_at` set is
completed to `status='completed'` by the terminalizer
(`repository.py:3099` transition `_write(session,"completed","running")`),
the stamp is NEVER cleared by any code path, and the row
is NEVER deleted by any daemon-side sweep** — the only
deleters are the boot `clear_all` (which arm 3 preserves
it from), the terminate-instance cascade
(`instance_lifecycle.py:5073`, never fires on normal
completion), instance hard-delete
(`instance/repository.py:2433, :2661`), project deletion
(`project/repository.py:1222`), and uncalled repo helpers
(`repository.py:4263, :4277` — no production callers).
**THE LEAK IS REAL AND UNBOUNDED:** every auto-continued-
then-terminalized task row survives every subsequent boot
forever. No retention job exists (checkpoint retention /
tmp-images / JobLockSweep never touch task rows); job-queue
reconciliation is status-writes only; System Cleanup
Bucket 5 zombie reaper targets NON-terminal instances only
(`instance/repository.py:1751-1755`) so COMPLETED
instances with stamped rows are never reaped; compaction
is checkpoint-store only. `auto_continued_at` writers /
readers exhaustively: `models.py:294`, `manager.py:6026-6034`,
migration, `repository.py :930 / :962-968 / :1043-1067` —
`mark_task_auto_continued` is the ONLY writer, NOTHING
NULLs it (`complete_task` auxiliary UPDATE sets only
`result/completed_at` at `:3105-3118`; fail / cancel /
requeue / heartbeat SQL never reference it). Leak paths:
(1) terminalizer completes (primary), (2) STR backstop
fails at boot+10min
(`stale_task_recovery.py:262/329/468/514/583`;
`manager.py:11714-11718`), (3) stamp-succeeds-then-worker-
completes race. Arm 3 with no other conjunct preserves ALL
of them monotonically; NOT bounded by the `message_queue`
side. **SCOPE OPTIONS:** (i) instance-non-terminal
co-condition — canonical `TERMINAL_INSTANCE_STATUSES`
frozenset at `constants.py:584-589` (duplicates at
`instance/repository.py:41`, `stale_task_recovery.py:33`;
`constants.py` is canonical per `command_dispatcher.py:107`);
`clear_all` SQL joins `instances` NOWHERE today
(`:4386-4393`); wrongly-still-preserved: stamped rows of
WAITING_CHILDREN / revived / long-lived instances (leak
shrinks to instance-lifetime); wrongly-deleted: stamped
rows of terminal instances — functionally safe (boot-pass
selection is running-only at `:953` + excludes terminal /
WC at `:975-981`, so a terminal stamped row is never a
candidate; audit value already covered by PP1 doomed-id
JOURNAL at `:4344-4364`). (ii) marker clearing at
terminalizer call site — dedicated repo method mirroring
`mark_task_auto_continued` (e.g. `UPDATE task SET
auto_continued_at=NULL WHERE id=:id AND status='completed'`)
invoked after successful `complete_task`; keeps shared
`complete_task` SQL byte-identical per the RATIFIED
pattern (`manager.py:11673-11683`: "The gate is AT THE
CALL SITE (Option (b) per the r3 approver — D18 r3 / D29)
— the shared complete_task SQL stays byte-identical to
pre-feature. The r2 fold's … conjunct inside the shared
SQL was REJECTED in D29"); single-writer stamp method
`:988-1067` is the symmetric precedent; residual = STR-
terminalized + race stamps. (iii) drop arm 3 entirely —
arm 3's unique coverage IS the terminal-stamped class
(the leak class); still-RUNNING stamped orphans are
covered by arm 1; forensics covered by PP1 journal.
**FACTUAL RECOMMENDATION:** (ii) minimal, compose with
(i) for zero residual. [W-5 CONFIRMED both halves:
`tap_node_return` `message_tap.py:189-252` upserts
(`message_id`, `created_at`) via `upsert_batch` at
`:240-244`, `_extract_ids` at `:180-187`, wired
`graph.py:9418-9419` with comment `:9406-9414` "tool_calls
AI messages and the AIMessage response are tapped
normally"; **ID mismatch STANDS and is STRONGER:**
`MessageMetadata.message_id` = `BaseMessage.id` UUID4
(`models.py:53, :76`); wake row `message_id` = fresh
uuid4 (`:3757`); source-suffix `completed_message_id` is
ALSO MessageQueue-space (`:698` `queue_repository.get`,
`:1039/:4579` `task get_by_message`) — the side table can
never join the wake row. **W-1 CONFIRMED both halves:**
`serialize_message` `utils.py:181-215` surfaces
`message_id` (`:186-188` resolved id with mint-writeback
fallback `:189-192`; first output key `:205-206`); chain
`completion_content.py:22-56` → `persistence.py:312/:521/:524`
(every dict carries the derived `message_id`);
**PRECISION:** `get_last_assistant_message` returns
`(content, created_at)` ONLY — the id comes from the
underlying serialized dicts; **stable-but-different
CONFIRMED** (checkpointed AIMessages always carry an
`add_messages`-minted uuid4; dedup key `:3498-3507` is
MessageQueue-space exact equality, never equal to
`BaseMessage.id`) → the operative cross-path dedup is
the `internal_report:{child_id}` PREFIX check:
queue-side generalization `WHERE instance_id=:parent AND
source LIKE 'internal_report:{child}:%' AND status IN
('ready','processing','completed')`; parent-history-side
`serialized.get("source","").startswith(f"internal_report:{child}")`
(`utils.py:264-266` surfaces `source`; drain stamps
`internal_report:{child_iid}`); precedent commits
`dfac6ff0` + `000f39db` ("prefix-match
`internal_report:{child}:%` for delivery evidence").]

---

## Explorer B Report — W-4 RDRS EVALUATION (verbatim, condensed where duplicative)

**VERDICT: RDRS is a 5-lane periodic+boot sweep that mints
`report_injection` DEFERRED markers and materializes wake
artifacts via `ensure_deferred → _reconcile_deferred_report`.
It misses F-2 because its only no-marker lane (Lane 2)
hard-filters on an anchor derived from the child's
`message_queue` rows — rows the boot wipe deletes — so
the wedge child is filtered out at `repository.py:1241`;
re-anchoring is feasible (query already joins
`instances.parent_id`) but the anchor is load-bearing for
the obligation-triple unique index + anchor-keyed
idempotency (incident `ca14e233/c3ac30f7`, documented
`repository.py:1053-1099` — 4× byte-identical re-delivery
from anchor-blindness).**

**Q1 FULL READ:** class `report_delivery_recovery.py:212-1133`;
STR-pattern daemon thread `:296-381`; constructed
`pool_orchestrator.py:308-336` (config knobs
`report_delivery_recovery_*` `config.py:1302-1378`); BOOT
`recover_on_startup` `:401-417` fire-and-forget off-loop
`pool_orchestrator.py:407-412` (after constructor wipe —
reads post-wipe state immediately post-boot); PERIODIC
thread `:448`, `_run_loop` `:383-399`, interval 300s
default; MANUAL `POST /api/recovery/recover_report_delivery`
→ `recover_now` (`:423-435`,
`routers/recovery.py:50`). 5 lanes
(`_run_all_lanes_sync` `:441-498`): (1) DEFERRED
`find_deferred_for_parent_all(parent_not_terminal=True)`;
(2) NO-ROW BACKSTOP
`find_completed_children_without_delivery(parent_not_terminal=True)`
`:861-903` — COMPLETED children with zero delivery
evidence; (3) PENDING-age `find_pending_past_age`; (4)
retry lane; (5) ORPHAN deferred of TERMINAL parents —
revival attempt + structured metric, never silent.
Creates per row: `ensure_deferred` (`:959`) →
`transition_deferred_to_pending` (`:1000-1004`) →
`manager._handle_recover_deferred_report` (`:1016-1021`)
→ `_reconcile_deferred_report` (`manager.py:8685`)
sub-shapes: (a) FULL creation
`_create_subshape_a_artifacts` `:9157-9340` (MessageQueue
READY + PENDING PROCESS_REPORT Task, NO `work_id`
`:9273-9281`, in-place injection backfill `:9181-9183` —
fresh INSERT would violate the triple index); (b)
task-only / message-only; (c) delivery-only + CARRIER-
REVIVAL seam (`:8971-8997`,
`_has_live_process_report_carrier` `:8614-8658`) — READY
message + no live carrier + alive parent → fresh carrier
+ `_notify_all_pools`. Does NOT: touch busy parents
(`has_instance_busy` `task/repository.py:1144`, per-row
skip `:929`), skip TOCTOU re-check, roll back terminal
states, silently drop terminal-parent obligations.
Terminal-child-safe: scans `child_inst.status==COMPLETED`
only (`:1222`); content from child CHECKPOINT
(`ChildReportsService._get_last_assistant_message`
`manager.py:9082-9108` sync / `:9134-9155` async), never
from the wiped anchor row; dead-parent guard T8(e)
(`:8773-8777, :9221-9253`); `ALIVE_INSTANCE_STATUSES` =
`{idle, running, paused, queued, waiting_children}`
(`constants.py:642-648`) — WC parents are IN scope.

**Q2 WHY NO HEAL:** wipe deletes (messages BEFORE tasks,
`:995-999`): parent PENDING wake row + child COMPLETED
rows + evidence rows (`message_queue/repository.py:1002-1008`);
PENDING NULL-`work_id` wake Task + child terminal tasks
(`task/repository.py:4380-4394`). SURVIVES: instances
rows, `report_injections` rows (NO delete of
`report_injections` in the boot path anywhere — only
`job_recovery_service.py:2143`, different lane),
`dependency_watchers` (only pause-path delete
`instance_lifecycle.py:5731`), child checkpoints. Lane-2
predicate-by-predicate post-wipe: `has_delivery_row`
(`:1136-1149`) wiped→false→QUALIFIES; `has_injection_row`
(`:1158-1167`) no marker→false→QUALIFIES;
`has_fired_watcher` (`:1177-1190`) join on wiped
tasks→false→QUALIFIES (either way no heal); STARVING:
`anchor_subq` (`:1197-1208` child's latest COMPLETED
message row) NULL → `.where(anchor_subq.is_not(None))`
`:1241` → FILTERED OUT. Lanes 1/3/4/5 are
`report_injections`-row-driven → structurally empty for
the pure no-row straddle. **Verdict: starvation hypothesis
CORRECT, refinement = the anchor requirement, not the
exclusions.** All three windows converge to the same
post-wipe shape (PENDING wake rows wiped regardless of
`enqueued_at` stamp state).

**Q3 RE-ANCHOR:** (a) lane 2 already instance-anchored
(`FROM instances c JOIN instances p ON
p.instance_id=c.parent_id` `:1216-1220`,
`c.status==COMPLETED` `:1222`, parent-terminal exclusion
`:1229-1233`; `waiting_children` NOT in
`_PARENT_TERMINAL_STATUSES` `{completed, error,
terminated, failed}` `repository.py:262-269`); (b)
~10-30 modified lines inside the ONE query
(`:1039-1253`); load-bearing: anchor value =
`child_message_id` in `ensure_deferred` (`:389-394`)
keying the write-once partial unique index
`(parent, child, child_message_id) WHERE state IN
(PENDING, DEFERRED)` (migration `20260819_000001:114-120`)
+ anchor-keyed duplicate suppression (`:1053-1099`); a
wipe-surviving anchor substitute is a semantics change to
the triple, not a mechanical edit; content unaffected
(already checkpoint-sourced); (c) timing already fits
(boot `:407-412` immediately post-wiring off-loop
post-wipe; first periodic +300s; no timing change needed);
(d) RDRS materializes THROUGH the deferred-marker path
(no parallel minting code — every lane routes
`ensure_deferred→transition→_handle_recover_deferred_report`
`:958-1029`); hard coupling: `_reconcile_deferred_report`
is keyed on an EXISTING injection row (`:8738-8744`) →
any healer on this machinery must first mint via
`ensure_deferred` which REQUIRES a `child_message_id` →
the re-anchor question reduces to "what survives the
wipe and can serve as the triple's third member."

**Q4 COMPARISON (condensed):** (A) re-anchored RDRS —
covers all 3 windows (post-wipe state window-invariant);
maximal reuse ~10-30 lines + tests; boot+periodic both
exist; reads post-wipe by construction; hazards = existing
reconcile profile (write_guard + `_session_scope`
`:8736-8737`, parent-lock on re-entry, 8s bounded bridge
`:8575-8596`, off-loop boot sweep); testable via EXISTING
suites (`tests/unit/test_report_delivery_recovery_service.py`,
`tests/postgres/test_report_delivery_recovery_pg.py`,
`tests/integration/test_report_delivery_double_delivery_pg.py`,
`tests/unit/test_report_delivery_self_heal_zero_row.py`,
`tests/job_queue/test_report_delivery_bug_family_pins.py`).
(B) new sweep module → deferred-marker path — same
coverage IF instance-anchored, but must independently
re-derive the zero-evidence test (duplicate-delivery risk
class); new module + wiring + tests. (C) direct mints —
largest hazard surface, cannot reuse
`_create_subshape_a_artifacts` without fabricating an
injection row anyway → converges to (B). **FOURTH
options:** D1 = RDRS + anchor patch only (smallest —
adopted); D2 = watcher-row-driven scan (wipe-immune rows,
moderate); D3 = lane inside JobRecoveryService boot
(`:522`, already manipulates `report_injections`
`:1790/:2143`).

**Q5:** SAME artifact-creation machinery, different
triggers (Site-1 drop sites
`message_processing_pipeline.py:851/:948`,
`child_reports.py:2801-2810/:3471-3478`; router resume path
`manager.py:10780` with BINDING ordering "mirror SQL
guards on state='PENDING' (`task/repository.py:951`)"
`:10683-10684` + revival-first `:10716-10753`; RDRS
boot+300s+manual+per-lane kill switches; HTTP endpoint).

---

## Explorer C Report — C-1 LINE-WALK + W-2 SEAMS (verbatim, condensed where duplicative)

**VERDICT: deferred-marker repair holds END-TO-END
provided the sweep drives the FULL chain `ensure_deferred
→ transition_deferred_to_pending → _reconcile_deferred_report`
(materializes MessageQueue row + PROCESS_REPORT Task
REGARDLESS of child terminal state — the reconcile never
reads child status; the `:2773-2787` guard only fires on
downstream RE-ENTRY, benign once artifacts exist because
the PROCESS_REPORT task is independently claimable).
Marker-only = the wedge (nothing claimable; terminalizer
accepts only CAS-stamped rows → resumed parent finishes
with no pending wake). The sweep-side seam is
`manager._handle_recover_deferred_report` (SYNC,
`manager.py:8487`, docstring "Sweep-side entry point"
`:8497`, calls `_reconcile_deferred_report` `:8546`,
bridges re-entry `run_coroutine_threadsafe(...).result(8.0)`
`:8586-8596`); the loop/router seam is
`_handle_recover_deferred_report_async` (`:8393`).
**NEVER wrap the public ChildReportsService entry in
`bus._get_parent_lock` — deadlock.**

**Q1:** `ensure_deferred` signature
(`parent_instance_id`, `child_instance_id`,
`child_message_id`, `deferred_reason`) ->
`ReportInjection|None`, `repository.py:389-395`; writes
`state='DEFERRED'`, `report_message_id=None`,
`content=_DEFERRED_MARKER_CONTENT_SENTINEL` (legacy NOT
NULL `:412-417`); idempotency = partial unique index
`uq_report_injections_oblig_triple WHERE state IN
(PENDING,DEFERRED)` (`:408-410`); three shapes
fresh / duplicate-absorbed-W6 / reason-update
(`:412-430`, "never duplicates; never escalates");
insert-on-missing Phase 4 (`:432-456`): terminal-row
pre-check `:518-555` → positive-evidence None; ZERO rows
→ insert fresh; "None NEVER returned for a zero-row
triple" (`:492-496`); deterministic IntegrityErrors
re-raise (`:461-478`). `_reconcile_deferred_report`
(`manager.py:8685-8692`, keyword-only: `child_instance_id`,
`child_message_id`, `injection_id`, `source`) keyed on
`session.get(ReportInjection, injection_id)` `:8738`
(missing→None); guards: already-terminal
INJECTED/TASK_DELIVERED `:8750-8759` → None; dead parent
(None or TERMINATED) `:8772-8777` →
`dead_parent_skip` shapes + injection FAILED dead-letter
`:9205-9209`; sub-shapes keyed on the injection row
`:8695-8712`: (a) `report_message_id IS NULL` →
`_create_subshape_a_artifacts` def `:9157` (MessageQueue
READY/FAILED-if-dead-parent `:9234-9269` + PROCESS_REPORT
Task/SKIPPED-if-dead `:9271-9281` + IN-PLACE injection
backfill `:9181-9183`) — atomic; (b) task-only
`:8908-8969` / message-only `:8818-8906` (session.commit
then `_notify_all_pools` OUTSIDE tx `:8892-8902/:8957-8965`);
(c) both exist → delivery-only + carrier revival
`:8971-9017`. Content:
`_fetch_subshape_a_content_sync` def `:9048` →
`ChildReportsService._get_last_assistant_message` via
`run_coroutine_threadsafe(...).result(8.0)` `:9092-9100`
over surviving child data; failure/empty → "[No response
content]" `:9100-9107`. Caller census: sync seam callers
ALL in `report_delivery_recovery.py` (`:735` lane 1,
`:1016` lane 2, `:1119` lanes 3/4); async seam
`manager.py:10780` from RESUME ROUTER
(`find_deferred_for_parent` `:10694-10697` → revival-
first `:10716-10753` → transition rowcount-guarded
`:10756-10758` → `await` async seam `:10780`; BINDING
ordering note `:10683-10684`); periodic YES 300s; boot
`pool_orchestrator.py:391-444`; manual
`routers/recovery.py:50`. **ARTIFACT PATH:** marker
DEFERRED → transition (`repository.py:941` rowcount-
guarded; required so the hot-path drain
`claim_for_injection` `state='PENDING'` `repository.py:163`
can claim) → reconcile materializes (MessageQueue
COMPLETION_REPORT READY + PROCESS_REPORT PENDING, same
`message_id`, committed, pools notified) → CLAIM
`claim_pending_task` `task/repository.py:2148`
(PROCESS_REPORT ranks FIRST `:2198-2203`, "PROCESS_REPORT
claim IS the parent wake" `:2612-2620`; per-instance
guard with WAITING_CHILDREN exception "prevents
child-report deadlock" `:2205-2218`; PROCESS_REPORT
bypasses cross-system job exclusion `:2286-2291`; pause
gate blocks paused/terminated) → DELIVERY
`ProcessMessageProcessor` loads row `task_processor.py:410-418`,
tri-state claim `:439-455` ("claimed" PENDING→
TASK_DELIVERED proceed; "already_delivered" skip-as-
completed — the ONLY delivery-time dedup; "missing"
PROCEED so the report is not lost `:433-438`) → drives
parent graph turn. Secondary hot path: live parent turn
drains PENDING rows before each LLM call
(`claim_for_injection` `graph.py:501`). For a parent mid-
boot-continue: pending PROCESS_REPORT stays queued;
terminalizer completing the orphan opens the claim-guard
→ pending wake lands FIFO-behind (`manager.py:11666-11671`).

**Q2 MARKER-ABSENT:** reconcile needs (1) existing
injection row (`ensure_deferred` first — insert-on-missing
guarantees a row unless terminal-positive-evidence), (2)
PENDING state via transition (reconcile itself proceeds in
DEFERRED but the hot-path drain requires PENDING; transition
`rowcount=0` = another actor won → skip,
`report_delivery_recovery.py:62-63`), (3) NO child-state
assumption (content from surviving messages; placeholder
fallback), (4) parent alive (the ONLY hard refusal:
`dead_parent_skip` — sweep must verify parent-not-dead or
revive-first per router precedent `:10716-10753`). Sweep
guarantees before minting: parent exists+not TERMINATED;
child's completed `message_id` in hand; continue past
marker into transition+reconc (stopping = zero claimable
artifacts = the wedge).

**Q3 W-2 REAL SEAMS:** real entry = `async
_process_child_completion_and_notify_parent(self,
instance_id: str, completed_message_id: str) -> None`,
`child_reports.py:2458` (docstring `:2477` "instance_id:
The child instance that completed"); the `:2560-2574`
block is INSIDE it (`async with await
bus._get_parent_lock(instance_id)` `:2563` wrapping
`to_thread(_process_child_completion_db_sync, instance_id,
completed_message_id, last_content)` `:2569-2574`;
db-sync def `:2674-2679`). **Takes CHILD id FIRST.**
Passing PARENT id: `session.get(Instance,parent_id)`
`:2711`; terminal `:2773` no; PAUSED `:2788` no; if
`instance.parent_id is None` `:2828` → root branch →
`count_pending_for_target_sync` `:2836` → `pending>0` →
"deferred_waiting_children" `:2846-2854` → dispatch
SSE-only `:4481-4495` → **SILENT NO-OP** confirmed (no
marker, no report, no wake); NON-root parent → regular-
child branch → mints a completion report addressed to
its OWN parent (worse than no-op).
`_dispatch_post_commit_side_effects` signature
(`result: _ChildCompletionDbResult`, `last_content: str`,
`completed_message_id: str`) `child_reports.py:4435-4440` —
pure post-commit dispatcher (`:4443`), holds NO transaction
context, mechanically invocable standalone BUT its input
is a `_ChildCompletionDbResult` produced only by the
db-sync helper → standalone caller must synthesize the
object + hand-pick an outcome. **DEADLOCK CONFIRMED:**
`_get_parent_lock` `dependency_bus.py:1627-1665` constructs
plain `asyncio.Lock()` (`:1664`) — NOT reentrant, same-task
re-acquire blocks forever, no timeout; other same-lock
sites `watch()` `:553`, `job_feedback_observer.py:1815`;
**NOTE** the method's lock is keyed on the `instance_id`
ARGUMENT (the child id), despite `:2534/:2540` comments
calling it "the per-parent lock."

**Q4 REPAIR-2 SHAPE (for the rejection record):**
MessageQueue mint `:3757-3769` (`message_id=fresh uuid4`
`:3757`, `instance_id=parent` `:3760`,
`content=last_content` `:3761`, `source=f"internal_report:{child}:{completed_message_id}"`
`:3762`, `type=COMPLETION_REPORT` `:3763`,
`status=READY` `:3764`, `priority=0` `:3765`,
`enqueued_at=now_utc_naive` `:3766-3767`); Task mint
`:3864-3871` (PROCESS_REPORT, `instance_id=parent`,
`message_id=same`, PENDING, `created_at`). Invariants:
`report_injection` row NOT required for claim/delivery
(tri-state "missing" → proceed); `:3498-3510` does NOT
fire at claim (pre-mint dedup only — a direct-mint sweep
must do its own source-match or rely on the triple
index); watcher NOT needed for the wake (PROCESS_REPORT
claim IS the wake; watchers drive JOB finalization, a
separate concern); natural path additionally writes
`report_injections` in the SAME transaction for crash-
consistency + live-turn drain (`:3910-3934`) — another
reason direct mints are inferior.

---

## End of REVISION CYCLE 2 RESEARCH
