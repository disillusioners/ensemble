# Code-Side Failure-Mode Catalog — Child 6c631666 COMPLETED but Report Never Delivered to Parent

**Investigation type:** read-only code analysis (code-analysis half of a two-sided investigation).
**Symptom class:** `instance shows DONE/COMPLETED, parent has no report` — child instance completed its work, parent never received the `completion_report`.
**Suspected feature:** pause/resume.
**Parallel workstream:** a live-DB/log evidence collector will match rows/log lines against the DB-signature and log-signature predictions below.

---

## 0. Baseline: the healthy report-delivery path (for signature diffing)

When child C (instance `6c631666`-like) completes its final turn, the pipeline's Stage 6 (`message_processing_pipeline.py:683-711` → `manager.py:6709`/`manager.py:5648`) calls `child_reports._process_child_completion_and_notify_parent` (`child_reports.py:1387`). The sync DB half (`_process_child_completion_db_sync`, `child_reports.py:1558`) commits **three artifacts in ONE transaction** for a regular child completion (`child_reports.py:2669 outcome="regular_child_completed"`):

1. **`message_queue` row** for the PARENT: `instance_id=<parent>`, `source="internal_report:<child>:<child_completed_message_id>"`, `type=completion_report`, `status='ready'`, `priority=0` (`child_reports.py:2366-2375`).
2. **`task` row** (the PROCESS_REPORT fallback): `task_type='process_report'`, `instance_id=<parent>`, `message_id=<report_message_id>`, `status='pending'` — **CONDITIONALLY** (see FM-2; skipped when parent is marker-paused or DB-paused) (`child_reports.py:2429-2437`).
3. **`report_injections` row**: `parent_instance_id=<parent>`, `child_instance_id=<child>`, `report_message_id=<report_message_id>`, `state='PENDING'` — **ALWAYS** (`child_reports.py:2461-2467`).

Post-commit (`child_reports.py:2682+`): bus terminal emit + corrective per-child emit (`child_reports.py:2865-2890`), `worker_pool.notify_work()` (`child_reports.py:2898`), `_report_injection_pending.add(parent)` hint (`child_reports.py:2915`).

**Delivery** then happens through exactly one of two exactly-once lanes:
- **Hot path:** parent's LIVE agent node drains `report_injections` PENDING→INJECTED before an LLM call and marks the companion `message_queue` row COMPLETED (`graph.py:2758`, `report_injection/repository.py:162-259`).
- **Fallback path:** WorkerPool claims the `process_report` task (`claim_pending_task`, `repository.py:1045`); `ProcessMessageProcessor.process` claims `report_injections` PENDING→TASK_DELIVERED (`task_processor.py:269-291` → `report_injection/repository.py:267-346`), then runs the normal pipeline for the parent turn.

**Terminal mirrors:** `complete_task` → `CompleteTurn` → `reconcile_turn_mirror(work_id)` (`repository.py:1738-1863`, `turn_transitions.py:343-379`) maps `message_queue` terminal via `CASE WHEN terminal_reason IN ('completed') THEN 'completed' WHEN ('failed','cancelled','orphaned_no_task') THEN 'failed' ELSE 'completed' END` (`repository.py:840-856`).

**Healthy signature:** `message_queue` report row `status='completed'`; `report_injections.state IN ('INJECTED','TASK_DELIVERED')`; `task` (process_report) `status='completed'`; parent graph history contains the report HumanMessage; `dependency_watchers` for the (parent, child) pair `state='FIRED'` with `enqueued_at` set.

**The symptom "child COMPLETED, parent never got the report" is exactly: report artifacts exist (or are missing) but neither lane ever delivered, AND the child still reached terminal `COMPLETED` + the bus watcher for the pair went FIRED (or was CANCELLED).**

---

## Failure-Mode Catalog (ranked by likelihood for this symptom)

### FM-1 (HIGH) — Resume-cleanup loop completes report message + cancels the live PROCESS_REPORT task ("phantom-complete the PENDING report turn")

**Location:** `daemon/manager.py:6297-6527` (the stale-message cleanup inside `_schedule_explicit_handle_resume`), PENDING-task branch at `manager.py:6427-6461`, terminal branch at `manager.py:6473-6518`.

**Mechanism:** On resume, the router calls `resume_processing_job` FIRST (handle still resolvable), which calls `_schedule_explicit_handle_resume`. That method's step 1 loops over the instance's `message_queue` rows in `(pending, processing, retrying)` and, for each, looks up the paired task by `message_id` (`manager.py:6349`):
- If the task is **PENDING** (which is exactly what `_resume_cascade_db_sync`'s `ResumeTurn` PAUSED→PENDING just produced — the resumed report task itself, or a sibling report task): the loop **completes the report message** (`_queue_repository.complete`, `manager.py:6434-6444`) **and cancels the task** (`cancel_task` "Superseded by resume_processing_job graph driver", `manager.py:6446-6460`).
- The branch is labeled as protecting against WorkerPool double-drive of a `process_message` checkpoint, but it is **type-blind**: a PENDING `process_report` task for this instance is killed the same way. The report content then relies entirely on `_resume_processing_background` driving the graph turn for the *target* work_id — if the target work_id is a different turn (e.g. the parent's own paused `process_message`), the killed `process_report` turn is never re-driven, and the report message is already `completed` so no retry path exists.

**Trigger conditions / timing:** pause fires while a PROCESS_REPORT is queued (or while its message row sits READY — see FM-2/FM-3), then resume is issued; the cleanup loop runs between `resume_processing_job` (router `instances.py:698` / `messages.py` PAUSED branch) and the cascade flip. Any PENDING report task at that instant is cancelled; its READY/PROCESSING message row is force-completed.

**Expected DB signature:**
- `task`: `task_type='process_report'`, `instance_id=<parent>`, `status='cancelled'`, `error='Task cancelled: Superseded by resume_processing_job graph driver'`, `cancel_requested=1`, `cancel_requested_at`+`completed_at` set.
- `message_queue`: report row (`source='internal_report:<child>:…'`) `status='completed'` with `completed_at` at resume time — **delivered=false** (no parent turn consumed it).
- `report_injections`: row still `state='PENDING'` (never claimed by either lane) — the strongest smoking gun; or `TASK_DELIVERED` if the claim happened before the kill.
- `instances`: child `completed`; parent `running`/`paused` per resume.

**Expected log signature:**
- `"[RESUME] cancelled stale PENDING task"` (manager.py:6454)
- `"Completed orphan message entry … for resume (paired PENDING task …)"` (manager.py:6437-6441)
- `"Completed stale message entry … for resume"` (manager.py:6479)
- `"[RESUME] cancelled stale task"` (manager.py:6509)
- Preceded by `"[RESUME] instance=<parent> route_outcome=report_or_external_resume …"` or `answer_gate_existing_turn`.

---

### FM-2 (HIGH) — Parent paused at child-completion time → PROCESS_REPORT task never created; report rides only on report_injections, and the PENDING injection row is never drained after resume

**Location:** guard at `daemon/services/child_reports.py:2412-2427` (`if marker_paused or db_paused: skip PROCESS_REPORT Task creation`); drain fast-path gate at `daemon/graph.py:269-282`; drain call site `graph.py:2758`; hint bump `child_reports.py:2915`.

**Mechanism:** When the child completes while the parent is paused (tree pause cascades to the parent while children keep finishing — pause skips terminal nodes but pauses `running/idle/waiting_children` parents), `db_paused=True` and the `process_report` task is intentionally skipped; the comment says "report_injection row will deliver on resume via claim_for_injection". But `claim_for_injection` only runs **before an LLM call inside a live agent node** (`graph.py:2758`), and its fast-path requires `instance_id in _manager._report_injection_pending` (`graph.py:275-277`) — a **RAM set**. If the daemon restarted between completion and resume, or the set entry was discarded (`graph.py:281-282` discards after a confirmed-empty drain racing the bump), the drain never fires. The `message_queue` report row stays `ready` forever: no task exists to claim it (none was created), resume's cleanup loop only touches `(pending, processing, retrying)` statuses... **except** `ready` IS `MessageStatus.READY.value` — wait: the cleanup filter is `(PENDING, PROCESSING, RETRYING)` on `msg.status`; a READY row is skipped with "Preserving PENDING message … for post-resume delivery" (`manager.py:6520-6525` — note the PENDING status enum maps to `'pending'`, not `'ready'`, so READY rows are actually *filtered out* of `pending_messages` entirely). With no PROCESS_REPORT task and no live-turn drain, the report is stranded.

**Trigger conditions / timing:** child's final turn completes while parent instance row is `paused` (user pause of the tree; or question-pause marker set on the parent); then resume happens via a path that does NOT drive an LLM call for the parent (silent child resume), or daemon restarts, or the fast-path hint is missing.

**Expected DB signature:**
- `message_queue`: report row `status='ready'`, `completed_at` NULL, `enqueued_at` at child-completion time — **permanent orphan**.
- `task`: **NO** `process_report` row for the parent with that `message_id` (absence is the signature).
- `report_injections`: `state='PENDING'`, `delivered_at` NULL.
- `instances`: child `completed`; parent resumed `running` (or later `completed` via other children).
- `dependency_watchers`: pair watcher `FIRED` by the corrective emit (`child_reports.py:2865`) — parent "knows" child is done without ever seeing content.

**Expected log signature:**
- `"child_reports: skipping PROCESS_REPORT Task creation for parent … reason=db_status"` or `reason=marker` (child_reports.py:2421-2426)
- `"Instance <child>… completed, sending report to parent <parent>…"` (creation of the artifacts)
- **NO** `"[ReportInjection] Drained … report(s) for parent"` line after resume
- **NO** `"[ReportInjection] Report … claimed for TASK delivery"` line.

---

### FM-3 (HIGH) — Pause lands between claim and pipeline mark-completed: PROCESS_REPORT task killed, message row stays PROCESSING, reconciler marks it `failed` and the report is swallowed as a "failed turn"

**Location:** claim gates `repository.py:1045-1160` (pause gate excludes paused instances at claim time only); pipeline stages `message_processing_pipeline.py:408` (claim message → `processing`), Stage 2 gate, Stage 4 `mark-completed`; pause path `instance_lifecycle.py:2105-2133` (`_request_registry.cancel_by_instance`) + `_pause_cascade_db_sync:3548-3576` (`SuspendTurn` RUNNING→PAUSED); worker B2 contract `worker_pool.py:517-570`; `AbortTurn` on cancel via `cancel_task` `repository.py:3094-3187`; reconciler message mapping `repository.py:840-856`.

**Mechanism:** The parent's PROCESS_REPORT task is claimed (task `running`, message `processing`), then pause fires. Cooperative cancel raises `OperationCancelledError`/`CancelledError`; the worker's B2 contract deliberately does NOT complete the task, and the pause cascade `SuspendTurn`s it to `paused`. On resume the cascade sets it `pending`; but the message row is still `processing` — and FM-1's cleanup loop then force-completes the message and cancels the task. Alternatively (no resume, or shutdown/reap), `cancel_task` runs `AbortTurn` → task `cancelled` → `reconcile_turn_mirror` maps the report message to **`failed`** (`terminal_reason='cancelled' → 'failed'`, `repository.py:850`). Either way the report content is never injected; the child's bus watcher was already FIRED by the child-completion hook, so the parent completes on all-children-done **without the report content**. The parent later finalizes via `_process_resume_finalize`/`_process_event` with `bus_pending==0` → COMPLETED. Parent shows DONE; report lost.

**Trigger conditions / timing:** pause_instance_cascade (user pause, question-pause on an ancestor, watchover activation, config flip) fires while the parent's PROCESS_REPORT turn is in-flight (between `claim_pending_task` and pipeline Stage 4/6).

**Expected DB signature:**
- `task`: `process_report`, `status IN ('cancelled','paused','pending')` with `cancel_requested=1` (if cancelled) or `suspension_reason IN ('paused_external','watchover_setup','awaiting_children','awaiting_answer')` (if still paused — resume never issued).
- `message_queue`: report row `status='failed'` (reconciler mapping) or `status='processing'` orphaned (pause before resume; Bug-B class) or `status='completed'` with no consumption (FM-1 kill).
- `report_injections`: `PENDING` (claim never reached) — **distinguishes from a true duplicate-delivery skip**.
- `instances`: child `completed`; parent eventually `completed` (via bus count 0) — the symptom.

**Expected log signature:**
- `"Worker <id>: task <id> paused (concurrent.futures.CancelledError — B2 contract: do NOT complete_task; pause cascade owns PAUSED write)"` (worker_pool.py:560-568)
- `"Cancelled graph task for instance <parent>…"` (instance_lifecycle.py:2131)
- `"pause transition outbox: work_id=…"` (instance_lifecycle.py:3596, DEBUG)
- On cancel: `"Reconciler invariant violation after cancel_task"` only on error; normally silent.
- Parent finalize: `"bus crash recovery: …"` or `"_process_resume_finalize"` lines showing finalize with zero pending watchers.

---

### FM-4 (MEDIUM-HIGH) — `find_paused_or_cancellable_turn` ambiguity ValueError → `invalid_or_missing_handle` → resume returns None, the paused PROCESS_REPORT task is left PAUSED forever, parent finalizes COMPLETED without the report

**Location:** `repository.py:336-466` (selector; raises `ValueError` when >1 concurrently eligible PAUSED/RUNNING turns of type process_message/process_report); `manager.py:6130-6141` + `manager.py:6167-6178` (catch → treat as no handle); `manager.py:6212-6220` (`invalid_or_missing_handle` warning + `return None`); router fallbacks `instances.py:711-716`, `messages.py:249-310` (target-only enqueue fallback — which delivers a NEW user message, not the report).

**Mechanism:** After a pause, if the parent has BOTH a paused `process_report` turn AND a running/paused `process_message` turn (exactly the multi-children + pause overlap shape), the one-running-turn-per-instance invariant is violated in the selector's eyes → `ValueError` → logged as "invariant violation" → `paused_turn=None` → resume returns None. The cascade (`resume_instance_cascade`) still flips the instance to `running` and its paused tasks to `pending` (cascade does NOT use the selector), but no graph driver is scheduled for the report turn... and if a user-message fallback enqueues a new `process_message` task, the per-instance `status='running'` guard plus FIFO ordering can starve or the report task may be claimed later and hit the FM-1 cleanup on a subsequent resume. Worst case the report task stays `pending` while `has_instance_busy` keeps returning True (blocks `job_continue`, zombie reaper defers) — or it is eventually cancelled by a sweep. Parent can still finalize via the bus (all watchers FIRED) → COMPLETED without report.

**Trigger conditions / timing:** pause overlaps two live turns on the parent (report turn + message turn); then any resume/answer/dismiss or PAUSED-branch message.

**Expected DB signature:**
- `task`: ≥2 rows for parent in `(pending,running,paused)` with type in `(process_message, process_report)` — one of them the stuck report turn (`status='pending'` or `'paused'` indefinitely).
- `message_queue`: report row `ready` (never claimed) or `processing` orphan.
- `report_injections`: `PENDING`.
- `instances`: parent may be `running` with no live graph task, or `completed`.

**Expected log signature:**
- `"[RESUME] instance=<parent> find_paused_or_cancellable_turn invariant violation: … matched N concurrently-eligible turns"` (manager.py:6170-6177)
- `"[RESUME] instance=<parent> route_outcome=invalid_or_missing_handle — no suspended or paused turn found"` (manager.py:6215-6219)
- Router: `"resume_processing_job returned None for target instance …"` (messages.py:267-278).

---

### FM-5 (MEDIUM) — Pipeline PAUSED re-check skips child completion entirely: child finishes, `_process_child_completion_db_sync` never runs, no artifacts created; child later reaped COMPLETED via another path

**Location:** `message_processing_pipeline.py:466-480` (`_is_instance_paused` → skip Stage 6 with log `"MessageProcessingPipeline: skipping child completion for … — instance is PAUSED"`); the "next message will run child completion" assumption.

**Mechanism:** The child's final turn ends while a pause cascade on the CHILD (not the parent) commits PAUSED during the shielded finally window. Stage 6 is skipped — no completion_report, no report_injection, no bus emit for the child. The code comments assume "the next message (resume) will run child completion with a clean state", but after resume the child's own re-driven turn ends and Stage 6 runs `_process_child_completion_db_sync` again — if by then the child row was already flipped terminal by another writer (idempotency_skip at `child_reports.py:1626-1640`), the report emission is permanently skipped. Child instance can still show `completed` (flipped by resume-finalize or the observer) with zero report artifacts.

**Trigger conditions / timing:** pause cascade commits PAUSED on the child between the child's graph END and pipeline Stage 6; child's status then terminalizes via a non-report path (resume finalize `_process_resume_finalize`, bus crash recovery, zombie reaper) before any later Stage-6 run.

**Expected DB signature:**
- `message_queue`: **NO** row with `source='internal_report:<child>:…'` (absence).
- `task`: no `process_report` for the parent referencing the child; child's own turn terminal (`completed` or `cancelled`).
- `report_injections`: NO row for `(parent, child)`.
- `dependency_watchers`: watcher for the pair still `PENDING` (never fired) or `CANCELLED` by reaper/reconciler.
- `instances`: child `completed` — but note pure-FM-5 leaves the watcher PENDING so the parent would normally NOT complete (stuck WAITING_CHILDREN); a "parent DONE + no report" outcome needs FM-5 combined with watcher CANCELLED (`reconcile_turn_mirror` child-liveness guard `repository.py:895-935` cancels watchers when the child instance is terminal!) — that guard flips the pair watcher to CANCELLED once the child is terminal, zeroing `bus_pending`, letting the parent finalize COMPLETED with **no report ever delivered**.

**Expected log signature:**
- `"MessageProcessingPipeline: skipping child completion for <child>… — instance is PAUSED (likely question() tool pause committed during shielded finally block)"` (pipeline:473-478)
- Then either no `_process_child_completion_and_notify_parent called:` line at all for the child, or a later one ending in `"already in terminal or paused state (…), skipping"` (child_reports.py:1630-1639).

---

### FM-6 (MEDIUM) — `_deferred_question_pause` marker asymmetry: report task created but parent's marker window kills delivery; or message enqueued without task (enqueue-side guard)

**Location:** enqueue guard `instance_messaging.py:1398-1426` (marker-only skip of Task creation — "KNOWN LIMITATION: message in narrow race window may not be delivered on resume"); report-side guard `child_reports.py:2391-2427` (marker OR db_paused).

**Mechanism:** For the general enqueue lane, when the deferred-pause marker is set on the parent, `_prepare_enqueued_message` creates the `message_queue` row but **skips the Task** — the message can never be claimed (READY rows are not in the resume-cleanup statuses and no drain exists for them). If the lost artifact is the completion_report of 6c631666 (reports created via `child_reports` inline, not `_prepare_enqueued_message`, but `internal_agent:`/follow-up lanes converge similarly), the parent's resume proceeds without it. The report-side guard avoids this for reports by falling back to report_injections — but then FM-2 applies (injection row undrained).

**Trigger conditions / timing:** question()/ask_questions pause marker set on the parent exactly while a child completion or follow-up enqueue lands; resume then relies on RAM marker pop ordering (`C1 fix` ordering assumed correct).

**Expected DB signature:**
- `message_queue`: row `status='ready'`, no paired `task` row (`SELECT * FROM task WHERE message_id=<id>` empty) — the audit-row-without-task signature.
- For the report variant: same as FM-2.

**Expected log signature:**
- `"instance_messaging: SKIPPING PROCESS_MESSAGE Task creation for instance … — reason=marker (in-window race); … KNOWN LIMITATION: message in narrow race window may not be delivered on resume."` (instance_messaging.py:1399-1410)
- `"instance_messaging: PROCESS_MESSAGE Task created for instance … with DB=PAUSED; relying on claim_pending_task SQL gate to defer until resume"` (the benign sibling — distinguish!).

---

### FM-7 (MEDIUM) — Bus crash recovery (daemon restart during pause) stamps the watcher and finalizes the parent COMPLETED, bypassing report delivery

**Location:** `daemon/api.py:1140-1400` (recovery loop): PAUSED-target preservation at `api.py:1156-1182`; `has_instance_busy` defer at `api.py:1300-1355`; direct finalize at `api.py:1357+`.

**Mechanism:** Daemon crashes after the child completed and the watcher FIRED (bus DB is persistent) but before the parent's report task ran. On restart, recovery finds the FIRED-unenqueued watcher. If the target parent is NOT paused and `has_instance_busy` returns False (report task cancelled by FM-1 pre-crash, or never created per FM-2), recovery finalizes the parent **COMPLETED directly** — no report content is ever injected; the row is stamped and never retried.

**Trigger conditions / timing:** crash/restart in the window between child completion (watcher FIRED) and parent report-task delivery.

**Expected DB signature:**
- `dependency_watchers`: pair watcher `state='FIRED'`, `enqueued_at` set at restart time.
- `task`: no live report task (cancelled per FM-1 or absent per FM-2).
- `message_queue`: report row `ready`/`failed`/`completed`-without-delivery.
- `report_injections`: `PENDING`.
- `instances`: parent `completed` with `updated_at` ≈ restart time.

**Expected log signature:**
- `"bus crash recovery: target=<parent>… has 0 PENDING watchers — deciding: defer if any live task, else finalize via single path"` (api.py:1276-1284)
- `"bus crash recovery: … has live task — deferring to natural finalize path"` (the branch that did NOT run)
- Absence of any `"[ReportInjection]"` delivery lines post-restart.

---

### FM-8 (MEDIUM-LOW) — Zombie reaper / cleanup sweep terminates the parent with live report work; or Task↔JobItem reconciliation gap (known) leaves the report task paused while idle-gates unblock the parent

**Location:** `job_queue_service.py:1320-1520` (`_has_live_work`, Bucket-5 reaper TOCTOU re-check via `has_instance_busy`); known open gap: "JobItem done/cancelled but linked Task stays paused, blocking idle-gates forever"; system cleanup nuclear sweep (multi-bucket).

**Mechanism:** (a) If the report task was reconciled to `cancelled` (FM-1) the parent has no live Task; the reaper sees no live work and terminates/finalizes it — parent DONE, no report. (b) The known reconciliation gap keeps a Task `paused` while its JobItem is done — `has_instance_busy=True` blocks reaper AND `job_continue`, parent stuck (this produces "parent stuck" not "parent DONE-no-report", so it is the *differential*: its absence plus parent DONE points back to FM-1/FM-7).

**Expected DB signature:** (a) parent `completed`/`terminated` via cascade at sweep time; report artifacts orphaned as in FM-2. (b) task `paused` + JobItem `done` — the known-gap signature.

**Expected log signature:** reaper lines `"_has_live_work … probe"`; `"zombie"`/`"reap"`/cleanup-bucket logs; for (b) no reap of the parent.

---

### FM-9 (LOW-MEDIUM) — Report-injection drain failure or double-claim skew: drain raises (fallback intended) but the fallback task is concurrently cancelled (FM-1) — both lanes lose

**Location:** `graph.py:2763-2772` (drain exception → "falling back to PROCESS_REPORT task delivery"); `claim_for_task_delivery` `report_injection/repository.py:267-346`.

**Mechanism:** Drain throws (DB hiccup) → falls back to the task lane; the task lane was simultaneously cancelled/completed by resume cleanup → report never delivered; `report_injections` stays PENDING with no remaining consumer (task gone, parent turn over).

**Expected DB signature:** `report_injections.state='PENDING'`; `task` cancelled (FM-1 signature) or completed-skip; message row `ready`/`completed`-no-delivery.

**Expected log signature:** `"[ReportInjection] drain failed for instance … falling back to PROCESS_REPORT task delivery"` followed by FM-1 lines.

---

### FM-10 (LOW) — `already_delivered` dedup skip racing an UNDELIVERED injection (false INJECTED)

**Location:** `task_processor.py:269-291` skip + `_skip_task_as_completed` `task_processor.py:132-183`.

**Mechanism:** If the injection row was marked INJECTED by a live drain whose LLM turn then got **cancelled by pause before the message was actually rendered into checkpoint history** (drain marks INJECTED before the LLM call; pause cancels the turn), the exactly-once design treats the report as delivered; the fallback task later claims `already_delivered` and skips. Content was never committed to the parent's checkpoint. Rare but real: the INJECTED claim is not transactional with LLM-call completion.

**Expected DB signature:** `report_injections.state='INJECTED'` with `delivered_at` inside the pause window; parent checkpoint (LangGraph) lacks the report HumanMessage; task `completed` with `result.skipped=true`.

**Expected log signature:** `"Task <id>: report … already delivered via report-injection (INJECTED) — skipping PROCESS_REPORT graph turn"` (task_processor.py:279-284) + a pause line in the same second.

---

## Analysis-area coverage map (per task requirements)

1. **Report turn lifecycle** — §0; creation `child_reports.py:2429-2467`; claim `repository.py:1045` (report lane bypasses cross-system guard, still subject to pause gate `repository.py:1296-1303` + per-instance `running` guard); completion via pipeline Stage 4 + `on_success`→`complete_task`; enqueue-to-parent is the `message_queue` row + `report_injections` (dependency_bus is the completion authority for the *pair*, fired at `child_reports.py:2831-2890`).
2. **Pause-during-report-turn** — FM-3 (in-flight), FM-2 (queued/taskless), FM-1 (post-resume kill). Row effects: task RUNNING→PAUSED (`SuspendTurn`), message stays `processing`, JobItem admission `paused` legacy path (`_resume_cascade_db_sync` UPDATE 4 legacy 'paused'), instances PAUSED. Resume: PAUSED→PENDING (`ResumeTurn` `turn_transitions.py:249-340`), no cancel stamping, mirrors NOT message-completed (Task non-terminal).
3. **Resume cascade** — `resume_processing_job` four outcomes (`manager.py:6033-6220`); `_resume_cascade_db_sync` (`instance_lifecycle.py:3698-3892`); silent-death paths enumerated: FM-1 (cancel in cleanup loop), FM-4 (ValueError→None), FM-7 (crash recovery), `already_resuming` dedup (`manager.py:6296-6305`), W1 fail_task-no-op→cancel fallback (`manager.py:6838-6906`).
4. **Completion/cascade** — parent finalize gated on bus pending (`job_feedback_observer.py:863-1100`); the reconcile child-liveness guard (`repository.py:895-935`) CANCELS pair watchers once the child is terminal — this is a **report-loss amplifier**: it lets the parent complete with zero pending watchers even when the report was never delivered (feeds FM-5/FM-7 into "parent DONE, no report"). Idle-gates `has_instance_busy` block only while a live Task exists; once FM-1 cancels it, gates unblock and finalize proceeds.
5. **CancellationReason / terminal_reason mapping** — `CancellationReason` has NO pause member (`cancellation.py:10-17`: timeout/watchdog_retry/manual/shutdown/session_terminated/user_stopped); pause uses `USER_STOPPED` tokens (`instance_lifecycle.py:2105-2107`) and coroutine cancel; worker B2 contract keeps the task RUNNING→(cascade)PAUSED, never `cancelled` on pause alone — any `cancelled` process_report row therefore came from an explicit `cancel_task` (resume cleanup FM-1, reap, or retry path), and `reconcile_turn_mirror` maps `cancelled`→message `failed` (`repository.py:850`) — the "swallow" point.

---

## Global ranked table

| Rank | Mode | One-line signature | Key DB evidence | Key log grep |
|---|---|---|---|---|
| 1 | FM-1 | Resume cleanup kills PENDING report task + force-completes its message | task process_report `cancelled` + msg `completed` (undelivered) + injections `PENDING` | `cancelled stale PENDING task` / `Superseded by resume_processing_job` |
| 2 | FM-2 | Paused parent ⇒ no report task; injection never drained | NO process_report task; msg `ready` orphan; injections `PENDING` | `skipping PROCESS_REPORT Task creation … reason=db_status` + no `[ReportInjection]` lines |
| 3 | FM-3 | Pause mid-report-turn; cancel path maps message to `failed` | task `paused/cancelled`; msg `failed`/`processing`; injections `PENDING` | `B2 contract: do NOT complete_task` + `Cancelled graph task` |
| 4 | FM-4 | Selector ambiguity → resume None | ≥2 live turns; stuck pending/paused report task | `invariant violation … concurrently-eligible turns` / `invalid_or_missing_handle` |
| 5 | FM-5 | Stage-6 skip on PAUSED child; watcher CANCELLED by liveness guard later | no report artifacts at all; watcher `CANCELLED` | `skipping child completion … instance is PAUSED` |
| 6 | FM-6 | Marker-window enqueue without task | msg `ready` w/o task row | `SKIPPING PROCESS_MESSAGE Task creation … reason=marker` |
| 7 | FM-7 | Crash recovery finalizes parent directly | watcher `FIRED`+`enqueued_at`; parent `completed` at restart | `bus crash recovery: … else finalize via single path` |
| 8 | FM-8 | Reaper/cleanup finalizes; known Task↔JobItem gap variant | parent terminal via sweep; (b) task `paused`+JobItem `done` | `_has_live_work` / bucket logs |
| 9 | FM-9 | Drain fail + task lane dead simultaneously | injections `PENDING`, task terminal | `drain failed … falling back` + FM-1 lines |
| 10 | FM-10 | INJECTED claimed but turn cancelled pre-render | injections `INJECTED`, checkpoint lacks msg, task skip | `already delivered via report-injection` near a pause line |

**Discriminator cheat-sheet for the evidence collector:**
- `report_injections.state` is the single most diagnostic column: `PENDING` ⇒ both lanes lost (FM-1/2/3/7/9); `INJECTED` w/o checkpoint content ⇒ FM-10; `TASK_DELIVERED` ⇒ task lane claimed (then check task/message terminal states).
- Missing `process_report` task ⇒ FM-2/FM-6. Cancelled one ⇒ FM-1/FM-3/FM-8. Paused-forever one ⇒ FM-4/FM-8b.
- Parent finalized within seconds of a resume ⇒ FM-1/FM-4; within seconds of a daemon restart ⇒ FM-7; with a `"skipping child completion … PAUSED"` line ⇒ FM-5.
