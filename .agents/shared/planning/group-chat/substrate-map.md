# Agent Group Chat — Consolidated Substrate Map
<!-- Doc-set Rev 6 (single alignment stamp; 2026-11-15 final fix pass). -->
<!-- Phase-1 DESIGN · planning-only artifact · NO code changes · NO commits · NO git operations -->
<!-- Provenance: consolidated from three investigation reports; ALL anchors at repo tip bf4bfde6e; every correction below preceded by a direct read at the pin. -->
<!-- Correction history: (C1) §F seam corrected — injection-drain wrapper terminal-verdicts only; todo-step seam = every-update reminder-return. (F-B) wrapper quartet :4942-4952 / :4984-4986 / :4995 / :5040, cap :4933. (Rev-4) END chain stated once (language_check → [room_completion_gate] → attestation_gate → END, region :13378-13415); latch :13159 graph-builder-local. (Rev-5) 12-cite anchor sweep (router :1610; WC :1572; midflight_qa :194; work_notifier :1339/:1583; hoisting → daemon/compaction.py:337/:425/:3233; constants :170; skill_meta_parser services; title-gen class :17; loader path; post-graph chain B3-corrected). (Rev-6) task_context stamp :5157-5162; DEFECT-A guard :4792-4816; language-check :4758/:4770/:4807-4812; F-1 predicate SQL :1312-1322; 10-state enum totality (`waiting` alive, no lane gap). -->
<!-- Tag legend: [R] = reusable as-is · [A] = adapt · [M] = mint new (greenfield). DEFECT / MISSING markers are report-verbatim. -->
<!-- Domains: A delivery lanes · B persistence/queues · C roster & membership · D tool injection & category minting · E system-message assembly · F nudge hook points · G naming machinery · H UI/audit surfacing · I concurrency primitives · J house conventions. -->

---

## A. Delivery lanes

**send_message routing — tri-lane.**
- **Injection lane (RUNNING only):** `INJECTION_ELIGIBLE_STATUSES` (constants.py:908-910); router `_route_send_message` (**def instance.py:1610-1728**); live-graph gate `has_live_graph_task` (manager.py:6833-6893); **DEFECT-A guard at the router call site (instance.py:4792-4816)** downgrades graphless-running to durable enqueue. RAM-FIFO `set_injection` per-instance list, oldest-first (manager.py:4909-5020), drained next agent_node pass atomically (graph.py:8752-8830). **[R]**.
- **Enqueue lane:** IDLE/WAITING/QUEUED and WAITING_CHILDREN — **the house router serves `waiting` via durable enqueue without a status flip** (docstring instance.py:1585-1591; WC-never-injects B1 note :1572; branch prose :1650) → `enqueue_message` (instance_messaging.py:2787+) writing MessageQueue+Task atomically (`_prepare_enqueued_message` :2130+). ⚠ Enqueue also auto-revives TERMINAL instances (COMPLETED/TERMINATED/ERROR/FAILED → RUNNING, instance_messaging.py:2537-2579). **[R]** — a room path must defeat the terminal arms with a status CAS and must NOT need to flip `waiting` to route it (the house lane already serves it).
- **Terminal-revive lane:** COMPLETED/TERMINATED/ERROR/FAILED → revive+enqueue; revive-once budget only for ERROR/FAILED priors (instance.py:1577-1590, 4999-5030). PAUSED → agent lane rejects; HTTP lane auto-resumes (routers/messages.py:353-504). **[A]**.
- **Enqueue-only exceptions:** `load_skill` (daemon/services/skill_meta_parser.py:94 `extract_load_skill`) and `context`; call-site override instance.py:4750-4774. **[A]** (context-flatten fallback block instance.py:4895-4930 = pattern for richer payloads).
- **Busy-guard:** reject when pending/processing >0 (block instance.py:4895-4930); `load_skill` never flattened. **[R]**.
- **Provenance:** `internal_agent:<caller_iid>` on `additional_kwargs["source"]`, stamped at drain (graph.py:8765-8773). **[R]**.
- **Context truncation:** `_TASK_CONTEXT_MAX_CHARS=4000` (instance.py:116); task_context HumanMessage stamped at **instance_messaging.py:5157-5162** (`injected_message=True` + `context_kind="task_context"`). **[R]**.
- **Guarantees:** FIFO incl. M1 requeue; injections mid-turn, enqueues later turns (W5 constants.py:876-884); RAM lane at-most-once/volatile: 1h TTL (manager.py:4907), pause clears FIFO (manager.py:7120-7160), restart loses it.
- **MISSING:** durable multi-recipient fan-out.

**job_inject — dual-lane tool template.** tools/job_queue.py:3405-3572; RUNNING+live graph → `set_injection` (⚠ no `source=` — job_queue.py:3561); graphless/WC → busy pre-check + durable enqueue; IDLE/PAUSED/terminal → error → job_continue. W2 queued-polarity trap (messaging_types.py:38-62). **[A]**.

**Injected-message durability (checkpoint replay).** Injections ARE checkpointed and replayed on revive (graph.py:383; child-report drain :9067-9073; turn-end FIFO drain :5026-5035). **[R]**.

**Dependency-bus.** 1:1 CAS watcher wake (services/dependency_bus.py) — **[A for rooms]** (optional wake primitive; not broadcast-native).

**QA mission fan-out.** daemon/services/midflight_qa.py **:194 `MISSION_RECEIPT_SCAN_CAP = 128`**; daemon/services/work_notifier.py **:1339 `QA_WATCHER_ROW_CAP = 256`** (def `notify_mission_qa_watchers` **:1583**). **[R pattern]**.

**mid_flight_report — milestone-report-by-toolcall shape.** daemon/tools/midflight_report.py:64 (`async def mid_flight_report`). **[R shape]**.

**FAN-OUT MISSING.** No generic "message all members" helper; daemon→N = N sequential sends. Templates: QA watcher fan-out + cascade gather (instance_lifecycle.py:2667+).

---

## B. Persistence / queues

**message_queue table.** models.py:56-128: one row per RECIPIENT; status pending|ready|processing|retrying|completed|failed; **NO seq column**; ordering `ORDER BY priority ASC, enqueued_at ASC` (repository.py:330/:348); Task-driven read path (instance_messaging.py:2130+); `get_by_instance` newest-first (:182); `get_pending_for_instances` (:1108, unordered). **[R]**.

**Boot recovery (house-critical).** F-1 wipe — **doomed-predicate SQL `message_queue/repository.py:1312-1322`** (block within :1289-1330; `MessageQueueRepository.clear_all` log line; orchestration manager.py:1407-1600): survive = rows backed by RUNNING/PAUSED tasks + non-terminal-instance rows; taskless READY rows deleted. Hard-reboot reset PROCESSING→READY (:467 `find_stuck_messages_before` / :489 `reset_stuck_message_to_ready`). **[A]**. ROOM RULE: boot story explicit (§J; architect §10 pinned pair).

**TTL / sweep.** `cleanup_old(24h)` (:1187) has ZERO callers — no runtime sweep. **MISSING:** runtime sweep + room-history GC.

**shared_meta_kv.** DB table, JSONB, unique (context_key, meta_key); **create_all — NO migration file** (manager.py:1205-1214; do not copy). context_key = tree-root (instance/repository.py:769-786) ⇒ parent + ALL descendants share ONE namespace. Limits: batch ≤100, key ≤128, value ≤4096; LWW; no sweep/TTL/GC ever. **[R]** as room shared-state; **MISSING:** notification, history, GC.

**BoardNote — closest precedent.** daemon/models/board.py:301-345 (append-only, no update/delete, NO ACTION FK); append primitive board_service.py:594-631. **[A]**; GLOBAL namespace opposes room scoping.

**Spawn-param persistence.** metadata JSONB key pattern (lifecycle:2346-2406 "Additive JSONB key — no migration"); `join_room` zero-migration **[R pattern]**; spawn signature instance.py:3320; result-string assembly instance.py:3745 region.

**The gap statement.** The room log must be NEW (append-only, seq-ordered, N-reader); `room_messages` + per-reader cursors = the missing core.

---

## C. Roster & membership primitives

**Membership durability.** States models.py:20-35 (**10 states**). `instances.parent_id` PERMANENT (instance/repository.py:838-860); `instance_hierarchy` rows TRANSIENT → **WRONG substrate**. Revive: terminal→RUNNING flip (instance_messaging.py:2538-2572); pause/resume cascade (lifecycle:3475/:4214). Daemon restart: instance rows are DB truth. **VERDICT:** metadata-JSONB membership survives **[R]**; a `chatroom_members` table **[M]** has fresh migration precedent.

**House status constants (verified at pin).** `ALIVE_INSTANCE_STATUSES = {idle, running, paused, queued, waiting_children}` (constants.py:1066-1072). **`waiting` is NOT in that constant** — but it IS a live enum member ("Active but no in-flight work", models.py:20-35). **Status taxonomy (enum-total, no complement shortcuts):** terminal = exactly {`completed`, `error`, `terminated`, `failed`}; the OTHER SIX — `idle`, `running`, `waiting`, `paused`, `queued`, `waiting_children` — are non-terminal (alive). Any predicate over statuses must be stated over this 10-member enum (architect §4.1), never as "complement of ALIVE".

**Tree-root roster.** `get_tree_root_id` (instance/repository.py:769-786) — the room-scope anchor; join-by-tree-root = the membership-admission predicate (architect §2).

**Authorization chokepoint.** Gate = the SENDER's team_members: CR-2 instance.py:4687-4734 → `_check_team_membership` (call :4724; _auth.py:90-256; caller list :169; implied set :173-176; fail-closed default :111). ⚠ **DEFECT worth not copying:** gate SKIPPED when target row lacks agent_id — `if target_agent_id:` (**instance.py:4723**); comment claims fail-closed, code fails OPEN. Room gate must not inherit. Transport **[R]**; chokepoint **[A]**; sibling-direct room hop **[M]**.

**Subtree read tools.** `_validate_subtree_target` (instance.py:1917-1981, deny :1969) — lineage-downward; cannot express a lateral room. Rendering conventions **[R]**; predicate **[A/M]**.

---

## D. Tool injection & category minting

`create_instance_tools` (instance.py:3245) → filter :7758 → resolve :433. **Minting checklist** (memoir/guidelines precedents): register category; construction extend BEFORE filter (decorator-only = INVISIBLE); privileged grant + per-agent allow + IN-BODY gate; pin tests (14 files; count 22→23; trailing-order pin). **Conditional per-instance grant precedent:** `_is_user_facing_root` (instance.py:3211; restore threading lifecycle:4846). **Mid-life membership mutation:** [M] gap (tools bind at spawn/restore only).

---

## E. System-message assembly

**Persona SystemMessage [REJECTED SEAM]:** `compose_system_prompt` (**daemon/loader.py:972**); cached per-AGENT (**daemon/loader.py:1356**); prepended at LLM-call (graph.py:8492).

**Per-turn context — THE seam:** `assemble_context_messages` (context_messages.py:2558); `_make_context_message` (:181); **14 kinds (verified)**; memoir_refresh precedent (:134-173). `CONTEXT_KIND_JOINED_ROOM` recommended.

**Skills:** `<meta>` parser **daemon/services/skill_meta_parser.py** (`extract_load_skill` :94); ENQUEUE pipeline only (spawn auto-dispatch is SKILL-LESS).

**Prompt-artifact boundary:** VERSIONED_PROMPT_ARTIFACTS (prompt_artifacts.py:119); write/edit refusals filesystem.py:1561/:1978; context-kind injection composes in memory — zero baseline/seal impact.

**Compaction (daemon/compaction.py):** threshold :1589 (0.80); hoisting :337 + :425 + :3233; budget :1404; dropped-batch clause :614; caps :1650 (4000/2000 chars); `ContextCompactor` :2223. **NO speaker model** — N voices collapse. Pull-over-push.

---

## F. Nudge hook points

**Todo machinery.** Store RAM-ONLY (todo_manager.py:12-15). **The reminder slot is returned on EVERY update** (`{"todos": [...], "reminder": str}` — todo_manager.py:403-461; `_compute_reminder` :1198-1276; done+comment fence = firing convention, not carrier scope); SSE-only hooks (todo_tools.py:249-264); comment-reminder precedent :491/:1198. **[A]**.

**CORRECTED — injection-drain wrapper acts ONLY on terminal verdicts.** `create_injection_aware_router` (span graph.py:4955-5040; enumeration :4942-4952, `terminal_routes` frozenset :4984-4986, check :4995, cap :4933). The todo-step seam = the **every-update reminder slot**; the wrapper = the (a2)-side completion backstop, latch-shared with (a) — preserving **"so a busy room cannot block completion forever"**.

**Turn-completion hook — ONE order.** **`language_check → [room_completion_gate] → attestation_gate → END`** (region :13378-13415; disabled-language seam = between the base terminal router and the attestation wrapper). Gate body shape: `create_language_check_node` **:4758** (skip logic :4770; fail-open :4807-4812); counter-reset scan :4778-4790. **Post-graph chain (B3):** definition **manager.py:11703**; facade delegation :11717; post-graph call sites **:8368 / :11880 / :15746**; internal parent-update **services/child_reports.py:1019**. ⇒ nudge must fire IN-GRAPH. Latch: `TurnRepairLatch` :1156 — **graph-builder-local (:13159)** ⇒ cross-layer sharing needs a manager-level registry.

**MISSING:** per-room read-watermark.

---

## G. Naming machinery

instance_name surfaces only in the completion header (child_reports.py:778-790); agent_name **[R]**; agent_tag (version identity — do not overload) **[R]**; instance_name (metadata) **[A]**; controlled vocabulary/uniqueness/@-tag/provenance **[M]**. Quick lane: purpose-bound field (config.py:172-182 `model_title`; bypass :449-458) — named quick field **[M→A]**. Inline precedent: TitleGenerationService (class services/title_generation.py:**17**; region :75-130; belt-and-braces :137-146; built manager.py:2357). Provenance hook graph.py:8765-8773 **[R]** (job_queue.py:3561 anonymous = anti-pattern). No uniqueness primitive anywhere — per-room registry = roster state **[M]**; no-block precedent `ensure_mcp_preloaded` (instance.py:3620-3630).

---

## H. UI / audit surfacing

HTTP role-scoped: messages :165; injection :793; message :843; events SSE :952 → **LiveEventHub (class live_event_hub.py:15; `stream_message` :150; `_stream_to_connections` :175 — lossy at-most-once, backpressure 50)**; global notifications routers/notifications.py:29. Hub is id-keyed ⇒ room publishes under room_id. **Durable audit:** EventBus.create_event (services/event_bus.py:155 → event table repositories/event/models.py:71; **EventKind enum range :27-54 is illustrative — additive members extend beyond**). **Room-as-instance-shaped id:** reuse events route + hub + event rows **[R]** with a room-resolution pre-branch (route 404s via `manager.get_instance`, routers/messages.py:960-963); no synthetic instance rows.

---

## I. Concurrency primitives

- **Bounded per-key locks** — sources/dispatcher.py:451-452 `_send_locks`; `_get_send_lock` :810 **[A → per-room write ordering]**.
- **TokenBucket / CircuitBreaker** — sources/rate_limiter.py:25, circuit_breaker.py:20 **[R — external pacing; rooms need neither]**.
- **Dependency-bus watchers** — repositories/dependency_bus/repository.py:77 **[A — optional wake]**.
- **Job AdmissionState** — services/job_state_machine.py:53-84 **[M as room substrate — do not model rooms as jobs]**.
- **Atomic write + fcntl.flock** — tools/filesystem.py:1451 `_atomic_write_text` **[A-but-moot — per-instance file locality cannot express N-reader scoping; DB append already gives transactional seq + cursor semantics]**.
- **Fan-out templates** — work_notifier.py:1339 (cap 256) + cascade gather. No generic N-member helper.

---

## J. House conventions

- **Migration doctrine:** own `.sql` per table (130 at tip; multi-table-single-file precedented — board migration, 3 tables).
- **No create_all deviations** (shared_meta_kv manager.py:1205-1214 — do not copy).
- **Event-loop discipline:** `asyncio.to_thread`; exemplars instance_messaging.py:1179-1184, task_processor.py:256-265, message_processing_pipeline.py:744-752, routers/messages.py:705/737/885; residual leak watchover_service.py:328.
- **Boot story explicit:** F-1 wipe (manager.py:1407-1600; predicate SQL repository.py:1312-1322) + epoch reset (:467/:489).
- **Prompt-artifact discipline:** agents/*.md runtime-immutable; context-kind injection only.
- **W2 queued-polarity trap** (messaging_types.py:38-62).
- **CHECKPOINT_TTL_HOURS = 168** (constants.py:170), 24h sweep cadence.
- **Anchor-drift warning:** pinned to bf4bfde6e; re-verify at implementation time (13 drifted cites corrected across Rev 5–6).
- **Zero prior art:** chatroom|chat_room|group_chat|groupchat = 0 matches.
- **Over-build guard:** the simpler mechanism that makes the doctrine true.

---

## MISSING-CAPABILITY SUMMARY (cross-domain)

| # | Missing capability | Domains | Notes |
|---|---|---|---|
| 1 | Durable multi-recipient fan-out | A, I | Per-member loop; QA-watcher template |
| 2 | Append-only, seq-ordered, N-reader store | B | room_messages + cursor = the missing core |
| 3 | Room-scoped (lateral) read predicate | C | subtree predicate is lineage-only |
| 4 | Per-room read-watermark / cursor | B, F | minted per participant, **owner included** |
| 5 | Change notification on shared state | B | shared_meta_kv is passive |
| 6 | Runtime sweep / GC for message history | B | cleanup_old zero callers |
| 7 | Mid-life tool re-grant (late join/leave) | D | tools bind at spawn/restore only |
| 8 | Speaker attribution through compaction | E | no speaker model |
| 9 | @-tag resolution + name-uniqueness registry | G | greenfield; per-room scope |
| 10 | General "message all members" helper | A, I | daemon→N = N sequential sends |
| 11 | Membership-admission predicate for a lateral shared scope | C | tree-root equality check is new (architect §2) |
| 12 | Owner/creator cursor substrate | C | seeded as an owner membership row at room_create (architect D2) |
