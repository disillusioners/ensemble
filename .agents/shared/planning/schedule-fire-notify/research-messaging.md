# Research — schedule-fire-notify: message lane + source classification

Tree: `/home/nea/ensemble-src-wt-schedule-fire-notify` (branch `feature/schedule-fire-notify`, base `0b5624dc9`). All file:line grepped in THIS tree at use time. Research only — no design.

---

## 1. ROUTING TO ONE INSTANCE

**The programmatic delivery function** (what a scheduler-side adapter calls — NOT the agent tool):

- `InstanceManager.enqueue_message_job(...)` — `daemon/manager.py:11995` (thin wrapper) → **`InstanceMessagingService.enqueue_message_job`** — `daemon/services/instance_messaging.py:3131`. Legacy sibling `enqueue_message` — `:2938`. Both converge on `_prepare_enqueued_message` (`:2281`): MessageQueue + Task rows in ONE transaction (`:2514-2529` insert, `:2646-2649` Task insert), then `worker_pool.notify_work()` unified dispatch (`:3048-3052`). No JobItem in the legacy path; the job path stamps `message_id` onto the JobItem mirror (`:3015-3034`).

**Status routing** — this is ONE code path in `_prepare_enqueued_message`, not three branches:

- **Target-exists guard (fail-closed)**: missing instance row → `raise ValueError(...not found...no MessageQueue row was written)` — `instance_messaging.py:2488-2493` (D4(ii/iii): instance MUST exist BEFORE any row is written).
- **Terminal COMPLETED / TERMINATED / ERROR / FAILED → auto-REVIVE + enqueue**: `is_terminal_revival` computed at `:2714-2719`; status flipped to `RUNNING` **in the same transaction** at `:2720-2726` (log "Reactivating terminal instance" `:2729-2732`). PAUSED is deliberately EXCLUDED (`:2695-2699`) — messages to a PAUSED instance queue as PENDING Task rows and are claimed on resume (`:2985-2992`).
- **IDLE / WAITING_CHILDREN → enqueue + flip to RUNNING**: same block `:2720-2726`; `is_idle_to_running` at `:2726`.
- **RUNNING (in-flight turn) → enqueue; the Task stays PENDING behind the busy/claim gate.** The durable enqueue lane NEVER injects mid-turn. Mid-turn injection on a live-graph RUNNING target is a SEPARATE lane: `set_injection` FIFO (`instance_messaging.py:2279`), used only by the chat live-turn path — `daemon/sources/registry.py:1152-1210`, gated on `manager.has_live_graph_task` (`registry.py:1166-1168`; a graphless-RUNNING target would strand a memory injection, so probe-failure falls through to durable enqueue `:1195-1204`).
- **Revive carve-out (Batch A / A1)**: freshly-revived terminal instance on a defer queue forces `is_deferred=False` — `:2584-2617` (revival carrier must never be born deferred).

**Revive cost** (cheap by construction):
- It is a ROW WRITE, not a revive decision: "no revive budget is consumed and F7 revivable-terminal semantics are untouched" — `daemon/manager.py:12299-12302` (`_revive_terminal_instance`, `:12279`). Auto-transition = status→RUNNING + version bump + `last_activity_at` (`:12348-12353`). Returns bool: True = revived-or-already-non-terminal, False = revival failure (`:12304-12310`).
- Checkpoint reuse: "terminal only records WHY the last run stopped; the checkpoint, message history, and LangGraph thread all persist in the DB and reload on the next graph.astream" — `instance_messaging.py:2690-2694`.
- Dependency watchers re-arm for the revived child (Core Architecture invariant; `instance_messaging.py:2594` — the freshly-revived instance is the only revival candidate).

**Return / raise contract**:
- Returns `AsyncMessageResult`: real `message_id`, `instance_id`, `status='queued'`, `job_id == Task.work_id` — `instance_messaging.py:3216-3220`.
- Raises: queue-resolution / Task-creation exceptions propagate BEFORE any JobItem is visible (`:3183-3188`); missing target → `ValueError` (`:2490-2493`).
- Best-effort precedent: the scheduler wraps the call in try/except, records FAILED, and re-arms — `daemon/sources/adapters/scheduler.py:2108-2133` (scrub-failure refusal leg `:2092-2107`).

---

## 2. ENQUEUE JOB SOURCE STRING

**Definition**: `enqueue_message_job` — `instance_messaging.py:3131-3147`. Signature: `(instance_id, message, source: str = "api", priority: int = 1, images=None, metadata=None, *, is_deferred=False, is_background=False, queue_id=None, image_refs=None, idempotency_key=None, dispatch_chain=None, mission_id=None)`. `priority`: 0=system, 1=user (`:3195`).

**Source stamped today**:
- Scheduler fire seams stamp `source="scheduler"`: deferred/one-time path `scheduler.py:2109-2116` (with `idempotency_key`), inline `_route_via_job_queue` path `scheduler.py:2723-2731`.
- Generic adapter pipeline mints `source = f"{source_id}:{msg.external_user_id}"` (`daemon/sources/registry.py:1150`) and calls `enqueue_message_job` at `registry.py:1318`.

**Free-form or validated?** The service-layer `source` param is a FREE-FORM `str` — no enum, no service-side validation. Validation lives at boundaries:
- HTTP `POST /api/jobs` gate: reserved-prefix and chat-prefix body sources → 422 (`daemon/routers/jobs_crud.py:528-545` reserved, `:567-580` chat). Pydantic `JobCreateRequest.source`: typed `str` (not `str|None`), `min_length=1`, default `"api"` (`:509-516`).
- Boundary helper `is_reserved_source` — `daemon/constants.py:1311-1335`.
- Internal callers bypass the HTTP body path entirely (`jobs_crud.py:500-506`).

**Where the source string surfaces**:
1. `MessageQueue.source` column (insert `instance_messaging.py:2514-2521`) + `MessageQueue.type` derived from source: `_derive_internal_message_type(source) or MessageType.HUMAN` (`:2386`; mapping tables `constants.py:1477-1518` — `"system:"` → `MessageType.SYSTEM`).
2. HumanMessage `additional_kwargs={"injected_message": True, "source": <source>}` when `is_internal_stamped_source(source)` (`instance_messaging.py:441-466, :611`; set `constants.py:1396-1448`).
3. Attestation scanner exclusion ladder branch 4b reads that stamp (canonical-home comment `constants.py:1350-1364`).
4. Provenance funnel: `stamp_user_origin_window` classifies the source each dispatch (Q3/Q4).
5. Worker-lane claim predicate keys on `CHAT_SOURCE_PREFIXES` only (`daemon/repositories/task/repository.py:203-233, :2648`; `constants.py:144`) — chat rows go to the dedicated chat pool; everything else to the normal pool.
6. Fresh-episode attestation-ledger reset discriminator: `priority==1 AND msg_type==HUMAN` (`instance_messaging.py:2733-2764`) — a `system:` source never resets the leader's deny memory.
7. Upgrade-journal wake-sweep re-stamp: recorded sources ride VERBATIM; empty falls back to the reserved literal (Q4).

---

## 3. SOURCE CLASSIFICATION (THE MAPPER)

**The classifier** (single source of truth): `classify_user_origin(source, registry_get)` — **`daemon/tools/upgrade_journal.py:5389-5439`**. Returns `(is_user_origin, detail_token)`, never raises, fails CLOSED on every failure mode.
- Arms ONLY on: exact `"api"` (`_USER_ORIGIN_EXACT` `:5367`, checked `:5404-5405`) OR a registry-resolved adapter whose daemon-controlled `source_type` ∈ `USER_ORIGIN_CHAT_SOURCE_TYPES = {telegram, slack, discord, whatsapp}` (`:5371-5373`, checked `:5422-5439`).
- Reserved lanes NEVER arm — checked BEFORE the registry (`:5406-5410`) via `is_reserved_source`, so even a mis-registered reserved id cannot arm via metadata. Failure tokens: `no-source` / `reserved-internal` / `unregistered` / `registry-unavailable` / `registry-error:*` / `source-type-not-chat:*`.

**Where bare `'api'` is accepted as genuine user origin (DF-4 lesson)**:
- The HTTP chat lane legitimately stamps `source="api"` — `daemon/routers/messages.py:722` (contract comment `:184`; the resume fallback uses reserved `api_resume_fallback` `:438`).
- The forgery hole: `jobs_crud.py` `body.source` is client-supplied and DEFAULTS to `"api"` (`jobs_crud.py:509-516`) — a non-chat caller passing nothing or literally `"api"` would classify as user-origin. Mitigations: the reserved/chat-prefix 422 gates (`:528-580`); agent-path callers were re-pointed to reserved literals (e.g. `auto_dispatch_fallback`, `constants.py:1298-1307`). `"api"` itself STAYS armed by design — it is the genuine web-UI chat path; the fix is caller discipline + reserved literals, not de-arming `"api"`. Internal code must NEVER stamp bare `"api"`.

**Registration/classification checklist for `system:schedule_fire_notify`** — because it starts with `"system:"`, EVERY point clears AUTOMATICALLY (zero constant edits, zero census change):

Accepted cleanly end-to-end:
1. `is_reserved_source` → True via the `"system:"` prefix family (`constants.py:1278`; prefix match `:1329-1331`). No addition to `RESERVED_SOURCE_PREFIXES` needed.
2. HTTP boundary: it is 422-rejected if ever supplied via HTTP (`jobs_crud.py:528-545`); minted only inside daemon code like every other internal lane (`jobs_crud.py:500-506`).
3. Worker-lane predicate: not a chat prefix → claimed by the normal (non-chat) worker pool (`constants.py:144`; `task/repository.py:203-233`).
4. `MessageQueue.type` derivation: `"system:"` ∈ `INTERNAL_SYSTEM_TYPED_SOURCE_PREFIXES` → `MessageType.SYSTEM` (`constants.py:1488`; applied `instance_messaging.py:2386`).

Non-user-origin everywhere it can matter:
5. `classify_user_origin` → `(False, "reserved-internal")` (`upgrade_journal.py:5409-5410`) → `stamp_user_origin_window` clears/POPs the window (`manager.py:6357-6358`); stamped observation recorded for diagnostics (`:6341-6349`).
6. `_inherit_provenance_window` inherits ONLY on `internal_agent:<caller>`; `system:*` explicitly gets NO direct and NO inherited window (`manager.py:6382-6386`).
7. `INTERNAL_STAMPED_SOURCE_PREFIXES` contains `"system:"` (`constants.py:1401`) → HumanMessage stamped `injected_message=True` → attestation scanner ladder branch 4b treats it internal; SOURCE-U anchor never moves (`constants.py:1350-1368`).
8. Fresh-episode attestation-ledger reset: `msg_type==SYSTEM` fails the `HUMAN` conjunct → no reset (`instance_messaging.py:2733-2764`).
9. Upgrade-gate sweep: never classifies user-origin (Q4).

**Behavioral deltas vs the adapter's current bare `"scheduler"` stamp** (planner input):
- Bare `"scheduler"` is a reserved EXACT value (`constants.py:1297`) but is DELIBERATELY ABSENT from `INTERNAL_STAMPED_SOURCE_PREFIXES` (`constants.py:1392-1395`, product ruling: "the scheduler is the de-facto requestor of a scheduled mission, so its message anchors by design") and is NOT in any msg_type table → derives `HUMAN`. A `"system:"`-prefixed stamp instead does NOT anchor SOURCE-U and derives `msg_type=SYSTEM` — internal-notice semantics, not user-requestor semantics.

**Reserved-string precedents and how they register** (there is NO registration ceremony — the mint site just uses the literal; prefix families do the rest):
- `"system:watchdog"` — `daemon/services/waiting_children_watchdog.py:179` (module constant `WATCHDOG_SOURCE`; used `:910`).
- `"system:resume_wake"` — `daemon/manager.py:15446`.
- `"system:injection_drain"` — `daemon/services/message_processing_pipeline.py:1224`.
- `"system:post_restart_arm_notify"` — `daemon/tools/upgrade_journal.py:1798`.
- `"system:sweep_no_user_origin_wake"` — `daemon/tools/upgrade_journal.py:1812` (DF-4 P1 constant; rationale `:1793-1811`).
- child_reports: `"internal_report:{child_instance_id}:{completed_message_id}"` mint at `daemon/services/child_reports.py:4877`; idempotent re-scans key on the exact source string (`:1352`, `:4594`; collision-safety comment `:4852-4859`).

---

## 4. UPGRADE-GATE WINDOW

**Constants**: `RESTART_WAKE_SOURCE = "system:post_restart_arm_notify"` (`daemon/tools/upgrade_journal.py:1798`); `SWEEP_NO_USER_ORIGIN_WAKE_SOURCE = "system:sweep_no_user_origin_wake"` (`:1812`; fail-closed rationale `:1793-1811`).

**What the sweep checks** (`UpgradeJournalSweepService`, `daemon/services/upgrade_journal_sweep.py`): delivers post-restart arm-notify wakes through `manager.enqueue_message` with an EXPLICIT source at BOTH delivery arms:
- `:1396-1414` — `source=(RESTART_WAKE_SOURCE if wake.kind == "restart" else (wake.source or SWEEP_NO_USER_ORIGIN_WAKE_SOURCE))`.
- `:1939-1941` — same shape for the `head` variant.
Rule encoded in the comment `:1399-1406`: restart-kind wakes NEVER ride a user source; a wake armed with NO user origin must never mint a DIRECT user-origin window (the retired bare-`"api"` fallback forged one — DF-4 P1); a recorded non-empty source still rides VERBATIM (the reserved literal is the fallback arm ONLY).

**What counts as user-origin there**: exactly `classify_user_origin` (Q3) — exact `"api"` + registered chat `source_type`s. The windows it stamps feed the upgrade factor-2 gate and the restart provenance gate (`stamp_user_origin_window` docstring `manager.py:6308-6328`; consumers `daemon/tools/upgrade_tools.py:2485-2512`, `:2611`; wake_source stamp `upgrade_tools.py:3301`).

**Requirement for the new source**: start with `"system:"`. Then `is_reserved_source` → `classify_user_origin` fail-closed handles every gate automatically — no sweep, gate, or constant edits. Additionally: the notify lane must never write a `PendingWake` row or touch recorded wake sources (the sweep rides those verbatim).

---

## 5. METRICS IDIOM (shadow-writer precedent)

**File**: `daemon/services/report_integrity_metrics.py` (105 lines; `:59-89` is `record_junk_report`). Repo has NO metrics infra — "NO-OP-SAFE seam" by design (docstring `:8-17`). The exact idiom:
- Module-level total `_total: int = 0` guarded by `threading.Lock` (`:38-39`) — increments arrive from event loop AND `asyncio.to_thread` workers (`:35-37`).
- Constant string lives in `daemon/constants.py`: `REPORT_INTEGRITY_JUNK_REPORT_TOTAL: str = "report_integrity_junk_report_total"` (`constants.py:1637`; block context `:1604-1636`).
- Optional sink: `attach_sink(callable)` (`:48-56`) — seam for a future Prometheus/OTel backend; receives one dict per observation `{"metric", "value", "total", "instance_id", "agent_id"}` (`:80-88`).
- Failure handling: the WHOLE increment body is try/except; ANY failure → `logger.debug(... non-fatal ...)` — never raise, never block the report path (`:67-92`, esp. `:89-92`). One structured INFO log line per increment naming the constant (`:71-77`).
- Readers: `get_junk_report_total()` (`:95-98`), test-only `reset_junk_report_total()` (`:101-105`).

**Existing scheduling/notifications metrics family?** NONE. The scheduler persists only a per-schedule run counter via `source_repo.increment_scheduler_run_counter` (`scheduler.py:2500`) and a display counter (`:1466-1474`) — persistence, not a metrics family. Sibling per-service counters (instance attrs `_x_total`, e.g. `eligible_pending_sweep.py:154-159`, `orphan_watcher_sweep.py:145-147`, `waiting_children_watchdog.py:697-698`) exist but lack the sink seam. **Verdict**: the idiom dictates a NEW family — a small sibling module (e.g. `daemon/services/schedule_fire_notify_metrics.py`) with lock-guarded module counters, `record_*` no-op-safe functions, `attach_sink`, `get_*` readers, and `"<lane>_<event>_total"` snake-string constants beside `constants.py:1637` (e.g. `schedule_fire_notify_sent_total` / `schedule_fire_notify_dropped_total`).

---

## 6. LOOP SAFETY + NO-SPAWN

**(a) What bounds message-triggered work today:**
- Graph loop breaker: `LOOP_BREAKER_DEFAULT_THRESHOLD = 3` consecutive identical tool-call units (`daemon/graph.py:214`; detection `:1445-1549`; looping history replaced by a repair summary `:2165-2184`). Bounds runaway tool-looping inside any turn — including a notified agent that misuses scheduling tools.
- Dependency-bus one-hop precedent: watcher rows transition `PENDING → FIRED` via an atomic CAS that prevents double-fire under concurrent terminal events (`daemon/repositories/dependency_bus/repository.py:14-27`; models `:54-70`, `:123-136`). The bus only transitions state and RETURNS FollowUps — the caller does the single enqueue (`daemon/services/dependency_bus.py:33`, `:43`, `:855`); fired FollowUps are delivered exactly once (`:823-832`). The fired notification does NOT re-arm watchers — TERMINAL coverage requires an explicit `watch_mission` re-call (receipt-keyed arming, mid-flight-QA design).
- child_reports precedent: one `internal_report:` MessageQueue row per (child, completed-message-id), idempotent by exact source-string re-scan (`child_reports.py:4877`, `:1352`, `:4594`).
- The scheduler fire itself is message-only by construction: both fire seams terminate in `enqueue_message_job` (`scheduler.py:2109`, `:2723`); the adapter has no chat-send path.
- Auto-trigger WITHOUT an explicit agent tool decision: exactly one class exists — dependency-bus FollowUps and child-report rows are enqueued with no agent decision, but each is durably deduped one-shot per key (watcher CAS / completion-id) and none arms a schedule. A scheduled task is created ONLY by an explicit agent tool call (scheduling tools, `daemon/tools/scheduling.py`); an inbound message with a reserved source can only produce one PROCESS_MESSAGE turn. **Verdict: one-hop is bounded by construction, PROVIDED the notify (i) never rides or mints a watcher row, (ii) never re-arms anything, and (iii) carries a `system:` source so downstream classifiers treat it as a terminal-traffic notice, not a user turn.**

**(b) Refuse-stranger-spawn (E1) precedent:**
- `BoundCallerRefusal` — `daemon/sources/mapper.py:89-129`: raised by `InstanceMapper.get_or_create_instance` when a mapping BOUND to a caller instance (`bound_to_caller` metadata, stamped by `self_alarm._bind_schedule_to_caller`, `:132-143`) resolves to a caller CONFIRMED gone. The lane must consume VISIBLY (`refused_permanent`), never spawn a stranger in the caller's place (`:90-108`).
- Raise site: `mapper.py:420-460` — log "E1 refused stranger spawn" `:444-449`, raise `:450-460`. Fail-open ONLY on indeterminate DB reads (`:434-439` — a transient error can never masquerade as confirmed absence).
- The scheduler adapter's reuse branch is documented as "the only stranger-spawn surface" (`scheduler.py:1964`, `:2433`, `:2804`).
- **The seam that guarantees message-only delivery can never spawn**: the entire enqueue lane (`enqueue_message_job` / `_prepare_enqueued_message`) addresses an EXISTING instance row and fail-closes on a missing one (`instance_messaging.py:2488-2493` + check-vs-commit backstop `:2700-2710`); there is NO create-instance branch anywhere in that path. Instance creation lives only in `mapper.get_or_create_instance` (source-adapter dispatch) and the spawn tools — never called by the enqueue lane. A notify that passes the already-known creator `instance_id` straight to `enqueue_message_job` and never touches the mapper has zero spawn surface.

---

## Cross-cutting notes for the planner

- `enqueue_message_job` resolves `queue_id` from the target's `project_id` with cross-project guard, default `system_parallel_queue` (`instance_messaging.py:3162-3163`, `:3222-3235`) — the notify rides the target's own queue.
- `idempotency_key` is documented as scheduler-only today ("only the scheduler adapter passes a real key (gated to one-time schedules)" — `:3206-3214`); a recurring notify needs its own dedup story (per-fire key or none).
- Body scrub is automatic: `scrub_enqueued_body` runs inside `_prepare_enqueued_message` before INSERT (`:2496-2513`) — the notify body gets the same secrets scrub for free.
- Case-sensitivity: source matching everywhere is case-SENSITIVE lowercase (`constants.py:1521-1540`) — mint the literal exactly.
