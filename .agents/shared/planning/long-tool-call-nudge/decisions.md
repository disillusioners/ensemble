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

**Decision**: One nudge per `(parent_id, child_id)` wedge **episode** (D-A episode granularity). Consecutive tool calls WITHOUT an intervening healthy (<threshold) tool completion on the same child = ONE episode (the bd4b36ef replay of 5 sequential long calls asserts exactly 1 nudge). Re-arm deterministically on (a) a healthy tool completion (close-on-tool-end, the primary re-arm), OR (b) the AD-37 stamp-TTL belt (`STALE_STAMP_TTL_SECONDS=7200`) — the SIMPLER of the two belts on top of the close mechanism. `close_episode(parent_id, child_id)` discards the dedup tuple (and the future-escalation counter) when phase 1's wrapper records the `tool_end` clear for the long call. The next crossing on a NEW `tool_call_id` is a fresh episode and re-nudges. Escalation (second/third nudge, operator page) is out of scope v1 — `_nudge_counts` is kept as a future hook but never gates firing. The close mechanism (scanner-side snapshot-diff close OR wrapper-side close with scan-time-cached parent_id) is pinned in **AD-42** (B4) — pick ONE in the implementer's first pass and amend the chosen mechanism here. *(Resolves from: phase 2 D2-3, Task 5, U3.)*

**Rationale**: The long-tool path has a reliable explicit close signal (`tool_end` from the wrapper's `finally`) — unlike the watchdog, which must re-derive from SQL because children can die silently. Closing on `tool_end` avoids both the false-negative of time-cooldowns and nudge spam on consecutive ticks. The belt is the cheap insurance against the rare case where a stamp never clears (wrapper regression, registry bug, kill-path race) — it forces a clear + close + re-arm at `4 × HARD_MAX`, a TTL no legitimate in-flight stamp can exceed (the process's task supervision cancels it first).

**Limitation (accepted)**: if phase 1 ever misses a clear, the episode stays open and a genuinely-new long call is suppressed. Mitigation is the `finally`-block contract + `TestWrappedToolsNodeExceptionClearsStamps`/`CancelClearsStamps` + the AD-37 belt (cleanup the leak, not prevent it).

---

### AD-10: Nudge delivery = A5 double-notify pattern (`enqueue_message` + direct `worker_pool.notify_work()`)

**Decision**: Delivery mirrors the watchdog's wedge-notify (`waiting_children_watchdog.py:1572-1619`): `await manager.enqueue_message(instance_id=parent_id, message=notice, source=LONG_TOOL_NUDGE_SOURCE, priority=0, metadata={...})`, then a best-effort direct `worker_pool.notify_work()` via `getattr(self._manager, "_worker_pool", None)`, with the `inspect.iscoroutine` shim (sync in prod, coroutine in some fixtures) and full exception-swallow (`[LongToolNudge] direct notify_work raised ... — relying on enqueue_message's internal notify`). *(Resolves from: phase 2 Task 3(e), U8–U10.)*

**Rationale**: The enqueue-commit/pool-notify race that stranded incident 33252 applies identically here. `enqueue_message` already calls `notify_work()` internally at `:1987`; the direct second notify is defense-in-depth for the lost-wake class.

**Consequence**: No new kwarg on `enqueue_message` — system nudges are foreground, `is_deferred`/`is_background`/`work_id`/`work_id_required` are NOT passed (phase 2 Task 3 CRITICAL note).

---

### AD-11: `priority=0` (system lane) — a nudge NEVER resets leader-attestation counters

**Decision**: The nudge enqueues with `priority=0`. The terminal-revive attestation-counter-reset branch requires `priority==1 AND msg_type==HUMAN` (`instance_messaging.py:1896-1902`), so a nudge that revives a WAITING_CHILDREN parent leaves `attestation_denied_count` / completion-gate state untouched. **Side effect (intended, AM-6)**: claim order is `ORDER BY priority ASC, enqueued_at ASC` (`message_queue/repository.py:184`) — priority-0 nudges are claimed **ahead of** priority-1 user messages, including cross-instance scans. This is the watchdog wedge-notice precedent (system advisories queue-jump user traffic). Pinned by U7 (unit) + I5 (integration). *(Resolves from: phase 2 D2-6.)*

**Rationale**: A system advisory must not corrupt an in-progress attestation episode as a side effect of waking its parent.

**Alternatives**: priority=1 — WRONG per the documented contract; priority=0 + opt-in-reset metadata flag or skip-if-attesting — complexity for zero v1 benefit.

---

### AD-12: PAUSED parent = pre-check, single WARN, skip (no stat counter, no deferred delivery)

**Decision**: `deliver_long_tool_nudge` reads `parent.status` FIRST; on PAUSED it logs one WARN and returns False — never raises into the scanner tick, and deliberately does NOT let the claim gate defer a stale notice to resume. **Clarified semantics (AM-7):** retry-every-tick-while-paused, fire-on-resume with fresh numbers — the `_fired_episodes` stamp-level set is only updated on a *successful* fire, so a PAUSED parent skips the stamp advance and the same crossing remains eligible on resume. The WARN is per-tick while the condition holds. Pause-cascade ends the stamp (child task is cancelled → wrapper `finally` clears) → the episode never opens in this case. Acceptable trade-off; the operator is typically present during pause. *(Resolves from: phase 2 Task 3(a), D2-12, U5, I4; mirrors watchdog `:996-1009`.)*

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

### AD-16: Nested `LongToolCallNudgeConfig(BaseSettings)` with `env_prefix="LONG_TOOL_NUDGE_"`, wired once on `EnsembleConfig` — ships in **Phase 1** (B1)

**Decision**: A nested pydantic-settings class (`enabled`, `interval_seconds`, `default_threshold_seconds`) placed between `ReportIntegrityConfig` and `LanguageConfig`, wired via `long_tool_nudge: LongToolCallNudgeConfig = Field(default_factory=LongToolCallNudgeConfig)` immediately after the LoopBreaker wiring line. **LANDING PHASE: Phase 1, not Phase 2** (AM-8 / B1 re-slice). Phase 1 must boot self-contained — the config class, the `HARD_MAX_THRESHOLD_SECONDS` constant, and the lifespan wiring all land together so Phase 2 owns delivery only and Phase 3 owns the tool only. *(Resolves from: phase 2 D2-1, Task 7 originally; relocated to phase 1 Task 7a.)*

**Rationale**: Three independent infra-loop knobs grouped under one import path beat cluttering the 3000-line flat `ServicesConfig`; `LoopBreakerConfig` (`config.py:1737-1756`, wired `:2109`) is the closer precedent and the documented modern style.

**Alternatives**: flat `SERVICES_*` fields (the older watchdog style) — rejected; no config block — rejected (the `enabled` toggle is a v1 hard requirement); per-instance only — rejected (chicken-and-egg for the first long call).

---

### AD-17: Env names `LONG_TOOL_NUDGE_ENABLED` / `LONG_TOOL_NUDGE_INTERVAL_SECONDS` / `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` — landing in **Phase 1** (B1)

**Decision**: The three env vars derive mechanically from the nested class's `env_prefix` + field names. Phase 1 consumes the dotted attribute path (`config.long_tool_nudge.*`); phase 2 / phase 3 read the same frozen value. Out-of-range env values fail fast at boot via `ge=`/`le=` validators. **LANDING PHASE: Phase 1** (moved from phase 2 Task 7 originally). *(Resolves from: phase 1 D6 + phase 2 D2-1/Task 7 (U14–U16); caller-mandated decision (4).)*

---

### AD-18: Defaults — interval 60 s, threshold 900 s, hard max 1800 s

**Decision**: `interval_seconds=60` (nudge latency ≤ threshold + 60 s; watchdog's 3600 s cadence exists because ITS threshold is 3600 s); `default_threshold_seconds=900` (catches the first bd4b36ef bash at ~15 min; operator tightens per-child via phase 3's tool); hard max `1800` (a parent cannot act usefully on a 4-hour tool — re-spawn is the answer by then). *(Resolves from: phase 1 D8 + phase 2 D2-8 (interval); phase 2 D2-9 (threshold); phase 2 D2-2 + phase 3 D7 (hard max). Deduplicated into one entry per the synthesis dispatch.)*

---

### AD-19: Hard max is a safety invariant, NOT operator-tunable — three defense layers

**Decision**: `1800` is enforced three ways, none env-tunable: the module constant, the `Field(le=HARD_MAX_THRESHOLD_SECONDS)` boot validator on `default_threshold_seconds`, and the runtime `min(metadata_or_default, HARD_MAX)` clamp in threshold resolution (second line of defense for values that bypass pydantic, e.g. a hand-edited metadata key). The **floor** `MIN_THRESHOLD_SECONDS = 60` (AD-38) lives at the same canonical home and is enforced on BOTH sides — the tool's loud ValueError (Phase 3 `set_instance_tunable`) AND the scanner's `_resolve_threshold` (Phase 1: any hand-edited metadata value `< 60` falls back to the configured default). The full chain is stated in **AD-41** (the canonical threshold precedence chain — stated ONCE, every other mention refers back). *(Resolves from: phase 2 D2-2; interacts with AD-30 (canonical home), AD-24 (tool-side loud reject), AD-38 (floor both sides), AD-41 (precedence chain).)*

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

### AD-22: Reject-vs-clamp — the tool LOUDLY RAISES on floor/ceiling violations; error-dict only for unknown keys; write ONLY via `set_metadata`; gated on `LONG_TOOL_NUDGE_ENABLED`

**Decision**: `value` outside `[60, 1800]` → loud `raise ValueError` listing the valid range (mirrors `spawn_councilor` strict model-validation — the "no silent fallback" house style for hard constraints); a parent typing `18000` WANTED 18000 and must re-read, not receive a silent 1800. Wrong `key` → recoverable error-dict `{"error_code": "UNKNOWN_KEY"}` (mirrors `project_set_metadata` — the LLM self-corrects from the message). Non-int / bool (explicit bool trap) → ValueError. Writes go exclusively through `instance_repository.set_metadata` (`:2099`) — NEVER `update_instance`, which rejects `instance_metadata` with ValueError at `:1094-1121` (test-pinned). Return shape: `{instance_id, key, prior_value, effective_value = min(value, 1800), applied_at}`; `set_metadata` returning None → `{"error_code": "NOT_FOUND"}`. **KILL-SWITCH GATING (AD-39): when `LONG_TOOL_NUDGE_ENABLED=0`, the tool returns a clear `{"error_code": "FEATURE_DISABLED", "message": "long-tool-nudge is disabled by config"}` and writes NO metadata; stamp/log presence in the daemon continues by design (the kill-switch gates delivery + tool write, not the duration-observability line).** *(Resolves from: phase 3 D2, D3, Task 2; caller-mandated decision (2) + AD-39 owner decision 3.)*

**Alternatives**: clamp + echo `{"warning": "clamped"}` — friendlier but hides mistakes; two-stage soft-warn/hard-reject — over-engineered.

---

### AD-23: Tool category = existing `"instance"`; exposure = category-wide by construction (RESOLVED, AM-3)

**Decision**: No new category. **Exposure is category-wide by construction** (RESOLVED via AM-3 architect verification) — every agent whose active `meta.json` allows `"instance"` receives the tool automatically (~15 agents today: leader, planner, developer, tester, governor, architect, coder, reviewer[v2], tidier[v2], wanderer, approver[v2], _mother, blueprinter, project-manager). Phase 3 Task 6 is a **conditional version-tag resolve** — read the ACTIVE `version_tag` per agent (planner[v2] / developer[v2] already allow `"instance"`; likely zero edits). Add `"instance"` only to a base-planner/developer variant that is actually resolved and missing it. Effective holder list documented in `docs/long-tool-nudge.md`. *(Resolves from: phase 3 D5, D8, D9, Tasks 5-6, 8-9; AM-3 RESOLVED.)*

**Rationale**: A new `"instance_tuning"` category forces a leader change for zero net win. A dedicated doc crosses operations + agent-behaviour + system-policy boundaries and is easier to link than a section in a sprawling operations page.

**Note (was caveat, now RESOLVED)**: the prior "exposure may narrow to leader-only" caveat was conditional on AD-35 verifying planner/developer spawn capability. AD-35 is now RESOLVED — the gateway is meta.json's `tools.allow` itself, and the active v2 variants already include `"instance"`.

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

### AD-26: Threshold unit = seconds everywhere; floor enforced on BOTH sides

**Decision**: The metadata key (`_seconds`), the env var (`_SECONDS`), the tool arg, and the scanner comparison are all integer seconds. Floor `MIN_THRESHOLD_SECONDS = 60` (AD-38, same canonical home as the hard max) prevents accidental micro-thresholds that would defeat the feature; enforced on BOTH sides — tool-side ValueError in `set_instance_tunable` AND scanner-side fallback in `_resolve_threshold` (any hand-edited metadata value `< 60` is treated as invalid and falls back to the configured default). *(Resolves from: phase 3 D10 + B4/AD-38.)*

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

### AD-30 (R3): `HARD_MAX_THRESHOLD_SECONDS = 1800` (and `MIN_THRESHOLD_SECONDS = 60`, AD-38) live in `daemon/services/long_tool_nudge.py`; BOTH `daemon/config.py` and `daemon/tools/instance.py` import from there — landing in **Phase 1** (B1)

**Decision**: One canonical home: the module constants in `daemon/services/long_tool_nudge.py` (defined in **Phase 1**, AM-8 / B1 re-slice). `daemon/config.py` imports `HARD_MAX_THRESHOLD_SECONDS` for the `le=` validator (import direction config→services is safe and already the house shape — config.py imports services constants elsewhere; the reverse, services importing config at module top, is what must be avoided for circularity). Phase 3's tool imports `HARD_MAX_THRESHOLD_SECONDS` AND `MIN_THRESHOLD_SECONDS` (AD-38) from `daemon.services.long_tool_nudge` — NOT from `daemon.config`, and NOT under aliases like `MAX_THRESHOLD_SECONDS`. `TestFloorAndCeilingConstants` pins by importing the same OBJECTS (identity, not just equality) from `daemon.services.long_tool_nudge`. *(Resolves from: phase 2 Task 1/7 vs phase 3 Task 5/D7 + B1/AM-8(a).)*

**Rationale**: Phase 1's plan put the constant in the services module and made config a consumer; phase 3 D7 assumed a config-side export that phase 2 never declared. Import-direction sanity: `daemon/config.py` is imported by nearly everything, so config must not reach INTO a deeper services module that itself imports config — here the services module does NOT import config at module top, so config→services is acyclic. A `daemon.constants` re-export adds indirection for one symbol. The **re-slice to Phase 1** (AM-8(a)) keeps the constant next to its single use site (the scanner's threshold resolution), so phase 2 imports-and-only-delivers, while phase 3 imports-and-only-writes.

---

### AD-31 (R4): Episode keying stated identically in both files — stamp-level `(child_id, tool_call_id)`, nudge-level `(parent_id, child_id)`; `tool_end` closes the episode, a NEW `tool_call_id` re-arms

**Decision**: Phase 1's "episode key" section and phase 2's dedup description now state the SAME two-level contract (full semantics in AD-2): phase 1's `(child_id, tool_call_id)` is the scanner's FIRE dedup (per-process, prevents consecutive-tick re-fires of one stamp); phase 2's `(parent_id, child_id)` is the NUDGE dedup (one nudge per episode; `close_episode` on the wrapper's `tool_end` clear; fresh `tool_call_id` = fresh episode, tested by U3). The two sets coexist BY DESIGN in the one scanner class; neither subsumes the other. *(Reconciles: phase 1 Coupling "Episode key" paragraph + D3 vs phase 2 Task 2/5 + D2-3. Both files edited for explicitness.)*

**Rationale**: Read naively, `(child_id, tool_call_id)` vs `(parent_id, child_id)` looked like a contradiction; in fact they gate different actions. But "can coexist only if stated precisely" — so both files now carry the same sentence.

---

### AD-32 (R6a — RESOLVED): Tool-registry resolution is per-spawn-and-per-restore; meta.json is a per-process snapshot — daemon RESTART reaches all instances via restore-rebuild (AM-1)

**Decision**: RESOLVED via architect verification (AM-1). Tool lists resolve per spawn AND per restore (`_tool_registry.py:15-18`, no resolved-list cache), but meta.json is **snapshotted once per process** (registry singleton, no runtime re-discover). BOTH a new tool and a `tools.allow` change therefore require a **daemon restart**; after restart, **all** instances receive them — newly-spawned via the spawn path, pre-existing via restore-path rehydration (`instance_lifecycle.py:1611/:1714/:3892/:3995`). **No silent-no-op failure mode.** Phase 3 Task 6 docs the full holder list in `docs/long-tool-nudge.md`. *(Was OPEN in prior revision; AM-1 closes it. Carried from phase 3 Risk 4.)*

---

### AD-33 (caller-mandated, OPEN): Per-call (not per-batch) attribution — future refinement

**Decision**: NOT resolved for v1. True per-call duration requires reimplementing dispatch inside the wrapper (manual per-id coroutines) — rejected for v1 (AD-3 alternatives) and captured as the named improvement target. Co-batched fast calls may read slow; parents see advisory nudges and decide. *(Resolves from: phase 1 D12; caller-mandated decision (7).)*

---

### AD-34 (caller-mandated, DECIDED — STRICTLY DEFER): Bounded per-call `bash` timeout — visibility gap, not unboundedness; full ticket embedded in `architecture-recommendation.md` §3.1 (AM-4)

**Decision**: **DECIDED — STRICTLY DEFER** (AM-4). The daemon's `bash` tool **already has** a per-call `timeout` kwarg — `timeout: int | float | None = 1800` (`bash.py:204`), validated `≤ 1800` (`:225-226`), enforced via `asyncio.wait_for(proc.wait(), timeout=...)` (`:327`). So nothing structural is missing; the bd4b36ef incident was a **visibility** gap, not an unboundedness gap — the last bash ran ~30 min and never returned, but a hard timeout would still leave the parent agent without a heads-up. The long-tool-nudge path closes the visibility side. Changing the default inside *this* feature would (a) violate its advisory-only contract (a hard timeout cancels the call — an action, not an advisory), (b) cross every agent's every bash call including legitimate long operations (this repo's own pytest suite, migrations, playwright), and (c) entangle two reviewable blast radii in one merge. v1 notice text must NOT reference the future env var (AD-8 structure locked). **Full follow-up ticket block is embedded verbatim in `architecture-recommendation.md` §3.1.** *(Resolves from: phase 1 D11; caller-mandated decision (6); AM-4 closes it.)*

### AD-35 (R6b — RESOLVED): Exposure is category-wide by construction — no meta.json one-liners needed; phase 3 Task 6 is a conditional version-tag resolve (AM-3)

**Decision**: RESOLVED via architect verification (AM-3). **Exposure is category-wide by construction** (no new category) — every agent whose active meta.json allows `"instance"` receives the tool automatically (~15 agents today: leader, planner, developer, tester, governor, architect, coder, reviewer[v2], tidier[v2], wanderer, approver[v2], _mother, blueprinter, project-manager). **Phase 3 Task 6 is now a conditional version-tag resolve**: read the ACTIVE `version_tag` per agent (planner[v2] / developer[v2] already allow `"instance"` — likely zero edits); add `"instance"` only to a base-planner/developer variant that is actually resolved and missing it. Worker is denied by the team-membership gate regardless. *(Was OPEN in prior revision; AM-3 closes it. Carried from phase 3 D9 / Risk on exposure.)*

### AD-36 (R6c — RESOLVED): `get_metadata_value(instance_id, key) -> Any | None` is the verified scanner read (AM-12)

**Decision**: RESOLVED via architect verification (AM-12). `get_metadata_value(instance_id, key)` is sync, single-key, no row hydrate, returns `None` on missing row / NULL / absent key, with JSON re-parse at `repository.py:1980`. It is the correct scanner read for `_resolve_threshold`. The fallback `repo.get(instance_id)` + dict access remains valid as defense-in-depth (one extra read) if a future regression breaks the signature. *(Was OPEN in prior revision; AM-12 closes it. Carried from phase 3 Risk 5.)*

---

### AD-37: Stamp-TTL force-close belt — closes the missed-`tool_end` suppression failure mode (AM-5)

**Decision**: At the end of every `run_once` tick, the scanner sweeps `registry.snapshot()` and for any stamp with `age > STALE_STAMP_TTL_SECONDS` (= `4 × HARD_MAX_THRESHOLD_SECONDS = 7200 s`):
- `registry.clear(instance_id, tool_call_id)` — force-clear the stamp
- `_active_episodes.discard((parent_id, child_id))` — close the (parent, child) episode
- `_fired_episodes.discard((child_id, tool_call_id))` — re-arm the stamp-level fire dedup
- `logger.warning("[LongToolNudge] STALE_STAMP force-cleared ...")` — DEBUG-grade audit trail

The **TTL constant `STALE_STAMP_TTL_SECONDS = 7200`** lives at the canonical home (`daemon/services/long_tool_nudge.py`, alongside `HARD_MAX_THRESHOLD_SECONDS` and `MIN_THRESHOLD_SECONDS`). The constant is **decoupled from the effective graph-task cap** per P-1 (the source of the 7200 s effective cap was not reconciled: `constants.py:36 TASK_TIMEOUT_S=300` vs the incident forensics and `MainLoopBridge.run_async` — defined as a module constant, not derived). The hygiene sweep in the same pass: discard `_fired_episodes` entries whose `(child_id, tool_call_id)` no longer appears in the snapshot — prevents unbounded set growth from any clear-path that bypasses the scanner's bookkeeping. The same sweep also walks `_active_episodes` for orphan tuples whose child stamp has fully cleared — a missed `close_episode` can never silently suppress future nudges forever. Tests: `TestScannerStaleStampForceCloses` (synthetic age 7300 s → force-cleared on next tick; a fresh `tool_call_id` on the same child fires normally afterwards) + `TestScannerOrphanEpisodeClose` (orphan tuple in `_active_episodes` with no live stamp → discard on next tick) + `TestScannerFiredEpisodesHygieneDiscard` (orphan stamp-level tuple with cleared stamp → discard on next tick).

**Rationale**: Zero false-positive risk — a *legitimate* in-flight stamp cannot exceed the graph-task lifetime (process task supervision cancels it first), so a `> 7200 s` stamp is by definition a leak. Belt cost ≈ 10 LOC + 3 tests; closes the silent-suppression failure mode that tests cannot forever prevent (future wrapper regression, registry bug, kill-path races). *(Resolves from: architecture-recommendation.md §3.3; B2; AM-5.)*

---

### AD-38: Floor `MIN_THRESHOLD_SECONDS = 60` enforced on BOTH sides — tool-side ValueError AND scanner-side fallback (D-A owner decision 4)

**Decision**: The 60 s floor (`MIN_THRESHOLD_SECONDS = 60`, defined in the canonical home `daemon/services/long_tool_nudge.py` alongside `HARD_MAX_THRESHOLD_SECONDS`, AD-30) is enforced on **both sides**:
- **Tool side (Phase 3, `set_instance_tunable`)**: `value < 60` → loud `raise ValueError` listing the valid range `[60, 1800]` (mirrors `spawn_councilor` strict-model-validation house style; the "no silent fallback" stance for hard constraints).
- **Scanner read side (Phase 1, `_resolve_threshold`)**: if the resolved `instance_metadata.long_tool_call_threshold_seconds` is `None`, non-int, OR `< 60`, treat as invalid and fall back to `default_threshold_seconds`. This closes the read-side floor-bypass case where an operator hand-edits metadata (or a future tool regression writes a below-floor value) and the scanner silently uses it.

The floor prevents accidental micro-thresholds that would defeat the feature (a 5 s threshold would re-fire on every normal CLI invocation). Test: `TestResolveThresholdFloorBypass` — mocked metadata of `30` falls back to the default; metadata of `60` is honored; metadata of `0` / `None` / `"60"` (string) fall back. *(Resolves from: D-A owner decision 4; AD-19 / AD-26 cross-reference.)*

---

### AD-39: `set_instance_tunable` is gated on `LONG_TOOL_NUDGE_ENABLED` — disabled ⇒ no metadata write, clear message (D-A owner decision 3)

**Decision**: When `LONG_TOOL_NUDGE_ENABLED=0`, the `set_instance_tunable` tool returns `{"error_code": "FEATURE_DISABLED", "message": "long-tool-nudge is disabled by config (LONG_TOOL_NUDGE_ENABLED=0); no metadata written"}` and writes NO metadata. The per-completion `[LongToolNudge] TOOL_COMPLETED` log line and the stamp registry continue to tick by design when disabled — **stamp/log presence ≠ delivery**, the kill-switch gates delivery + tool write, not the duration-observability line. The config attribute is read via `config.long_tool_nudge.enabled` (Phase 1's `LongToolCallNudgeConfig`), so Phase 3 imports the gated-value decision rather than re-reading the env. A regression pin: the tool's `TestSetInstanceTunableGateKillSwitch` covers `enabled=False → returns FEATURE_DISABLED, no set_metadata call`; the stamp/log continuity is covered separately by T1-T7 (Phase 1, do not regress when the kill-switch flips). *(Resolves from: D-A owner decision 3; AD-22 cross-reference.)*

---

### AD-40: Terminal parents skip the nudge (TERMINATED/ERROR/FAILED + COMPLETED in v1) — never revive, just WARN-log (D-A owner decision 2)

**Decision**: `deliver_long_tool_nudge` checks `parent.status` BEFORE the PAUSED branch. **ALL terminal statuses** — `COMPLETED`, `TERMINATED`, `ERROR`, `FAILED` — skip the nudge and emit a single WARN log per attempted fire; `enqueue_message` is NEVER called (no revive path on this feature). **Justification**: reviving a terminal parent to deliver an advisory nudge would wake a parent the operator explicitly stopped, and could corrupt state by triggering status-side-effects; the orphan-child terminal-class is owned by existing machinery (`terminate_instance` / cascade-resume / instance_lifecycle) and out of scope for this feature. In v1, COMPLETED parents are ALSO skip+WARN (no exception) — the WAITING_CHILDREN → RUNNING revive path (U12 + I2) remains valid for the non-terminal-RUNNING parents that are the common case. A future AD may re-enable revive on COMPLETED if telemetry shows a useful segment. Test pins: `TestDeliverLongToolNudgeTerminalParentSkips` — for each terminal status, assert `enqueue_message` called ZERO times, `logger.warning` called with the status, return value `False`, no exception into the scanner tick. The PAUSED test (U5) and the test pinning WAITING_CHILDREN→RUNNING revive (I2) remain unchanged. Any test that previously asserted terminal-revive DELIVERY (the early U13 / I2 draft variants) flips to skip+WARN assertions. *(Resolves from: D-A owner decision 2.)*

---

### AD-41: Canonical threshold precedence chain — stated ONCE, every other mention refers back (D-A owner decision 5)

**Decision**: The **threshold precedence chain** is stated exactly once in the package — in the Working-Names Table of `plan-overview.md` — and every other mention (AD-9, AD-19, AD-22, AD-26, AD-38, AD-39, the phase plans, the docs, the tests) refers back to that single statement by name ("see canonical threshold precedence chain"). The chain (top to bottom):

1. **Kill-switch** `LONG_TOOL_NUDGE_ENABLED` (default ON). When OFF: no scanner fires, no tool write, no delivery — but stamp registry and per-completion log line continue by design.
2. **Per-child metadata key** `instance_metadata["long_tool_call_threshold_seconds"]` (read via `get_metadata_value(instance_id, key)` per AD-36). If absent / None / non-int / `< 60` (read-side floor bypass, AD-38) → fall through.
3. **Env default** `LONG_TOOL_NUDGE_DEFAULT_THRESHOLD_SECONDS` (validated `ge=1 le=1800` at boot via `Field(le=HARD_MAX_THRESHOLD_SECONDS, default=900)`).
4. **`min(·, HARD_MAX_THRESHOLD_SECONDS=1800)` clamp** (defense layer 2 — environment ceiling; AD-19).
5. **Strict `>` comparison** in the scanner (`elapsed_seconds > effective_threshold`, matches watchdog `age > threshold` precedent, AD-4) — fire boundary.

The same shape is referenced in the Phase 1 `_resolve_threshold` implementation, the Phase 3 tool's documented behaviour, and the docs. Future amendments to any rung MUST update the canonical statement AND broadcast to every reference. *(Resolves from: D-A owner decision 5.)*

---

### AD-42: Episode close mechanism — pick ONE (scanner-side snapshot-diff OR wrapper-side cached parent_id) and pin in phase 2 (B4)

**Decision**: One mechanism is picked and pinned in phase 2 deliverable prose + the B4 follow-up hygiene. The two candidates:

- **Option (i): Scanner-side snapshot-diff close.** The scanner holds a `_last_seen_stamps: dict[(child_id, tool_call_id), started_at]` snapshot from the prior `run_once`; at every tick, it computes `now_stamps - last_stamps`; ids that **dropped** between snapshots correspond to a `tool_end` event and trigger `close_episode(parent_id, child_id)` — but the scanner needs `parent_id` for that, so it reads it from the registry. Requires the scanner to capture `parent_id` at fire time and remember it for the close sweep. **No wrapper signature change.**
- **Option (ii): Wrapper-side close with scan-time-cached parent_id.** The wrapper's `finally` block already runs on every `tool_end`; it can read `parent_id = state["configurable"]["thread_id"]` → `instance.parent_id` (sync, one read per clear) and call `close_episode` directly. The scanner keeps `_fired_episodes`; the wrapper has the cached parent_id. **Adds a parent_id read per tool call** — cheap (sync repo, no row hydrate).

The **deciding trade-off**: (i) keeps `close_episode` out of the wrapper hot path but relies on snapshot diff to discover closes; (ii) makes the close authoritative on the `finally` path at one extra repo read per tool call. (ii) is the SIMPLER mechanism — the architect's recommendation (the wrapper's `finally` "must reliably close episodes"; the cached `parent_id` survives just long enough to make the close call) and is the explicit owner decision (B4: "The wrapper's `finally` must reliably close episodes even though the wrapper lacks parent_id (that's the point of the cached parent_id / snapshot-diff choice)"). **Phase 2 pick: Option (ii)** — wrapper-side close with cached `parent_id` read at clear time. Implementation: capture `parent_id` at `record_start` (one sync read per `tool_start`, mirrors D2 alt a for parents), pass it through the registry stamp shape (the `_Stamp` gains `parent_id` for the lifetime of the stamp), `finally` calls `close_episode(parent_id, child_id)` on the cached value. **The hygiene sweep extends to `_active_episodes`**: at end of `run_once`, discard orphan tuples whose child stamp has fully cleared — a missed close can never silently suppress future nudges forever (belt + suspenders, joins AD-37's TTL-belt cleanup). *(Resolves from: B4 owner decision.)*

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
| phase3-plan.md | D9 | AD-23, AD-35 (RESOLVED, AM-3) |
| phase3-plan.md | D10 | AD-26 (floor enforced both sides — see AD-38) |
| synthesis dispatch | R1 | AD-28 |
| synthesis dispatch | R2 | AD-29 |
| synthesis dispatch | R3 | AD-30 (landing phase: 1) |
| synthesis dispatch | R4 | AD-2, AD-31 |
| synthesis dispatch | R5 | Working-Names Table in `plan-overview.md` |
| synthesis dispatch | R6 | AD-32 (RESOLVED, AM-1), AD-35 (RESOLVED, AM-3), AD-36 (RESOLVED, AM-12) |
| doc-consolidation pass | D-A episode granularity | AD-9 (amended), AD-37 |
| doc-consolidation pass | D-A terminal parents | AD-40 |
| doc-consolidation pass | D-A set_instance_tunable kill-switch gating | AD-22 (cross-ref), AD-39 |
| doc-consolidation pass | D-A floor unification both sides | AD-26 (cross-ref), AD-38 |
| doc-consolidation pass | D-A canonical threshold precedence chain | AD-41 (statement location: Working-Names Table) |
| doc-consolidation pass | B1 phase re-slice (config + constant + lifespan → Phase 1) | AD-16 (amended), AD-17 (amended), AD-20, AD-30 (amended) |
| doc-consolidation pass | B2 stamp-TTL belt | AD-9 (amended), AD-37 |
| doc-consolidation pass | B3 resolution fold-in | AD-1..AD-12 (all AM-resolved where applicable); AD-32/34/35/36 RESOLVED |
| doc-consolidation pass | B4 close-mechanism pin | AD-42 (Option ii: wrapper-side cached parent_id) |
| doc-consolidation pass | AM-1..AM-11 resolutions | AD-11 (queue-jump rationale), AD-12 (clarified semantics), AD-23 (RESOLVED), AD-34 (DECIDED — STRICTLY DEFER) |
