# Plan Overview: Watchover Feature

Date: 2026-08-05
Author: planner[v2] via plan-creation worker
Status: Synthesized (all 4 planning workers complete; plan + technical-analysis reconciled)

## Objective

Add a per-instance "watcher" capability that intercepts every tool call from a
watched instance (DevOps-first) BEFORE the ToolNode executes, evaluates it via
a lightweight LLM call against a user-defined requirement, and terminates the
instance after 3 denials in a single turn — giving operators a safety net over
autonomous infrastructure agents.

## Key Architecture Decisions (Resolved)

| # | Decision | Resolution | Evidence |
|---|----------|------------|----------|
| AD-1 | Interception point | **New `watchover_check` conditional-edge node between `agent` and `tools`**, mirroring the existing `create_post_tools_router` pattern (`graph.py:3056`). Not a slot, not a post-tools hook — the watcher must see the tool call before execution. | `graph.py:3317-3416` wiring; `create_post_tools_router` `graph.py:3056-3097` |
| AD-2 | Watcher invocation | **Lightweight single LLM call inside the graph node**, NOT a full instance spawn. Reuses the `LoopRepairer.repair()` pattern (`graph.py:1024-1174`): `asyncio.to_thread` + `asyncio.wait_for` timeout guard, returns Allow/Deny verdict. The watcher's `agents/watcher/soul.md` serves as the system prompt. | `graph.py:1024-1174` |
| AD-3 | 3-strikes termination | **Deferred marker (Option B)**: the node sets `_deferred_watchover_terminate(instance_id)`, routes to END; the actual `terminate_instance` cascade runs from the post-graph completion path — identical to the C2 fix for `question_pause_node` (`graph.py:3142-3200`). Direct cascade would self-cancel the graph task and produce a torn DB state. | `graph.py:3142-3200`; C2 DB torn-state fix critical note |
| AD-4 | State storage | **`instance_metadata` JSONB** (`instance/models.py:63-66`) for `watchover_enabled` + `watchover_context` + `watchover_requirement`. No schema migration for flags. **LangGraph state keys** for the per-turn `watchover_denial_count`. **`SuspensionReason.WATCHOVER_SETUP` is optional for phase 1** — reuse `PAUSED_EXTERNAL` (suspension_reason is a TEXT/VARCHAR column, NOT a PostgreSQL native enum, so no `ALTER TYPE` is needed; if added, just append a Python enum member). See Technical Reconciliation §1. | `instance/models.py:63-66`; `task/models.py:52-60`; `technical-analysis.md` §C3 |
| AD-5 | Watcher agent | **Lightweight invocation, NOT a spawned instance.** `agents/watcher/` exists for prompt + metadata (model config), but the watchover node loads `soul.md` as the system prompt and makes a single LLM call. `team_members: []`, `tools: []`, `innate_skills: []`. | `agents/devops/meta.json` pattern |
| AD-6 | Failure handling | **Bifurcated (LD-2 ACCEPTED)** — replaces the original uniform fail-closed. **Judgment errors** (malformed response, unparseable verdict, empty reason, tool-call validation, config invalid) → fail-CLOSED → Deny + count +1. **Infrastructure errors** (LLM timeout, provider 5xx, network exception) → fail-OPEN → Allow tool call + log + emit `watchover_event{status: "degraded"}` SSE (no count). Rationale: uniform fail-closed creates a self-DoS cascade (provider outage → mass termination of all watched instances). | `architecture-recommendation.md` CR-2, LD-2 |
| AD-7 | Children | **Phase-1 scope: parent only.** Watchover does not cascade to child instances. Child instances are independent (Gap #13). Future scope. | Requirements Gap #13 |
| AD-8 | Loop-breaker interaction | **Denial counter NOT reset by loop repair** (corrected from initial assumption). Denial ToolMessages marked `additional_kwargs.watchover_denial=true`; `LoopDetector.scan` excludes those pairs from loop detection. The third denial terminates before another repair pass can run. | Requirements Gap #15; `technical-analysis.md` §F2 |
| AD-9 | Parallel tool calls | **Deny-whole-batch (LD-1 ACCEPTED)** — replaces the original mixed-batch / `watchover_finalize_denials` design. Evaluate ALL calls in a batch independently; if ANY is denied, deny the ENTIRE batch (inject denial ToolMessages for all calls + "deferred" messages for allowed-but-not-executed; route back to `agent`). Eliminates the `watchover_finalize_denials` node, message-replacement machinery, and checkpoint/restart inconsistency surface. AC-EC.9 re-scoped: "each call independently evaluated; a denied call blocks its batch." | `architecture-recommendation.md` CR-1, LD-1 |
| AD-10 | Sensitive reads | **Watcher blocks critical-path reads (Decision #8).** The watcher denies read operations on sensitive paths (e.g., `cat /etc/shadow`, `.env` with secrets, `kubectl get secrets`), not just destructive writes. Documented in `agents/watcher/soul.md` as part of the security-auditor persona. The watcher evaluates the ACTION (verb classification per SC-2), not the JUSTIFICATION — arguments are untrusted. | Leader Decision #8; `architecture-recommendation.md` SC-2 |

## Scope

### In Scope
- `agents/watcher/` agent definition (prompt-based, not spawned)
- Pre-tools graph interception node (`watchover_check`) + `create_pre_tools_router`
- Lightweight LLM watcher invocation (Allow/Deny/Terminate)
- 3-strikes deferred termination (C2-safe marker pattern)
- ToolMessage injection on Deny (instance sees why it was blocked)
- `POST /instances/{id}/watchover` API endpoint (activate/deactivate)
- Activation lifecycle: pause → compaction → set flags → resume
- `SuspensionReason.WATCHOVER_SETUP` Python enum (pure-Python, NO SQL migration — TEXT/VARCHAR column, not PostgreSQL enum)
- Frontend Watchover button + toggle signal + localStorage + API call
- SSE events for denial / termination feedback
- **Deny-whole-batch parallel tool-call handling (LD-1)** — no mixed-batch message replacement
- **Bifurcated failure handling (LD-2)** — fail-open on infra errors, fail-closed on judgment errors
- **Sensitive-read blocking (Decision #8)** — watcher blocks critical-path reads, not just writes
- DevOps as the first integrated target agent
- Crash-recovery flag restore from `instance_metadata`

### Out of Scope
- **Cascading watchover to child instances** — Gap #13; children are independent in v1. Future scope.
- **Per-tool allow-lists / granular tool policies** — v1 evaluates every tool call against one requirement. Granular policies are a future enhancement.
- **Watchover for non-DevOps agents** — DevOps-first (Constraint C-3); the graph plumbing is agent-agnostic, but only DevOps is wired for v1.
- **Watcher as a spawned instance with its own team** — AD-5: lightweight LLM call only.
- **UI for editing the watchover requirement mid-session** — requirement is set at activation time only.
- **No PostgreSQL enum migration** — `suspension_reason` is TEXT/VARCHAR, not a native enum; no `ALTER TYPE` needed (works identically on SQLite and PostgreSQL)

## Phases

| Phase | Name | Objective | Tasks | Coupling | Status |
|-------|------|-----------|-------|----------|--------|
| 1 | Watcher Agent Definition & Core Graph Interception | Create `agents/watcher/` and insert the `watchover_check` node + pre-tools router into `build_instance_graph()`, wiring Allow/Deny/Terminate routing and denial-counter state keys. Includes topology invariant test (T1.0), **global kill-switch** (T1.0b), `watchover_turn_id` threading (T1.4b), dual-path crash-recovery fix (T1.9). | 12 | independent (foundation) | pending |
| 2 | Watcher Invocation & Decision Logic | Implement the lightweight LLM Allow/Deny call (LoopRepairer pattern), **bifurcated** failure handling (fail-open infra / fail-closed judgment per LD-2), ToolMessage injection on Deny, **deny-whole-batch** parallel calls (LD-1), 3-strikes deferred termination, **persistent termination marker** (TD-8), `terminal_reason` threading (TD-3/4), SSE cleanup ordering fix (TD-5). | 10 | tight with Phase 1 (shared router/node) | pending |
| 3 | Activation/Deactivation Lifecycle & API | Add `POST /watchover` endpoint, pause→quiescence→compaction→set-flags→resume sequence, watchover_context construction, `set_metadata_many` atomic helper (TD-7), `wait_for_instance_quiescent` (TD-2), raw-tail compaction fallback (TD-6), **full pause→disable→resume deactivation** (FR-14), in-flight limitation documentation (LD-4). No SQL migration (TEXT column). | 13 | tight with Phase 1 (flags) + loose with Phase 2 (uses denial node) | pending |
| 4 | Frontend Integration | Watchover button (LEFT of Think), signal + localStorage + onToggle handler, POST /watchover call, SSE denial/termination events, active styling, **Instance API schema fields** (TD-12). | 7 | loose with Phase 3 (consumes API) | pending |
| 5 | Edge Cases, Persistence & Hardening | Crash recovery (DB flag restore), compaction-during-watchover refresh, concurrent isolation, loop_breaker reset, parallel tool-call eval, test suite. | 7 | tight with Phases 1-2 (touches node + invocation) | pending |

## Phase Dependency Graph

```
Phase 1 (Graph Interception)
    │
    ├──► Phase 2 (Decision Logic)      [sequential — needs the node from P1]
    │        │
    │        └──► Phase 5 (Hardening)  [sequential — needs node + logic]
    │
    ├──► Phase 3 (Lifecycle & API)     [sequential — needs flags from P1; can start in parallel with P2 for API skeleton]
    │        │
    │        └──► Phase 4 (Frontend)   [sequential — needs the API from P3]
    │
    └──────────────────────────────────► Phase 5 [also depends on P3, P4]
```

**Parallelization:**
- Phase 2 and Phase 3 can be **partially parallelized** after Phase 1: the API skeleton (endpoint signature, request model) is independent of the LLM decision logic, but the compaction + flag-setting in Phase 3 depends on Phase 1's state keys.
- Phase 4 must wait for Phase 3's endpoint to exist.
- Phase 5 is the convergence phase — it hardens all prior work.

## Coupling Map

| | Phase 1 | Phase 2 | Phase 3 | Phase 4 | Phase 5 |
|---|---|---|---|---|---|
| Phase 1 | — | tight (shared node + router) | tight (shared state keys + flags) | independent | tight (hardens node) |
| Phase 2 | tight | — | loose (P3 activates the node P2 fills) | independent | tight (hardens logic) |
| Phase 3 | tight | loose | — | tight (P4 consumes P3 API) | loose (P5 hardens recovery) |
| Phase 4 | independent | independent | tight | — | loose (P5 adds SSE edge cases) |
| Phase 5 | tight | tight | loose | loose | — |

## Risks

| # | Risk | Impact | Likelihood | Mitigation |
|---|------|--------|------------|------------|
| 1 | **Graph topology change breaks existing flows** — inserting a new conditional edge between `agent` and `tools` affects every agent instance, not just watched ones. | High | Medium | Phase 1 routes non-watched instances through the new node with an instant Allow passthrough (reads `watchover_enabled` flag, returns `"tools"` immediately). Add regression tests for all existing graph paths in Phase 1. |
| 2 | **Lightweight LLM call adds latency to every tool call** — watched instances pay an extra LLM round-trip per tool call. | High | High | Use a cheap/fast model for the watcher (configured via `agents/watcher/meta.json`). Document the latency trade-off. Cap via `asyncio.wait_for` timeout (reuse LoopRepairer timeout). Phase 5 adds latency profiling. |
| 3 | **Deferred termination race condition** — the deferred marker (`_deferred_watchover_terminate`) may not fire if the graph task is cancelled by another mechanism (e.g. timeout monitor) before the post-graph completion path runs. **A RAM-only marker (copying `_deferred_question_pause`) leaves a crash window between graph END and the post-graph callback.** | High | Low | **Persist the termination intent in `instance_metadata.watchover_pending_termination`**, not RAM-only. Add a stale-marker sweep in startup recovery / `stale_task_recovery.py` (mirrors the existing 5-step sweep). The denial count is in LangGraph state, so crash-mid-turn resets it naturally. See Technical Reconciliation §2. |
| 4 | **Compaction during watchover invalidates `watchover_context`** — if the watched instance's context is compacted mid-turn, the watcher's reference summary becomes stale. | Medium | Medium | Phase 3: re-derive a lightweight context snapshot per turn (or per N tool calls) rather than freezing it at activation. Phase 5 hardens with a freshness check. |
| 5 | **Loop-breaker repair interacts with watchover** — a denial sends a ToolMessage back to `agent`, so repeated denials can look like a tool loop to LoopDetector. Two sub-risks: (a) LoopRepairer could remove denial evidence by rewriting messages; (b) counter reset on repair would be unfair. | Medium | Medium | **Corrected from initial assumption:** the denial counter must NOT be reset by loop repair (it's in SessionState, repair only touches `messages`). Instead: (a) mark denial ToolMessages with `additional_kwargs.watchover_denial=true`; (b) teach `LoopDetector.scan` to exclude those call/result pairs from loop detection; (c) the third denial terminates before another repair pass can run. See Technical Reconciliation §3 / `technical-analysis.md` §F2. |
| 6 | **No PostgreSQL `ALTER TYPE` needed for `WATCHOVER_SETUP`** — `SuspensionReason` is a Python `str, Enum` over a TEXT/VARCHAR column (`task/models.py:55-60`; migration `20260801_000001` line 35-45), not a native enum. Adding the enum member is pure-Python. Phase 1 can reuse `PAUSED_EXTERNAL` and skip the member entirely. *(Corrected from initial research — see Technical Reconciliation §1.)* | Low | Low | Resolved by technical-analysis §C3. No migration risk. |
| 7 | **Parallel tool calls** — if the LLM emits multiple tool calls in one turn. | Low | Low | **RESOLVED by LD-1 (deny-whole-batch):** evaluate ALL calls in batch independently; if ANY denied, deny entire batch (inject denial ToolMessages for all + "deferred" for allowed-but-not-executed; route back to agent). Eliminates the `watchover_finalize_denials` node and checkpoint/restart surface. AC-EC.9 re-scoped. |
| 8 | **Feature branch divergence** — Constraint C-2 requires `feature/watchover` branch with no merge to `latest`. Long-lived branches accumulate merge conflicts. | Medium | Medium | Keep the branch rebased on `latest` regularly. Minimize core changes (Constraint C-1) so the conflict surface is small. |
| 9 | **SSE cleanup ordering drops termination events (CR-4, TD-5)** — `terminate_instance` calls `cleanup_instance` (`instance_lifecycle.py:1289-1290`) BEFORE post-commit `stream_status_change` (`:1399-1408`), dropping the SSE connection before the termination event is delivered. Watchover terminations silently lost. | High | High | Phase 2 T2.8: reorder `cleanup_instance` to run AFTER `stream_status_change` + `watchover_event` emission in the terminate cascade. Small, safe change ensuring FR-23 reliability. |
| 10 | **`terminal_reason` hard-coded as 'aborted' (TD-3, TD-4)** — `instance_lifecycle.py:2935` hard-codes `terminal_reason='aborted'`; `_STATUS_CANONICAL_MAP` doesn't know `watchover_terminated`. Watchover terminations mislabeled + unified work API leaks non-canonical status. | High | High | Phase 2 T2.7: thread explicit `terminal_reason` through manager → lifecycle → `_terminate_instance_db_sync`; watched root gets `"watchover_terminated"`, descendants keep `"aborted"`; add `"watchover_terminated": "cancelled"` to `_STATUS_CANONICAL_MAP`. |

## Success Criteria

| # | Criterion | How to Measure | Threshold |
|---|-----------|----------------|-----------|
| 1 | Watched instance's tool calls are evaluated before execution | Instrument the `watchover_check` node; verify it fires on every tool call for watched instances and passes through for non-watched | 100% of watched tool calls intercepted; 0% overhead added to non-watched passthrough routing logic beyond the flag check |
| 2 | Allowed tool calls execute normally | End-to-end: activate watchover with a permissive requirement; DevOps runs `bash ls` → Allow → tool executes | Tool result visible in chat |
| 3 | Denied tool calls do not execute | End-to-end: activate watchover with a restrictive requirement; DevOps runs `bash rm -rf /` → Deny → ToolMessage injected, tool does not execute | No filesystem mutation; instance sees denial message |
| 4 | Instance terminates after 3 denials in a turn | End-to-end: trigger 3 denied tool calls in one turn → instance terminated via deferred cascade | Instance status = TERMINATED; cascade cleanup runs; no torn DB state |
| 5 | Non-watched instances are unaffected | Run the existing test suite (graph, instance lifecycle, message flow) on instances without watchover enabled | 0 regressions; all existing tests pass |
| 6 | Activation lifecycle works | Click Watchover button in FE → instance pauses → compaction runs → flags set → instance resumes → subsequent tool calls intercepted | Full sequence completes; instance resumes with watchover active |
| 7 | Crash recovery restores watchover state | Enable watchover, simulate crash (kill process), restart, send a tool call → watcher evaluates using restored `instance_metadata` flags | Watcher active after restart without manual re-activation |
| 8 | Watcher LLM call does not freeze the graph | Measure P99 latency of `watchover_check` node including the LLM call | < 5s P99 (configurable timeout) |

## Research Insights

The plan is grounded in the explorer's file:line citations, all of which were
verified against the actual codebase:

- **`create_post_tools_router`** (`graph.py:3056-3097`) — canonical conditional-edge
  interception pattern. `watchover_check` mirrors this but inserts BEFORE tools.
- **`create_should_continue`** (`graph.py:2239-2259`) — wrapper pattern for adding
  routing destinations to the agent node.
- **`LoopRepairer.repair()`** (`graph.py:1024-1174`) — canonical synchronous-LLM-in-node
  pattern via `asyncio.to_thread` + `asyncio.wait_for`. Reused for the watcher call.
- **`question_pause_node`** (`graph.py:3142-3200`) — deferred-marker pattern for
  graph-side cascades (C2 fix). Reused for 3-strikes termination.
- **`build_instance_graph()`** (`graph.py:3317-3416`) — graph wiring. The new node
  and router are added here.
- **`instance_metadata` JSONB** (`instance/models.py:63-66`) — no migration needed
  for flags.
- **`SuspensionReason`** (`task/models.py:52-60`) — new `WATCHOVER_SETUP` value.
- **`resume_target_turn_id`** (`task/models.py:140`) — exists for suspended turn tracking.
- **`ContextCompactor.compact_state()`** (`compaction.py:380-781`) — compaction entry, not
  graph-dependent. `CompactionContext` dataclass at `compaction.py:219-231`.
- **`terminate_instance`** (`manager.py:5303-5346`) → `_lifecycle_service.terminate_instance`.
- **`pause_instance_cascade`** (`manager.py:5348`), **`resume_processing_job`** (`manager.py:5382`).
- **Router endpoints** (`instances.py`) — pause `:527`, resume `:558`, answer `:618`,
  terminate `:434`. New `POST /watchover` follows the same pattern.
- **FE toggle pattern** (`chat.html:49-85`) — `header-right` container, Angular signal
  + localStorage + onToggle handler.

## Assumptions

1. The watcher LLM model is configured via `agents/watcher/meta.json` (Gap #3) —
   the `llm_config` available in the agent_node closure is used; if the watcher
   meta specifies a different model, a separate client is constructed.
2. Bifurcated failure handling (AD-6, LD-2) — infra errors (timeout/5xx/network) fail-open (Allow + degraded SSE, no count); judgment errors (malformed/unparseable) fail-closed (Deny + count toward 3-strike). A dead watcher must not mass-terminate via self-DoS.
3. Per-tool-call evaluation (Gap #8) — every tool call in a turn is evaluated
   individually, not batch-evaluated.
4. `watchover_context` is derived from `ContextCompactor` output + the user
   requirement, stored in `instance_metadata` at activation time, and optionally
   refreshed per-turn (Phase 5).
5. The feature lives on `feature/watchover` and is NOT merged to `latest` until
   reviewed (Constraint C-2).

## Open Questions

1. **Watcher model selection** — ✅ **RESOLVED (LD / W-13):** configurable via `agents/watcher/meta.json` with a "quick" fast/cheap default model suitable for per-tool-call evaluation. Runtime fallback chain: watcher `watchover.llm_model` → watched instance's resolved model → global default. Typed `WatchoverAgentConfig` added to the registry model. Phase 1 T1.1.
2. **Refresh interval for `watchover_context`** — ✅ **RESOLVED (W-11/OQ-2 → Phase 3):** `set_metadata_many` atomic helper is a Phase 3 task (T3.3b, TD-7). Context refresh defaults to per-turn in Phase 5, frozen at activation in Phase 3, with configurable interval.
3. **SSE event granularity** — per-denial event for real-time feedback (Phase 4). No change.
4. **`wait_for_instance_quiescent`** — ✅ **RESOLVED (OQ-1 → LD-4):** Phase 3 prerequisite but the in-flight tool-call limitation is documented as accepted (LD-4 ACCEPTED). NFR-15 marked "partially met (graph-boundary safe, not thread-safe)". Phase 3 T3.9.
5. **`watchover_turn_id` threading** — ✅ **RESOLVED (OQ-3 → Phase 1):** Phase 1 task T1.4b (W-6, SC-3). Thread `work_id` as `configurable.turn_id`; eager reset (no tool_calls) is primary, turn_id comparison is the crash-recovery safety net.
6. **`watchover_context` source (Gap #1)** — ✅ **RESOLVED:** user requirement (at activation time) + compaction summary of current instance state. Phase 3 T3.4 implements this.
7. **FR-27 authorization (TD-9)** — ✅ **RESOLVED (phase 1 descope):** manager-internal only for phase 1 — no cross-session authorization. The project has no instance-ownership primitive; full 403 cross-session rejection deferred to phase 2. See `phase3-plan.md` T3.7 descope note.

## Technical Reconciliation (Aggregator Synthesis)

> This section was synthesized by the planner (v2) after the technical-analysis
> worker (`technical-analysis.md`, ~745 lines) ran in parallel with the
> plan-creation worker and surfaced corrections and implementation-blocking debt
> that the per-phase plans above could not incorporate. It is the authoritative
> reconciliation layer between the plan and the deeper architecture analysis.
> **All implementation work should honor the corrections here over the per-phase
> files where they conflict.**

### Corrections to Research (verified against source)

1. **`SuspensionReason` is NOT a PostgreSQL enum — no `ALTER TYPE`.** It is a
   Python `str, Enum` (`task/models.py:55-60`) persisted to a nullable
   **TEXT/VARCHAR** column (`task/models.py:135-140`; migration
   `20260801_000001_task_turn_handles.sql:35-45`; PG ensure at
   `manager.py:3756-3765`). The initial research claim of a required
   `ALTER TYPE` migration was **wrong**. **Impact:** Phase 3's
   `SuspensionReason.WATCHOVER_SETUP` is a pure-Python enum addition with zero
   migration cost — or can be skipped entirely (reuse `PAUSED_EXTERNAL`).
   *(Resolves Risk #6.)*

2. **`manager.terminate_instance` is SOFT terminate, not hard-delete.** It
   delegates to `InstanceLifecycleService.terminate_instance`
   (`manager.py:5303-5318`), preserving the instance row and audit context.
   `hard_delete_instance` is a separate destructive API
   (`manager.py:5320-5346`). Watchover MUST use soft terminate. **Impact:** Phase
   2's termination must call `terminate_instance`, not `hard_delete_instance`.

3. **Loop-breaker interaction is NOT "reset the counter."** Denial ToolMessages
   must be marked and excluded from loop detection; the counter stays intact
   across repairs. *(Updated AD-8 and Risk #5 above.)*

### Implementation-Blocking Technical Debt (from `technical-analysis.md` §Tech Debt)

These existing-code debt items are **high severity and must be addressed during
implementation** (they are not optional polish):

| # | Debt | Blocks | Phase | Fix |
|---|------|--------|-------|-----|
| TD-1 | Both `agent` conditional-edge maps route `"tools"` directly to ToolNode — missing either is an NFR-12 bypass | NFR-12 (unbypassable) | 1 | Migrate BOTH maps (`graph.py:3350-3375`); add topology test |
| TD-2 | Pause cancels/pops graph task but does NOT await tool/graph quiescence | NFR-15/FR-28 (in-flight atomicity) | 3 | Add deferred boundary pause + `wait_for_instance_quiescent` barrier (`instance_lifecycle.py:1864-1886`) |
| TD-3 | Termination hard-codes `terminal_reason='aborted'` (`instance_lifecycle.py:2918-2948`) | Watchover termination reason | 2 | Thread explicit `terminal_reason` through manager/lifecycle/`_terminate_instance_db_sync`; watched root gets `"watchover_terminated"`, descendants keep `"aborted"` |
| TD-4 | `_STATUS_CANONICAL_MAP` (`work_status.py:102-156`) doesn't know `watchover_terminated` | Unified work API | 2 | Add `"watchover_terminated": "cancelled"` mapping |
| TD-5 | Terminate cleanup removes LiveEventHub connections BEFORE post-commit status SSE (`instance_lifecycle.py:1289-1290`, `1399-1408`) | FR-23 (termination event to FE) | 2 | Reorder cleanup or use persistent/global channel |
| TD-6 | `compact_state` returns `None` for short histories — no public "summarize snapshot" (`compaction.py:596-653`) | Activation on fresh instance | 3 | Add raw-tail fallback (AC-EC.7) |
| TD-7 | `set_metadata` writes one JSON key at a time (`instance/repository.py:782-845`) — partial watchover config on crash | NFR-5 (atomicity) | 3 | Add atomic multi-key `set_metadata_many` |
| TD-8 | Deferred question marker is RAM-only (`manager.py:730-739`) | Crash window for 3-strikes | 2/5 | Persist termination intent in `instance_metadata.watchover_pending_termination` |
| TD-9 | **No existing session-ownership authorization** on instance endpoints (`instances.py:526-608`) | FR-27 (authorization) | 3 | **Needs caller decision** — see Open Question #4 below |
| TD-10 | Spawn and restore construct graphs in separate call sites (`instance_lifecycle.py:956-988` + `2556-2586`) | Crash recovery (FR-26) | 1/5 | Thread `WatchoverSlot(manager)` through BOTH or restart bypasses watchover |
| TD-11 | ~~Mixed parallel batches need filtered AIMessage + post-tools denial finalization~~ — **ELIMINATED by LD-1 (deny-whole-batch).** No message replacement, no finalization node, no checkpoint/restart surface. | Parallel tool calls (Gap #8) | 2 | Test deny-whole-batch: any denied → all denied; all allowed → execute |
| TD-12 | Instance API + frontend model do not expose watchover state (`instances.py:350-430`; `frontend/src/app/models/index.ts:4-32`) | FE reliable state restore | 4 | Add watchover fields to Instance schema; don't rely on localStorage alone |

### Refined Architecture Decisions (from technical-analysis.md)

| Decision | Resolution |
|----------|------------|
| Interception | **Option A — new `watchover_check` node** (chosen over 3 alternatives). Closes NFR-12 trivially: `agent → watchover_check → tools` is the ONLY path to ToolNode. Reversibility: high (reverting removes the node, restores unconditional edge). |
| Parallel tool calls (Gap #8) | **Deny-whole-batch (LD-1 ACCEPTED)** — evaluate ALL calls in batch; if ANY denied, deny entire batch (inject denial ToolMessages for denied calls + "deferred" for allowed-but-not-executed; route back to agent). Eliminates the `watchover_finalize_denials` node and checkpoint/restart surface entirely. AC-EC.9 re-scoped. |
| Watcher invocation | Real `agents/watcher/` prompt via `load_and_cache_prompt` + `registry.get_version()/get_resolved()` (honoring the version-tag resolution critical note); invoked as fresh unbound `ThinkingChatOpenAI` via LoopRepairer `asyncio.wait_for(asyncio.to_thread(...))` pattern. Add typed `WatchoverAgentConfig` to `AgentMetadata`. |
| Model selection (Gap #3) | Fallback chain: watcher `llm_model` → watched instance model → global default. |
| Crash recovery | `WatchoverSlot` hydrates from `instance_metadata` on both spawn and restore call sites (TD-10); durable transition journal + persistent termination intent (TD-8). |

### Open Questions Requiring Caller Decision

| # | Question | Why it blocks |
|---|----------|---------------|
| OQ-1 | **`watchover_context` source** (Gap #1): user-provided requirement at activation + compaction (assumed), OR original instance prompt + compaction, OR both? | Determines Phase 3 context-build step |
| OQ-2 | **FR-27 authorization** (TD-9): the project has **no existing instance-session-ownership primitive**. Define "session owner" or descope FR-27 to "manager-internal only" for phase 1. | **Single biggest blocker** — may require a new ownership model |
| OQ-3 | **Watcher model default** (Gap #3): confirm fallback chain (watcher → watched → global) and whether a cheaper default model should ship |
| OQ-4 | **Sensitive reads** (Gap #16): should the watcher block read-only ops on critical paths (e.g. `cat /etc/shadow`)? Prompt-engineering decision for `agents/watcher/soul.md` |

### Files in the Plan Directory

| File | Author (worker) | Content |
|------|-----------------|---------|
| `requirements.md` | requirements-analysis | 31 FR (incl. FR-30/31 sensitive reads), 25 NFR (incl. NFR-25), 10 constraints, 16 gaps, acceptance criteria, edge cases |
| `technical-analysis.md` | technical-analysis | 745-line deep-dive: interception topology, watcher invocation, state/persistence, lifecycle, termination, edge cases F1–F5, trade-offs, 12 debt items. `watchover_finalize_denials` sections marked SUPERSEDED by LD-1. |
| `architecture-recommendation.md` | architect (council) | Council validation, CR-1 to CR-5, LD-1 to LD-5, 9 phase-1 simplifications |
| `approach-comparison.md` | architect (council) | 4-option interception comparison + failure/parallel/termination comparisons |
| `plan-overview.md` | plan-creation + **planner synthesis** | This file — 10 architecture decisions, scope, 5-phase table, dependency graph, coupling map, 10 risks, success criteria, research insights, reconciliation, leader-decision propagation log |
| `phase1-plan.md`–`phase5-plan.md` | plan-creation + **propagation fixes** | Per-phase tasks, files, dependencies, success criteria — all reconciled with LD-1 to LD-5 |

## Leader Decision Propagation Log (Reviewer Fix Pass, 2026-08-05)

> The Reviewer identified 9 critical propagation failures: phase plans written
> BEFORE the leader decisions (LD-1 to LD-5) and architect recommendations were
> confirmed. This section records the propagation of each decision into the plan
> files. **All 9 critical issues + 7 key warnings resolved.**

| Issue | Decision | Files Fixed | Status |
|-------|----------|-------------|--------|
| 🔴-1 | LD-2 bifurcated failure (fail-open infra / fail-closed judgment) | `plan-overview.md` AD-6; `phase2-plan.md` T2.3, P2-R2, exit criterion | ✅ |
| 🔴-2 | CR-4 SSE cleanup ordering (reorder after post-commit events) | `phase2-plan.md` new T2.8; `plan-overview.md` Risk #9 | ✅ |
| 🔴-3 | AD-8 loop-breaker (mark+exclude, NOT reset counter) | `phase5-plan.md` M5.2, T5.2, exit criterion, reuse callout | ✅ |
| 🔴-4 | LD-1 deny-whole-batch (eliminate `watchover_finalize_denials`) | `plan-overview.md` AD-9, Risk #7, In-Scope; `phase2-plan.md` P2-R4; `technical-analysis.md` supersession notes | ✅ |
| 🔴-5 | Decision #8 sensitive reads (block critical-path reads) | `plan-overview.md` AD-10, In-Scope; `phase1-plan.md` T1.2; `requirements.md` FR-30/31, NFR-25 | ✅ |
| 🔴-6 | TD-3/4 `terminal_reason` threading | `phase2-plan.md` new T2.7; `plan-overview.md` Risk #10 | ✅ |
| 🔴-7 | TD-10 dual graph construction paths | `phase1-plan.md` new T1.9 | ✅ |
| 🔴-8 | Invalid ALTER TYPE migration | `phase3-plan.md` C3.1/T3.2/P3-R3 deleted; `plan-overview.md` AD-4, In-Scope | ✅ |
| 🔴-9 | FR-14 deactivation pause→disable→resume | `phase3-plan.md` T3.6, exit criterion | ✅ |
| W-10 | Topology test as T1.0 | `phase1-plan.md` | ✅ |
| W-6 | `watchover_turn_id` threading | `phase1-plan.md` T1.4b; `plan-overview.md` OQ-5 | ✅ |
| W-13 | Watcher model default + fallback chain | `phase1-plan.md` T1.1; `plan-overview.md` OQ-1 | ✅ |
| W-5 | `set_metadata_many` atomic helper | `phase3-plan.md` T3.3b; `plan-overview.md` OQ-2 | ✅ |
| W-8 | try/except rollback in T3.5 | `phase3-plan.md` T3.5 | ✅ |
| W-9 | In-flight limitation documentation | `phase3-plan.md` T3.9 | ✅ |
| W-11 | OQ resolution propagation | `plan-overview.md` OQ-1/2/4/5/6/7; `architecture-recommendation.md`; `requirements.md`; `technical-analysis.md` | ✅ |
| LD-A | Global kill-switch (WATCHOVER_ENABLED env) | `phase1-plan.md` T1.0b | ✅ |
| LD-B | Denial counter agent-visibility NFR | `requirements.md` NFR-26 | ✅ |
| LD-C | FR-27 phase 2 aspirational marking | `requirements.md` FR-27; `plan-overview.md` OQ-7 | ✅ |
| LD-D | compact() → compact_state() naming | `phase3-plan.md`, `phase5-plan.md`, `technical-analysis.md`, `plan-overview.md` | ✅ |
