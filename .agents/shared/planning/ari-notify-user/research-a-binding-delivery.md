# Research A — session→source binding and the chat delivery seam

Scope: `ari-notify-user` planning commission (privileged ARI-ONLY `notify_user` tool).
Worktree: `/home/nea/ensemble-src-wt-notify-user` (branch `feature/ari-notify-user` @ `d4ecdbd3d`).
All citations are worktree-relative `file:line`. Read-only investigation; no repo files modified.

Legend: **[SPECULATION]** = inference not directly stated by code; everything else is a direct code claim.

---

## A1. Instance origin source and session-root resolution

**The origin is not a column — it is the write-once `instance_metadata["original_source"]` string.**

1. Instance table: `daemon/repositories/instance/models.py:47-76`.
   - `parent_id` column: `models.py:61`.
   - `instance_metadata` field mapped onto a JSONB column literally named `metadata`: `models.py:64-67`.
   - `instance_hierarchy(parent_id, child_id)` junction: `models.py:38-44`.
2. The hierarchy junction is the **working set**: rows are deleted when a child completes (`daemon/repositories/instance/repository.py:262-272` explains the distinction). The **permanent** tree is `instances.parent_id`.
3. **Stamping site (lazy, write-once, per instance):** `daemon/services/instance_messaging.py:3463-3539`.
   - Non-internal / non-system turn ⇒ `dispatch_source = message_source` (`:3512-3514`).
   - Persist only if absent: `current = instance_metadata.get("original_source")`; if empty and source does not start with `internal_` ⇒ `_instance_repository.set_metadata(instance_id, "original_source", message_source)` (`:3521-3528`).
   - Missing-metadata fallback does the same (`:3531-3539`).
   - **Only `internal_*` is excluded from stamping.** `scheduler`, `api`, etc. are NOT excluded (`:3522`, `:3533`).
4. **Inheritance at spawn:** the child copies the parent's `original_source` inside the same `WriteGuardSession` transaction — `daemon/services/instance_lifecycle.py:5577-5593` (parent read `:5582-5586`, merge `:5591-5593`); logged at `:2462-2466`. So every descendant of a chat session carries the root's chat source from creation.
5. **Spawn/creation path:** source adapters go through `InstanceMapper.get_or_create_instance` (`daemon/sources/mapper.py:359-540`), which spawns via `manager.spawn_instance_with_mcp` (`mapper.py:486`) and writes/updates the mapping (`mapper.py:519-540`). Scheduler reuse does the same (`daemon/sources/adapters/scheduler.py:2631-2636`).
6. **Session-root resolution — no single helper.** Primitives:
   - Up-walk of `instances.parent_id`: `daemon/services/snapshot_executor.py:447-468` (`_resolve_lineage_root`, depth-capped at 256, returns the topmost row).
   - Permanent tree API (root first): `daemon/manager.py:13820-13844` `get_tree_ids_permanent` → `repository.get_tree_ids_permanent` (docs at `repository.py:602-620`).
   - Another up-walk: `daemon/services/turn_transitions.py:771`.
   - Do NOT use `instance_hierarchy` for root resolution when children may have completed (`repository.py:262-272`).

---

## A2. Registered sources and what "chat-capable" means

1. **Registry:** `daemon/sources/registry.py:138-151` (`SourceRegistry.__init__(source_repo, manager, job_queue_service=None, instance_repo=None)`); `register` `:170`; **`get(source_id)` `:236-246`**; `list_adapters` `:257`; `start_all` `:275`.
2. **Adapter factory switch:** `registry.py:389-558`:
   - telegram → `TelegramAdapter` `:464-468`
   - scheduler → `SchedulerAdapter` `:469-541` (receives `job_queue_service`, `source_repo`, `instance_repo`, `manager`)
   - slack → `SlackAdapter` `:542-548`
   - discord → `DiscordAdapter` `:549-555`
   - anything else ⇒ `"Unsupported source type"` → `None` `:556-558`.
3. **Identity validation universe:** `daemon/sources/mapper.py:46-55` — `telegram`, `webhook`, `slack`, `discord`. (`webhook` has NO adapter in the registry → not a live delivery target.)
4. **Runtime source identifier:** the composite string `f"{source_id}:{external_user_id}"` built on the incoming path — `daemon/sources/registry.py:1055-1056`. `source_id` is an operator-defined slug; the dispatcher validates it as `^[a-zA-Z0-9_-]{1,64}$` (`daemon/sources/dispatcher.py:540`).
   - Discord external id grammar: `dm:{user_id}` | `{guild_id}:{channel_id}` | `{guild_id}:{parent_channel_id}:{thread_id}` — `mapper.py:64-76`; parse at `daemon/sources/adapters/discord/adapter.py:1353-1383`.
   - Slack external id grammar: `{workspace_id}:{channel_or_user_id}[:{thread_ts}]` — `mapper.py:57-62`; parse at `daemon/sources/adapters/slack/adapter.py:480-490`.
5. **Incoming path:** adapter → `on_message` closure → `SourceRegistry._handle_message` (`registry.py:456-461`, `:914`), which resolves/creates the instance (`:1028-1036`) and enqueues with the composite source (`:1055-1056`).
6. **Chat-capable vs internal:**
   - chat roots are colon-suffixed and resolve to a registered adapter; internal sources (`api`, `scheduler`, `system:*`, `internal_*`) are colon-less or unregistered.
   - Each chat adapter refuses to send unless it is RUNNING: discord `adapter.py:1595-1599`; slack `adapter.py:472-474`; telegram analogous (`daemon/sources/adapters/telegram.py:186`, `:259`).
   - The scheduler adapter is **one-way**: it has no `OutgoingMessage`/chat send path — its fire seam is `enqueue_message_job` (see A4/A5).

---

## A3. Watch-event delivery path (works today)

1. **Origin:** watcher notification `notify_work_watchers` (`daemon/services/work_notifier.py:286`) enqueues per watcher `instance_manager.enqueue_message(...)` (`:1131-1134`) with
   `source = _job_event_source(work_id, status)` = `f"internal_agent:job_event:{work_id}:{status}"` (`:226-242`). Mission-QA twin: `:1490-1493`.
2. **Turn completes → final dispatch:** `daemon/services/message_processing_pipeline.py:720-801` (`_dispatch_completed`).
   - **DECISION POINT:** `dispatch_source = context.message_source` (`:739`); the inner-report set at `:740-747` **includes** `internal_agent:job_event:`; `:748-767` replaces it with `instance_metadata["original_source"]`; JQ guard skips missing/`internal_*` (`:772-782`); empty skip (`:784-785`); then `self._source_dispatcher.dispatch_completed(...)` (`:788-794`).
3. **Dispatcher:** `daemon/sources/dispatcher.py:485-671` (`dispatch_completed`). Gates in order:
   - running `:511-513`; registry present `:515-517`; non-empty content `:519-522`; not already progressively sent `:524-529`;
   - **`if ":" not in source: return` (internal skip) `:531-535`**; `source_id` regex `:539-542`; external id length `:544-547`;
   - skip `internal_report` / `internal_error_report` `:551-556`; `adapter = registry.get(source_id)`; skip on miss `:558-566`;
   - marker/image extraction `:570-615`; build `OutgoingMessage` `:617-626`; per-user lock `:628-631`; `adapter.send(outgoing)` `:632`.
4. **Progressive lane (same turn):** `dispatch_message(source=dispatch_source, content=...)` at `daemon/services/instance_messaging.py:4972-4976` (deferred final after language check: `:5238-5246`) → `dispatcher.dispatch_message` (`dispatcher.py:673-808`).
5. **Eligibility summary:** a response reaches chat iff its dispatch source (turn source, or `original_source` for `internal_report:` / `internal_error_report:` / `internal_agent:job_event:`) is a colon-suffixed string whose prefix resolves to a registered chat adapter.
6. `daemon/graph.py` contains **no** chat-dispatch eligibility code (grep for dispatch hits only LLM-dispatch/withhold-budget logic, e.g. `graph.py:4849`, `:5020`).

---

## A4. Why scheduler / self-alarm turns do not deliver

**Implicit, not an explicit skip.**

1. Scheduler fire enqueues with `source="scheduler"`:
   - direct job-queue path: `daemon/sources/adapters/scheduler.py:2663-2671`;
   - deferred path: `scheduler.py:2078-2086`.
2. The turn's dispatch source resolves to `"scheduler"` (it is neither an internal report nor `system:*`): `instance_messaging.py:3463-3514`; `message_processing_pipeline.py:739`.
3. `dispatch_completed(source="scheduler")` hits the colon-less skip: `dispatcher.py:531-535`; progressive lane hits the mirror skip: `dispatcher.py:691-695`. There is **no** `is_scheduler` branch anywhere on the path.
4. On a reused chat instance `original_source` is already set and is **not** overwritten by the scheduler turn (`instance_messaging.py:3521-3530`) — so the binding survives, but dispatch never consults it for a scheduler source.
5. On a fresh scheduler-spawned instance, `"scheduler"` itself would be stamped as `original_source` **[SPECULATION: consequence of `:3512-3539` lacking a scheduler guard; the code path is direct]**.
6. No dispatch eligibility logic in `daemon/graph.py` (see A3.6).

---

## A5. v0.26.2 wake-the-caller semantics (`daemon/tools/self_alarm.py`)

1. `SELF_ALARM_INSTANCE_MODE = "reuse_instance"` — `self_alarm.py:134`; payload builder sets `"instance_mode": SELF_ALARM_INSTANCE_MODE` — `:858-871` (`:870`). Rationale docstring `:37-48`, `:124-133`.
2. **Binding:** `_bind_schedule_to_caller` (`self_alarm.py:874-976`) writes
   `source_repo.create_instance_mapping(source_id=<schedule source_id>, external_user_id=<same source_id>, agent_instance_id=<caller>, metadata={"source": "self_alarm", "bound_to_caller": True, "caller_instance_id": …})` at `:942-956`.
   - Failure raises `BoundCallerRefusal` (REFUSE-TO-ARM): `:922-936`, `:957-967`; class + semantics `daemon/sources/mapper.py:89-129`.
   - Docstring states the engine resolves `get_instance_mapping(source_id, source_id)` and dispatches into `mapping.agent_instance_id` (`self_alarm.py:885-901`).
3. **Fire:** `SchedulerAdapter._execute_run` selects `_route_via_job_queue` for reuse mode (`scheduler.py:2524-2534`) → `mapper.get_or_create_instance(source_id, external_user_id=source_id, force_new=False)` (`:2631-2636`) → `enqueue_message_job(instance_id=<mapped caller>, message=<alarm text>, source="scheduler", metadata=…)` (`:2663-2671`). The alarm therefore lands as an ordinary user-role message in the caller's session with `source="scheduler"`.
4. **Why still no chat delivery:** the woken turn's `message_source` is `"scheduler"` → colon-less skip (`dispatcher.py:531-535`). The session's chat `original_source` is irrelevant because it is only consulted for `internal_report:` / `internal_error_report:` / `internal_agent:job_event:` (`message_processing_pipeline.py:740-767`).

---

## A6. v0.27.3 deferred / coalesced alarm delivery

1. Design comment (defect + fix): `scheduler.py:1082-1095` — a fire at a BUSY caller used to be silently dropped; now it becomes a durable PENDING delivery (PENDING Task + MessageQueue row + JobItem), the fire is recorded as `deferred`, and repeated fires **coalesce** into that one delivery (at-most-once restored).
2. Bounds: `_defer_bounds()` reads `SchedulingConfig.defer_max_count` / `defer_max_age_seconds`, defaults `(16, 86400)` — `scheduler.py:1097-1115`.
3. Episode carrier: `_defer_state()` / `_apply_defer_state()` read/write the schedule row's `config` JSON keys `_defer_gen`, `_defer_message_id`, `_defer_count`, `_defer_since` — `scheduler.py:1117-1161`.
4. Identifiers: deferred execution id prefix `deferred-` (`:115`, `:2036`); idempotency key `scheduler:{source_id}:deferred:v{gen}` (`:1281-1294`); body `_format_deferred_message` (`:1231`), metadata `_deferred_metadata` (`:1258`).
5. Coalescing write: `_coalesce_into` (`:1792`); rollover / marker-miss fallback `:2012-2034`; ordering "row first, then dispatch" `:2041-2058`.
6. **Delivery seam:** first fire of a generation (or rollover) calls `enqueue_message_job(instance_id, message=scrubbed, source="scheduler", priority=…, metadata=…, idempotency_key=…)` — `scheduler.py:2078-2086`. The pending MessageQueue row is then consumed by the normal queue/turn machinery (the busy-idle transition is what releases it; see reuse-check ordering note `:2295`).
7. **Chat reach: none.** The deferred path never touches a chat adapter; its enqueue source is `"scheduler"` (`:2082`) → same colon-less skip as A4. The deferred lane is about not LOSING the wake, not about delivering to chat.

---

## A7. Dispatch seam candidates for a one-shot agent tool

**(i) Same lane watch events use — resolved `original_source` + `dispatch_completed`:**
```python
await manager.source_dispatcher.dispatch_completed(
    instance_id=<session-root or current>,
    message_id=<synthetic id, unused by the body>,
    source=<resolved original_source>,   # "source_id:external_user_id"
    content=<text>,
    message_type="final",
)
```
- Signature: `daemon/sources/dispatcher.py:485-494`. Async. Requires `manager.source_dispatcher` (`daemon/manager.py:1651-1652`) and a running dispatcher (`dispatcher.py:511-513`).
- No locking/circuit/rate logic inside `dispatch_completed` beyond the per-user send lock (`:628-631`, `_get_send_lock` `:810-835`; `MAX_SEND_LOCKS = 10000` `:433`).
- Source binding: the seam sends to whatever `source` it is given — it does **not** verify that the source belongs to the instance. The tool must resolve the binding itself.

**(ii) `dispatch_message(source, content)` — the progressive lane:**
- Signature/body: `daemon/sources/dispatcher.py:673-808`; same gates; per-user lock (`:629`, `_get_send_lock` `:810-835`).
- Side effect: marks `source` in `_progressive_sent_sources` (`:806`), which **suppresses a later `dispatch_completed` for the same source in the same turn** (`:524-529`). Relevant if the tool fires mid-turn and the turn also produces a final response.
- No circuit breaker / token bucket in the dispatcher itself — those are adapter-local: discord `adapter.py:1601-1603` (`can_execute`), slack `adapter.py:332`, telegram `adapter.py:186`, `:259`; rate limiters `daemon/sources/rate_limiter.py:25` (`TokenBucketLimiter`), slack `adapters/slack/rate_limiter.py:91-176` (`TokenBucket` + `SlackTieredRateLimiter`, constructed `adapters/slack/adapter.py:150`), telegram `adapters/telegram.py:130`.
- Callable from a tool: yes — plain async method on an object reachable via the manager; tools are already async (`self_alarm.py:493`).

**(iii) "progressive dispatch" / message-display lanes:**
- `dispatch_message` **is** the progressive chat lane (comments at `instance_messaging.py:3463` "effective source for progressive dispatch"; `:4972-4976`).
- The frontend **message-display** lane is separate: `MessageQueue.image_refs` + SSE (`manager.enqueue_message` docs `manager.py:9673-9683`) — it targets the UI, not an external chat source; not a candidate for `notify_user`.

---

## A8. Discord adapter caps

1. `DISCORD_MAX_MESSAGE_LENGTH = 2000` — `daemon/sources/adapters/discord/constants.py:35`.
2. Splitter: `_split_message` — `adapters/discord/adapter.py:1280-1344`.
   - returns `[content]` when it fits (`:1298-1299`);
   - 5-tier boundary chain over a `remaining[:max_length]` window: paragraph `\n\n` (`:1307-1309`) → line `\n` (`:1312-1314`) → sentence `. ` / `! ` / `? ` (`:1316-1328`) → word/space (`:1320-1328`) → hard cut at `max_length` (`:1330-1332`);
   - chunk is `rstrip()`ed, remainder `lstrip("\n")` (`:1334-1335`), empty-chunk guard `:1336-1338`, final short remainder appended `:1342-1343`.
3. Send flow: circuit check → resolve target (DB) → split → channel lock → semaphore → per-chunk sequential send — `adapter.py:1588-1593`; chunk loop `:1761-1790`.
4. Other adapter-level caps:
   - per-channel ordering locks with LRU eviction: `_get_channel_lock` `:789-824`; `MAX_CHANNEL_LOCKS = 1000` (`constants.py:10-12`).
   - outbound concurrency semaphore `DiscordSendSemaphore` (`adapters/discord/resilience.py:12`; `DEFAULT_MAX_CONCURRENT_SENDS = 5` `constants.py:48-51`; constructed `adapter.py:264-266`).
   - 429 handling: discord.py handles backoff internally; 429 is excluded from the circuit failure count — `adapter.py:1542-1569`.
   - channel/thread sends require a DB mapping (`_resolve_send_target` `:1385-1429`; missing mapping → warn + None); DMs need none (`:1401-1408`).
   - Slack has a tiered token bucket (`adapters/slack/rate_limiter.py:151-176`) and per-channel locks (`thread_manager`/adapter); Slack `send()` also does a DB mapping lookup (`adapter.py:463-499`).

---

## A9. Tool context resolution

1. **What a tool factory receives:** services + repos + `current_instance_id` + `agent_id` (+ `agent_tag`). Precedent: `create_self_alarm_tools(service, *, source_repo=None, instance_repository=None, project_repository=None, current_instance_id="", agent_id="", agent_tag=None)` — `self_alarm.py:409-443`. Wired from manager attributes in `daemon/tools/instance.py:2381-2418` (`manager.scheduling_service`, `manager._source_repository`, `manager._instance_repository`, `manager._project_repository`). Board precedent: `instance.py:2346-2378` (receives `manager.board_service`).
2. Tools are **closures** — there is no ambient request/turn context. Anything the tool needs (manager, registry, dispatcher, instance repo) must be passed at factory time.
3. **Privileged posture (three layers):**
   - category membership in `PRIVILEGED_TOOL_CATEGORIES` (`daemon/tools/_tool_registry.py:338`+; count pinned at 16, comment `:326`);
   - explicit per-agent allow entry — `agents/ari/meta.json:11`, `:40` (`"self-alarm"`);
   - a body-level gate (`SELF_ALARM_AUTHORIZED_AGENT = "ari"`, `self_alarm.py:116`; rationale `:19-29`).
4. **No single helper exists** for `current instance → session root → origin/registered source → live adapter`. Compose from primitives:
   1. instance row: `manager._instance_repository.get(instance_id)` (`self_alarm.py:318`; facade precedent `manager.py:13820-13844`);
   2. root walk: `_resolve_lineage_root` pattern (`snapshot_executor.py:447-468`) over `instances.parent_id`; reusable repo primitive `get_tree_ids_permanent` (`repository.py:602-620`) / `list_child_ids_permanent` (`:262-283`);
   3. origin: `row.instance_metadata.get("original_source")` (`instance_messaging.py:3500`; `message_processing_pipeline.py:765-767`);
   4. binding/mapping: `source_repo.get_instance_mapping(source_id, external_user_id)` (`scheduler.py:2412-2414`; `adapters/discord/adapter.py:1417-1421`);
   5. live adapter: `manager.get_source_registry().get(source_id)` (`manager.py:13978-13980`; `registry.py:236-246`);
   6. dispatcher: `manager.source_dispatcher` (`manager.py:1651-1652`).
5. Note for wiring: `create_self_alarm_tools` does **not** receive the manager (only repos). A `notify_user` factory that needs the registry/dispatcher must add a `manager` (or explicit registry/dispatcher) parameter and extend the `*_if_available` wiring in `daemon/tools/instance.py`.

---

## A10. Non-chat origin edge cases

1. **HTTP API origin:** enqueue uses `source="api"` (`daemon/routers/messages.py:691`; also `"api_resume_fallback"` `:427`). Colon-less → dispatcher skip (`dispatcher.py:531-535`). It **is** stamped into `original_source` on a fresh API-created instance (`instance_messaging.py:3512-3539`; only `internal_*` is excluded).
2. **Job / job-event origin:** source tokens like `internal_agent:job_event:{work_id}:{status}` (`work_notifier.py:226-242`). Dispatch resolves to the instance's `original_source` (`message_processing_pipeline.py:740-767`); if that is `"api"` → colon-less → skip.
3. **Scheduler origin:** `"scheduler"` (A4) — colon-less → skip; on a fresh scheduler instance it would be the stamped `original_source` **[SPECULATION: direct consequence of `:3512-3539`]**.
4. **What the system knows at refusal time:** only the persisted string `instance_metadata["original_source"]`. There is no enumerated "origin kind" field. Therefore the tool's chat-capability test must be:
   - `original_source` non-empty **and** contains `":"`, **and**
   - the prefix names a registered adapter (`registry.get(prefix)` not `None`), **and**
   - that adapter's `source_type ∈ {"discord", "slack", "telegram"}` (adapters exist only for these plus `scheduler` — `registry.py:464-555`).
   - `"webhook"` passes mapper validation (`mapper.py:46-55`) but has **no** adapter → must be classified non-chat.
   - The scheduler adapter is registered but is one-way → must be excluded despite the registry hit.
5. **Children:** spawned under a chat session, a child inherits `original_source` inside the spawn transaction (`instance_lifecycle.py:5591-5593`), so its own origin check resolves to the root's chat source without an explicit parent walk — provided it was created that way (write-once: inheritance happens at creation, before any message can stamp it).
6. **Session-level implication for `notify_user`:** since `original_source` is write-once and never overwritten by scheduler/alarm turns (`instance_messaging.py:3521-3530`), a reused ari session retains a valid chat binding across self-alarm wakes — the binding is available even though the current turn (source `"scheduler"`) would never dispatch.

---

## Consolidated answer to the commission's central question

The only durable session→chat binding is `instances.instance_metadata["original_source"]` (composite `"{source_id}:{external_user_id}"`, write-once, inherited at spawn). Chat dispatch exists at exactly one seam — `ResponseDispatcher.dispatch_completed` / `dispatch_message`, gated by `":" in source` + a registered RUNNING adapter (`dispatcher.py:531-566`). Turns deliver today only when their *turn source* (or, for `internal_report:` / `internal_error_report:` / `internal_agent:job_event:`, their resolved `original_source`) is such a composite string. Scheduler/alarm turns carry the colon-less source `"scheduler"` and therefore never deliver, by construction rather than by an explicit skip. A `notify_user` tool can ride `dispatch_completed` (or `dispatch_message`) with `source = original_source` resolved from the session root — the seam performs no ownership check, so binding verification is the tool's responsibility — and must refuse when the resolved origin is absent, colon-less, or not a live chat adapter.
