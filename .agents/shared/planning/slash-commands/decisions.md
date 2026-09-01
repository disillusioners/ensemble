# Decisions: /compact Slash Command — Backend Phase

Date: 2026-08-31 (revised 2026-08-31 — architect verdicts folded in; post-review adjudication folded in 2026-08-31 (C1 DECIDED + O8–O13 PINNED); authoritative source: `architecture-recommendation.md`)
Branch: feature/slash-commands @ 5e16f791
Owner: backend phase1-plan (this file feeds the architect's plan-overview.md)
Binding prior decisions: `.agents/shared/planning/context-compaction/decisions.md` (D2 boundary groups, D3 RemoveMessage sentinels, D4 SystemMessage summary, D5 progressive reduction, D6 chunking, D9 60s dedup, D10 skip-on-is_retry, D12 compacted_at schema, D13 emergency truncation termination).

---

## Decided

### D-B1. REVISED per architect (2026-08-31 §2/§9.3): Reuse `ContextCompactor.compact_state` via a `force` flag — extend, do not build a parallel path; force bypasses THRESHOLD ONLY
- **Evidence:** `compact_state` (compaction.py:608-664) owns ALL invariants: dedup (:618-620), injected-partitioning (:627-640), min-messages (:645-651), threshold (:659-664), chunked summarization (:819-840), replacement build (:1044-1079), truncate fallback (:1081-1111 via :753-772).
- **Rationale:** the engine is NOT factored into pure helpers (worker B verified :608-1130); a "new orchestration over helpers" would re-implement D2/D3/D5/D6 invariants = exactly the parallel code path the feature brief forbids. A `force: bool = False` parameter (default preserves automatic behavior) is additive and testable.
- **REVISED scope (architect gate table §2):** `force=True` bypasses the threshold check (:659-664) ONLY. Min-messages (:645-651) and D9 60s dedup (:618-620) stay in-engine untouched. The executor owns two pre-checks BEFORE any engine call: `compacted_at` recency <60s → `success + noop + reason=recently_compacted` (no engine invocation, no LLM spend — safety net intact); noop floor <5% of the resolved per-instance window → `success + noop + reason=below_floor` (knob `SLASH_COMMANDS_NOOP_FLOOR_RATIO`, default 0.05, tuning expected). Engine `None` = "CAN'T compact"; below-floor = "WOULD but SHOULDN'T".
- **Result shape:** `CompactionResult` gains additive dataclass defaults `forced: bool = False`, `failure_kind: str | None = None`; all construction sites keep working; reactive None-check (graph.py:3514-3516) unaffected. **Anti-drift test: `forced=False` on both auto paths.**
- **Consequence:** both existing callers (proactive instance_messaging.py:1179, reactive graph.py:3513) are untouched by default.

### D-B2. Intercept seam = router-level, in POST /messages after :240 validation, before :243 status capture
- **Evidence:** messages.py:159-553 branch map; validation ends at :240; status captured :243. Research Explorer 2 recommendation.
- **Rationale:** one check covers all 4 status branches (RUNNING injection, WC enqueue, PAUSED auto-resume, IDLE/terminal enqueue); HTTP-only blast radius matches FE scope; command ack rides the POST response.
- **Rejected:** enqueue_message_job payload sniff (manager.py:6428 — the durable path, not the RUNNING injection lane) misses that lane entirely; job-processor admission adds post-commit redirect + queue latency with no coverage gain; language_check_node (graph.py:2593-2679, the pre-LLM drain site) would execute the command inside the turn whose checkpoint it rewrites — incoherent. (Line numbers corrected 2026-08-31 — those bytes are the language-check closure, not agent_node.)

### D-B3. ExecutionGate acquisition is MANDATORY for every /compact execution
- **Evidence:** ExecutionGate per-instance asyncio.Lock serializes graph runs (execution_gate.py:108-143, run :118); external checkpoint writes during an in-flight astream are silently overwritten or corrupt the live list (no merge exists).
- **Rationale:** the gate is the single serialization point shared with turns.
  - IDLE path: probe `wait_for_instance_quiescent(timeout=0)` + `has_instance_busy=False` → take gate → compact (gate blocks a turn starting mid-compaction).
  - PAUSED-quiescent path: take gate → compact (blocks a concurrent auto-resume turn until compaction ends).
  - RUNNING path: pause-first (pause_instance_cascade :2685-2691 → quiescence ≤30s manager.py:3362-3431) → take gate → compact → resume_instance_cascade (:2971).

### D-B4. REVISED per architect (2026-08-31 §3/§9.1/§9.8): Adaptive timeout lives at the compaction.py:1038 site — per-PROMPT token estimate, facade margin pinned, per-chunk preservation added
- **Evidence:** site `asyncio.wait_for(..., timeout=30.0)` at compaction.py:1027-1039 is the BINDING cap today (facade 45s at :1011 never reached; llm_failover.py:529/:623/:706).
- **REVISED input (correction 1, §3):** the formula input is the **prompt being sent**, computed inline at the `_call_summarization_llm` call site — NOT `context.messages`. Three call origins (:900 single-batch / :939 merge / :971 condense) have very different prompt sizes; `context.messages` (full conversation incl. preserved + injected) over-estimates every call after the first chunk and massively over-estimates merge/condense calls (tiny prompts) — burning budget headroom and delaying fallback. Estimator `estimate_messages_tokens` (**daemon/loader.py:465**) unchanged.
- **Formula:** `min(cap, base + (prompt_tokens / 100_000) * per_100k)` with base=90s, per_100k=60s, cap=300s (config knobs).
- **Both paths improve by construction:** proactive (:1179) and reactive (:3513) call the same `_call_summarization_llm`. Verify with tests asserting the shared call site.
- **Facade value PINNED (§9.8):** `wall_clock_cap_s` = per-call cap + **5s margin** at :1011 (replaces the plan's "+15s" guess) — wraps cleanly after the inner cancel; tenacity retries stay INSIDE the cap (llm_failover.py:559-568); keeps :1038 binding by design.
- **NEW engine change (correction 2, §9.2):** per-chunk `try/except TimeoutError` inside the :838-840 loop so a mid-run timeout PRESERVES previously successful chunk summaries — today the outer catch (:753-772) discards ALL of them → full truncate. Timed-out batch's raw messages flow into the replacement. Coherent, D3-safe, benefits proactive/reactive too; regression test required.

### D-B5. REVISED per architect (2026-08-31 §4/§9.4): Trim fallback = the EXISTING in-engine `_truncate_fallback` + `failure_kind` reporting + ONE id-deterministic marker line INSIDE the function; LLM summary-line variant REJECTED
- **Evidence:** summarization failure/timeout is already caught at compaction.py:753-772 → `_truncate_fallback` (:1081-1111) → result `compacted_type="truncation"` + `summarization_error`. Destructive-trim-without-summary on LLM outage is pre-existing engine behavior.
- **Rationale:** wrapping again at the orchestration layer would double-apply trims and duplicate the pairing/terminal invariants. Executor maps the engine result to distinct outcome phases (`failure_kind="timeout"` → `timed_out` → `fallback_applied`; `failure_kind="error"` → `failed` + fallback note); engine result extended with `failure_kind` ("timeout" | "error" | None).
- **REVISED marker ruling (Q5 DECIDED):** ONE marker line added INSIDE the existing function, before the preserved-groups loop: `replacement.append(SystemMessage(content="[Earlier messages trimmed to fit context]", id=f"truncation-marker-{uuid4()}"))`. Id-deterministic → `add_messages` reducer de-dups on re-compaction; no collision with the synthetic system message (persistence.py:404-449 prepend stays independent); pairing guard untouched (RemoveMessage + preserved only). **The LLM summary-line variant is REJECTED decisively:** generating it requires an LLM call — re-triggering the exact failure (LLM outage/timeout) the fallback exists to escape; also a second fallback implementation in spirit (violates the no-parallel-path rule).
- **Hard rule:** NO outer timeout at the executor around `compact_state` — an outer cancel can tear the two `aupdate_state` persistence calls (:1190-1202 recipe). Cancellation must only ever trip between LLM calls inside the engine (operation budget, see D-B6), never between aupdate calls.

### D-B6. A whole-operation budget is REQUIRED once adaptive timeouts land (not optional)
- **Evidence:** chunked summarization issues N sequential calls (:819-840 batches, merges :846/:939/:958/:971) — worst case ≈ N×30s unbounded today; after D-B4 each call can consume up to 300s → N×300s. An unbounded user-facing command is unacceptable.
- **Decision:** budget trips BETWEEN engine LLM calls; on exhaustion the engine proceeds to its existing truncate fallback for the remainder. Default ≈ the hard cap (300s), env-tunable (`COMPACTION_OPERATION_BUDGET_S`). Applies to both paths (proactive gets bounded too — today it is unbounded, so this is a strict improvement).

### D-B7. Execution model: async — POST acks immediately, progress via SSE, GET fallback for loss
- **Evidence:** with base timeout 90s even a small compaction can legitimately run minutes (D-B4); holding the HTTP request open 300s+ breaks on proxies/FE fetch timeouts and loses the result exactly when the user most needs it. Research: SSE hub is live-only, no replay (live_event_hub.py); GET /{id}/injection (messages.py:572+) is the established REST-fallback pattern.
- **Decision:** POST returns `{"status":"command","command":"compact","command_id":...,"state":"accepted"}` immediately; executor runs as a managed asyncio task; `command_progress` SSE events carry phase transitions; **GET `/api/instances/{id}/commands/active`** returns authoritative state (`{exists:false} | {exists:true, command:<progress event>}`) from an in-memory bounded registry — normative shape per architect §7 (2026-08-31; supersedes the per-command_id GET in the original draft). Restart ⇒ `{exists:false}`.

### D-B8. JAFP compliance: direct service execution, NO new JobItem type
- **Evidence:** the public work primitive is `JobItem` (job-as-front-primitive, JAFP 2026-07-07); `enqueue_job` ACCEPTS `job_type='message'` (job_queue_service.py:574, special-cased at :646/:652/:773/:779 — JobItem is a pure mirror, PG trigger skips the `job_locks` claim); however a `/compact` is ephemeral UI-triggered maintenance (not a public message primitive), so the precedent class for "ephemeral, internal, no JobItem" remains RAM injection / question packs. No durable wrap needed for this feature.
- **Consequence:** commands do NOT survive daemon restarts. A crash mid-compaction leaves state checkpoint-consistent (either no aupdate happened, or messages compacted with `compacted_at` missing — benign, matches existing proactive exposure between :1190-1194 and :1197-1202).

### D-B9. F3 timing rule: emit `waiting` BEFORE any pause-flag mutation
- **Evidence:** pause_instance_cascade cancels the in-flight graph task; SSE is the only live channel to show the user why the instance went quiet. Mirrors the injection-SSE ordering discipline (messages.py:114-156 pattern).

### D-B10. SSE emission is best-effort, never fails the API call
- **Evidence:** `_emit_injection_sse` pattern (messages.py:114-156): try/except → WARNING swallow. Copied for command_progress.

### D-B11. Per-instance model resolution for window math in the /compact executor
- **Evidence:** proactive path passes GLOBAL `config.llm.model` (instance_messaging.py:1160); per-model windows exist via `context_window_overrides` (config.py:715-749); the summarization LLM already resolves session model (compaction.py:997-1008).
- **Decision:** the executor resolves the instance's session model and passes it into CompactionContext so threshold/window math uses the right window (and the noop floor measures against that window). Extending this to the proactive path: accepted as designed per the O-B8 ruling (architect 2026-08-31) — no per-path override in this feature.

### D-B12. Config follows existing patterns: `COMPACTION_*` for timeout knobs, new `SLASH_COMMANDS_*` group for the command subsystem
- **Evidence:** `CompactionConfig` BaseSettings with `env_prefix="COMPACTION_"` (config.py:706-753, verified on branch).

---

## Decided by architect (2026-08-31)

All 13 open questions are closed by `architecture-recommendation.md` §8 (basis column cited). Backend baselines were upheld on 10 of 13; the three that changed are captured in the REVISED decisions above (D-B1, D-B4, D-B5). D-B2 (router seam — approach A, weighted 4.35) and D-B3 (mandatory ExecutionGate) were explicitly ratified.

| Q | Verdict (architect §8) |
|---|---|
| **O-B1** `'//'`-escape | ✅ **Adopt `//`** — checked BEFORE `/`; strip one slash, deliver as text (Slack convention, cheap, testable) |
| **O-B2** sync/async | ✅ **Pure async** — no grace window (300s cap makes sync untenable; grace buys nothing) |
| **O-B3** RUNNING instance | ✅ **Pause-first** (Watchover precedent watchover_service.py:1004) + reject-on-quiesce-failure (`quiescence_timeout`) |
| **O-B4** terminal instances | 🔨 **DECIDED-with-code-verification: REJECT** `reason=terminal_instance` + `detail="Send a message to start a new turn, then /compact."` — code-verified chain (§5): terminal guard load-bearing (aupdate on `next=()` → astream instant-return → :1132-1140 documented COMPLETED→RUNNING→COMPLETED collapse bricks revive-on-send); auto-revive (:1646-1682) does NOT call aupdate_state; revive-then-compact either no-ops or bricks; post-compact zombie RUNNING. Anti-drift: extract module-private `_is_terminal_checkpoint(...)` shared by the proactive site (instance_messaging.py:1146-1150) and the compact executor. Flip condition: dedicated compact-on-terminal lifecycle = separate refactor with revive-on-send regression tests |
| **O-B5** force detail | ✅ Option (a) keyword + additive result fields — **scope per Q6: threshold-only bypass** (see REVISED D-B1) |
| **O-B6** availability scope | ✅ Global now; `availability` predicate hook in `CommandSpec` for later per-agent policy |
| **O-B7** durability | ✅ **Stay ephemeral** (JAFP); `command_id` + `handler: Callable` seam keeps a future durable `JobItem('command')` wrap open without touching `CommandSpec` |
| **O-B8** proactive latency | ✅ **Accept as designed** — bounded ≤300s (budget) + completing truncate vs today's unbounded N×30s; no per-path override; monitor via `context_usage` SSE |
| **O-B9** unknown command | ✅ **400 UNKNOWN_COMMAND** + available-commands detail (parse-time client error; **additive over :222-229 envelope** per O13, 2026-08-31 — not a mirror) |
| **O-B10** refusals | ✅ **200 `state:"rejected"` + reason enum** (semantic refusals): `terminal_instance \| busy \| rate_limited \| pending_injections \| compaction_disabled \| quiescence_timeout` |
| **O-B11** pending injections | ✅ **Reject** `reason=pending_injections` — drain would couple injection delivery to compaction persistence; retry is cheap |
| **O-B12** constants mirror | ✅ **Two-PR plan**: update mirror in the feature PR + schedule mirror DELETION as a separate tidy PR (6-month drift argues for removal) |
| **O-B13** rate limiting | ✅ **Adopt**: 1 in-flight per instance (`busy`) + 10s min-interval (`rate_limited`), config knobs, **checked BEFORE ExecutionGate acquisition**; the ONLY abuse guard (executor recency pre-check makes duplicate spam a cheap noop) |

---

## Post-review adjudication (2026-08-31 — reviewer-council NEEDS_CHANGES)

Source of truth: `architecture-recommendation.md` §"Post-Review Adjudication (C1 + O8–O13)" (lines 255–306). This section folds the binding-decision application pass into `decisions.md`.

### C1. DECIDED (2026-08-31): Hybrid = option (i)'s distinct wire value **with** option (ii)'s trim semantics for the timed-out span
- **Evidence:** §3 correction-2 (per-chunk try/except preserving completed summaries) and §4's D-B5 mapping (timeout → `_truncate_fallback` → `timed_out_fallback`) cannot both hold on a mid-run timeout: with partials preserved, `_truncate_fallback` never fires, no trim happened, the marker (pinned inside `_truncate_fallback`) never emits, and FE copy "compacted via trimming" is false. The reviewer is right; §4's mapping as written only covers the zero-partials case.
- **Resolution (engine-level, binding):** on any mid-run stop (per-chunk timeout caught in-loop at `:838-840`, OR whole-op budget exhaustion between LLM calls), let S = completed batch summaries, B = batches not successfully summarized (in-flight failed + un-attempted): (1) `|S| ≥ 1` → **partial path**: replacement = summaries(S) + truncation marker + preserved tail + injected; B's messages are **DROPPED** — true trim of the un-summarized span (bounded shrink guaranteed: reduction ≥ un-summarized span → "user never stuck" provable). (2) `|S| = 0` → existing whole-fallback path fires unchanged (single-batch-timeout edge case). (3) `compacted_at` stamps on BOTH paths. Marker emitted via shared `_append_truncation_marker(replacement)` called by BOTH `_truncate_fallback` and the partial assembly (mutually exclusive construction paths → exactly-once).
- **Engine API:** `_summarize_chunked` returns typed `ChunkedOutcome(summaries, failed_batches, stop_reason ∈ "completed"|"timeout"|"error"|"budget")`; outer `:744-772` branches on `summaries` empty vs non-empty; **NO per-caller branching** — proactive/reactive get identical semantics. Auto-path `== "summary"` checks intentionally do NOT match partials (correct semantics; auto-path tests assert the new value only under timeout scenarios).
- **WS-3.4 acceptance (a)–(d) (replaces prior task text):** (a) first-batch timeout → `truncation` WITH marker, no summaries; (b) ≥2 batches, batch-2 timeout → `partial_summary`: batch-1 summary present, batch-2 messages absent, marker exactly once; (c) budget exhaustion → same as (b) with stop_reason="budget"; (d) proactive + reactive observe identical outcome semantics.
- **§7 amendment (carried verbatim into phase1 WS-5 and phase2 Task 6):** `compacted_type` enum gains `"partial_summary"` → full enum `"summary" | "partial_summary" | "truncation" | "noop"`; phase machine UNCHANGED; three-way executor mapping (summary → success; partial_summary + truncation → `timed_out → fallback_applied` distinct by `compacted_type`; noop → success). Per-value FE copy table lives in phase1 WS-5 / phase2 Task 6.
- **Pointer:** see `architecture-recommendation.md` §"Post-Review Adjudication (C1 + O8–O13)" lines 255–293 for the full amendment block; reconciliation note (§9 items 2 and 4 superseded by C1; item 6 amended) at lines 304–306.

### O8. PINNED (2026-08-31): New `daemon/services/_checkpoint_utils.py` hosts `_is_terminal_checkpoint`; `compaction.py` stays free of checkpoint-state semantics
- **Evidence:** `instance_messaging.py:1146-1150` is the only place today (refactor source); the engine should not import checkpoint-state semantics.
- **Resolution:** create a NEW small module `daemon/services/_checkpoint_utils.py` hosting the helper; import from `instance_messaging.py` and `compact_executor.py`; `compaction.py` stays free of checkpoint-state semantics (engine reuse boundary preserved).
- **Verification:** source-level `grep` asserting `from daemon.services._checkpoint_utils import` finds exactly the two expected import sites; `compaction.py` does NOT import the helper.

### O9. PINNED (2026-08-31): Single `try/except` around pause→quiesce; single enum value `quiescence_timeout`
- **Evidence:** the executor's pause→quiesce sequence can fail via timeout OR raised exception; the FE needs a single rendering for both.
- **Resolution:** wrap the **entire** pause → quiesce sequence in ONE `try/except`. ANY failure (timeout or raised exception) → `rejected + reason=quiescence_timeout` with the exception class name in `detail`. **Do NOT add a second enum value** for raised exceptions. If pause half-succeeded, attempt best-effort `resume_instance_cascade` in a `finally` block BEFORE emitting the rejection (never leave a rejected command having mutated instance state; if resume itself fails, `detail` records `left-paused`). The async task must never crash.

### O10. PINNED (2026-08-31): Registry on `CommandDispatcher`; per-instance active slot + daemon-wide terminal ring LRU ≤ 20, TTL = `ttl_seconds` (600 default); keyed by `instance_id`
- **Evidence:** the GET `/commands/active` endpoint needs an authoritative source; FE instance-switching must not lose mid-command state; TTL bounds the memory window.
- **Resolution:** the registry is owned by `CommandDispatcher` (`daemon/services/command_dispatcher.py`); one active slot per instance + a daemon-wide terminal ring LRU ≤ 20, TTL = `ttl_seconds` (600 default — mirrors the ack field). Eviction triggers: terminal event, TTL expiry, instance delete/terminate (mirrors the existing `_pending_injections` cleanup path). **Keyed by `instance_id` (NOT FE session)** so FE instance-switching loses nothing — FE re-syncs via `GET /commands/active` on re-mount.

### O11. PINNED (2026-08-31): Executor resolves model via manager session-model accessor; global fallback WARNING-logged
- **Evidence:** proactive path passes GLOBAL `config.llm.model` (instance_messaging.py:1160); per-model windows via `context_window_overrides` (config.py:715-749); the summarization LLM already resolves session model (compaction.py:997-1008).
- **Resolution:** the executor resolves the instance's session model via the manager session-model accessor — **the same source the summarization LLM client already uses (compaction.py:997-1008)** — then layers `context_window_overrides` (config.py:715-749). Global `config.llm.model` is used ONLY as a **WARNING-logged** fallback (never silent) so window/floor math is auditable; the warning carries `instance_id` + resolved window.

### O12. PINNED (2026-08-31): `GET /api/instances/{id}/commands/active` in `daemon/routers/instances.py`; auth mirrors `GET /messages`; mounted unconditionally; uniform `{exists:false}` when disabled
- **Evidence:** GET `/messages` lives in instance-scoped routing; the FE contract should not change shape across config flips.
- **Resolution:** endpoint lives in `daemon/routers/instances.py` (instance-scoped state); auth mirrors `GET /messages`; **mounted unconditionally** — with `slash_commands.enabled=false` it returns uniform 200 `{exists:false}` (FE contract invariant across config flips; no route-surface change when disabled).

### O13. PINNED (2026-08-31): 400 `UNKNOWN_COMMAND` ADDITIVE over `messages.py:222-229` envelope; `detail:{available:[...]}` is the new field
- **Evidence:** `messages.py:222-229` already returns `{code, message}` for parse-time errors; FE toasts on `code`; future slash autocomplete will want an `available` list.
- **Resolution:** keep the existing `:222-229` `{code, message}` envelope; add `code:"UNKNOWN_COMMAND"` + NEW `detail:{available:[...]}` field. FE toasts on `code`; `detail.available` later feeds slash autocomplete without a contract change. **ADDITIVE, not a mirror.**

### Reconciliation note (2026-08-31)
§9 plan-change list in `architecture-recommendation.md`: item 2 (per-chunk preservation) and item 4 (marker) are **superseded** by C1 above; item 6 (WS-5 normative schema) is **amended** by the §7 amendment text (now in phase1 WS-5 and phase2 Task 6). All other §9 items stand.

Companion tech-question verdicts (technical-analysis.md Q1–Q9, architect §8): Q1 seam adopted · Q2 availability adopted with matrix corrections · Q3 pure async · Q4 pause-first + quiescence_timeout · Q5 🔨 DECIDED (see REVISED D-B5) · Q6 🔨 DECIDED (see REVISED D-B1) · Q7 adopted with 2 corrections (see REVISED D-B4) · Q8 dispatcher in `daemon/services/command_dispatcher.py` mirroring `daemon/sources/registry.py:47-159` · Q9 6 phases + 10s heartbeat upgraded with `phase_seq`/`elapsed_ms`/`eta_ms`/`ttl_seconds` (normative §7).

### Open verification tasks (not open decisions — tracked work)

- **V-1 (🟡)** Verify ExecutionGate is RELEASED when `pause_instance_cascade` cancels the graph task, AND that ALL resume entry points (`is_retry=True`) re-acquire through the gate — worker D could not fully verify resume-path gate coverage. WS-6 exit criterion (phase1-plan.md).
- **V-2 (🟡)** Load-check tenacity facade behavior at ~305s wall clock (retry semantics at high caps read but not load-tested — worker C unverified item). WS-6 exit criterion.
- **Revive-brick regression test (🔴, now task 2.5)** — no test currently pins the brick mode itself (`aupdate_state` on `next=()` checkpoint → `astream` instant-return); add one so the terminal guard's load-bearing status is testable.
- 🟢 tracked (risk register, phase1-plan.md): noop-floor 5% tuning guess; SSE keepalive-on-idle unverified; GET-fallback auth must mirror `GET /messages`; rate-limit rapid-click race needs an integration test.
