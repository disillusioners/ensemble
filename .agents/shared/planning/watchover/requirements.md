# Requirements: Watchover Feature

Date: 2026-08-05T20:03:59 UTC
Author: planner[v2] via requirements-analysis worker
Status: Draft
Source Request: New "watcher" agent that monitors other agents (especially DevOps) to prevent destructive actions. Per-instance activation via FE button. Intercepts each tool call → Allow/Deny. 3 denials in a turn → terminate instance.

## Stakeholders

- **Requester:** User (system owner / operator)
- **Affected users:** End users running instances (especially DevOps workflows); users monitoring long-running agent tasks
- **Affected systems:**
  - agents-ensemble daemon (graph, job queue, services, instance lifecycle)
  - agents/{name}/ agent definitions (new `watcher` agent)
  - Frontend (chat header — new Watchover button)
  - PostgreSQL (new tables/columns for watchover state)
  - LangGraph state schema (denial counter, watchover_context, watchover_enabled flag)
  - SSE event surface (denial/termination events to FE)

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | Watchover is OFF by default for every instance | Safety default — no implicit surveillance; user opt-in only | Must | Activation |
| FR-2 | User activates watchover per instance via FE button | Explicit, manual control | Must | Activation |
| FR-3 | Activation sequence: pause instance/job → build watchover_context → enable interception node → resume | Safe-state transition; no mid-flight tool call escapes interception | Must | Activation |
| FR-4 | watchover_context = user requirement + compaction of current instance state | Watcher needs full picture to evaluate destructive intent | Must | Activation |
| FR-5 | Each new tool call from the watched agent is intercepted BEFORE ToolNode execution | Destructive side effects must not occur before evaluation | Must | Interception |
| FR-6 | Watcher receives: system message + watchover_context + mirrored instance messages + the new tool call | Sufficient context for the Allow/Deny decision | Must | Interception |
| FR-7 | Watcher returns Allow or Deny (with optional reason) | Binary decision + UX support | Must | Interception |
| FR-8 | On Allow: tool call proceeds to ToolNode unchanged | No-op for safe calls; preserve existing tool execution path | Must | Interception |
| FR-9 | On Deny: tool call is rejected, agent receives a ToolMessage indicating denial + reason, asked to correct | Agent retains agency to try a safer alternative | Must | Interception |
| FR-10 | Per-turn denial counter increments on each Deny; resets when LLM returns without tool_calls | "Turn" = one LLM cycle with tool calls; reset at natural boundary | Must | Termination |
| FR-11 | When denial counter reaches 3 within a turn, the watcher terminates the instance/job cleanly | Hard cap on destructive attempts within a turn | Must | Termination |
| FR-12 | Termination is clean: current job cancelled via existing job_state_machine; instance marked with terminal_reason="watchover_terminated" | No orphans; distinguishable from normal death | Must | Termination |
| FR-13 | User can deactivate watchover via FE button | Reversible activation | Must | Deactivation |
| FR-14 | Deactivation sequence: pause instance/job → disable interception node → resume | Safe-state transition | Must | Deactivation |
| FR-15 | Watchover enabled flag persists in DB per instance | Survives pause/resume/restart | Must | Persistence |
| FR-16 | Watchover state is per-instance; multiple instances independent | One instance activation doesn't affect others | Must | Persistence |
| FR-17 | Watcher is a new agent: `agents/watcher/` with meta.json + soul.md + rule.md + workflow.md + tools_note.md | Follows existing agent pattern; auto-registered | Must | Watcher Identity |
| FR-18 | Watcher is invoked via lightweight LLM call (not full `spawn_instance`) | Cost/context efficiency — full spawn is too heavy for per-tool-call evaluation | Must | Watcher Identity |
| FR-19 | Watchover enabled flag appears in FE; user can toggle | Visibility into state | Must | FE |
| FR-20 | Watchover button is positioned LEFT of the thinking visibility toggle button in chat header | Spatial placement requirement | Must | FE |
| FR-21 | When watchover is active, button reflects active state (color/styling consistent with existing toggle pattern: Think=amber, Tools=blue, System=green) | UX consistency | Should | FE |
| FR-22 | Denial events surface to user in chat (visible message indicating which tool was denied + reason + counter) | User awareness of watcher activity | Must | Feedback |
| FR-23 | Termination event surfaces to user in chat (instance terminated by watchover + denial count) | User awareness of outcome | Must | Feedback |
| FR-24 | Activation event surfaces to user in chat (watchover now active) | User awareness of state change | Should | Feedback |
| FR-25 | Compaction during active watchover rebuilds watchover_context with the latest summary | Watcher context stays current after long conversations | Should | Lifecycle |
| FR-26 | Crash recovery restores watchover enabled state from DB | Resume consistency after daemon restart | Must | Persistence |
| FR-27 | Authorization check on watchover toggle endpoint — **Phase 1: manager-internal only (no cross-session 403). Phase 2 (full cross-session authorization): ASPIRATIONAL, NOT committed for this feature.** | Security — no cross-session toggling | Must (phase 1: descope) | Security |
| FR-28 | Tool calls during activation sequence (between pause and resume) are deferred until resume, not lost | Pause does not drop in-flight tool calls | Must | Lifecycle |
| FR-29 | Watchover denial is logged with: instance_id, tool name, denial reason, denial counter value, turn id | Audit trail | Should | Feedback |
| FR-30 | The watcher blocks critical-path READ operations that could expose sensitive data or credentials (e.g., `cat /etc/shadow`, reading `.env` files with secrets, `kubectl get secrets`) | Sensitive data exposure is a destructive-equivalent risk even without writes | Must | Interception |
| FR-31 | The watcher's stance on sensitive reads is documented in `agents/watcher/soul.md` as part of the security-auditor persona | Operator clarity on what the watcher blocks | Must | Watcher Identity |

### Theme: Activation & Deactivation Lifecycle

**FR-3:** Activation sequence (pause → build context → enable → resume)
- **Rationale:** Cannot toggle interception node safely while instance is in mid-flight; pause ensures clean state
- **Priority:** Must
- **Notes:** Reuses existing `pause_instance_cascade` / `resume_instance_cascade` / `resume_processing_job` pattern from `daemon/manager.py` (~5348). Interception node read from instance state, not global config.

**FR-14:** Deactivation sequence
- **Rationale:** Symmetric with activation; same safety requirements
- **Priority:** Must
- **Notes:** Same pause/resume pattern; flag flip happens during pause window; resume proceeds with interception node disabled.

### Theme: Interception & Evaluation

**FR-5:** Per-tool-call interception before ToolNode execution
- **Rationale:** Destructive side effects must not occur before evaluation
- **Priority:** Must
- **Notes:** Implemented as new slot in graph between `agent_node` (when tool_calls returned) and `tools_condition`. Reuses explicit slot threading pattern (`injection_slot`, `report_injection_slot`, `throttle_slot`, `loop_breaker_slot`) — NOT a new generic middleware framework.

**FR-9:** On Deny — reject tool call, inject correction message
- **Rationale:** Agent retains ability to recover (try safer alternative, ask user, etc.)
- **Priority:** Must
- **Notes:** Rejection injected as `ToolMessage(role="tool", content="Watchover denied: <reason>. Please correct and try again.")`. Routes back to `agent_node`. Counter incremented before injection.

### Theme: Denial Counting & Termination

**FR-10:** Per-turn denial counter
- **Rationale:** "Turn" matches natural agent lifecycle; counter resets on LLM no-tool-call return (turn boundary)
- **Priority:** Must
- **Notes:** Counter stored in LangGraph state (instance-level), not in DB; resets at turn boundary (when `agent_node` LLM returns without `tool_calls`)

**FR-11:** 3 denials → terminate
- **Rationale:** Hard cap prevents infinite denial loop within a turn
- **Priority:** Must
- **Notes:** Termination uses existing job cancellation + instance finalize with `terminal_reason="watchover_terminated"`. Reuses `job_state_machine.py` AdmissionState transitions.

**FR-12:** Clean termination
- **Rationale:** No orphaned jobs or zombie instances
- **Priority:** Must
- **Notes:** Cancel current job, finalize instance, emit SSE termination event. Distinct from `DEAD` admission state — uses `watchover_terminated` reason.

### Theme: FE Integration

**FR-20:** Button placement (LEFT of thinking toggle)
- **Rationale:** Spatial convention per user requirement
- **Priority:** Must
- **Notes:** `header-right` container; new button before existing thinking toggle; follows Angular signal + localStorage + onToggleX() pattern

**FR-21:** Active styling
- **Rationale:** UX consistency with existing toggles
- **Priority:** Should
- **Notes:** Suggest purple or another distinct color to avoid confusion with amber/blue/green.

### Theme: Feedback & Visibility

**FR-22 / FR-23 / FR-24:** Events in chat
- **Rationale:** User must see what watcher is doing
- **Priority:** Must (denial + termination) / Should (activation)
- **Notes:** Uses existing SSE event surface; renders as assistant/system messages in chat.

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Performance | Per-tool-call interception added latency | Wall-clock time added to a single tool call (watcher LLM roundtrip + overhead) | ≤ 2× LLM roundtrip + 1s overhead | Bench on DevOps instance with watchover active; log interception durations in telemetry |
| NFR-2 | Performance | Activation sequence completes in reasonable time | Pause→build→enable→resume wall-clock | ≤ 30s typical, ≤ 60s worst case | Telemetry on activation duration |
| NFR-3 | Performance | Non-watched instances unaffected | Latency/throughput parity with pre-feature | No measurable regression (≤ 5% on p95 latency) | Compare baseline vs post-feature metrics on non-watched instances |
| NFR-4 | Reliability | Watcher LLM error/timeout handling (LD-2 bifurcated) | Behavior on watcher LLM failure | **Bifurcated:** infrastructure errors (timeout, 5xx, network) → fail-OPEN (Allow + log + degraded SSE, no count); judgment errors (malformed, unparseable, config invalid) → fail-CLOSED (Deny + count) | Inject LLM timeout → verify Allow (infra); inject malformed response → verify Deny + count (judgment); 3 judgment errors → terminate |
| NFR-5 | Reliability | Pause/resume atomicity for activation | No torn states (partially activated) | Always either fully on or fully off | Crash test mid-activation sequence; verify clean state on recovery |
| NFR-6 | Reliability | Watchover state survives daemon restart | DB-backed watchover flag | 100% restored | Restart daemon mid-session with watchover active; verify still active after instance resume |
| NFR-7 | Reliability | Compaction-during-watchover consistency | watchover_context rebuilds on compaction trigger | 100% updated within one turn after compaction | Compaction trigger + next tool call interception |
| NFR-8 | Security | Watched agent cannot disable watchover | No tool in any watched agent's toolset can toggle watchover | Static check + runtime guard | Grep agents/ for any watchover mutation capability; add runtime check in toggle endpoint |
| NFR-9 | Security | Session-owner authorization (phase-1 descope) | Toggle endpoint authorization | **Phase 1: manager-internal only, no cross-session 403 check.** Phase 2: full session-owner authorization pending instance-ownership primitive (TD-9) | Phase 1: document manager-internal descope; Phase 2: integration test with cross-session toggle attempt |
| NFR-10 | Security | Watcher prompt/context cannot be modified by watched agent | No tool exposed to watched agent that mutates watchover_context or watcher soul | 100% immutable to watched agent | Static + runtime check; isolate watcher prompt storage from agent-accessible paths |
| NFR-11 | Security | Denial counter cannot be tampered with by watched agent | Counter lives in graph state, not in agent-visible context | 100% agent-invisible | Static analysis of state schema; agent cannot read or write counter |
| NFR-12 | Security | Watcher LLM invocation cannot be skipped | No code path from `agent_node` tool_calls to `ToolNode` that bypasses interception slot | 100% coverage | Graph topology test; static analysis |
| NFR-13 | Concurrency | Multiple instances independent | Per-instance watchover flag in DB | 100% isolation | Integration test: instance A on, instance B off, instance C toggling |
| NFR-14 | Concurrency | Activation on instance A doesn't affect instance B | No global lock held | Concurrent activations on different instances complete without contention | Load test 10 concurrent activations on different instances |
| NFR-15 | Concurrency | Activation while tool call in flight is atomic | Tool call either fully evaluated before activation OR fully after | Zero mid-flight tool calls bypass | Race condition test; pause guarantees atomicity |
| NFR-16 | Usability | Denial reason visible to user | Reason text in chat | 100% denials have reason (or "no reason provided" fallback) | UI inspection |
| NFR-17 | Usability | Watchover active state visually distinct | Button styling distinct from OFF state | Matches existing toggle pattern with distinct color | UI inspection |
| NFR-18 | Usability | Activation/deactivation progress feedback | UI shows intermediate state during pause→enable→resume sequence | Spinner/loading indicator visible | UI inspection |
| NFR-19 | Maintainability | Reuses existing graph slot pattern | New interception added as new slot, no new middleware framework | Code review pass | Diff inspection against `daemon/graph.py` |
| NFR-20 | Maintainability | Reuses existing pause/resume cascade | Activation/deactivation flows through `pause_instance_cascade` / `resume_instance_cascade` / `resume_processing_job` | Code review pass | Diff inspection against `daemon/manager.py` |
| NFR-21 | Maintainability | Reuses existing FE toggle pattern | Watchover button follows Angular signal + localStorage + onToggleX() | Code review pass | Diff inspection |
| NFR-22 | Maintainability | Watcher agent follows agent prompt writing guide | `agents/watcher/` conforms to `docs/agent-prompt-writing-guide.md` | Code review pass | Diff inspection |
| NFR-23 | Maintainability | DB migrations follow PostgreSQL pattern | New tables/columns use `_ensure_postgres_columns()` | Code review pass | Migration diff inspection |
| NFR-24 | Observability | All watchover events logged | Structured logs for: activation, deactivation, denial, termination | 100% event coverage | Log inspection |
| NFR-25 | Security | Critical-path reads (sensitive files, secrets) are blocked by the watcher, not just destructive writes | Watcher denies read ops on critical paths (e.g., /etc/shadow, .env files with secrets) | 100% of critical-path reads blocked | Integration test: DevOps runs `cat /etc/shadow` → Deny |
| NFR-26 | Security | Denial counter is user-facing only, NEVER injected into agent messages | Counter visible in SSE events + UI only; NEVER in ToolMessage content or any agent-visible context | 100% — counter absent from all agent-visible message content | Integration test: inspect all messages sent to watched agent → counter value is never present; inspect SSE events → counter IS present |

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | Minimize changes to core system (LangGraph, job queue) — reuse existing patterns | User | No new middleware framework; reuse slot threading + cascade pause/resume |
| C-2 | Workflow | New branch `feature/watchover` (already created) — do NOT merge to latest after done | User | PR targets `feature/watchover` branch; merge decision deferred to user |
| C-3 | Scope | First phase focused on DevOps instance usage | User | Other instance types (coder, planner, etc.) may not have full feature parity in phase 1 |
| C-4 | Architecture | Agent definitions follow `agents/{name}/` pattern with meta.json + soul.md + rule.md + workflow.md + tools_note.md | Project convention | Watcher agent follows this structure exactly |
| C-5 | Architecture | Interception must reuse explicit slot threading, NOT introduce new generic middleware | Project pattern (`daemon/graph.py`) | New slot defined alongside `injection_slot`, `report_injection_slot`, `throttle_slot`, `loop_breaker_slot` |
| C-6 | Architecture | Pause/resume must reuse deferred cascade pattern (graph sets marker, post-graph callback calls cascade outside task) | Project pattern (`daemon/manager.py` ~5348) | Activation/deactivation flow through `pause_instance_cascade` / `resume_instance_cascade` / `resume_processing_job` |
| C-7 | Architecture | PostgreSQL is primary DB; new tables/columns must use `_ensure_postgres_columns()` for existing tables | Project constraint | New watchover state tables go through PostgreSQL column-ensure path |
| C-8 | Frontend | Frontend is Angular 21; chat header is `header-right` container with toggle buttons | Project stack | Watchover button lives in same container as Think/Tools/System toggles, follows Angular signal + localStorage + onToggleX() pattern |
| C-9 | Frontend | Button placement is LEFT of the thinking toggle (spatial requirement) | User | DOM order: Watchover button rendered before Thinking button |
| C-10 | Architecture | Job lifecycle uses 4-value AdmissionState (QUEUED/ACTIVE/DONE/DEAD); new terminal_reason values are allowed | Project pattern (`daemon/services/job_state_machine.py`) | Termination uses new `terminal_reason="watchover_terminated"`, not a new AdmissionState |

## Acceptance Criteria

### FR-1: Default OFF for all instances

**AC-1.1** (happy path)
- **Given:** A fresh instance starts with no prior watchover state
- **When:** Instance begins processing
- **Then:** Watchover is OFF; no interception occurs; tool calls execute normally
- **Test type:** integration

**AC-1.2** (persistence check)
- **Given:** Instance with watchover OFF
- **When:** Instance is queried for watchover state
- **Then:** DB row shows watchover_enabled=false (or absent if lazy-created)
- **Test type:** integration

### FR-2 / FR-3 / FR-4: Activation Sequence

**AC-2.1** (happy path)
- **Given:** Instance is running, watchover OFF
- **When:** User clicks Watchover button
- **Then:** Sequence runs (pause → build watchover_context → enable interception → resume) without errors
- **Test type:** e2e

**AC-2.2** (context built correctly)
- **Given:** Instance has user requirement + conversation history
- **When:** Activation sequence builds watchover_context
- **Then:** Context contains user requirement text + compaction summary of current state
- **Test type:** integration

**AC-2.3** (resume after activation)
- **Given:** Activation sequence completes
- **When:** Instance resumes processing
- **Then:** Instance continues with watchover active; tool calls now intercepted
- **Test type:** e2e

**AC-2.4** (in-flight tool calls handled)
- **Given:** Instance mid-tool-call when activation begins
- **When:** Activation sequence runs
- **Then:** In-flight tool call completes (Allow by default since not yet intercepted) OR is deferred until resume; no tool call lost
- **Test type:** integration

### FR-5 / FR-6 / FR-7: Interception & Evaluation

**AC-5.1** (Allow path)
- **Given:** Watchover active
- **When:** Agent makes a non-destructive tool call (e.g., read_file)
- **Then:** Watcher returns Allow; tool executes normally; no latency regression vs non-watched baseline
- **Test type:** integration

**AC-5.2** (Deny path)
- **Given:** Watchover active
- **When:** Agent makes a destructive tool call (e.g., bash with `rm -rf`, sql with DROP TABLE)
- **Then:** Watcher returns Deny with reason; tool does NOT execute; agent receives ToolMessage with denial reason; counter increments
- **Test type:** integration

**AC-5.3** (interception timing)
- **Given:** Watchover active
- **When:** Agent returns tool_calls from LLM
- **Then:** Interception occurs BEFORE ToolNode executes (verified by graph trace + execution log)
- **Test type:** unit

**AC-6.1** (watcher invocation)
- **Given:** Interception triggered
- **When:** Watcher LLM is called
- **Then:** Watcher receives (a) system message, (b) watchover_context, (c) mirrored instance messages, (d) the new tool call — verified by inspecting the watcher prompt
- **Test type:** integration

### FR-9: On Deny — reject and correct

**AC-9.1** (correction opportunity)
- **Given:** Tool call denied
- **When:** Agent receives denial ToolMessage
- **Then:** Agent can produce a new tool call (or text response); denial counter increments
- **Test type:** integration

**AC-9.2** (correction visible to user)
- **Given:** Tool call denied
- **When:** Denial is injected
- **Then:** Chat shows a message indicating which tool was denied + reason + current counter (e.g., "Watchover denied read_file (attempt 1/3): <reason>")
- **Test type:** e2e

### FR-10 / FR-11 / FR-12: Denial Counting & Termination

**AC-10.1** (counter increments on Deny)
- **Given:** Watchover active, denial counter at 0
- **When:** First Deny in turn
- **Then:** Counter = 1
- **Test type:** unit

**AC-10.2** (counter resets on turn end)
- **Given:** Counter at 2 in current turn
- **When:** LLM returns without tool_calls (turn end)
- **Then:** Counter resets to 0 on next turn
- **Test type:** unit

**AC-11.1** (3-strikes terminates)
- **Given:** Counter at 2 in current turn
- **When:** Third Deny occurs
- **Then:** Instance terminates cleanly; terminal_reason="watchover_terminated"; job cancelled; no orphans
- **Test type:** e2e

**AC-12.1** (termination visible)
- **Given:** Termination triggered
- **When:** User views chat
- **Then:** Chat shows termination message with denial count + reason (e.g., "Instance terminated by watchover after 3 denials in this turn")
- **Test type:** e2e

### FR-13 / FR-14: Deactivation

**AC-13.1** (deactivation happy path)
- **Given:** Watchover active on instance
- **When:** User clicks Watchover button to deactivate
- **Then:** Sequence runs (pause → disable interception → resume); watchover flag in DB updated to OFF
- **Test type:** e2e

**AC-13.2** (post-deactivation behavior)
- **Given:** Watchover just deactivated
- **When:** Agent makes tool calls
- **Then:** No interception occurs; tool calls execute normally; FE button shows OFF state
- **Test type:** integration

### FR-15 / FR-16: State Persistence

**AC-15.1** (DB persistence)
- **Given:** Instance with watchover active
- **When:** Instance is queried for state
- **Then:** DB shows watchover_enabled=true; watchover_context stored alongside
- **Test type:** integration

**AC-16.1** (per-instance isolation)
- **Given:** Instance A watchover active, Instance B watchover OFF
- **When:** Activation/deactivation on either
- **Then:** Other instance unaffected (DB flag independent; graph state independent)
- **Test type:** integration

### FR-17 / FR-18: Watcher Identity

**AC-17.1** (agent follows convention)
- **Given:** `agents/watcher/` directory exists with meta.json + soul.md + rule.md + workflow.md + tools_note.md
- **When:** Daemon starts
- **Then:** Watcher agent auto-registered via AgentRegistry.discover(); tools/prompt loaded per meta.json
- **Test type:** unit

**AC-18.1** (lightweight invocation)
- **Given:** Watchover active
- **When:** Interception occurs
- **Then:** Watcher invoked via lightweight single LLM call (one roundtrip), NOT via full `spawn_instance`; verified by inspecting invocation path
- **Test type:** integration

### FR-19 / FR-20 / FR-21: FE Toggle

**AC-19.1** (button visible)
- **Given:** User opens chat with instance
- **When:** Chat header renders
- **Then:** Watchover button is visible in `header-right` container
- **Test type:** e2e

**AC-20.1** (placement correct)
- **Given:** Header-right with Think, Tools, System, Watchover buttons
- **When:** User views header
- **Then:** Watchover button is LEFT of Think button (per spatial requirement; verified by DOM order)
- **Test type:** e2e

**AC-21.1** (active styling)
- **Given:** Watchover active
- **When:** Header renders
- **Then:** Button shows active state styling (distinct color from OFF state, consistent with toggle pattern)
- **Test type:** e2e

**AC-21.2** (state persisted in localStorage)
- **Given:** User toggles Watchover
- **When:** Page reloads
- **Then:** Button state reflects last toggle (matches Angular signal + localStorage pattern)
- **Test type:** e2e

### FR-22 / FR-23 / FR-24: Feedback

**AC-22.1** (denial in chat)
- **Given:** Tool call denied
- **When:** User views chat
- **Then:** Denial event rendered with tool name + reason + counter (1/3, 2/3, 3/3)
- **Test type:** e2e

**AC-23.1** (termination in chat)
- **Given:** Instance terminated by watchover
- **When:** User views chat
- **Then:** Termination event rendered with denial count + reason + watchover_terminated marker
- **Test type:** e2e

### FR-25: Compaction during watchover

**AC-25.1** (context refresh on compaction)
- **Given:** Watchover active, conversation grows large enough to trigger compaction
- **When:** Compaction runs (C3 checkpoint via `graph.aget_state()`)
- **Then:** watchover_context rebuilt with fresh compaction summary; subsequent interception uses updated context
- **Test type:** integration

### FR-26: Crash Recovery

**AC-26.1** (state restored after restart)
- **Given:** Watchover active, daemon crashes
- **When:** Daemon restarts and instance resumes
- **Then:** Watchover still active; flag restored from DB; interception resumes on next tool call
- **Test type:** integration

**AC-26.2** (partial activation recovery)
- **Given:** Activation sequence crashed mid-execution (after pause, before resume)
- **When:** Daemon restarts and instance resumes
- **Then:** Instance resumes cleanly; watchover state is either fully ON or fully OFF (no partial state)
- **Test type:** integration

### FR-27: Authorization

**AC-27.1** (manager-internal authorization — phase 1 descope)
- **Given:** Watchover toggle endpoint with no cross-session ownership model (phase 1)
- **When:** Any caller toggles watchover
- **Then:** Request processed at manager level (no 403 cross-session check in phase 1); documented limitation: "Cross-session authorization is deferred — watchover is manager-internal only for phase 1"
- **Test type:** integration
- **Note:** Full cross-session 403 rejection deferred to phase 2 pending an instance-ownership primitive.

### NFR-4: Watcher LLM Error/Timeout (LD-2 bifurcated handling)

**AC-NFR-4.1** (fail-open on infrastructure timeout — LD-2)
- **Given:** Watchover active
- **When:** Watcher LLM call times out (infrastructure error)
- **Then:** Treated as Allow (fail-open); tool call proceeds; counter does NOT increment; `watchover_event{status: "degraded", reason: "watcher_infra_error"}` SSE emitted; logged with reason="watcher_timeout"
- **Test type:** integration

**AC-NFR-4.2** (3 judgment errors in turn = termination)
- **Given:** Counter at 2 with two previous judgment-error (malformed/unparseable)
- **When:** Third judgment error occurs
- **Then:** Termination triggered (3 denials = termination applies to judgment failures)
- **Test type:** integration

**AC-NFR-4.3** (fail-closed on judgment error — LD-2)
- **Given:** Watchover active
- **When:** Watcher returns malformed/unparseable verdict (judgment error)
- **Then:** Treated as Deny (fail-closed); counter increments; logged with reason="watcher_judgment_error"
- **Test type:** integration

### Edge Cases

**AC-EC.1** (compaction during watchover)
- **Given:** Watchover active, conversation grows large enough to trigger compaction
- **When:** Compaction runs
- **Then:** watchover_context rebuilt; subsequent interception uses updated context; no interception skipped during compaction
- **Test type:** integration

**AC-EC.2** (concurrent instances)
- **Given:** 10 instances running, some with watchover active, some without
- **When:** All instances make tool calls simultaneously
- **Then:** Each instance evaluated independently; no cross-instance interference; counter isolation verified
- **Test type:** integration (load test)

**AC-EC.3** (false positive denial — non-destructive tool denied)
- **Given:** Watchover active with conservative watcher prompt
- **When:** Agent makes a safe tool call that watcher incorrectly denies
- **Then:** Tool rejected; counter increments; agent can correct; user sees denial in chat with reason
- **Test type:** integration

**AC-EC.4** (denial loop — agent keeps retrying denied action)
- **Given:** Agent's first 2 tool calls denied in current turn (counter at 2)
- **When:** Agent retries the same denied action
- **Then:** Third denial triggers termination (no escape via repetition)
- **Test type:** integration

**AC-EC.5** (deactivation mid-turn)
- **Given:** Watchover active, denial counter at 1 in current turn
- **When:** User deactivates watchover
- **Then:** Counter resets on deactivation; subsequent tool calls not intercepted
- **Test type:** integration

**AC-EC.6** (race: activation + tool call in flight)
- **Given:** Instance mid-tool-call when user clicks Watchover
- **When:** Activation sequence runs
- **Then:** In-flight tool call completes (Allow by default — interception not yet enabled) OR is queued for resume; no tool call executed against post-activation watchover state without evaluation
- **Test type:** integration

**AC-EC.7** (empty compaction — fresh instance)
- **Given:** Watchover active on instance with very short history (no compaction yet)
- **When:** Activation sequence builds watchover_context
- **Then:** watchover_context includes user requirement + raw recent messages (no compaction summary available); still functional
- **Test type:** integration

**AC-EC.8** (watcher on non-DevOps instance)
- **Given:** Watchover active on a non-DevOps instance (per C-3 phase 1 limitation)
- **When:** Instance makes tool calls
- **Then:** Interception still works; behavior is identical to DevOps; only feature-parity limitations are documented, not functional failures
- **Test type:** integration

**AC-EC.9** (parallel tool calls — deny-whole-batch, LD-1/AD-9)
- **Given:** Watchover active, LLM returns 3 tool_calls in one response
- **When:** Interception processes the batch
- **Then:** Each tool call is independently evaluated; if ANY call in the batch is denied, the ENTIRE batch is denied and returned to the agent for correction (no partial execution). Counter increments per denied batch. No `watchover_finalize_denials` node.
- **Test type:** integration

**AC-EC.10** (paused instance with watchover active)
- **Given:** Watchover active, instance is paused
- **When:** User unpauses
- **Then:** Watchover still active; interception resumes
- **Test type:** integration

## Gaps & Ambiguities

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| 1 | What is the exact "user requirement" that seeds watchover_context? | Confirm: watchover_context source = (a) original instance prompt + compaction, (b) user-provided directive at activation time, (c) both, or (d) configurable? | High |
| 2 | ~~Fail-open or fail-closed when watcher LLM times out/errors?~~ | **✅ RESOLVED (LD-2): bifurcated — infra errors (timeout/5xx/network) fail-open (Allow + degraded SSE, no count); judgment errors (malformed/unparseable) fail-closed (Deny + count).** See NFR-4. | High |
| 3 | Does the watcher LLM use the same model as the watched instance, a smaller/cheaper model, or configurable? | Confirm model selection strategy. Suggest configurable via `meta.json` | Medium |
| 4 | What is the exact set of "destructive" actions to watch for? | Should watcher have configurable ruleset in soul.md, or rely on LLM judgment? Suggest: LLM judgment for phase 1, explicit rules deferred | High |
| 5 | When termination occurs, what is the exact user-facing message wording? | Confirm exact chat message format | Low |
| 6 | Does deactivation require confirmation? | Confirm UX intent. Suggest: no confirmation for v1 (matches existing toggle pattern) | Medium |
| 7 | Can watchover be activated on instances mid-tool-call? Or only when instance is in safe state? | Activation sequence pauses first, so should be safe; confirm no edge case missed | Medium |
| 8 | What happens if multiple parallel tool calls occur in one LLM response? | Confirm per-tool-call vs per-batch evaluation. Default proposed: per-tool-call | Medium |
| 9 | When watcher denies, does counter persist across agent's "correction" attempts within same turn, or reset per correction? | Confirm: counter is per turn (cumulative within turn), not per tool call attempt | Medium |
| 10 | Is watchover available on all instance types in phase 1, or restricted to DevOps? | User says "first phase on DevOps" — confirm watcher works for other types too, or DevOps-only? | High |
| 11 | Should watcher decisions be auditable? | Confirm audit logging requirement. Default proposed: structured logs (FR-29) but no UI viewer | Low |
| 12 | What happens if user enables watchover but no compaction available (fresh instance)? | Edge case: empty compaction. Default proposed: use raw recent messages | Low |
| 13 | Does watchover work during instance cascade (parent/child instances)? | Confirm: child instances inherit watchover from parent? Independent? | Medium |
| 14 | Does watchover count as a "tool call" for throttling purposes? | Confirm: watcher LLM call is separate from watched agent's throttled tool calls | Low |
| 15 | How does watchover interact with the loop breaker? | If loop breaker triggers, does watchover state get reset? | Medium |
| 16 | What is the watcher's stance on read-only operations on critical paths (e.g., reading /etc/shadow)? | Watcher judgment policy: only block destructive writes, or also sensitive reads? | Medium |

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| 1 | Watcher is invoked via lightweight single LLM call, not full `spawn_instance` | Cost/context efficiency; full spawn would be too heavy for per-tool-call evaluation | If wrong, performance/cost model invalidated; different invocation mechanism needed |
| 2 | "Turn" boundary = LLM returns without tool_calls | Standard LangGraph cycle: agent→tools→agent, until no tool_calls | If wrong, counter resets at wrong time; termination logic breaks |
| 3 | Per-instance scope (not per-session or per-project) | User says "when enabled per-instance" | If wrong, scoping model invalid |
| 4 | Watchover state is DB-persisted per instance | Aligns with project persistence pattern; survives restarts | If wrong, crash recovery breaks |
| 5 | Interception slot is added to graph between `agent_node` tool_calls output and `tools_condition` routing | Natural insertion point in existing graph topology | If wrong, interception happens at wrong point or fails to integrate |
| 6 | Activation/deactivation uses existing `pause_instance_cascade` / `resume_instance_cascade` / `resume_processing_job` pattern | Project constraint to reuse cascade (C-1, C-6) | If wrong, torn states possible |
| 7 | FE toggle follows Angular signal + localStorage + onToggleX() pattern | Existing toggle buttons (Think, Tools, System) follow this | If wrong, FE pattern inconsistency |
| 8 | 3-strikes rule applies per turn (cumulative within turn), not per session | User wording: "3 denials in a turn" | If wrong, termination threshold differs |
| 9 | Watcher LLM receives a synthesized system prompt + watchover_context + message mirror + tool call, single turn | Standard evaluation pattern; mirrors existing agent invocation but lightweight | If wrong, watcher needs different invocation shape |
| 10 | Termination uses existing job cancellation + instance finalize, with new `terminal_reason="watchover_terminated"` | Existing finalize machinery; just new reason | If wrong, custom termination path needed |
| 11 | Denial counter lives in LangGraph state (instance state), not in DB | Per-turn counter; ephemeral within turn | If wrong, persistence model needs revision |
| 12 | Watcher agent definition (`agents/watcher/`) follows the project's agent prompt writing guide (`docs/agent-prompt-writing-guide.md`) | Project convention per critical notes | If wrong, watcher agent prompt may not align with conventions |
| 13 | watchover_context is rebuilt on compaction (per FR-25) | Compaction summarizes long conversations; watcher must have fresh context | If wrong, watcher works with stale context |
| 14 | Watcher uses same prompt format / meta.json schema as other agents | Existing agent pattern | If wrong, watcher registration may fail |
| 15 | The "tool call" payload sent to watcher includes tool name, arguments, and any relevant metadata (e.g., target file path) | Sufficient context for Allow/Deny decision | If wrong, watcher cannot evaluate accurately |
| 16 | Watcher's evaluation prompt is single-turn (no ReAct loop) | Lightweight invocation per FR-18 | If wrong, watcher becomes heavy; cost model breaks |
| 17 | The watcher's Allow/Deny decision is binary; no "Allow with caveat" or "Confirm with user" intermediate state | Simplifies the integration; matches user's binary description | If wrong, additional routing logic needed |
| 18 | The interception slot is bypassed when watchover is OFF (early return) | Performance: no LLM call when not needed | If wrong, every tool call invokes watcher regardless of state |
| 19 | Phase 1 uses manager-internal authorization only. No cross-session ownership primitive exists in the project (pause/resume/answer endpoints have no 403 path). Full session ownership is aspirational for a future phase, NOT committed for this feature. | Honest about project limitations | Phase 2 ownership model would require new infrastructure |
| 20 | Watchover activation does not require user re-authentication; existing session is sufficient | Standard UX; consistent with other instance-level toggles | If wrong, additional auth flow needed |

## Out of Scope (Deferred)

- **Watcher customization UI** — per-instance watcher prompt/rules config. Deferred to phase 2.
- **Watcher audit log viewer** — UI to review past watcher decisions. Deferred. (Structured logs still emitted per FR-29.)
- **Watcher rules engine** — explicit rule-based watcher (regex/blocklist). Deferred — phase 1 is LLM judgment only.
- **Watcher analytics dashboard** — denial rate, latency stats. Deferred.
- **Cross-instance watcher coordination** — multiple instances watching each other. Out of scope.
- **Watcher for non-DevOps instance types (full feature parity guarantees)** — Phase 1 is DevOps-first per C-3; other types may not have all features wired in phase 1, but interception itself works (per AC-EC.8).
- **Watcher deactivation confirmation dialog** — pending UX decision (Gap #6).
- **Model selection UI for watcher** — deferred (Gap #3).
- **Watchover auto-disable on N consecutive false positives** — deferred.
- **Integration with blueprint or other subsystems beyond core graph/job queue** — phase 1 stays minimal per C-1.
- **Watcher policy templates / library** — predefined rule sets users can pick from. Deferred.
- **Watcher decision explanation UI** — user-friendly explanation of why watcher denied. Phase 1 has reason text only; deferred for richer explanations.
- **Watcher multi-modal evaluation** — evaluating non-text tool calls (screenshots, file diffs). Deferred.
- **Per-tool-type watcher policies** — different watcher prompts for different tools. Deferred.
- **Watcher learning / feedback loop** — improving watcher over time based on user feedback. Deferred.
