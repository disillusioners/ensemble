# Architecture Decisions: Long Tool Call Nudge

**Date:** 2026-09-13
**Author:** plan-creation worker (synthesis) — consolidates the open-decision sections of `phase1-plan.md` (D1–D12), `phase2-plan.md` (D2-1…D2-16), and `phase3-plan.md` (D1–D10) into one AD-N register, plus the cross-phase reconciliations R1–R6 from the synthesis dispatch.
**Branch:** `plan/long-tool-call-nudge` @ `b6329a89` (phase files) → synthesis commit (this file + `plan-overview.md` + reconciliation edits)
**Status convention:** each AD records the recommendation the phase plans made-by-default. The architect reviews and enriches; disputes land here first, and the losing phase file gets a follow-up edit. Entries marked **OPEN** are explicitly NOT decided — they are carried for the implementer/architect.

---

## Decision Log

### Detection

### AD-1: Detection shape = (a) node-level wrapper at `add_node("tools", ...)` + (c) periodic scanner over a RAM stamp registry — NOT (b) a post-hoc node

**Decision**: Stamp per-tool duration at the single shared `"tools"` node seam via a wrapper factory (`_wrapped_tools_node`) that delegates to the bare `ToolNode`, and detect long calls with a lifespan-wired scanner loop (`LongToolNudgeScanner`, 60 s tick) reading the registry. *(Resolves from: phase 1 Tasks 3/4/7/8; phase 1 D1; phase 2 Coupling.)*

**Rationale**: Both graph wiring variants (`agent → watchover_check → tools` and the manager-less `agent → tools` fallback) converge on the ONE `add_node("tools", ToolNode(...))` site (`daemon/graph.py:7879`), so a single wrapper covers everything with a one-line swap. A post-hoc node (alternative b) would only see batch boundaries AFTER the fact — it cannot observe in-flight elapsed time, which is the entire detection primitive. The scanner over a RAM registry then adds zero hot-path cost beyond two dict ops per tool call.

**Consequence**: The wrapper MUST clear stamps in a `finally` block (pause-cancel via `graph_task.cancel()` and the 7200 s task-cap `TimeoutError` both raise through the node) so no entry leaks as eternal in-flight; `handle_tool_errors=True` semantics (errors return `ToolMessage`, stamps clear normally) are preserved by delegating, not reimplementing, the `ToolNode`.

**Alternatives**: (b) post-hoc/reports-side node that reconstructs durations from message timestamps after the turn — rejected: too late to nudge, and the bd4b36ef burn happened *during* the turn. Per-tool wrapping of each individual tool callable — rejected: must re-derive `Command` handling, error formatting, and stream-writer semantics; high blast radius for the same data.

---

### AD-2: Episode identity is TWO-LEVEL — stamp-level `(child_id, tool_call_id)` for firing, nudge-level `(parent_id, child_id)` for dedup

**Decision**: The scanner's *fire* gate dedups on `(child_id, tool_call_id)` (per-process set `_fired_episodes`, cleared when the stamp clears); the *nudge* gate dedups on `(parent_id, child_id)` (`_active_episodes`, closed on `tool_end`, re-armed by a NEW `tool_call_id` crossing). The episode CONTEXT carries `tool_call_id` throughout (`EpisodeCtx`). *(Resolves from: phase 1 D3 + phase 2 D2-3 — reconciled R4; both phase files now state the same keying.)*

**Rationale**: These are different questions. "Has THIS stamp already fired?" needs the `tool_call_id` — two ticks on the same in-flight stamp are one event (bd4b36ef's five bash signatures were five DIFFERENT tool calls on five DIFFERENT AI messages, each its own stamp). "Has the parent already been told about THIS child's episode?" needs `(parent_id, child_id)` — one nudge per episode, full stop, with `tool_end` as the close signal (the same call completing, erroring, or the child moving on). A time-based cooldown was rejected (phase 2 D2-3 alt a): it would suppress a legitimate new long call starting 30 min later.

**Consequence**: `(child_id, tool_call_id, tool_name)`-keyed state lives in the scanner; `(parent_id, child_id)`-keyed state ALSO lives in the scanner but gates a different action. Both reset on daemon restart (AD-14). `tool_name` is advisory metadata, never part of a key. `args_hash` was rejected (phase 1 D3 alt B): args can be sensitive and `tool_call_id` already disambiguates.

---

### AD-3: Per-batch overestimation semantics (v1 accepted)

**Decision**: All `tool_call_id`s in one AI message's batch get the SAME `started_at` at wrapper entry (LangGraph `ToolNode._arun_batch` dispatches via `asyncio.gather`, so per-call start/end is unavailable); each clears independently at batch return. A fast call co-batched with a slow one observes `duration ≈ batch wall-clock`. **Ship v1 as overestimation**; pin the semantics in the module docstring + tests. *(Resolves from: phase 1 D1.)*

**Rationale**: False positives are recoverable — the nudge is advisory and the parent decides (v1 non-goal: no auto-kill). False negatives (missing a bd4b36ef) are not recoverable. Overestimation is the conservative direction.

**Consequence**: Docstring + `TestWrappedToolsNodeStampsAndClears` pin it. Per-call attribution is the named future refinement (AD-33).

**Alternatives**: Reimplement per-call dispatch inside the wrapper (manual `asyncio.gather` of per-id coroutines) — rejected for v1: risk surface is large (`Command` handling, error formatting, `_combine_tool_outputs` shape that `create_post_tools_router` depends on). Single per-batch stamp — rejected: collapses attribution entirely.

---

### AD-4: Fire boundary is strict `>` (elapsed strictly greater than threshold)

**Decision**: The scanner fires when `now - started_at > threshold`; at exactly `== threshold` it does NOT fire. *(Resolves from: phase 1 D10, pinned by T3.)*

**Rationale**: Matches the watchdog's `age > threshold` SQL precedent ("child at threshold-1 s is NOT hung, child at threshold+epsilon IS hung"). Off-by-one consistency aids forensics.

**Alternatives**: `>=` — rejected (diverges from house precedent, no operational demand); configurable — rejected (a knob nobody asked for).

---

### AD-5: `parent_id` read at scan time, NOT stamp time

**Decision**: Stamps carry only `(tool_call_id, tool_name, started_at)`; the scanner reads `instance.parent_id` from the repo lazily when a stamp is about to fire. *(Resolves from: phase 1 D2.)*

**Rationale**: `parent_id` is permanent on the child row and stable for the stamp's lifetime; stamping it would add a DB read to the graph-node hot path (every tool entry) to save one read per threshold-crossing. Scan-time reads also mean a stamp survives parent-reparenting edge cases without stale denormalized data.

---

### AD-6: Registry overflow cap = 1024 tracked instances, drop-oldest + WARN

**Decision**: `_LONG_TOOL_REGISTRY` caps at 1024 instances with in-flight stamps; on overflow it drops the oldest instance's stamps and logs WARN. *(Resolves from: phase 1 D9.)*

**Rationale**: Order-of-magnitude match with the watchdog's active-instance assumptions; drop-oldest is safe because an instance with no in-flight calls has nothing to drop, and the detector only cares about CURRENT in-flight stamps.

**Alternatives**: Unlimited — rejected (bd4b36ef-class stuck instances could grow the dict unboundedly on a long-lived daemon); 4096 — accepted as a follow-up tunable if production drops too aggressively.

---

### Delivery + Dedup

### AD-7: Hand-off seam ships as a phase-1 stub (`handoff_stub_enabled=True`), phase 2 replaces the body

**Decision**: Phase 1 implements `deliver_long_tool_nudge(parent_id, child_id, episode_ctx) -> None/bool` as an INFO-log stub (`[LongToolNudge] STUB_FIRE`) so the detector is end-to-end observable without delivery; phase 2 flips the flag and fills the real `enqueue_message` body. The `EpisodeCtx` field set is pinned by `TestDeliverLongToolNudgeStub` and must not shrink between phases (additive fields allowed). *(Resolves from: phase 1 D5; phase 2 Coupling.)*

**Rationale**: The seam IS the architectural contract — without it, phase 2 would choose between an inline call site (implicit contract) and a parallel delivery path (forbidden). Stub-then-replace decouples phase 1 from the delivery path's final shape.

**Note**: The seam returns `bool` in the phase-2 contract (True = enqueued this call; False = suppressed/deduped). Phase 1's stub may return `None`; the phase-2 flip includes the return-type tightening. This is an additive contract change permitted by the pinned-field rule.

---

### AD-8: Notice text has ONE canonical home (`_build_long_tool_notice`) with a locked 5-section structure and a `# FUTURE` extensibility seam for Feature #1

**Decision**: `_build_long_tool_notice()` in `daemon/services/long_tool_nudge.py` is the only writer of the nudge body (ATTESTATION_NUDGE_TEXT single-source discipline). Structure locked: (a) header with child/tool/call-id/elapsed/threshold; (b) why-it-matters (busy-slow weak-model signature, loop-breaker evasion); (c) exactly three recommendations using EXISTING parent tools — inspect via `subtree_messages`/`get_instance_info`; `send_message` the child with the explicit "lands at next turn boundary — mid-tool child CANNOT receive" warning (the bd4b36ef 19-min lesson); `terminate_instance` + re-spawn past 2× threshold — and explicitly NO pause/resume advice (agents have no pause tools); (d) the `# FUTURE` comment block: "re-spawn-with-higher-intelligence-model" template for the companion Feature #1, greppable, NOT implemented today; (e) footer "advisory only — Episode id". *(Resolves from: phase 2 Task 4, D2-11, D2-13; caller-mandated decision (5).)*

**Rationale**: The seam must exist so future work drops in WITHOUT restructuring the body; making the body operator-tunable would break the test-pinned structure. U11 asserts all five sections, the absence of pause/resume as instruction verbs, and the `# FUTURE` marker.

**Alternatives**: Put the body in `graph.py` next to `ATTESTATION_NUDGE_TEXT` — rejected (graph.py is in-turn nudges; this is scanner-delivered); a `FUTURE_NUDGE_INSERTIONS` registry of callables — rejected as over-engineered for a one-line future addition.

---

### AD-9: Episode dedup closes on `tool_end`, re-arms on a NEW `tool_call_id`; no escalation ladder in v1

**Decision**: One nudge per `(parent_id, child_id)` episode. `close_episode(parent_id, child_id)` discards the dedup tuple (and the future-escalation counter) when phase 1's wrapper records the `tool_end` clear for the long call. The next crossing on a NEW `tool_call_id` is a fresh episode and re-nudges. Escalation (second/third nudge, operator page) is out of scope v1 — `_nudge_counts` is kept as a future hook but never gates firing. *(Resolves from: phase 2 D2-3, Task 5, U3.)*

**Rationale**: The long-tool path has a reliable explicit close signal (`tool_end` from the wrapper's `finally`) — unlike the watchdog, which must re-derive from SQL because children can die silently. Closing on `tool_end` avoids both the false-negative of time-cooldowns and nudge spam on consecutive ticks.

**Limitation (accepted)**: if phase 1 ever misses a clear, the episode stays open and a genuinely-new long call is suppressed. Mitigation is the `finally`-block contract + `TestWrappedToolsNodeExceptionClearsStamps`/`CancelClearsStamps`.

---

### AD-10: Nudge delivery = A5 double-notify pattern (`enqueue_message` + direct `worker_pool.notify_work()`)

**Decision**: Delivery mirrors the watchdog's wedge-notify (`waiting_children_watchdog.py:1572-1619`): `await manager.enqueue_message(instance_id=parent_id, message=notice, source=LONG_TOOL_NUDGE_SOURCE, priority=0, metadata={...})`, then a best-effort direct `worker_pool.notify_work()` via `getattr(self._manager, "_worker_pool", None)`, with the `inspect.iscoroutine` shim (sync in prod, coroutine in some fixtures) and full exception-swallow (`[LongToolNudge] direct notify_work raised ... — relying on enqueue_message's internal notify`). *(Resolves from: phase 2 Task 3(e), U8–U10.)*

**Rationale**: The enqueue-commit/pool-notify race that stranded incident 33252 applies identically here. `enqueue_message` already calls `notify_work()` internally at `:1987`; the direct second notify is defense-in-depth for the lost-wake class.

**Consequence**: No new kwarg on `enqueue_message` — system nudges are foreground, `is_deferred`/`is_background`/`work_id`/`work_id_required` are NOT passed (phase 2 Task 3 CRITICAL note).

---

### AD-11: `priority=0` (system lane) — a nudge NEVER resets leader-attestation counters

**Decision**: The nudge enqueues with `priority=0`. The terminal-revive attestation-counter-reset branch requires `priority==1 AND msg_type==HUMAN` (`instance_messaging.py:1896-1902`), so a nudge that revives a WAITING_CHILDREN parent leaves `attestation_denied_count` / completion-gate state untouched. Pinned by U7 (unit) + I5 (integration). *(Resolves from: phase 2 D2-6.)*

**Rationale**: A system advisory must not corrupt an in-progress attestation episode as a side effect of waking its parent.

**Alternatives**: priority=1 — WRONG per the documented contract; priority=0 + opt-in-reset metadata flag or skip-if-attesting — complexity for zero v1 benefit.

---

### AD-12: PAUSED parent = pre-check, single WARN, skip (no stat counter, no deferred delivery)

**Decision**: `deliver_long_tool_nudge` reads `parent.status` FIRST; on PAUSED it logs one WARN and returns False — never raises into the scanner tick, and deliberately does NOT let the claim gate defer a stale notice to resume. *(Resolves from: phase 2 Task 3(a), D2-12, U5, I4; mirrors watchdog `:996-1009`.)*

**Rationale**: The claim gate would defer the Task to resume anyway, but the notice's elapsed/threshold numbers would be stale by then. Skip is the cleaner contract. A dedicated metric hookup is out of scope v1; the WARN + `[LongToolNudge] tick stats` INFO line suffice for debugging.

---

### AD-13: No Facade-Forwarding Discipline work — no new kwarg on `enqueue_message`

**Decision**: Use only existing facade kwargs (`instance_id, message, source, priority, images, metadata` — verified forwarded at `manager.py:6777-6873`). Observability lives in `metadata` (`long_tool_nudge: True`, plus tool/tool_call_id/elapsed/threshold keys). The facade-forwarding grep is documented in the plan; no facade change, no forwarding test required. *(Resolves from: phase 2 D2-7.)*

---

### Daemon-Restart Semantics

### AD-14: RAM-only stamps + episodes + fire-dedup; durable delivery via the `enqueue_message` txn

**Decision**: All three volatile state shapes are RAM-only and reset on daemon restart: the stamp registry (singletons die with the process; scanner rebuilds from the next `tool_start`), `_active_episodes` (worst case ≤1 duplicate nudge if a child is still mid-tool across the restart — accepted, benign), `_fired_episodes` (phase-1-era per-process log-volume control). The NUDGE ITSELF is durable the instant `enqueue_message` returns (MessageQueue + Task rows in one txn, `instance_messaging.py:1981-1991`). Documented in the module docstring ("Restart semantics" subsection); pinned by I3. *(Resolves from: phase 2 D2-4, D2-5, Task 9, and the durability table; phase 1 D4; caller-mandated decision (3).)*

**Rationale**: In-flight calls are cancelled by a restart anyway — a persisted stamp would outlive the tool it tracks and require its own stale-stamp sweeper. A SQL-persisted episode set adds a write per nudge + a read per tick to prevent ONE benign duplicate per restart. RAM is honest about what is knowable post-restart.

**Alternatives**: SQL episode table or `instance_metadata` `last_nudge_at` timestamp — rejected (write amplification, TTL question, tick-read cost) for a marginal benefit.

---

### AD-15: No `report_injections`-style obligation table for nudges

**Decision**: The MessageQueue row IS the durable record; the nudge rides the ordinary system-source message lifecycle (claim → deliver → row settles). No obligation/backstop table, no cleanup sweep. *(Resolves from: phase 2 D2-14.)*

**Rationale**: The watchdog's wedge-notice path — the closest sibling — has no obligation table either. Adding one is a DB write per nudge for a failure mode the ordinary queue lifecycle already covers.

---

### Config

### AD-16: Nested `LongToolCallNudgeConfig(BaseSettings)` with `env_prefix="LONG_TOOL_NUDGE_"`, wired once on `EnsembleConfig`

**Decision**: A nested pydantic-settings class (`enabled`, `interval_seconds`, `default_threshold_seconds`) placed between `ReportIntegrityConfig` and `LanguageConfig`, wired via `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=LongToolCallNudgeConfig)` immediately after the LoopBreaker wiring line. *(Resolves from: phase 2 D2-1, Task 7; caller-mandated decision (4).)*

**Rationale**: Three independent infra-loop knobs grouped under one import path beat cluttering the 3000-line flat `ServicesConfig`; `LoopBreakerConfig` (`config.py:1737-1756`, wired `:2109`) is the closer precedent and the documented modern style.

**Alternatives**: flat `SERVICES_*` fields (the older watchdog style) — rejected; no config block — rejected (the `enabled` toggle is a v1 hard requirement); per-instance only — rejected (chicken-and-egg for the first long call).

---

### AD-17: Env names `LONG_TOOL_NUDGE_ENABLED` / `LONG_TOOL_NUDGE_INTERVAL_SECONDS` / `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS`

**Decision**: The three env vars derive mechanically from the nested class's `env_prefix` + field names. Phase 1 consumes the dotted attribute path (`config.long_tool_nudge.*`) and fails LOUD ("long_tool_nudge config missing — phase 3 required") if the attribute is absent — never silently disables. Out-of-range env values fail fast at boot via `ge=`/`le=` validators. *(Resolves from: phase 1 D6 + phase 2 D2-1/Task 7 (U14–U16); caller-mandated decision (4).)*

---

### AD-18: Defaults — interval 60 s, threshold 900 s, hard max 1800 s

**Decision**: `interval_seconds=60` (nudge latency ≤ threshold + 60 s; watchdog's 3600 s cadence exists because ITS threshold is 3600 s); `default_threshold_seconds=900` (catches the first bd4b36ef bash at ~15 min; operator tightens per-child via phase 3's tool); hard max `1800` (a parent cannot act usefully on a 4-hour tool — re-spawn is the answer by then). *(Resolves from: phase 1 D8 + phase 2 D2-8 (interval); phase 2 D2-9 (threshold); phase 2 D2-2 + phase 3 D7 (hard max). Deduplicated into one entry per the synthesis dispatch.)*

---

### AD-19: Hard max is a safety invariant, NOT operator-tunable — three defense layers

**Decision**: `1800` is enforced three ways, none env-tunable: the module constant, the `Field(le=HARD_MAX_THRESHOLD_SECONDS)` boot validator on `default_threshold_seconds`, and the runtime `min(metadata_or_default, HARD_MAX)` clamp in threshold resolution (second line of defense for values that bypass pydantic, e.g. a hand-edited metadata key). *(Resolves from: phase 2 D2-2; interacts with AD-30 (canonical home) and AD-24 (tool-side loud reject).)*

---

### AD-20: Lifespan wiring mirrors the watchdog block exactly

**Decision**: In `daemon/api.py`, after the waiting-children-watchdog block (`:715-759`): lazy import, ctor inside `try/except` that logs ERROR and sets `app.state.long_tool_nudge_task = None` on failure (app boots anyway), `asyncio.create_task(run_long_tool_nudge_loop(...), name="long-tool-nudge")` only when enabled, INFO log on start ("Long-tool-nudge scanner started: interval=…s, default_threshold=…s") and on disable ("disabled by config"). The loop skeleton mirrors `run_waiting_children_watchdog_loop` (`:1668-1732`): disabled → immediate return; `CancelledError` propagates; generic exceptions log ERROR and continue; sleep swallows its own cancel. Reuse the existing watchdog `SQLModelInstanceRepository` instance rather than constructing a second repo. The block sits AFTER `setup_worker_pool()` so `manager._worker_pool` is wired (a missing pool is INFO-logged and skipped, never fatal). *(Resolves from: phase 1 Task 7 + phase 2 Tasks 6/8, D2-15, D2-16 — the two plans describe the same block from their sides; the phase-2 wording is the operative one and phase-1-only tests tolerate the config attribute being absent per AD-17.)*

---

### Storage + Tool

### AD-21: `set_instance_tunable` — generic allowlist tool, not a single-purpose tool

**Decision**: One tool, `set_instance_tunable(instance_id, key, value)`, with `ALLOWED_TUNABLES: frozenset = {"long_tool_call_threshold_seconds"}`. Future tunables add an allowlist entry + validator, not a new `@tool`. Registered inside `create_instance_tools` (factory closure) and added to the `_tool_registry.py` alphabetic name list between `"send_message"` and `"shared_meta_kv"`. *(Resolves from: phase 3 D4, Tasks 1-4.)*

**Rationale**: Tool-count scales poorly; the allowlist pattern matches the watchover precedent of stacking runtime keys in `instance_metadata`.

**Alternatives**: single-purpose `set_long_tool_call_threshold` — simpler now, scales linearly; per-key validator-registry dispatch — over-engineered v1.

---

### AD-22: Reject-vs-clamp — the tool LOUDLY RAISES on floor/ceiling violations; error-dict only for unknown keys; write ONLY via `set_metadata`

**Decision**: `value` outside `[60, 1800]` → loud `raise ValueError` listing the valid range (mirrors `spawn_councilor` strict model-validation — the "no silent fallback" house style for hard constraints); a parent typing `18000` WANTED 18000 and must re-read, not receive a silent 1800. Wrong `key` → recoverable error-dict `{"error_code": "UNKNOWN_KEY"}` (mirrors `project_set_metadata` — the LLM self-corrects from the message). Non-int / bool (explicit bool trap) → ValueError. Writes go exclusively through `instance_repository.set_metadata` (`:2099`) — NEVER `update_instance`, which rejects `instance_metadata` with ValueError at `:1094-1121` (test-pinned). Return shape: `{instance_id, key, prior_value, effective_value = min(value, 1800), applied_at}`; `set_metadata` returning None → `{"error_code": "NOT_FOUND"}`. *(Resolves from: phase 3 D2, D3, Task 2; caller-mandated decision (2).)*

**Alternatives**: clamp + echo `{"warning": "clamped"}` — friendlier but hides mistakes; two-stage soft-warn/hard-reject — over-engineered.

---

### AD-23: Tool category = existing `"instance"`; exposure = leader + planner + developer (pending AD-35 verification)

**Decision**: No new category. Leader already allows `"instance"` (zero meta.json change); planner + developer each gain ONE line in `tools.allow`. Docs land in a NEW `docs/long-tool-nudge.md` (deep-linkable from `context_injection` heuristics + `tool_help`), with an optional one-line cross-ref only if a top-level operations doc exists. *(Resolves from: phase 3 D5, D8, D9, Tasks 5-6, 8-9.)*

**Rationale**: A new `"instance_tuning"` category forces a leader change for zero net win. A dedicated doc crosses operations + agent-behaviour + system-policy boundaries and is easier to link than a section in a sprawling operations page.

**Caveat**: exposure scope may narrow to leader-only if AD-35 verifies planner/developer cannot spawn children (a tuning tool is useless to an agent that holds no children) — the verification, not this AD, settles it.

---

### AD-24: No parent-ownership check on the tool (house stance)

**Decision**: Any agent with the `"instance"` category may tune any instance's threshold. No ancestor walk. Documented in the tool docstring + docs "Caveats". *(Resolves from: phase 3 D6.)*

**Rationale**: `send_message` deliberately has no parent-ownership check; scoping discipline (Cardinal #2) is the mitigation. Blast radius is one advisory-threshold metadata key — low. An ancestor walk adds a DB round-trip and new failure modes (revive-restored children; permanent-vs-hierarchy lineage source).

**Alternatives**: ancestor walk via the `:668-678` walker — defence-in-depth at runtime cost; optional `caller_instance_id` self-verify param — viable future addition.

---

### AD-25: No spawn-time threshold kwarg chain in v1

**Decision**: Runtime-set only (tool + `set_metadata`), following the watchover tunables precedent (`watchover_enabled/context/requirement` live in `instance_metadata`, set post-spawn; `instance_lifecycle.py:3905-3939` re-resolves `model_override` from metadata on revive — same substrate). The 4-site `model_override`-style spawn kwarg chain (~80 LOC: tool validation → spawn kwarg → lifecycle persist → restore revalidate) is a v2 candidate. *(Resolves from: phase 3 D1.)*

**Rationale**: Smallest surface that works on EXISTING children without touching spawn contracts; the metadata key is already read by the scanner regardless of when it was written.

---

### AD-26: Threshold unit = seconds everywhere

**Decision**: The metadata key (`_seconds`), the env var (`_SECONDS`), the tool arg, and the scanner comparison are all integer seconds. Floor 60 prevents accidental micro-thresholds that would defeat the feature. *(Resolves from: phase 3 D10.)*

**Alternatives**: minutes (+ unit arg) — conversion hazards at the 60-second boundary; milliseconds — absurd precision for a 60-1800 s range.

---

### AD-27: Duration observability = structured log line per completion; no DB write in v1

**Decision**: Every tool completion emits ONE INFO line, marker `[LongToolNudge] TOOL_COMPLETED`, fields `instance_id` (8-char truncation per watchdog convention), `tool_call_id`, `tool_name`, `duration_ms`, `threshold_seconds`, `threshold_crossed` — logged REGARDLESS of crossing (bd4b36ef forensics needed the five prior durations that never existed). No `tool_duration_log` table; re-evaluate in phase 3 only if log volume measured in production demands it. *(Resolves from: phase 1 D7, Task 6, T7.)*

**Rationale**: "Per-tool duration never recorded" is answered by a durable log line at the level the forensic driver requires; a per-completion DB insert compounds write volume for an observability signal logs already provide; an aggregated per-instance row loses the per-call attribution the forensics need.

---

### Cross-Phase Reconciliations (R1–R6)

### AD-28 (R1): ONE canonical stamp store — the module-level `_LONG_TOOL_REGISTRY` singleton; the scanner READS it and owns no parallel store

**Decision**: Phase 1's `LongToolNudgeRegistry` module-level singleton (`_LONG_TOOL_REGISTRY`) is the ONLY stamp store. The wrapper writes via `record_start`/`clear` (in `finally`); the scanner reads via `await registry.snapshot()`. Phase 2's scanner ctor does NOT declare `_in_flight: dict` and does NOT define `start_stamp`/`clear_stamp` — those duplicated the wrapper's write path and would have created two stores for the same data (drift). The scanner keeps ONLY its own nudge-level state: `_fired_episodes`, `_active_episodes`, `_nudge_counts`, and `close_episode`. Per-graph registry allocation remains FORBIDDEN (pinned by phase 1 T8: `id(registry) == id(_LONG_TOOL_REGISTRY)`). *(Reconciles: phase 1 Task 2/8 vs phase 2 Task 2. Phase 2's task table edited to match.)*

**Rationale**: Two stores for stamp state guarantees drift — which one does `close_episode` observe? The singleton is the phase-1 architecture (lifespan-wired scanner + graph-wired wrapper MUST share one instance); the phase-2 wording predates the final phase-1 wiring shape.

**Alternatives**: scanner-owns-stamps (phase 2's shape) — rejected: it inverts the write path (the wrapper would call INTO the scanner, coupling graph.py to delivery-phase state) and breaks the T8 singleton pin.

---

### AD-29 (R2): Component name = `LongToolNudgeScanner` (phase 1's `LongToolNudgeDetector` renamed)

**Decision**: The scanning class is `LongToolNudgeScanner` everywhere (module exports, api.py lifespan import, loop function name `run_long_tool_nudge_loop`, test class names). Phase 1's working name `LongToolNudgeDetector` is renamed in place. *(Reconciles: phase 1 Tasks 4/7 + T3/T5 vs phase 2 throughout. Phase 1 edited.)*

**Rationale**: "Scanner" describes the actual behaviour (periodic scan over a snapshot; the loop IS the component); "Detector" implied a one-shot probe. Phase 2's name also already appears in the loop-function and lifespan naming both plans share, so renaming phase 1 is the smaller diff.

---

### AD-30 (R3): `HARD_MAX_THRESHOLD_SECONDS = 1800` lives in `daemon/services/long_tool_nudge.py`; BOTH `daemon/config.py` and `daemon/tools/instance.py` import it from there

**Decision**: One canonical home: the module constant in `daemon/services/long_tool_nudge.py` (phase 2 Task 1 stands). `daemon/config.py` imports it for the `le=` validator (import direction config→services is safe and already the house shape — config.py imports services constants elsewhere; the reverse, services importing config at module top, is what must be avoided for circularity). Phase 3's tool imports `HARD_MAX_THRESHOLD_SECONDS` from `daemon.services.long_tool_nudge` — NOT from `daemon.config`, and NOT under the name `MAX_THRESHOLD_SECONDS`. Phase 3's Task 5 wording ("Import `MAX_THRESHOLD_SECONDS` from `daemon.config`") and D7 are corrected accordingly; `TestFloorAndCeilingConstants` pins by importing the same OBJECT (identity, not just equality) from `daemon.services.long_tool_nudge`. *(Reconciles: phase 2 Task 1/7 vs phase 3 Task 5/D7. Phase 3 edited.)*

**Rationale**: Phase 2's plan put the constant in the services module and made config a consumer; phase 3 D7 assumed a config-side export that phase 2 never declared. Import-direction sanity: `daemon/config.py` is imported by nearly everything, so config must not reach INTO a deeper services module that itself imports config — here the services module does NOT import config at module top, so config→services is acyclic. A `daemon.constants` re-export adds indirection for one symbol.

---

### AD-31 (R4): Episode keying stated identically in both files — stamp-level `(child_id, tool_call_id)`, nudge-level `(parent_id, child_id)`; `tool_end` closes the episode, a NEW `tool_call_id` re-arms

**Decision**: Phase 1's "episode key" section and phase 2's dedup description now state the SAME two-level contract (full semantics in AD-2): phase 1's `(child_id, tool_call_id)` is the scanner's FIRE dedup (per-process, prevents consecutive-tick re-fires of one stamp); phase 2's `(parent_id, child_id)` is the NUDGE dedup (one nudge per episode; `close_episode` on the wrapper's `tool_end` clear; fresh `tool_call_id` = fresh episode, tested by U3). The two sets coexist BY DESIGN in the one scanner class; neither subsumes the other. *(Reconciles: phase 1 Coupling "Episode key" paragraph + D3 vs phase 2 Task 2/5 + D2-3. Both files edited for explicitness.)*

**Rationale**: Read naively, `(child_id, tool_call_id)` vs `(parent_id, child_id)` looked like a contradiction; in fact they gate different actions. But "can coexist only if stated precisely" — so both files now carry the same sentence.

---

### AD-32 (R6a — OPEN): Does `_tool_registry.py` cache resolved tool lists per-spawn?

**Decision**: NOT resolved here. Phase 3 Task 7 must read `daemon/tools/_tool_registry.py` for cache behaviour before relying on the meta.json exposure: if resolution is per-spawn, document that `tools.allow` changes affect only newly-spawned instances (live instances need re-spawn); if per-call, no mitigation needed. *(Carried from phase 3 Risk 4.)*

---

### AD-33 (caller-mandated, OPEN): Per-call (not per-batch) attribution — future refinement

**Decision**: NOT resolved for v1. True per-call duration requires reimplementing dispatch inside the wrapper (manual per-id coroutines) — rejected for v1 (AD-3 alternatives) and captured as the named improvement target. Co-batched fast calls may read slow; parents see advisory nudges and decide. *(Resolves from: phase 1 D12; caller-mandated decision (7).)*

---

### AD-34 (caller-mandated, OPEN): Bounded per-call `bash` timeout

**Decision**: NOT decided — explicitly out of scope v1 (non-goal: no per-tool hard-timeout enforcement; no 7200 s cap change). The bd4b36ef last bash ran ~30 min and never returned; a bounded per-call timeout (e.g. default `timeout_seconds` per call rather than per task-cap) would have cancelled cleanly and let the loop-breaker/parent path observe the tool result. Recorded as a future-feature candidate with the forensics attached. *(Resolves from: phase 1 D11; caller-mandated decision (6).)*

---

### AD-35 (R6b — OPEN): Can planner/developer spawn children at all?

**Decision**: NOT resolved here. Phase 3 D9's own text flags it: planner/developer `tools.allow` lists no `"instance"` category today, so their child-holding capability is unverified. If they cannot spawn children, exposure narrows to LEADER ONLY (the documented fallback) and the two meta.json one-liners are dropped. Verify during phase 3 Task 7 (registry category-resolution against each agent's effective allow-set). *(Carried from phase 3 D9 / Risk on exposure.)*

---

### AD-36 (R6c — OPEN): `get_metadata_value` signature confirmation at `repository.py:1931`

**Decision**: NOT resolved here. Phase 3 Task 2 re-reads `:1931` at implementation time; if the signature differs (e.g. it is `get_metadata` returning the whole dict), fall back to `instance_repository.get(instance_id)` + `instance.instance_metadata.get(key)` (one extra read, same result). Phase 1's threshold resolution carries the same check. *(Carried from phase 3 Risk 5.)*

---

## Source Index (phase-plan → AD mapping)

| Phase file | Original IDs | Land in |
|---|---|---|
| phase1-plan.md | D1 | AD-1, AD-3 |
| phase1-plan.md | D2 | AD-5 |
| phase1-plan.md | D3 | AD-2, AD-31 |
| phase1-plan.md | D4 | AD-14 |
| phase1-plan.md | D5 | AD-7 |
| phase1-plan.md | D6 | AD-16, AD-17, AD-20 |
| phase1-plan.md | D7 | AD-27 |
| phase1-plan.md | D8 | AD-18 |
| phase1-plan.md | D9 | AD-6 |
| phase1-plan.md | D10 | AD-4 |
| phase1-plan.md | D11 | AD-34 (OPEN) |
| phase1-plan.md | D12 | AD-33 (OPEN) |
| phase2-plan.md | D2-1 | AD-16, AD-17 |
| phase2-plan.md | D2-2 | AD-19, AD-30 |
| phase2-plan.md | D2-3 | AD-2, AD-9, AD-31 |
| phase2-plan.md | D2-4 | AD-14 |
| phase2-plan.md | D2-5 | AD-14 |
| phase2-plan.md | D2-6 | AD-11 |
| phase2-plan.md | D2-7 | AD-13 |
| phase2-plan.md | D2-8 | AD-18 |
| phase2-plan.md | D2-9 | AD-18 |
| phase2-plan.md | D2-10 | AD-20 (ctor mirrors watchdog; note: stamp fields removed per AD-28) |
| phase2-plan.md | D2-11 | AD-8 |
| phase2-plan.md | D2-12 | AD-12 |
| phase2-plan.md | D2-13 | AD-8 |
| phase2-plan.md | D2-14 | AD-15 |
| phase2-plan.md | D2-15 | AD-20 |
| phase2-plan.md | D2-16 | AD-20 |
| phase3-plan.md | D1 | AD-25 |
| phase3-plan.md | D2 | AD-22 |
| phase3-plan.md | D3 | AD-22 |
| phase3-plan.md | D4 | AD-21 |
| phase3-plan.md | D5 | AD-23 |
| phase3-plan.md | D6 | AD-24 |
| phase3-plan.md | D7 | AD-30 (corrected import home) |
| phase3-plan.md | D8 | AD-23 |
| phase3-plan.md | D9 | AD-23, AD-35 (OPEN) |
| phase3-plan.md | D10 | AD-26 |
| synthesis dispatch | R1 | AD-28 |
| synthesis dispatch | R2 | AD-29 |
| synthesis dispatch | R3 | AD-30 |
| synthesis dispatch | R4 | AD-2, AD-31 |
| synthesis dispatch | R5 | Working-Names Table in `plan-overview.md` |
| synthesis dispatch | R6 | AD-32, AD-35, AD-36 (all OPEN) |
