# Research Findings: /compact Slash Command (Backend)

Date: 2026-08-31
Branch: feature/slash-commands @ 5e16f791
Compiled by: plan-creation worker (backend phase)
Sources: Explorer 1 (compaction deep-dive, HIGH confidence), Explorer 2 (API/lifecycle/SSE map, HIGH confidence), Explorer 3 (frontend digest, HIGH confidence — FE planning lives in phase2-plan.md, not this phase).

> **Correction applied during compilation (verified on branch):** the message-token
> estimator lives at **`daemon/loader.py:465`** (`estimate_messages_tokens`), NOT
> `daemon/utils/loader.py` as stated in the raw Explorer 1 report. `daemon/utils/`
> does not exist. All citations below use the verified path.
>
> **Spot-verified on feature/slash-commands @ 5e16f791:** `compaction.py:1038`
> (`timeout=30.0` literal), `compaction.py:1011` (`wrap_langchain_failover`),
> `instance_messaging.py:1146-1150` (terminal guard), `messages.py:222-243`
> (validation order + status capture at :243), `config.py:706-715`
> (`CompactionConfig`, `env_prefix="COMPACTION_"`), `graph.py:2433-2438`
> (`compacted_at` schema field).

---

## 1. Compaction engine (Explorer 1)

### Entry points (2 call sites of `compact_state`, both automatic)

**Path A — proactive (pre-invocation):**
POST /messages → `manager._process_message_with_tracking` (manager.py:6479) →
InstanceMessaging turn pipeline → `if not is_retry: await self._maybe_compact_context(instance_id, graph, config)` (instance_messaging.py:3570-3571).
`_maybe_compact_context` (instance_messaging.py:1116-1220):
- compactor-None guard :1123
- `aget_state` :1128
- **terminal-checkpoint guard `if not state.next: return` :1146-1150** — `aupdate_state` on a finished graph clears `next=()` and bricks turn resume (comment :1132-1145)
- messages :1152
- system_prompt_tokens from prompt cache (:966-980, 0 on miss)
- CompactionContext built :1157-1176 with **model_name = GLOBAL `config.llm.model` (:1160)**
- `compact_state` :1179
- persistence = TWO `aupdate_state` calls: `{'messages': result.replacement_messages}` as_node='agent' (:1190-1194), then `{'compacted_at': iso}` (:1197-1202)
- whole body try/except → warning + skip (:1219-1220, never blocks the message)

**Path B — reactive (CLE retry, inside agent_node):**
`create_agent_node(compactor=...)` (graph.py:2716 — corrected 2026-08-31 from the prior `:2719` near-miss; :2734-2742; threaded from instance_lifecycle.py:1627);
`except ContextLengthExceededError` (:3489): no-compactor → re-raise (:3490-3492);
`aget_state` → CompactionContext with **system_prompt_tokens=0** (:3499-3511);
`compact_state` :3513 (None → re-raise :3514-3516);
`aupdate_state` messages + compacted_at (:3518-3520);
re-read state, rebuild `[SystemMessage(system_prompt)]` + checkpoint msgs (:3524-3525);
pairing guard `_ensure_tool_result_pairing` (:3527-3542);
C3 re-append injected (:3553-3560) + report (:3566-3567) msgs;
ephemeral half is a documented no-op since 2026-07-29 (:3569-3601);
single re-invoke (:3603-3608).

### Threshold decision — inside `ContextCompactor.compact_state` (compaction.py:608-664)

- dedup guard (:618-620, `_is_recently_compacted` 60s window :1113-1130)
- injected-message partitioning (:627-640)
- min-messages (:645-651)
- `total_tokens <= context_window * config.threshold` (:659-664)
- All knobs from `CompactionConfig` (config.py:706-753, wired :1643, loaded :1928-1929); constants.py:80-86 mirror defaults but engine reads only config.
- ALL GLOBAL — no per-instance config; proactive uses the global model for window math; per-MODEL windows via `context_window_overrides` (config.py:715-749).

### Engine init

manager.py:381-409 — `if config.compaction.enabled: ContextCompactor(config, llm_config={... base_url_backup :390, request_timeout :395, buffer_response_header :400})` else None (:409). Compactor adds proxy headers itself (compaction.py:591-606).

### Summarization LLM call — `_call_summarization_llm` (compaction.py:980-1042)

- Lazy imports `ThinkingChatOpenAI` + `clean_llm_config` from `.graph` **IN FUNCTION BODY** (:994-995 — **tests must patch `daemon.graph`, NOT `daemon.compaction`**)
- Model: `config.summarization_model` override else session model (:997-1008); default `""` (config.py:751); purpose-bound Layer-1, never gated by allowed_models
- Prompts: summarize :889-898, merge :933-938, condense :966-970, fixed SystemMessage :1031-1034
- NON-streaming, single invoke via `asyncio.to_thread` (:1027-1039)
- Failover: `wrap_langchain_failover(llm, llm_config)` (:1011) called WITHOUT `wall_clock_cap_s` → facade default **45.0s** applies (llm_failover.py:529/:623/:706); bounded retry 3 timeout/transient attempts, stop_after_attempt|stop_after_delay (:559-568, :840-854)

### EXACT timeout stack per call

1. `asyncio.wait_for(..., timeout=30.0)` at **compaction.py:1027-1039** — the BINDING cap (cancels inner task; TimeoutError → :753 except → truncation fallback); comment :1013-1026 says facade cap is "the real cap home" but the 30s site cap fires first. *(Verified: literal present at :1038 on this branch.)*
2. facade `wall_clock_cap_s=45` unreached
3. `llm_config.request_timeout` subsumed

**No whole-operation cap:** chunked summarization issues N sequential calls (batches of 20 groups :819-840, merges :846/:939/:958/:971) → worst case ≈ N×30s unbounded.

**Adaptive-timeout plug point: the `timeout=30.0` literal at compaction.py:1038.**
`_call_summarization_llm(prompt, context)` already receives full CompactionContext (.messages), so `estimate_messages_tokens(context.messages)` (**daemon/loader.py:465**; tiktoken cl100k_base, same estimator as context_usage SSE) can compute base-90s + 60s/100k scaling cap-300s right there. Secondary: pass `wall_clock_cap_s=<same+margin>` at :1011.

### Persistence — checkpoint-only, NO DB rows

`aupdate_state` with RemoveMessage sentinels + summary + preserved tail (D3: direct list assignment CONCATENATES under `add_messages` — decisions.md:40-63 of prior plan `.agents/shared/planning/context-compaction/`).

Replacement list `_build_replacement_messages` (compaction.py:1044-1079): RemoveMessage per compactable-with-id (:1060-1066) + summary SystemMessage + preserved verbatim + injected re-attached.

Summary = `SystemMessage("[Conversation Summary]\nTimestamp: {iso}\n{text}", id=f"compaction-{uuid4()}")` (:903-906); merge id `compaction-merge-*` (:943); condense id `compaction-condense-*` (:975). ID rename `lc_run--UUID`→`truncated-<uuid4>` happens ONLY on emergency-truncation path (:707-710).

`compacted_at` is a DECLARED state-schema field (graph.py:2433-2438; D12: unknown keys silently dropped by aupdate_state). *(Verified on branch.)*

### FE/API visibility

GET /messages (persistence.py:254) reads checkpoint channel_values[:316-318], skips ToolMessages, prepends synthetic system msg (:404-449). After compaction FE sees: synthetic system → role=system `compaction-<uuid>` "[Conversation Summary]" message → preserved tail. NO flag/metadata marks compaction.

**SSE: nothing emitted at compaction time**; only the indirect context_usage event (LiveEventHub.stream_context_usage live_event_hub.py:255-284; `emit_context_usage_for_instance` instance_messaging.py:1061-1114 — one call gives FE an immediate token-drop refresh after compaction).

### On-demand triggers — NONE exist

Watchover's compaction summary is dormant/uncalled (watchover_service.py:173-207). Closest manager-service template: `emit_context_usage_for_instance` (:1061); closest route template: routers/instances.py status-action endpoints.

### Trim logic (fallback) — exists ONLY inside compaction module

- `_truncate_fallback` (compaction.py:1081-1111 — drops oldest compactable groups via RemoveMessage, keeps preserved window) — **THIS IS the existing trim fallback**, already runs when summarization fails
- `emergency_truncate` (:421-500, 4-pass char truncation + oldest-drop)
- `_truncate_batch_to_fit` (:503-562)

**NOTE:** today summarization failure/timeout → caught at :753-772 → `_truncate_fallback` → compaction still applies as `compacted_type="truncation"` with `summarization_error` recorded — i.e., DESTRUCTIVE TRIM WITH NO SUMMARY on LLM outage.

### Failure modes

- Proactive exception → warning + turn proceeds uncompacted (:1219-1220)
- Reactive None-result (dedup/min-msgs/threshold) → re-raise CLE → turn fails (llm_error_classifier.py:388-391); single re-invoke only

### Gaps /compact must add

1. force flag — `compact_state` refuses below threshold/within 60s dedup/under min-messages
2. API route + command parsing
3. concurrency gating — proactive only runs between turns; /compact while RUNNING races in-flight aupdate_states
4. per-instance model resolution for window math (today global)
5. adaptive timeout
6. SSE event at compaction time

### Config knobs

compaction.enabled (True), threshold (0.80), recent_message_window (10), min_recent_window (3), target_ratio (0.40), min_messages_before_compaction (10), summarization_model (""), summarization_chunk_threshold (0.60), context_window_overrides ({}), context_window_default (0) — env prefix `COMPACTION_*` (config.py:709-753). Hard-coded: emergency char caps 2000/4000, batch 20 groups, dedup 60s, site timeout 30s, summary cap 10% window.

### Prior-plan decisions still binding

From `.agents/shared/planning/context-compaction/decisions.md`:
D2 boundary groups never split AI+ToolMessage; D3 RemoveMessage sentinels in ONE aupdate_state; D4 summary as SystemMessage never AIMessage; D5 progressive window reduction; D6 chunked summarization; D9 60s dedup; D10 skip on is_retry; D12 compacted_at in schema; D13 emergency truncation guarantees termination.

---

## 2. API / lifecycle / SSE map (Explorer 2)

### POST /api/instances/{id}/messages (daemon/routers/messages.py:159-553)

Validation order = 503 write-pause :202-203, 404 :206-215, 400 empty content :222-229, 400 images-without-vision :232-240; then **status captured at :243** → branches:
- RUNNING (+WC legacy) → RAM injection slot :402-500 → 202 `{status:"injected", message_id=echo_id, pending_count}`
- WC+flag → durable enqueue
- PAUSED → auto-resume :252-378 → 200 `{auto_resumed:true, resume_info}`
- IDLE/QUEUED/terminal → durable enqueue_message_job :502-553 → 200 `{status:'queued', message_id, job_id}`

Terminal instances take the NORMAL branch (no 409); send_message revives terminal instances (instance_messaging.py:1486-1510).

**RECOMMENDED intercept seam: router-level check after :240 validation, BEFORE :243 status capture** — covers all 4 branches with one check; HTTP-only blast radius (exactly FE scope; agent-to-agent/sources/job_inject/job_continue bypass it — acceptable, they aren't UI users); synchronous command ack in POST response + SSE progress.

Alternatives (rejected/worse):
- enqueue_message_job payload sniff (manager.py:6428) — misses the RUNNING injection lane entirely
- job-processor admission — post-commit redirect, queue latency, no added coverage
- language_check_node pre-LLM drain (graph.py:2593-2679; line numbers corrected 2026-08-31) — universal but the command would execute INSIDE the turn whose checkpoint it rewrites: incoherent

### SSE

LiveEventHub (daemon/services/live_event_hub.py, app.state.live_hub) — in-memory per-instance queues, NO persistence/replay. `stream_message(instance_id, message=payload, event_type="command_progress")` (:150-173) supports ANY custom event_type, zero new hub code.

Copy `_emit_injection_sse` pattern (messages.py:114-156): flat payload + additive correlation id + try/except WARNING-swallow (best-effort SSE never fails the API call).

Payload shapes: message-framed `{instance_id, event_type, event_id, message, checkpoint_id}` or flat lifecycle `{instance_id, event_type, content, timestamp, ...}`. Live-only → pair with POST-response status and/or GET fallback modeled on GET /{id}/injection (messages.py:572+).

### Lifecycle

InstanceStatus = IDLE, RUNNING, WAITING_CHILDREN, PAUSED, COMPLETED, ERROR, FAILED, TERMINATED (repositories/instance/models.py).

Pause-first: `pause_instance_cascade` (instance_lifecycle.py:2685-2691, cascade_to_root default True — 5 internal callers, don't flip) → `graph_task.cancel()` (checkpoint-safe, freezes at node boundary; cancelled tasks stay PROCESSING; CancellationReason discriminates) → bounded quiescence `wait_for_instance_quiescent(instance_id, timeout=30.0)` (manager.py:3362-3431; timeout<=0 = immediate probe; never raises) → mutate → `resume_instance_cascade` (:2971) is DB-only PAUSED→RUNNING; next dispatch runs `_process_message_with_tracking(is_retry=True)` replaying from checkpoint. First consumer: WatchoverService.activate_watchover (watchover_service.py:1004, suspension_reason="watchover_setup").

### Idle-instance ops

IDLE = no in-flight graph task, no PENDING/RUNNING/PAUSED tasks. Quiescence-without-pause helpers: `wait_for_instance_quiescent(timeout=0)` (registry, fast) + `has_instance_busy` (DB PENDING+RUNNING+PAUSED, canonical).

**What breaks if compaction runs while RUNNING:** in-flight astream loaded state at turn start commits at node boundaries — an external checkpoint write is silently overwritten by the next commit, or corrupts the live message list; NO merge exists.

**ExecutionGate per-instance asyncio.Lock (daemon/services/execution_gate.py:108-143, run :118) serializes graph runs — a compaction executor must acquire the SAME lock (or verify no lease) so it can't race a turn; taking it while RUNNING blocks until turn end (valid 'wait then compact', unbounded latency).**

Practical rule:
(a) IDLE + quiescent-probe True + has_instance_busy False → run directly under ExecutionGate lock (prevents a turn starting mid-compaction);
(b) RUNNING/PAUSED-with-turn → pause-first → compact → resume.
PAUSED with no in-flight task is effectively quiescent (checkpoint frozen; SQL claim gate `claim_pending_task` at task/repository.py:1146 excludes paused/terminated from claim — no worker steals the turn; corrected 2026-08-31 from the prior `:646-671` anchor). Companion `has_instance_busy` at `:543` is the broader PENDING/RUNNING/PAUSED predicate and counts PAUSED rows → stale-busy read possible between probe and gate-acquire (benign: re-check under the gate with retry-once).

### JAFP

Job = public work primitive; internal paths use enqueue_message only; `job_type='message'` JobItems are pure mirrors — `enqueue_job` ACCEPTS them (job_queue_service.py:574, special-cased at :646/:652/:773/:779 — PG trigger skips the `job_locks` claim). (Plan wording corrected 2026-08-31: the prior "REJECTS" line inverted the JAFP claim.)

**Codebase-implied shape for slash commands: router interception + direct service execution (NOT a new JobItem type)** — a /compact is ephemeral UI-triggered maintenance matching the question-pack/injection class of RAM-scale transient ops. Unless commands must survive daemon restarts as durable work.

### Concurrency guards to respect

- ExecutionGate (THE guard)
- `claim_pending_task` SQL gate (task/repository.py:1146; corrected 2026-08-31 from the prior `:646-671` anchor)
- has_instance_busy
- per-instance claim serialization (task/repository.py:1348-1407)
- RAM injection FIFO `_pending_injections` (manager.py:643 definition, accessed :2462+, :2528, :3638; corrected 2026-08-31 from the prior `:2398` anchor) — drain-or-reject when non-empty
- reconcile_turn_mirror suppression while status ∈ (waiting_children, paused, running) (task/repository.py:780-827) if mirror rows written

---

## 3. Frontend digest (Explorer 3)

FE is **Angular 21** (NOT React): standalone components + signals, Material, RxJS.
Send: message-input.component.ts → chat.component.ts onSendMessage (:1225-1362) → api.service.ts:187-191 POST /api/instances/{id}/messages.
SSE: per-instance EventSource /api/instances/{id}/events; sse.service.ts connectInternal() :249-600, one addEventListener per type → signals; new event type = listener + signal + REST fallback (fetchPendingInjection :630-653 pattern; SSE live-only no-replay).
Reusable UI: pending-injection card, provisional pending bubbles, snackbar, isSending spinner. No slash palette today.
Input-clear contract: parent clears on API success only.
Tests: Playwright e2e frontend/e2e/ (never networkidle — notification stream stays open; use domcontentloaded+waitForSelector; model send-pause-button.spec.ts), Jest logic-mirror units (no TestBed). Zero compaction rendering today; compaction summary surfaces as role=system message (hidden behind showSystemPrompt toggle by default).

*FE planning belongs to phase2-plan.md (other owner). Backend must pin the POST-ack + SSE contract explicitly (see phase1-plan.md §WS-5).*

---

## 4. Test-suite anchors (verified on branch)

- `tests/unit/test_compaction.py` — main compaction suite (extend for force flag, adaptive timeout, fallback transitions)
- `tests/unit/test_compaction_multimodal.py` — multimodal compaction coverage
- `tests/unit/services/test_execution_gate.py` — gate semantics pattern
- `tests/unit/routers/test_message_status_endpoint.py` — messages-router test pattern (extend for intercept tests)
- `tests/unit/services/test_compact_fired_watchers_deliver_before_compact.py` — compaction/watchover interplay pattern
- Repo hazard: file-backed SQLite (tmp_path) only, never StaticPool in-memory — see repo conventions
- FE tester note: frontend/e2e Playwright exists for web-automation coverage in phase 2
