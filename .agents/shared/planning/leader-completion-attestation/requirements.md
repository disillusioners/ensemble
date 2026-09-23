# Requirements: Leader Completion Attestation

Date: 2026-09-05 (initial), reconciled 2026-09-05 (post-leader ruling pass)
Author: planner[v2] via requirements-analysis worker
Status: Draft — post-reconciliation (R1/R2/C1-C5/O1-O4 applied)
Source Request: Prevent premature leader completion caused by inter-instance LLM hallucination. When a child agent reports work as "in progress" (but the leader's LLM hallucinates / prematurely closes its turn without doing the work), the leader instance/mission gets marked COMPLETED while the actual work is unfinished. Introduce an explicit completion attestation mechanism that forces the leader to call a tool before declaring itself done, with bounded-retry recovery if attestation is missing.

---

## Glossary

| Term | Definition |
|------|------------|
| **Attestation** | An explicit tool call the leader instance makes to signal "I have finished the actual work for this turn/mission and the mission can finalize." Distinct from the leader merely emitting a final assistant message. |
| **Attestation tool** | The new tool the leader invokes to record an attestation. Has a stable name and a deterministic no-arg (or minimal-arg) signature. |
| **Attestation window** | The configurable number N of most-recent messages the system inspects when checking for the attestation tool call. Default proposed as 3. |
| **Completion gate** | The decision point at which the orchestration system evaluates whether a leader instance is permitted to transition to a terminal status (COMPLETED). Implemented in-graph (Candidate B per architect ruling): a `create_should_continue`-style wrapper translates `END` → `"end_candidate"` and routes through an `attestation_gate` node wired in **both** `create_should_continue` branches (`daemon/graph.py:2707-2734` precedent). The only chokepoint shared by all 4+ completion stampers — any placement that misses a stamp surface is unsafe (see `architecture-recommendation.md` §2 trade-off matrix, A disqualified as bypassable, E disqualified as defer-starvation footgun). |
| **Gate deny input** | The predicate the gate evaluates. Per **R2**, the gate denies ONLY when **all** of the following hold simultaneously: (a) the most recent AIMessages do not contain an attestation tool call within the window; (b) `pending_children == 0` (no children in WAITING_CHILDREN or ACTIVE — i.e., delegation has wound down); (c) `queued_or_expected_wakeups == 0` (no message in `MessageQueue` for this instance, no WC-wake pending). If ANY of these is non-zero, the gate **allows** the would-be END without attestation. This kills "nudge-flood" on legitimate delegation turn-ends. |
| **Hallucinated completion** | A leader LLM behavior in which the LLM emits a final assistant message (or otherwise triggers a graph END path) without having actually executed the work its prompt describes. Causes the orchestration to mark the leader terminal while the mission is still in progress. |
| **Nudge** | The act of injecting a checkpoint-durable `HumanMessage` into the same leader execution when the gate denies. The nudge is the leader's continuation signal: "The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message." It is injected in-graph (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`) and routes the execution back to the `agent` node — no `manager.enqueue_message` is called on deny, no instance is revived (the leader is RUNNING throughout). |
| **Bounded retry** | A per-instance counter (`attestation_denied_count`, row-scoped instance column) tracking consecutive denied completions. After the bound is reached, the gate allows the next would-be END AND emits a `gate_terminal_after_bound` observability event AND sets a persistent `completion_gate_escalated=true` flag on the instance row. Reset to 0 on **attested allow only** (leader ruling 1 — `allowed_legitimate_pending_wakeup` MUST NOT reset; row survives revive, unlike the loop-breaker in-memory `_loop_breaker_state`). The same single reset op also clears `completion_gate_escalated` (leader ruling 2 — both columns share the per-mission lifecycle). |
| **Mode** | A single tri-state env var `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce` (default `dry` at ship). Replaces the previous two-env-pair design (one env for the on/off state plus a separate env for a dry-run toggle) because the pair admits an inconsistent combination with no well-defined meaning. Restart-read via Pattern C resolver (`daemon/services/instance_messaging.py:114-191` precedent). `off` ⇒ gate does not run (legacy behavior preserved); `dry` ⇒ gate evaluates and emits `leader_completion_gate` decision-log entries with `decision: "dry_log"` (canonical enum per Phase 4 task 4.5 / CR-4) and scanner diagnostics, but allows every END (zero side effects); `enforce` ⇒ gate denies per FR-3. Promote to `enforce` only after ≤2-week soak with adjudicated dry-log false-positive rate (computed from `dry_log_deny_predicate_total / dry_log_total` — replaces the previous retired counter name per CR-4). |
| **Attempt ledger** | The per-instance persistence of `attestation_denied_count` (counter) and `completion_gate_escalated` (escalation marker flag), stored as **row-scoped instance-row columns** (NOT an in-memory dict, NOT a side table). PG+SQLite-safe migration (fresh-SQLite boot trap is a live hazard). Counter resets to 0 on **attested allow only** (leader ruling 1) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message (fresh episode) AND on instance creation (column default 0). `allowed_legitimate_pending_wakeup` (R2 un-attested allow) MUST NOT reset the counter — that non-reset IS the loop protection. `daemon/manager.py:3734/:3798/:8548` is the in-memory precedent only, not applicable to DB columns. |
| **Fail-open (gate exception path)** | When the scanner or gate raises an unexpected exception (programming error, transient I/O), the gate MUST allow completion and emit a structured error log. This is the **gate-level** fail-OPEN (W4 precedent `graph.py:2663-2688`, narrow exception set). It is distinct from the scanner's **cannot-prove ⇒ deny** rule (compaction summary encountered first ⇒ deny, since the gate cannot prove attestation). The narrow bootstrap exception set deliberately does NOT cover SQLAlchemy `OperationalError` (the `attestation_denied_count` ledger DB seam) — that path must surface as a structured error log. |

---

## Stakeholders

- **Requester:** product/operations (raised after observing a leader-instance completion where child work was still pending and the leader LLM had not actually performed its turn).
- **Affected users:** any user dispatching a mission whose root instance is a leader — premature COMPLETED currently strands the mission and any downstream state that depended on the leader actually doing work.
- **Affected agents:**
  - **Leader** (`agents/leader/meta.json` v1.1.0, `tools.allow` = 13 categories at lines 14-15) — gains a new tool category and a prompt contract requiring attestation before declaring done.
  - **Child agents** (planner, developer, reviewer, tidier, approver, architect, tester, giter, devops, explorer, wanderer, kb-writer, doc-writer — `team_members` at line 17) — no behavioral change. They continue to emit reports; the bug class is upstream of their behavior.
  - **Orchestration system** (`daemon/services/child_reports.py`, `daemon/services/job_feedback_observer.py`, `daemon/services/instance_messaging.py`, `daemon/graph.py`) — gains the gate, the ledger, and the recovery injector.
- **Affected systems:** the graph execution path (`daemon/graph.py`), the completion writers in `child_reports.py` and `job_feedback_observer.py`, the instance messaging path (`instance_messaging.py`), the tool registry (`daemon/tools/_tool_registry.py`), the leader agent meta + rule files, the kill-switch config resolver.

### Scenario Narrative (the incident)

1. User dispatches a mission → leader instance is created and starts a turn.
2. Leader LLM dispatches sub-tasks to children via `subtree_messages` / `instance` tools.
3. Children return reports → leader receives them as `ToolMessage` / `HumanMessage` content depending on path.
4. Leader LLM (hallucinated) emits a final assistant message such as "All sub-tasks complete; finishing now." and routes END.
5. The graph's `should_continue` wrapper translates END → `end_candidate` → attestation gate evaluates (per FR-3): attest missing in window AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` ⇒ deny.
6. **The deny injects a checkpoint-durable HumanMessage nudge into the same execution ("The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message.") and routes back to the `agent` node.** No `manager.enqueue_message`, no revive. If the leader LLM had pending children or queued wakeups at step 5, the gate would have allowed the END without attestation (legitimate delegation turn-end, R2).

The feature fixes step 6 by requiring an explicit attestation tool call between step 4 and step 6; missing attestation in a no-pending state triggers an in-graph nudge rather than terminal write.

---

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | The leader agent MUST have access to a new attestation tool whose name, signature, and category are stable. The tool is a no-op except for recording the call into the instance's message history. | The attestation signal must be observable in the LangGraph message stream (tool_calls list of the most recent AIMessage) and must not depend on the LLM's text output. | Must | Attestation tool |
| FR-2 | When an instance's completion gate evaluates, the system MUST scan the last N messages (configurable) of the leader's `state.values['messages']` for an AIMessage whose `tool_calls` includes the attestation tool name. | Tool-call introspection of AIMessages is the only deterministic way to confirm a real tool was invoked (vs. the LLM claiming it was). N is configurable per requirement NFR-2. | Must | Attestation check |
| FR-3 | When the in-graph gate (a) observes a leader turn-end (the `attestation_gate` node is reached), (b) sees no attestation tool call within the window per FR-2, (c) sees `pending_children == 0` AND `queued_or_expected_wakeups == 0`, the gate MUST deny the completion. A deny MUST NOT write the terminal status. The deny MUST trigger the in-graph nudge per FR-4, NOT `manager.enqueue_message` and NOT instance revival. | Per R2, this is the bug fix: a hallucinated completion followed by zero pending state. Legitimate delegation turn-ends (children active / wakeups pending) are allowed without attestation — the R2 input kills "nudge-flood". Without this denial, hallucinated completions continue to strand missions. | Must | Hallucination recovery |
| FR-4 | The deny path MUST inject a checkpoint-durable `HumanMessage` directly into the leader's graph state (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`), with content *"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."* The injection lands in the SAME execution, the leader remains RUNNING, the graph routes back to the `agent` node. NO `manager.enqueue_message` call on deny, NO instance revival. The durable-enqueue recovery injector (and its facade-forwarding / JAFP tests) is RELOCATED to phase6 (`phase6-fastfollow-plan.md`) as a C backstop, post-soak. | Durability comes from LangGraph checkpointing (no RAM-only injection); the in-state nudge survives node-boundary checkpoints because it IS a checkpointed message. Same-execution routing avoids the observer-vs-revive race that a deferred enqueue would reintroduce. The phase6 backstop only matters if the in-graph nudge later proves insufficient (e.g., for OS-2 / parent-cascade no-leader-turn completions). | Must | Hallucination recovery (in-graph nudge) |
| FR-5 | The leader agent's prompt (workflow.md / rule.md / dispatch prompts) MUST contain a contract that the leader MUST call the attestation tool before declaring itself done. This contract is documented in `agents/leader/rule.md` under a canonical `### Must` block (house style). | Without a prompt-side obligation, the leader LLM has no reason to call the tool. The house-style `### Must` block placement ensures prompt hygiene and reviewer visibility. | Must | Prompt contract |
| FR-6 | The system MUST track a per-instance denied-completion counter and bound it at a configurable maximum (default proposed: 3). When the bound is exceeded, the system MUST allow the instance to terminate normally AND emit an escalation observability event AND set a persistent flag on the instance row indicating "completion gate denied N times before terminal." The counter MUST reset to 0 on **attested allow only** (leader ruling 1 — `allowed_legitimate_pending_wakeup` MUST NOT reset) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message AND on instance creation. The same single reset op also clears `completion_gate_escalated` (leader ruling 2). | Prevents infinite loops if the leader LLM is pathologically incapable of producing an attestation. Keeps missions from being permanently stuck. The reset-on-allow rule is critical because instance-row columns survive revive — without reset, a revived leader starts its next mission pre-burdened. | Must | Loop safety |
| FR-7 | The attestation window size N MUST be configurable at boot via the restart-read resolver pattern (env → yaml → default) and MUST default to a sane value (architect to confirm; proposed default: 3). At startup the resolver MUST assert `N ≤ min_recent_window` (currently 3 — `daemon/compaction.py`), raising a one-time boot WARN if the constraint is violated; the gate continues running regardless, but the violation is operator-visible. | Different mission classes may have different message densities; hardcoding N creates brittleness. The boot assert (per O1) is the operator-facing guard for the known failure mode where raising WINDOW above the compaction floor causes false-positive folding-attestation denies. | Must | Configurability |
| FR-8 | The feature's mode MUST be set via a single tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce`, resolved at boot by the restart-read Pattern C resolver (cached global + one-time boot log). **2026-09-06: operator override — default mode enforce (user decision); dry-at-ship rationale superseded; fail-open + off-kill-switch remain the safety valves.** Ship default is now `enforce` (was `dry` per the original D2 RESOLVED). When mode=`off`, the gate MUST behave as if no attestation is required — i.e., the system behaves exactly as today. When mode=`dry`, the gate evaluates and emits structured decision-log entries with `decision: "dry_log"` (canonical enum per Phase 4 task 4.5 / CR-4) and scanner diagnostics (`pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`), but every END is allowed (zero side effects). When mode=`enforce`, the gate denies per FR-3. The legacy single-bool env var (under a different name) is NOT a supported surface — the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE`, and the resolver raises `ResolverError` if any non-tri-state key is set. | Operators need a tri-state mode for incident response: instant revert (off), observable soak (dry), production enforcement (enforce). Promoting dry → enforce is operator-driven after a ≤2-week soak with adjudicated dry-log false-positive rate. The kill-switch posture is unchanged: `off` is the instant-revert and remains restart-read Pattern C (no live flip). | Must | Mode (kill-switch replacement) |
| FR-9 | The attestation tool category MUST be opt-in via `agents/leader/meta.json` `tools.allow` (the agent's existing authz path), MUST be added to a category list whose membership is fail-closed, and MUST be discoverable by the existing tool registry drift tests (`tests/unit/tools/test_upgrade_registration.py`). | Mirrors the existing authz discipline (absent category = unavailable). Drift tests catch silent-registration omissions. | Must | Authorization |
| FR-10 | The system MUST emit an observability event for each gate decision (allowed / denied / terminal_after_bound / dry_log / allowed_legitimate_pending_wakeup — canonical 5-value enum per Phase 4 task 4.5; fail-open is an exception-path event with `event=leader_completion_gate_error` / `leader_completion_gate_db_error` and `error_class: str`, NOT a decision value) carrying: instance_id, gate_decision, attestation_present (bool), denied_count, gate_location, leader_prompt_version, mode, pending_children, queued_or_expected_wakeups, attest_seen_outside_window (any attestation call observed beyond the window — diagnostic for the R2 threshold), messages_scanned (int, >0 in healthy dry runs), scanned_window_size. The event MUST be persisted to the system log via the standard structured logging path. In `dry` mode every gate evaluation MUST emit a decision-log entry regardless of the deny/allow choice. | Post-incident diagnosis requires knowing what the gate did and why. The expanded schema (vs the original FR-10) is required for W5 dry-run measurability and for adjudicating the R2 input (how often is `attest_seen_outside_window` true in dry logs?). | Should | Observability |
| FR-11 | The feature MUST be scope-aware: it covers the leader agent by default (graph-build-time `agent_id == "leader"` check; non-leader graphs are untouched per D3). Leader-only scope is **RESOLVED-by-D3** for MVP. Broadening to other parent agents is a future consideration (CANDIDATE in `phase6-fastfollow-plan.md`, not committed). | Avoids over-scoping before the bug is reproduced in non-leader parents. | Should | Scope |
| FR-12 | The system MUST log a one-time boot line announcing the resolved effective values: (mode, attestation_window N, denied_completion_bound, gate_locations_active, boot_assert_N_le_min_recent_window (PASS/WARN)). | Operators need a single line they can grep to confirm config at boot. The boot line is the same pattern as the WC-wake resolver (`instance_messaging.py:114-191`). The N-vs-min_recent_window assert is part of the boot line so a misconfigured WINDOW is operator-visible. | Should | Observability |
| FR-13 | The gate MUST fail-OPEN on any scanner/gate exception (try/except, W4 precedent `graph.py:2663-2688`). On exception the gate MUST allow completion, MUST emit a structured `gate_exception` log entry with exception type and stack-trace summary, and MUST set a transient `gate_exception_seen=true` flag on the instance row for operator visibility. The narrow bootstrap exception set deliberately does NOT cover SQLAlchemy `OperationalError` raised by the `attestation_denied_count` ledger DB seam — that path emits a `gate_ledger_db_error` log and the gate behavior is implementation-defined (it MUST NOT silently inflate the counter; idempotency is per-denial-epoch). | An unhandled scanner exception on the routing path would error every leader mission, which is D2's outage class. The W4 precedent shows a narrow try/except set does NOT cover DB OperationalError, so this FR codifies the explicit exclusion. | Must | Resilience (fail-open) |

### Theme: Attestation Tool

**FR-1:** A new tool category — proposed name `attestation` (architect to confirm) — is registered with a single tool method whose canonical name is proposed as `attest_completion`. The tool:
- **Rationale:** must be unambiguously introspectable in `state.values['messages']` via AIMessage.tool_calls.
- **Priority:** Must.
- **Notes:**
  - The tool body is a no-op aside from returning a structured success payload (e.g. `{"attested": true, "timestamp": "<iso>"}`). It does not mutate any persistent state by itself — attestation is recorded in the message history by virtue of the tool call existing there.
  - Registration MUST follow the three-step + statics discipline (`daemon/tools/_tool_registry.py:106`; `DYNAMIC_TOOL_NAMES`; `KNOWN_TOOL_NAMES` drift test; decorator-only registration is silently invisible).
  - Tool surface placed in a new file under `daemon/tools/attestation.py` (or co-located with the gate — architect decides).

### Theme: Attestation Check (Scanner)

**FR-2:** A scanner reads `state.values['messages']` via `aget_state` (same pattern as `instance_messaging.py:1258,1292`). It walks the **last N** AIMessages (default N=3) and returns `attested=True` iff at least one of those AIMessages has a `tool_calls` entry whose `name` equals the attestation tool's canonical name.
- **Rationale:** AIMessage.tool_calls is the only first-class signal that an LLM actually invoked a tool; text claims are unreliable.
- **Priority:** Must.
- **Notes:**
  - N is configurable per FR-7.
  - The scanner must be unit-testable in isolation from the gate (pure function over a messages list → attested bool + diagnostic detail).

### Theme: Hallucination Recovery (in-graph nudge per R1)

**FR-3 + FR-4:** When the gate reaches the `attestation_gate` node (FR-3 conditions all hold: attest missing in window AND `pending_children == 0` AND `queued_or_expected_wakeups == 0`), the gate:
1. Increments the per-instance `attestation_denied_count` (row-scoped column; DB survives revive).
2. If counter < bound (FR-6), the gate **denies** by injecting a checkpoint-durable `HumanMessage` directly into the leader's graph state with content `"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."`, and routing back to `agent`. The leader remains RUNNING throughout. There is **no** `manager.enqueue_message` call, **no** instance revival, **no** cross-turn delivery.
3. Returns a denial decision; the terminal-status write MUST NOT occur for this turn.
4. If counter ≥ bound, allows terminal AND emits the escalation event `gate_terminal_after_bound` (FR-10) AND sets `completion_gate_escalated=true` on the instance row.

- **Rationale:** prevention + bounded retry + escalation. The in-graph nudge is durable by virtue of LangGraph checkpointing at node boundaries — no RAM-only state is touched. The phase6 backstop (`phase6-fastfollow-plan.md`) is the durable-enqueue recovery injector for OS-2 / no-leader-turn cascade paths that B (the in-graph gate) cannot see by construction; it does NOT run on the MVP deny path.
- **Priority:** Must.
- **Notes:**
  - **In-graph nudge mechanism:** mirror `language_check` reminder (`daemon/graph.py:2666-2685`); use `additional_kwargs={'attestation_nudge': True}` marker; route via the `should_continue` wrapper's deny branch back to `agent`.
  - **Why the deny branch uses no durable enqueue (R1):** the in-graph deny keeps the execution on the same RUNNING graph; an enqueue-then-revive cycle reintroduces the observer-vs-revive race that B exists to eliminate, and double-delivers (the enqueued task fires after the eventual attested END → spurious revive of a COMPLETED instance). The C backstop addresses the OS-2 class (parent-cascade no-leader-turn), not the in-graph deny.
  - **Pending-wakeup input (R2):** the gate's deny input requires BOTH `pending_children == 0` AND `queued_or_expected_wakeups == 0`. If either is non-zero, the gate ALLOWS (no nudge). This kills nudge-flood on legitimate delegation turn-ends. The gate MUST log `pending_children`, `queued_or_expected_wakeups` (R2 inputs), and the R2-deny predicate (the boolean `dry_log_deny_predicate_total` metric increment; computed from R2 inputs and `attestation_present`, without actually denying) for every evaluation in `dry` mode to keep W5 measurability.
  - **Compaction-and-history preservation:** the nudge is part of `state['messages']`; LangGraph checkpoints it. The nudge can itself trigger a future compaction event — that is expected and the scanner's cannot-prove ⇒ deny rule still applies cleanly.

### Theme: Prompt Contract

**FR-5:** The leader agent's `agents/leader/rule.md` (or `workflow.md` — architect to choose canonical home) gains a new `### Must` block under `## Must`:

> *When your work for this mission is genuinely complete and you are about to be done, you MUST call the `attest_completion` tool. Do not declare done in plain text. If you receive a HumanMessage in the conversation containing "The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message.", treat it as a real user instruction: review your current progress, complete the remaining work, and only then call `attest_completion`.*

- **Rationale:** without an explicit prompt contract, the leader LLM has no reason to call the tool. The HumanMessage framing in the contract matches the actual delivery shape (in-state `HumanMessage`, not a `MessageQueue` enqueue).
- **Priority:** Must.
- **Notes:**
  - House style per `agents/leader/rule.md` is mandatory `### Must` blocks under `## Must`. Follow exactly.
  - The text above is a draft; the architect + reviewer may refine it.

### Theme: Loop Safety

**FR-6:** Per-instance `attestation_denied_count` persisted as a **row-scoped instance-row column** (NOT an in-memory dict, NOT a side table). Bound is configurable (default proposed: 3, env `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`). When the counter reaches the bound, the gate MUST allow the next would-be END AND emit a `gate_terminal_after_bound` observability event carrying: instance_id, denied_count, bound, last_denial_reason. A persistent `completion_gate_escalated=true` flag MUST be set on the instance row.
- **Rationale:** prevents pathological infinite loops while preserving mission forward progress. The DB column survives instance revival (terminal→RUNNING via `enqueue_message` to terminal instance at `instance_messaging.py:1867-1909`); without reset-on-allow, a revived leader starts its next mission pre-burdened.
- **Priority:** Must.
- **Notes:**
  - **Reset semantics (leader ruling 1, SUPERSEDES prior "every allow" wording):** `attestation_denied_count` resets to 0 on **attested allow only** (`Decision.ALLOWED` with `attestation_present=True` per the scanner verdict) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message AND on instance creation. `allowed_legitimate_pending_wakeup` MUST NOT reset the counter — that non-reset IS the loop protection. The same single reset op also clears `completion_gate_escalated` (leader ruling 2). Reset is on the same tx as the allow write, so the counter never inflates across instance reuse.
  - **Migration:** PG+SQLite-safe (fresh-SQLite boot trap from migration `20260714_000001` is a live hazard — known project risk).
  - **Failure mode on the ledger DB seam:** if `attestation_denied_count` increment raises `OperationalError` (DB unavailable), the gate MUST NOT silently inflate or double-count; idempotency is per-denial-epoch (a documented per-increment nonce or upsert). See FR-13.
  - **Pause-mid-gate double-increment (O4):** the increment must be idempotent across denial epochs. Implementation choice between per-denial-epoch nonce and per-instance idempotency is developer-decision; documented inflation is acceptable if disclosed in the boot-time docs.

### Theme: Configurability & Mode

**FR-7 + FR-8 + FR-12:** Three config knobs follow the existing restart-read resolver pattern (Pattern C from research — `instance_messaging.py:114-191` WC-wake variant: module env resolver + cached global + one-time boot log):
- `ENSEMBLE_LEADER_ATTESTATION_MODE` (tri-state `off`|`dry`|`enforce`, default **`dry` at ship**). Default is RESOLVED per architect ruling D2 — promote to `enforce` after ≤2-week soak on adjudicated dry-log false-positive rate.
- `ENSEMBLE_LEADER_ATTESTATION_WINDOW` (int, default 3). Boot assert (FR-7): if `WINDOW > min_recent_window` (currently 3 in `daemon/compaction.py`), emit a one-time WARN log line; the gate continues running (no hard-fail); the violation is operator-visible.
- `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` (int, default 3).

A single boot log line announces resolved effective values (FR-12), including `mode`, `WINDOW`, `BOUND`, `gate_locations_active`, and `N_le_min_recent_window=PASS|WARN`.

- **Rationale:** restart-read avoids race conditions on hot-reload; explicit resolver matches existing WC-wake pattern; tri-state mode avoids the inconsistent-state class that a two-env pair design would create. Defaults are explicit, not implicit.
- **Priority:** Must.
- **Notes:**
  - **Mode = off** ⇒ gate is a no-op; legacy behavior preserved. **Mode = dry** ⇒ gate evaluates every would-be END, emits canonical `dry_log` decision log (per Phase 4 task 4.5 / CR-4), allows all END (zero side effects). **Mode = enforce** ⇒ gate denies per FR-3.
  - **Boot assert (O1, FR-7):** a misconfigured `WINDOW` above the compaction floor invites false-positive folding-attestation denies; raising WINDOW without checking the floor is the documented trap. The boot WARN is the operator-facing guard.
  - **No legacy single-bool surface:** the canonical key is the tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE`; under any prior single-bool surface the resolver is the sole entry point and the legacy key is rejected. AC-7.9 codifies this.

### Theme: Authorization

**FR-9:** The attestation tool category is added to `agents/leader/meta.json:14-15` `tools.allow`. The category is registered in `daemon/tools/_tool_registry.py` with `@register_tool_category` and a `CATEGORY_MODULES` entry. `KNOWN_TOOL_NAMES` regeneration is required (drift test will fail if skipped).
- **Rationale:** fail-closed authorization is the existing discipline; absent category = unavailable to leader.
- **Priority:** Must.
- **Notes:** the architect must confirm whether the tool is in `PRIVILEGED_TOOL_CATEGORIES` (`_tool_registry.py:101-103`) or a new opt-in-only category. Recommendation: NOT privileged; only the leader agent opts in by adding it to its allow list.

---

## Non-Functional Requirements

| ID | Category | Requirement | Metric | Target | Measurement |
|----|----------|-------------|--------|--------|-------------|
| NFR-1 | Performance | Gate decision overhead on the completion path MUST NOT exceed P95 of 20 ms wall-clock. | Wall-clock latency added to a completion-eligible transition. | P95 ≤ 20 ms | Daemon log timing around gate; integration test measuring path latency. |
| NFR-2 | Performance | Scanner MUST inspect at most N AIMessages (configurable; default 3). | Scan depth bound. | Scan depth = N, no full-history scan | Unit test asserts scan bounds with a synthetic 1000-message state. |
| NFR-3 | Loop safety | Per-instance denied-completion counter MUST be capped; the cap MUST NOT be circumventable by leader turn boundaries. | Counter monotonicity and bound enforcement. | Counter ≤ bound before terminal | Unit test asserts counter behavior; integration test asserts no infinite loop. |
| NFR-4 | Reliability | The in-graph nudge MUST survive daemon crash between gate decision and nudge injection — durability is provided by LangGraph node-boundary checkpointing (the `HumanMessage` IS a checkpointed message in `state['messages']`). Restart in the same execution branch produces an identical message stream. | Crash-recovery correctness. | After restart, the leader sees the nudge in its message history | Integration test: kill daemon mid-gate, restart, assert nudge persists in state. |
| NFR-5 | Reliability | Mode env MUST be honored within one boot cycle. No live-flip; restart required to change. | Configuration read timing. | Env change requires restart to take effect | Test: change env, assert no behavior change until restart, then assert behavior change. |
| NFR-6 | Security | Nudge text MUST be a server-authored constant, NOT echo of any leader-supplied or user-supplied content. | Injection safety. | Nudge text is a hard-coded constant | Unit test: verify constant; integration test: try to inject content via malformed gate state, assert unchanged. |

**NFR-6 addendum (2026-09-07, per user request):** The nudge text now embeds a server-authored Mermaid completion-flow diagram (a fenced ```` ```mermaid ```` ... ```` ``` ```` block appended to the END of `ATTESTATION_NUDGE_TEXT` in `daemon/graph.py:2819+`). The diagram is part of the SAME hard-coded constant — single source of truth — so the plain prose body remains byte-identical to NFR-6's canonical text (no leader-supplied / user-supplied fragments added). The introducer line is exactly `Completion flow:` on its own line. The mermaid body contains literal `{` and `}` characters; the constant is a parenthesized implicit string concat (no f-string, no `.format`, no `%`), so the braces are literal in the rendered message. All existing full-equality pins across `tests/` (5 unit + 6 integration files, all of which import `ATTESTATION_NUDGE_TEXT` directly or alias it as `NUDGE_TEXT`) auto-track the new length at the SAME strength — equality pins stay full-equality, no conversion to substring/contains checks.
| NFR-7 | Security | Attestation tool MUST be fail-closed — agents without the category in their `tools.allow` MUST NOT be able to invoke it (regardless of how the LLM phrases a request). | Authorization correctness. | Leader-only invocation; non-leader parents cannot invoke | Unit test: instantiate non-leader agent, assert tool absent; integration test: assert non-leader parent completion is NOT attestation-gated (per FR-11 RESOLVED scope, leader-only). |
| NFR-8 | Observability | Each gate decision MUST emit a structured log entry conforming to the canonical schema at Phase 4 task 4.5 (verbatim pointer — do not restate): `decision` is one of the canonical `Decision` enum values `allowed | denied | terminal_after_bound | dry_log | allowed_legitimate_pending_wakeup`; all canonical schema fields are present (`event`, `decision`, `instance_id`, `attestation_present`, `denied_count`, `gate_location`, `leader_prompt_version`, `pending_children: int`, `queued_or_expected_wakeups: int`, `attest_seen_outside_window`, `messages_scanned`, `scanned_window_size`, `mode`, `scanner_window_truncated`, `scanner_summary_seen`). In `dry` mode every evaluation MUST emit a decision-log entry (zero-side-effect) with `decision: "dry_log"` (canonical enum) and the R2-deny predicate derivable from the canonical schema fields (`pending_children: int == 0 AND queued_or_expected_wakeups: int == 0 AND attestation_present: bool == False` ⇒ R2-deny predicate satisfied). Exception-path evaluations (C3 fail-open) emit the mode-appropriate canonical decision (`decision: "allowed"` under enforce, `decision: "dry_log"` under dry) and the exception detail is recorded in the diagnostics layer via the C3 error log (`event=leader_completion_gate_error` or `event=leader_completion_gate_db_error`, with `error_class: str` per Phase 4 task 4.5) — the canonical `Decision` enum does not include a separate exception-path value; fail-open is a PATH, not a decision value. | Log signature completeness. | All decisions logged with full schema | Integration test asserts log entries match schema; log search asserts all required keys present. |
| NFR-9 | Observability | Boot log line MUST announce effective resolved values for the three config knobs plus the boot-assert result for `N ≤ min_recent_window`. | Operator visibility. | One log line at boot with all keys | Boot the daemon with each env var set/unset; assert the resolved value matches expectation. |
| NFR-10 | Compatibility | The feature MUST NOT alter behavior when `ENSEMBLE_LEADER_ATTESTATION_MODE=off`. | Backward compatibility. | Existing behavior preserved when off | Integration test: run completion path with mode=off, assert byte-equivalent behavior to a pre-feature baseline reference. |
| NFR-11 | Compatibility | The feature MUST NOT break: normal leader completion WITH attestation; mission finalize path; revive semantics (COMPLETED → RUNNING on `enqueue_message`); WC-wake routing lanes (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF — instance_messaging.py:114-191); existing report-delivery recovery sweeps (`ReportDeliveryRecoveryService`, `WaitingChildrenWatchdog`); the report-injection claim state machine PENDING→INJECTED rowcount-guard (graph.py:416-490); the in-state `HumanMessage` reminder precedent (`daemon/graph.py:2666-2685`). | Must-not-break surface list. | All listed surfaces behave identically across all three mode values | Integration test suite parameterized over `mode ∈ {off, dry, enforce}` asserting no behavior delta on the must-not-break list. |
| NFR-12 | Compatibility | The feature MUST NOT introduce a defer-starvation footgun: if the gate ever emits a `gate_deferred` return that is not re-armed or finalized, the related job strands in `admission_state='active'` indefinitely. | Job-strand avoidance. | Any `gate_deferred` path either re-arms or finalizes | Code review + integration test asserts job state after denied completion. |
| NFR-13 | Maintainability | The attestation tool name, mode, window N, bound, and dry-log emission MUST be configurable, NOT hardcoded. | Configurability. | All values come from the resolver, not literals | Code review + drift test on hardcoded constant grep. |
| NFR-14 | Maintainability | The gate decision logic MUST be unit-testable in isolation from the graph (pure function over `(attestation_present, pending_children, queued_or_expected_wakeups, denied_count, bound, mode, scope)` → Decision). The Decision value is one of the canonical 5-value enum values defined at Phase 4 task 4.5 (verbatim pointer — do not restate). | Testability. | Pure function exists and is unit-tested | Unit test file exercises the pure decision function over a full input matrix. |
| NFR-15 | Resilience | On ANY unhandled exception in the scanner or gate decision logic, the gate MUST allow completion AND emit a structured `gate_exception` log entry with exception type and a stack-trace summary AND set a transient `gate_exception_seen=true` flag on the instance row. The exception set MUST include the bootstrap precedent `except Exception` (W4, `graph.py:2663-2688`) and MUST NOT include SQLAlchemy `OperationalError` raised by the `attestation_denied_count` ledger DB seam — that path emits `gate_ledger_db_error` and is implementation-defined within idempotency constraints. | Fail-open continuity. | Scanner exception ⇒ allow; ledger DB exception ⇒ controlled, observable | Unit test injects scanner exception and asserts allow; integration test simulates `OperationalError` on increment and asserts idempotency + structured log. |
| NFR-16 | Observability | The dry-mode decision log MUST include all R2 inputs (`pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`) and a `messages_scanned` count > 0 on healthy evaluations. The schema MUST be sufficient to adjudicate: (a) what fraction of dry evaluations would trigger the R2-deny predicate under enforce, (b) how often `attest_seen_outside_window=true` (the signal that the R2 input is firing correctly), (c) how often `pending_children>0` allows an R2-allow. | Dry-run promotion gate data. | Adjudicated dry-log false-positive rate is measurable before promote-to-enforce | Integration test replays a recorded session with mode=dry and asserts the schema is sufficient to derive all three rates; operator runbook references this NFR by ID. |

---

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | The feature MUST integrate via the existing tool registration three-step + statics discipline; decorator-only registration is silently invisible and is forbidden. | Architecture blueprint | Any tool implementation must follow the registry pattern or it won't be discoverable. |
| C-2 | Technical | Attestation tool category MUST go through the existing `tools.allow` opt-in pattern in `meta.json`; there is no other authorization path. | Architecture blueprint | Categories not listed are unavailable — fail-closed. |
| C-3 | Technical | The deny path MUST inject an in-state `HumanMessage` directly into the leader's graph (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`); the durable-enqueue recovery path (`manager.enqueue_message`) is RELOCATED to `phase6-fastfollow-plan.md` as a C backstop. The MVP path MUST NOT call `manager.enqueue_message` on deny and MUST NOT revive the leader. | R1 (architecture recommendation §3) | durable-delivery call on deny would reintroduce the observer-vs-revive race B is meant to eliminate, and would double-deliver once the leader does attest. |
| C-4 | Technical | The gate's deny input MUST require `attestation_present == false` AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` simultaneously. If any of the three is non-zero, the gate allows without attestation. | R2 (architecture recommendation §3) | Without the pending-wakeup input the gate would nudge-flood legitimate delegation turn-ends. |
| C-5 | Business | The tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE` default (`dry` at ship) and the promote-to-`enforce` criterion (≤2-week soak on dry-log false-positive rate) are RESOLVED per architect ruling D2. | Architect | The default governs the initial rollout; the promote-runbook entry must ship with the gate. |
| C-6 | Business | Scope (leader-only vs. all parents) is RESOLVED closed-by-leader per D3 — leader-only v1 (graph-build-time `agent_id == "leader"` check; non-leader graphs are untouched). | D3 record (`decisions.md:221`) | Determines which instances are gated (D3 RESOLVED closed-by-leader). |
| C-7 | Technical | The gate's `except Exception` (W4 precedent `graph.py:2663-2688`) MUST NOT cover SQLAlchemy `OperationalError` raised by the `attestation_denied_count` ledger DB seam — that path emits `gate_ledger_db_error` and is implementation-defined within idempotency constraints. | Architecture recommendation §4 | If the wider `except Exception` covered `OperationalError`, the leader counter could silently inflate on transient DB failures, causing spurious escalation. |
| C-8 | Technical | The feature MUST coexist with the WC-wake variant (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF, module-level env resolver + cached global + one-time boot log — `instance_messaging.py:114-191`). The new tri-state MODE env resolver MUST use the same Pattern C shape (cached global + one-time boot log). | Architecture blueprint + critical notes | Both envs and both boot logs must not interfere; orthogonal concerns. |
| C-9 | Technical | The feature MUST coexist with the existing known blind spot: during the inter-report gap (previous child report processed, next not arrived), bus gate and pending-tasks gate can BOTH pass — premature finalize window. This feature addresses the leader's hallucinated case; the inter-report gap case is **out of scope** (see Out of Scope §OS-2). The C fast-follow (phase6) addresses the parent-cascade no-leader-turn path (OS-2) and ships only after the in-graph gate soak data is adjudicated. | Research / incident analysis | Other completion paths remain vulnerable to a different bug class; do not promise a fix for that class in the MVP. |
| C-10 | Technical | The pre-call compaction and other pre-completion middleware MUST run before the gate evaluates, so the gate sees the final message stream state. Order of middleware evaluation is an architect concern. The boot assert (FR-7 / NFR-9) MUST verify `WINDOW ≤ min_recent_window` to avoid the `WINDOW=5` while compaction fold floor is `min_recent_window=3` failure mode. | Architecture blueprint (D10(b)) | Wrong order or misconfigured WINDOW → gate makes decisions on stale messages or false-positives folded-attestation denies. |
| C-11 | Technical | The `attestation_denied_count` column MUST reset to 0 on **attested allow only** (leader ruling 1 — `allowed_legitimate_pending_wakeup` MUST NOT reset) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message AND on instance creation. Without reset, a revived leader instance (terminal→RUNNING per `instance_messaging.py:1867-1909`) starts its next mission pre-burdened. Reset is implementation-defined (idempotent per-denial-epoch upsert, OR a documented single-tx UPDATE) but MUST be observable in the gate decision log. The same single reset op also clears `completion_gate_escalated` (leader ruling 2). | Architecture recommendation D5 | O2 architect ruling. |
| C-12 | Business | The instrumented dry-run path is satisfied by the default `dry` mode + promotion-metrics NFR-16 + an operator runbook. There is NO separate "block-before-Phase-2" dry-run activity; dry observability is in the gate from Phase-1 onward. | Architecture recommendation D8 | A pre-Phase-2 dry-run observable would not protect leader missions until Phase-1 ships. |

---

## Acceptance Criteria

### FR-1: Attestation tool exists

**AC-1.1** (happy path)
- **Given:** leader agent is loaded with the new attestation tool category in its `tools.allow`.
- **When:** leader LLM emits an AIMessage with `tool_calls=[{"name": "attest_completion", "args": {}, "id": "..."}]`.
- **Then:** the tool executes successfully and returns `{"attested": true, "timestamp": "<iso>"}`.
- **Test type:** unit.

**AC-1.2** (authz)
- **Given:** a non-leader agent (e.g., developer) is loaded WITHOUT the attestation tool category in its `tools.allow`.
- **When:** that agent's tools are resolved.
- **Then:** the `attest_completion` tool is NOT present in the agent's available tool list.
- **Test type:** unit.

**AC-1.3** (drift test)
- **Given:** the new tool is registered.
- **When:** `tests/unit/tools/test_upgrade_registration.py` runs.
- **Then:** it passes (i.e., the tool appears in `KNOWN_TOOL_NAMES` and `DYNAMIC_TOOL_NAMES`).
- **Test type:** unit.

### FR-2: Scanner detects attestation in last N messages

**AC-2.1** (attested within window)
- **Given:** a state with messages `[..., AIMessage(tool_calls=[attest_completion]), AIMessage(...), AIMessage(...)]` where the attesting AIMessage is within the last N=3.
- **When:** the scanner runs.
- **Then:** it returns `attested=True`.
- **Test type:** unit.

**AC-2.2** (attested outside window)
- **Given:** a state with messages `[AIMessage(tool_calls=[attest_completion]), <N+1 other messages>, AIMessage(...)]`.
- **When:** the scanner runs with N=3.
- **Then:** it returns `attested=False`.
- **Test type:** unit.

**AC-2.3** (text-only claim)
- **Given:** a state where the last AIMessage's content is "I am done. Calling attest_completion now." but `tool_calls` is empty.
- **When:** the scanner runs.
- **Then:** it returns `attested=False`.
- **Test type:** unit.

**AC-2.4** (non-attestation tool calls)
- **Given:** a state where the last N AIMessages contain only `subtree_status` / `instance` / other tool calls (none being `attest_completion`).
- **When:** the scanner runs.
- **Then:** it returns `attested=False`.
- **Test type:** unit.

**AC-2.5** (window bounds)
- **Given:** a state with 1000 messages.
- **When:** the scanner runs with N=3.
- **Then:** it inspects only the last 3 AIMessages (no full-history scan).
- **Test type:** unit.

### FR-3: Gate denies non-attested completion (with pending-wakeup input per R2)

**AC-3.1** (deny path: would-complete-without-attestation)
- **Given:** the gate reaches the `attestation_gate` node, `mode=enforce`, scope applicable (leader), AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` AND `scanner_result.attested == False` AND `attestation_denied_count < bound`.
- **When:** the gate evaluates the would-be END.
- **Then:** the terminal-status write is NOT performed AND a checkpoint-durable `HumanMessage` with content `"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."` is appended to `state['messages']` AND the graph routes back to the `agent` node AND `attestation_denied_count` increments AND a `denied` decision-log entry is emitted (carrying `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`). NO `manager.enqueue_message` call is made; no instance revival occurs.
- **Test type:** integration.

**AC-3.2** (allow path)
- **Given:** the gate evaluates a leader instance.
- **When:** `scanner_result.attested == True`.
- **Then:** the terminal-status write proceeds normally AND no nudge is injected AND `attestation_denied_count` is reset to 0 on the same tx AND an `allowed` decision-log entry is emitted.
- **Test type:** integration.

**AC-3.3** (allow when pending-wakeup input is non-zero — R2)
- **Given:** the gate reaches the `attestation_gate` node, `mode=enforce`, scope applicable, AND `scanner_result.attested == False`, AND any of the following is true: `pending_children > 0` OR `queued_or_expected_wakeups > 0`.
- **When:** the gate evaluates the would-be END.
- **Then:** the gate allows the END without attestation AND NO nudge is injected AND an `allowed_legitimate_pending_wakeup` decision-log entry is emitted carrying the R2 inputs (`pending_children`, `queued_or_expected_wakeups`). This is the "nudge-flood kill" per R2.
- **Test type:** integration.

**AC-3.4** (no full-history scan)
- **Given:** a 1000-message state, `WINDOW=3`.
- **When:** the scanner runs as part of gate evaluation.
- **Then:** at most 3 AIMessages are inspected (per NFR-2); the full message list is not loaded.
- **Test type:** unit (invariant; asserted across the AC-3.1/3.2/3.3 suite).

### FR-4: In-graph nudge semantics (durability by checkpoint, no enqueue, no revive per R1)

**AC-4.1** (in-state HumanMessage injection)
- **Given:** the gate decides denial per AC-3.1.
- **When:** the gate fires the deny branch.
- **Then:** a `HumanMessage` with content `"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."` is appended to `state['messages']` with `additional_kwargs={"attestation_nudge": True}` (exact mirror of `language_check` reminder precedent at `daemon/graph.py:2666-2685`) AND the graph returns control to the `agent` node. NO `MessageQueue` row is created, NO `Task` row is created, NO `worker_pool.notify_work()` is called, NO `manager.enqueue_message` is invoked.
- **Test type:** integration.

**AC-4.2** (nudge durability by LangGraph checkpoint)
- **Given:** the gate has injected the nudge per AC-4.1.
- **When:** the daemon is killed and the leader's checkpoint is reloaded.
- **Then:** the `HumanMessage` is present in `state['messages']` (LangGraph persists it as part of the node-boundary checkpoint). The leader resumes from the same message stream.
- **Test type:** integration (chaos test).

**AC-4.3** (no JAFP / no JobItem)
- **Given:** the gate fires the deny branch.
- **When:** the queue tables are inspected.
- **Then:** there is NO `MessageQueue` row, NO `Task` row, NO `JobItem` row for the nudge. R1: the deny path is in-graph only; phase6 (`phase6-fastfollow-plan.md`) carries the durable-enqueue backstop with its own JAFP tests.
- **Test type:** integration.

**AC-4.4** (nudge text is server-authored constant)
- **Given:** the gate fires the deny branch.
- **When:** the injected `HumanMessage` content is asserted.
- **Then:** it matches the verbatim constant `"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."` exactly — no LLM-supplied or user-supplied fragments are concatenated. (NFR-6.)
- **Test type:** unit + integration.

**AC-4.5** (enqueue-based durable recovery: RELOCATED to phase6)
- **Given:** the gate decides denial per AC-3.1.
- **When:** looking up the durable-enqueue recovery path.
- **Then:** this acceptance is NOT in scope for this milestone. The durable `manager.enqueue_message` recovery injector and its facade-forwarding + JAFP tests live in `phase6-fastfollow-plan.md` (C backstop, post-soak). Per R1's C5 interpretation fork, B's deny path is in-graph only; the enqueue path is the OS-2 backstop.
- **Test type:** deferred to phase6.

### FR-5: Prompt contract is in place

**AC-5.1** (rule.md contains contract)
- **Given:** `agents/leader/rule.md` is read.
- **When:** the document is searched for the attestation tool's canonical name.
- **Then:** the canonical name appears in a `### Must` block under `## Must`.
- **Test type:** manual review + grep test.

**AC-5.2** (contract references nudge text)
- **Given:** the leader's rule.md is read.
- **When:** the nudge text is searched for.
- **Then:** it appears verbatim in the leader's prompt, instructing the leader to treat the `HumanMessage` as a real user instruction.
- **Test type:** manual review + grep test.

### FR-6: Bounded retry + terminal fallback

**AC-6.1** (counter increments)
- **Given:** `attestation_denied_count = k` and `bound = 3`.
- **When:** a deny fires (AC-3.1 path).
- **Then:** `attestation_denied_count` becomes `k+1`.
- **Test type:** unit.

**AC-6.2** (allow at bound)
- **Given:** `attestation_denied_count = bound = 3`.
- **When:** the next completion-eligible evaluation occurs.
- **Then:** the gate allows terminal AND emits `gate_terminal_after_bound` event AND sets `completion_gate_escalated=true` on the instance row AND resets `attestation_denied_count` to 0.
- **Test type:** integration.

**AC-6.3** (no infinite loop)
- **Given:** a leader LLM that NEVER calls the attestation tool.
- **When:** the gate runs 10 consecutive denial cycles (more than any reasonable bound).
- **Then:** the gate terminates the instance after `bound` denials and emits the escalation event exactly once.
- **Test type:** integration (replay from a recorded session).

**AC-6.4** (counter resets on new mission)
- **Given:** a leader instance that has been escalated (`completion_gate_escalated=true`).
- **When:** a new mission is dispatched to a fresh leader instance.
- **Then:** the fresh instance starts with `attestation_denied_count = 0`.
- **Test type:** unit.

**AC-6.5** (counter resets on attested allow only — leader ruling 1 supersedes the prior "every allow" wording — O2)
- **Given:** the leader has `attestation_denied_count = 2`, `mode=enforce`.
- **When:** the leader calls `attest_completion` before END and the gate re-evaluates with `attested=True` (canonical `Decision.ALLOWED` with `attestation_present=True`).
- **Then:** `attestation_denied_count` is reset to 0 on the same tx as the allow write (or as a same-DB-session UPDATE). The same single reset op also clears `completion_gate_escalated` (leader ruling 2 — both columns share the per-mission lifecycle). Crucially, this MUST happen — without reset, a revived leader starts the next mission pre-burdened. The `allowed_legitimate_pending_wakeup` decision value (R2 un-attested allow) MUST NOT reset the counter — that non-reset IS the loop protection.
- **Test type:** unit + integration.

**AC-6.6** (ledger DB OperationalError ⇒ structured log, NOT silent inflation — C7)
- **Given:** the gate would increment `attestation_denied_count`, but the SQLAlchemy UPDATE raises `OperationalError` (DB unavailable).
- **When:** the exception is raised.
- **Then:** the gate emits a `gate_ledger_db_error` log entry AND does NOT silently inflate `attestation_denied_count` AND does NOT silently allow-completion; idempotency is per-denial-epoch (a documented per-increment nonce OR per-instance monotonic counter that the gate logic uses for the next evaluation). Implementation choice between "drop the increment, deny anyway" and "retry the increment with backoff" is developer-decision; both are acceptable if observable.
- **Test type:** unit (mock) + integration (chaos — kill DB connection).

### FR-7 + FR-8: Mode + Window + Bound configuration

**AC-7.1** (window N from resolver)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_WINDOW=5`.
- **When:** the daemon boots.
- **Then:** the scanner uses N=5; the boot log line includes `window=5`.
- **Test type:** integration (boot the daemon with the env var set).

**AC-7.2** (window default)
- **Given:** no env var set.
- **When:** the daemon boots.
- **Then:** the scanner uses the resolver default (3); the boot log line includes `window=3`.
- **Test type:** integration.

**AC-7.3** (mode=off bypasses the gate)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_MODE=off`.
- **When:** the daemon completes a leader mission.
- **Then:** the gate does NOT run; behavior is byte-equivalent to a pre-feature baseline reference; no `leader_completion_gate` decision log entries are emitted.
- **Test type:** integration.

**AC-7.4** (mode=enforce fires deny-nudge)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_MODE=enforce`.
- **When:** the daemon completes a leader mission without attestation AND `pending_children == 0` AND `queued_or_expected_wakeups == 0`.
- **Then:** the gate denies and the in-graph nudge is injected per AC-4.1.
- **Test type:** integration.

**AC-7.5** (mode=dry allows every END with full decision log)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_MODE=dry`.
- **When:** the daemon evaluates a would-be END per AC-3.1 conditions (would-be-deny).
- **Then:** the gate logs `decision: "dry_log"` (canonical enum per Phase 4 task 4.5 / CR-4) AND allows the END AND no nudge is injected; the dry-log entry's canonical schema fields include `dry_log_deny_predicate_total`-computable values (i.e. the R2 inputs `pending_children: int == 0` AND `queued_or_expected_wakeups: int == 0` AND `attestation_present: bool == False`, indicating the deny predicate is satisfied in dry mode); the decision log carries all NFR-8 keys (canonical schema per Phase 4 task 4.5).
- **Test type:** integration.

**AC-7.6** (restart-read)
- **Given:** the daemon is running with the mode env set to one value.
- **When:** the mode env is changed and the daemon is NOT restarted.
- **Then:** behavior does not change.
- **Test type:** integration.

**AC-7.7** (effective values boot log — NFR-9)
- **Given:** any combination of env vars (the three CONFIGURABLE knobs: `ENSEMBLE_LEADER_ATTESTATION_MODE`, `ENSEMBLE_LEADER_ATTESTATION_WINDOW`, `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`).
- **When:** the daemon boots.
- **Then:** a single boot log line is emitted announcing the resolved effective values for the three configurable knobs (mode, window, bound) plus the derived `attestation_enabled` flag (which is `mode != "off"` per the resolver — not a separate configurable knob) AND the result of the `N ≤ min_recent_window` boot assert (`N_le_min_recent_window=PASS|WARN`). The line format mirrors the WC-wake boot log (`daemon/services/instance_messaging.py:114-191`).
- **Test type:** integration.

**AC-7.8** (boot assert — O1)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_WINDOW=5` while `min_recent_window=3` (compaction floor).
- **When:** the daemon boots.
- **Then:** the resolver emits a one-time WARN line (`N_le_min_recent_window=WARN`), AND the gate continues running with the configured WINDOW (no hard-fail). The WARN is operator-visible in the boot log per AC-7.7.
- **Test type:** integration.

**AC-7.9** (Pattern C fail-OPEN posture — single tri-state key only, C-5/C-12)
- **Given:** the resolver sees a legacy single-bool env surface (under any prior canonical name) AND/OR a typo'd value for the canonical key `ENSEMBLE_LEADER_ATTESTATION_MODE` (`enabled`, `enabled=true`, `True`, etc.).
- **When:** the resolver parses the environment.
- **Then:** **the resolver fails OPEN to the default (`mode=dry`) with a one-shot WARN log line.** The legacy key is NOT consumed (the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE` and only that is honored); the typo'd value falls back to `dry` with WARN. **The daemon DOES NOT refuse to start** — this mirrors the WC-wake resolver's Pattern C fail-OPEN posture for typo'd keys per `daemon/services/instance_messaging.py:114-191` (the same one-shot WARN + default fallback shape). The test target is unambiguous: the resolver returns `mode=dry`, a one-shot WARN is logged, the daemon boots normally. (Note: this AC supersedes the original "fail-CLOSED + raise ResolverError" prose — ruling 4 mandates Pattern C fail-OPEN; the canonical resolver module ships that contract.)
- **Test type:** unit.

### FR-13: Fail-open on scanner/gate exceptions

**AC-13.1** (scanner exception ⇒ allow)
- **Given:** the scanner raises an unexpected exception (e.g., `AttributeError` from a malformed message).
- **When:** the gate catches the exception via the W4 precedent try/except.
- **Then:** the gate allows completion, emits a `gate_exception` log entry with exception type and stack-trace summary, AND sets `gate_exception_seen=true` on the instance row.
- **Test type:** unit.

**AC-13.2** (OperationalError is NOT in the bootstrap exception set — C7)
- **Given:** the gate would increment `attestation_denied_count` and the SQLAlchemy UPDATE raises `OperationalError`.
- **When:** the gate catches the exception (it is a different code path than AC-13.1).
- **Then:** the gate emits `gate_ledger_db_error` (not `gate_exception`) AND implements the per-denial-epoch idempotency per AC-6.6. The behavior is observable; no silent inflation; no silent allow.
- **Test type:** integration.

### FR-9: Authorization is fail-closed

**AC-9.1** (leader has tool)
- **Given:** `agents/leader/meta.json` lists the attestation category in `tools.allow`.
- **When:** the leader's tools are resolved.
- **Then:** `attest_completion` is in the tool list.
- **Test type:** unit.

**AC-9.2** (non-leader lacks tool)
- **Given:** `agents/developer/meta.json` does NOT list the attestation category.
- **When:** developer's tools are resolved.
- **Then:** `attest_completion` is NOT in the tool list.
- **Test type:** unit.

**AC-9.3** (drift test)
- **Given:** the tool is registered.
- **When:** `tests/unit/tools/test_upgrade_registration.py` runs.
- **Then:** it passes.
- **Test type:** unit.

### FR-10 + FR-12: Observability

**AC-10.1** (every gate decision logged)
- **Given:** 1000 leader missions complete.
- **When:** the daemon log is searched.
- **Then:** 1000 gate decision log entries exist, each with all required schema keys (including `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`, `scanned_window_size`, `mode`, and the R2 inputs are present and consistent); in `dry` mode every evaluation emits a decision-log entry regardless of choice.
- **Test type:** integration.

**AC-10.2** (escalation event unique)
- **Given:** a leader instance that hits `bound` denials.
- **When:** the daemon log is searched.
- **Then:** exactly one `gate_terminal_after_bound` event exists for that instance.
- **Test type:** integration.

**AC-10.3** (dry-mode R2-deny-predicate schema)
- **Given:** `mode=dry`, leader mission whose R2-deny predicate is satisfied.
- **When:** the daemon log is searched.
- **Then:** a decision log entry exists with `decision=dry_log` (canonical enum per Phase 4 task 4.5 / CR-4; dry allows terminal so the equivalent-allow marker is the R2-deny predicate being satisfied under `dry_log`) AND the R2-deny predicate is `True` (i.e. `attestation_present == False AND pending_children == 0 AND queued_or_expected_wakeups == 0`) AND all R2 input fields (`pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`) are present and consistent with the underlying scanner/gate state. This is the W5 dry-log measurability requirement per NFR-16.
- **Test type:** integration.

**AC-10.4** (gate_exception log entry on scanner exception)
- **Given:** the scanner raises an unexpected exception (AC-13.1 setup).
- **When:** the daemon log is searched.
- **Then:** a `gate_exception` log entry exists (per Phase 4 task 4.5 C3 error log `event=leader_completion_gate_error`) with exception type, stack-trace summary, `instance_id`, `gate_location`, and `error_class: str`; the gate's decision-log entry carries the mode-appropriate canonical decision (per Phase 4 task 4.5 — `decision: "allowed"` under `mode="enforce"`, `decision: "dry_log"` under `mode="dry"`) — fail-open is a PATH, not a separate decision value.
- **Test type:** unit + integration.

### FR-11: Scope

**AC-11.1** (leader-only v1 default)
- **Given:** the recommended default (leader-only) is in effect.
- **When:** a non-leader parent instance is about to complete.
- **Then:** the gate does NOT run; behavior is unchanged.
- **Test type:** integration.

### E2E Flow Acceptance Criteria

**AC-E2E-1** (full hallucination → deny-nudge → continue → attested finalize)
- **Given:** a leader instance with no pending children (the original incident class) and no queued wakeups; a child has just sent an "in progress" report.
- **When:** the leader LLM hallucinates a final assistant message and routes END without calling `attest_completion`.
- **Then:**
  1. The gate evaluates, scanner returns `attested=False`.
  2. `pending_children == 0` and `queued_or_expected_wakeups == 0` (R2 satisfied).
  3. `attestation_denied_count` increments from 0 to 1.
  4. In-graph `HumanMessage` with content `"The work is not yet finished — check current progress (tasks/children status) and continue. Reminder: when — and only when — the work is truly complete, you MUST call the attest_completion tool before finishing; completions without that call are premature and will be blocked again. Attestation is a SEPARATE step: FIRST deliver your full detailed final report as its own message, THEN call attest_completion alone as a subsequent step — never bundle the report into the attestation tool-call message."` is appended to `state['messages']`.
  5. Graph routes back to `agent` (no enqueue, no revive).
  6. Terminal-status write is NOT performed.
  7. Leader resumes, sees the in-state `HumanMessage` as a fresh user instruction.
  8. Leader's prompt contract instructs it to continue; leader completes remaining work and calls `attest_completion`.
  9. Gate re-evaluates, scanner returns `attested=True`; `attestation_denied_count` resets to 0 on the same tx (AC-6.5).
  10. Terminal-status write proceeds; mission finalizes.
  11. Log entries: 1× `denied` (with all NFR-8 keys), 1× `allowed`. No `gate_terminal_after_bound`. No `MessageQueue` / `Task` rows for the nudge.
- **Test type:** end-to-end integration (record/replay or scripted LLM mock).

**AC-E2E-1b** (pending-wakeup delegation turn-end allows without attestation — R2)
- **Given:** a leader instance with `pending_children > 0` (a child is in WAITING_CHILDREN or ACTIVE) when the leader LLM emits a final assistant message and routes END.
- **When:** the leader LLM does NOT call `attest_completion`.
- **Then:**
  1. The gate evaluates, scanner returns `attested=False`.
  2. `pending_children > 0` (R2 satisfied).
  3. The gate allows the END without attestation. NO nudge is injected. `attestation_denied_count` is NOT incremented. `attestation_denied_count` resets to 0 if it was non-zero (because this is an allow).
  4. Terminal-status write proceeds (or the gate defers normally if the leader has children to process first — that is the pre-existing behavior).
  5. Decision log entry: `decision=allowed_legitimate_pending_wakeup` with R2 inputs (AC-3.3 schema).
- **Test type:** integration.

**AC-E2E-2** (bound-exceeded escalation)
- **Given:** a leader LLM that cannot be induced to call `attest_completion`.
- **When:** the leader attempts completion `bound + 1` times in a no-pending state.
- **Then:**
  1. First `bound` attempts produce `denied` log entries and in-state nudges (per AC-3.1/AC-4.1).
  2. `(bound + 1)`-th attempt produces a `gate_terminal_after_bound` event and the instance transitions to COMPLETED.
  3. The instance row carries `completion_gate_escalated=true`; `attestation_denied_count` is reset to 0 (AC-6.2).
  4. Mission finalizes; downstream state sees a terminal leader.
- **Test type:** integration.

**AC-E2E-3** (normal attested completion unaffected)
- **Given:** a leader LLM that properly calls `attest_completion` before END.
- **When:** the gate evaluates.
- **Then:**
  1. Scanner returns `attested=True`.
  2. Terminal-status write proceeds normally.
  3. No nudge is injected.
  4. `attestation_denied_count` resets to 0 (or stays at 0 from prior allow).
  5. Mission finalizes with normal semantics.
  6. Log shows `allowed` only.
- **Test type:** integration.

**AC-E2E-4** (mode=off disables the feature entirely)
- **Given:** `ENSEMBLE_LEADER_ATTESTATION_MODE=off`.
- **When:** a leader hallucinates a completion.
- **Then:**
  1. The gate does NOT run.
  2. Terminal-status write proceeds immediately.
  3. Mission finalizes.
  4. No `leader_completion_gate` log entries appear.
- **Test type:** integration.

**AC-E2E-5** (must-not-break surfaces)
- **Given:** the feature is ON in all three mode values.
- **When:** each of the must-not-break surfaces is exercised (normal completion with attestation, mission finalize, revive semantics, WC-wake, report-delivery recovery sweeps, report-injection claim state machine, in-state `HumanMessage` reminder precedent).
- **Then:** each surface behaves identically to the OFF baseline.
- **Test type:** integration (parameterized over `mode ∈ {off, dry, enforce}`).

**AC-E2E-6** (instrumented dry-run produces adjudicated data — NFR-16)
- **Given:** a recorded leader-mission dataset and `mode=dry`.
- **When:** the dataset replays through the gate.
- **Then:** the decision-log corpus is sufficient to compute: (a) `dry_log_deny_predicate_total` fraction (the canonical metric is `dry_log_deny_predicate_total`, the count of dry evaluations where the R2-deny predicate is satisfied — i.e. `attestation_present == false AND pending_children == 0 AND queued_or_expected_wakeups == 0`), (b) `attest_seen_outside_window=true` rate, (c) `pending_children>0`-allows rate (canonical `allowed_legitimate_pending_wakeup` decision per Phase 4 task 4.5 / CR-4). Promote-to-enforce is operator-decision per the runbook; the metric source is reproducible. **Recorded-corpus ownership** (resolves the yellow-note ambiguity): the recorded leader-mission dataset lives at `tests/fixtures/recorded_leader_missions/` (test fixture location, checked into the repo) and is owned by the Phase 5 test author. The fixture loader is a deterministic emitter that replays each mission's AIMessage sequence through the gate and captures the canonical log schema (Phase 4 task 4.5) into a JSONL file at `tests/fixtures/recorded_leader_missions/_runs/<mission_id>.jsonl`. The corpus is versioned alongside the test fixtures; reproducibility requires the corpus to be committed. The replay driver is `tests/support/recorded_corpus_replay.py` (new — Phase 5 task 5.17 owns this file).
- **Test type:** integration.

**AC-E2E-7** (fail-open on scanner exception)
- **Given:** a leader mission; the scanner raises `AttributeError` partway through evaluation.
- **When:** the gate evaluates the would-be END.
- **Then:**
  1. Gate catches the exception (W4 bootstrap exception set); allows completion.
  2. `gate_exception` log entry per AC-10.4.
  3. `gate_exception_seen=true` set on the instance row.
  4. Mission finalizes.
- **Test type:** unit + integration.

**AC-E2E-8** (phase6 backstop is NOT in MVP — R1/C5 relocation)
- **Given:** the MVP ships (Phases 1-5).
- **When:** looking up durable-enqueue recovery injector code/tests.
- **Then:** the code (`attestation_recovery.py`), D6 source mapping, facade-forwarding + JAFP no-JobItem tests live in `phase6-fastfollow-plan.md`. They are NOT shipped in MVP. The MVP deny path is in-graph only (no `manager.enqueue_message`, no revive on deny).
- **Test type:** reviewer checklist (no test code in MVP).

---

## Gaps & Ambiguities

| # | Gap / Ambiguity | Question for Caller | Severity |
|---|-----------------|---------------------|----------|
| G1 | Scope: should the gate apply only to the `leader` agent, or to any parent instance? | **RESOLVED → D3**: leader-only scope confirmed for MVP — graph-build-time `agent_id == "leader"` check; non-leader graphs are untouched. Broadening to other parent agents (planner/developer/reviewer/tidier/approver/architect/tester/etc.) is a future consideration CANDIDATE in `phase6-fastfollow-plan.md`, explicitly NOT committed. | — (closed by D3) |
| G2 | ~~Kill-switch default: ON or OFF at first deploy?~~ | **RESOLVED → C5 + FR-8:** `ENSEMBLE_LEADER_ATTESTATION_MODE` defaults to **`dry`** at ship; promote to `enforce` after ≤2-week soak on adjudicated dry-log false-positive rate. The legacy single-state-mode env (under any prior name) is not a supported surface. | — (closed) |
| G3 | Attestation tool canonical name: `attest_completion` vs. `mark_done` vs. `complete_mission` vs. other. | Architect to confirm. Recommendation: `attest_completion` — descriptive and unambiguous. | Medium |
| G4 | Attestation tool argument shape: no-arg, or accept an optional mission id / notes field? | Architect to confirm. Recommendation: no required args; optional `notes` field for diagnostics. | Medium |
| G5 | Default attestation window N: 3? 5? 1? | **Tied to compaction floor**: FR-7 requires `N ≤ min_recent_window` (currently 3 in `daemon/compaction.py`) at boot — N=3 is the default and matches the floor; raising above the floor fires the boot WARN per AC-7.8. | Medium |
| G6 | Default denied-completion bound: 3? 5? | Architect to confirm. Recommendation: 3 — low enough to flag pathological behavior quickly, high enough to survive noisy turns. | Medium |
| G7 | Where does the gate live? `should_continue` (graph.py:2462-2533), child_reports._process_child_completion_db_sync (root_completed path :2566), job_feedback_observer._finalize_job_db_sync (:3703-3758), or multiple? | **RESOLVED → D1=B**: in-graph `end_candidate` interception, wired under its own flag in BOTH branches of `create_should_continue(language_check_enabled)` (the `language_check=on` AND `language_check=off` paths — `:2707` has two paths; piggybacking on language_check wiring silently disables the gate for most instances). Graph-build-time `agent_id == 'leader'` check keeps non-leader graphs untouched. | — (closed by D1) |
| G8 | Where is `attestation_denied_count` persisted? Instance row, side ledger, or LangGraph state? | **RESOLVED → D5**: instance-row column (`attestation_denied_count`, `completion_gate_escalated`). PG+SQLite-safe migration. `attestation_denied_count` resets to 0 on **attested allow only** (leader ruling 1) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message AND on instance creation (architect ruling: rows survive revive). | — (closed by D5) |
| G9 | Does the recovery message always use the same verbatim text, or should it include mission-specific context (instance id, mission id, attestation_denied_count)? | **Moot per R1**: the in-graph nudge is a server-authored constant; mission context is emitted to the dry-log schema, not into the message. | — (closed) |
| G10 | Does the recovery message pre-emptively raise the leader's `attestation_denied_count` so the leader sees its own count? | **Moot per R1**: there is no separate recovery message; the nudge is the in-state `HumanMessage`; the leader's `attestation_denied_count` is row-stored and not surfaced to the LLM. | — (closed) |
| G11 | When the gate denies, does the leader's existing checkpoint get reused (revive) or does a new turn start fresh? | **RESOLVED → R1**: the leader is RUNNING throughout; no revive is involved. The in-graph nudge is appended to existing `state['messages']`; LangGraph checkpoints at node boundaries. | — (closed by R1) |
| G12 | Should the gate also evaluate when the instance is being TERMINATED by operator (not hallucinating)? | Architect to confirm. Recommendation: NO — operator termination bypasses the gate; operator action is intentional. | Medium |
| G13 | Should the recovery message have a fixed `priority`? | **Moot per R1**: no separate recovery message; the in-state nudge is ordered by LangGraph checkpoint append (no `priority` field). | — (closed) |
| G14 | Boot assert (O1): should the gate hard-fail or merely WARN when `WINDOW > min_recent_window`? | **RESOLVED → FR-7**: WARN-only. The gate continues running; the violation is operator-visible in the boot log (per AC-7.7, AC-7.8). Hard-fail would block Phase 2 deployment for misconfigured WINDOW values, which is operationally brittle. | — (closed) |
| G15 | Fail-open exception set (C7): does the bootstrap `except Exception` cover SQLAlchemy `OperationalError`? | **RESOLVED → C7**: NO. The W4 precedent (`graph.py:2663-2688`) narrow set does NOT cover `OperationalError` raised by the `attestation_denied_count` ledger DB seam; that path emits `gate_ledger_db_error` (per AC-6.6) and is implementation-defined within idempotency constraints. | — (closed) |

---

## Resolved Decisions (Reference)

These were OPEN at the planner stage and are resolved by the architect's review and the leader rulings. Listed here so the spec layer reflects the post-reconciliation shape.

| # | Decision | Resolution | Source |
|---|----------|------------|--------|
| **R1** | Deny path semantics | **In-graph checkpoint-durable HumanMessage nudge (R1)**. NO `manager.enqueue_message` on deny, NO revive. Durable-enqueue recovery injector relocated to `phase6-fastfollow-plan.md` (C backstop, post-soak). | `architecture-recommendation.md` §3 C5 interpretation fork |
| **R2** | Gate deny input | **Pending-wakeup input required**: deny ONLY when `attestation_present == false` AND `pending_children == 0` AND `queued_or_expected_wakeups == 0`. Legitimate delegation turn-ends are allowed un-attested. | `architecture-recommendation.md` §3 |
| **D1** | Gate placement | **B (in-graph `end_candidate` interception)** under its own flag in both `create_should_continue` branches, leader-only by graph-build-time `agent_id` check. | `architecture-recommendation.md` §1 D1 |
| **D2** | Mode env (kill-switch replacement) | **Tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce`**, default `dry` at ship. The single-state-mode env shape is NOT supported on any legacy key; the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE`. | `architecture-recommendation.md` §1 D2 |
| **D5** | Retry bound + ledger semantics | `attestation_denied_count` (row-scoped instance column, PG+SQLite-safe migration), bound 3, reset to 0 on **attested allow only** (leader ruling 1) AND on `terminal_after_bound` finalization AND on revive-from-COMPLETED via a NEW top-level user/mission message AND on instance creation, escalation flag `completion_gate_escalated` (cleared by the SAME single reset op per leader ruling 2). | `architecture-recommendation.md` §1 D5 |
| **D6** | Recovery source value | **RELOCATED to phase6** (D6 status: DEFERRED-to-phase6). The MVP deny path is in-graph only; the durable-enqueue path is the C backstop, post-soak. | `architecture-recommendation.md` §1 D6 |
| **D7** | Tool semantics | `attest_completion`, no-arg, idempotent (any call in window counts), short confirmation ToolMessage return, NOT privileged. | `architecture-recommendation.md` §1 D7 |
| **D8** | Dry-run / observability | The tri-state `dry` mode IS the dry-run (no separate pre-Phase-2 activity). Dry lines carry scanner diagnostics (window truncated, summary-seen) so dry→enforce promotion is adjudicated on data, not conjecture. NFR-16 codifies promotion-gate data. | `architecture-recommendation.md` §1 D8 |
| **O1** | Boot assert (N ≤ min_recent_window) | WARN-only at boot (FR-7 / AC-7.8). Violation is operator-visible; gate continues running. | `architecture-recommendation.md` §1 D10(b) |
| **O2** | Reset semantics | `attestation_denied_count` reset on **attested allow only** (leader ruling 1 — `allowed_legitimate_pending_wakeup` MUST NOT reset) + reset on `terminal_after_bound` + reset on revive-from-COMPLETED via a NEW top-level user/mission message + reset on instance creation (column default 0). The earlier in-memory-dict cleanup precedent (named at the planner stage) is DROPPED — row-scoped DB columns need no per-instance in-memory cleanup hooks (the precedent applies to the loop-breaker counter, not to DB columns). | `architecture-recommendation.md` §4 + §5 phasing adjustments |
| **O4** | Pause-mid-gate double-increment | Idempotent per-denial-epoch upsert OR documented inflation; implementation-defined within FR-13/AC-6.6 constraints. | `architecture-recommendation.md` §4 |

---

## Assumptions

| # | Assumption | Reason | Risk if Wrong |
|---|------------|--------|---------------|
| 1 | The bug class — "leader LLM hallucinates a completion with no pending state" — is reproducible enough that the gate's deny path will be exercised in production. | The user's report describes the incident; the feature is built around it. | If the bug rarely manifests, the gate adds latency for no benefit. Mitigation: mode (FR-8) defaults to `dry` so the gate is observable but not enforcing at ship. |
| 2 | The leader LLM is willing to call the attestation tool when its prompt contract tells it to. | Standard tool-use behavior; the tool is in the leader's tool list. | If the LLM ignores the prompt contract, the gate injects a nudge every turn until the bound. Mitigation: prompt engineering + bound (FR-6) + the pending-wakeup input (R2) prevents nudge on legitimate delegation turn-ends. |
| 3 | An AIMessage with `attest_completion` in `tool_calls` is a reliable signal. | Tool-call introspection is the existing pattern (LoopDetector at `daemon/graph.py:1037-1044`). | If the LLM produces malformed tool_calls, the scanner returns False. This is conservative and safe; `attest_seen_outside_window` flag surfaces the diagnostic in dry logs. |
| 4 | The in-graph nudge is fast enough to be injected synchronously during the gate decision (within NFR-1's 20 ms budget). | The nudge is an in-state `HumanMessage` append — no DB write, no enqueue, no worker notify; well under 20 ms in normal conditions. | If latency is a concern, the gate can defer the in-state append to a follow-up node (architect decision). The phase6 backstop (durable enqueue) is NOT on this latency budget — it's the post-soak safety net for OS-2. |
| 5 | The leader is the only agent that hallucinates this specific completion pattern. | The user's report only mentions leader; the bug class is upstream of child behavior. | If non-leader parents hallucinate similarly, scope (G1) must widen. |
| 6 | The current `language_check` reminder precedent (`daemon/graph.py:2666-2685`) generalizes to the gate's deny path. | The in-state `HumanMessage` injection shape is the same; only the conditional-edge wiring differs. | If the precedent turns out to be insufficient, the gate can switch to the phase6 durable-enqueue path on a per-instance basis (architect decision). |
| 7 | Per-instance attempt counting via an instance-row column is sufficient for the bound; no per-mission or per-tree aggregation is needed. | The incident is per-instance; bound is per-instance. | If missions are dispatched across instances (rare), per-mission counting may be desired. |
| 8 | The mode env is restart-read via Pattern C (cached global + one-time boot log); live flip is not required. | Matches the WC-wake precedent (`daemon/services/instance_messaging.py:114-191`). | Operators wanting live flip may be surprised; clear runbook required (the dry→enforce flip is documented in the operator runbook per C-12 / NFR-16). |

---

## Out of Scope (Deferred)

- **OS-1:** The durable-enqueue recovery injector and its facade-forwarding / JAFP tests. RELOCATED to `phase6-fastfollow-plan.md` (C backstop, post-soak per R1/C5 interpretation). The MVP deny path is in-graph only.
- **OS-2:** The inter-report gap premature-finalize bug class (during the gap between processing one child report and the next arriving, the bus gate and pending-tasks gate can both pass). This is a separate completion-path vulnerability with different root cause; it is the OS-2 class that the C backstop (phase6) addresses — but only AFTER Phase-1 through Phase-5 soak data is adjudicated.
- **OS-3:** Child-side hallucination prevention (preventing child agents from emitting "in progress" reports when work isn't started). Different problem class; out of scope for leader-completion-attestation.
- **OS-4:** Per-tree or per-mission attempt counting (only per-instance is in scope).
- **OS-5:** Live-flip mode env (restart-only is in scope; live flip deferred to a future feature if requested).
- **OS-6:** Nudge text customization per mission type (a single constant text is in scope, mirrors `language_check` reminder convention).
- **OS-7:** Replay of historical hallucination incidents to validate the feature (manual testing only at MVP; production soak validates later via `AC-E2E-6` dry-mode adjudication).
- **OS-8:** Cross-instance attestation coordination (multiple leaders attesting a shared mission). Not in scope; leaders are per-instance.
- **OS-9:** A pre-Phase-2 dry-run observable. The instrumented dry-run is satisfied by the default `mode=dry` plus `NFR-16` promotion metrics plus an operator runbook — there is no separate blocking pre-Phase-2 dry-run activity (per architect D8 ruling).

---

## Traceability Matrix

| Constraint | Requirement(s) |
|------------|----------------|
| Loop safety (per-instance bounded retry + reset-on-allow + reset-on-terminal_after_bound) | FR-6, NFR-3, AC-6.1, AC-6.2, AC-6.3, AC-6.4, AC-6.5, AC-E2E-2, C-11 |
| Mode env (kill-switch replacement) + restart-read | FR-8, FR-12, NFR-5, AC-7.3, AC-7.4, AC-7.5, AC-7.6, AC-7.7, AC-E2E-4 |
| Configurable attestation window (not hardcoded; boot-assert against compaction floor) | FR-7, NFR-13, AC-7.1, AC-7.2, AC-7.8, AC-2.5, O1 |
| Leader-scoped authz via meta.json tools.allow + fail-closed | FR-9, NFR-7, AC-1.2, AC-9.1, AC-9.2, AC-9.3 |
| **In-graph nudge semantics** (per R1: no enqueue, no revive on deny) | FR-3, FR-4, NFR-4, NFR-6, C-3, AC-3.1, AC-4.1, AC-4.2, AC-4.3, AC-4.4, AC-E2E-1, AC-E2E-8 (relocated enqueue to phase6) |
| **Pending-wakeup input** (per R2: deny only when `pending_children == 0` AND `queued_or_expected_wakeups == 0`) | FR-3, C-4, AC-3.3, AC-E2E-1b |
| Dry-log schema (R2 inputs + `attest_seen_outside_window` + `messages_scanned`) | FR-10, NFR-8, NFR-16, AC-10.1, AC-10.3, AC-E2E-6 |
| Fail-open on scanner exception (W4 precedent) + OperationalError carve-out | FR-13, NFR-15, C-7, AC-6.6, AC-10.4, AC-13.1, AC-13.2, AC-E2E-7 |
| Must-not-break: normal completion with attestation | NFR-11, AC-E2E-3 |
| Must-not-break: mission finalize | NFR-11, AC-E2E-5 |
| Must-not-break: revive semantics | NFR-11, AC-E2E-5, G11 (resolved by R1: no revive on deny) |
| Must-not-break: WC-wake routing lanes | C-8, NFR-11, AC-E2E-5 |
| Must-not-break: report-delivery recovery sweeps | NFR-11, AC-E2E-5 |
| Must-not-break: report-injection claim state machine | NFR-11, AC-E2E-5 |
| Must-not-break: in-state HumanMessage reminder precedent | NFR-11, AC-E2E-5, FR-4 |
| Must-not-break: defer-starvation footgun | NFR-12 |
| Three-step tool registration discipline | C-1, FR-9, AC-1.3, AC-9.3 |
| Facade-forwarding discipline (manager.enqueue_message) — RELOCATED to phase6 with the durable-enqueue recovery injector | C-7 (orig), phase6 C-7 |
| KNOWN_TOOL_NAMES drift test | C-1, FR-9, AC-1.3, AC-9.3 |
| Performance bound (P95 ≤ 20 ms) | NFR-1, NFR-2 |
| Observability (log signature + boot line + W5 dry-adjudication) | FR-10, FR-12, NFR-8, NFR-9, NFR-16, AC-10.1, AC-10.2, AC-10.3, AC-10.4, AC-7.7 |
| Scope (leader-only vs all parents) — RESOLVED → D3 (leader-only v1, closed-by-leader) | FR-11, C-6, G1, AC-11.1 |
| Tool canonical name — RESOLVED → D7 (closed-by-leader: `attest_completion`, no-arg, idempotent, NOT privileged) | G3 |
| Window N default — RESOLVED → D4 (closed-by-leader: N=3, env `ENSEMBLE_LEADER_ATTESTATION_WINDOW`, Pattern C resolver, restart-read) | G5, FR-7 |
| Bound default — RESOLVED → D5 (closed-by-leader: bound=3, env `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`, Pattern C resolver, restart-read; sub-resolution at `decisions.md:321`) | G6 |
| Per-instance attempt ledger storage — RESOLVED → D5 | FR-6, AC-6.5, C-11 |
| Operator termination bypass — OPEN | G12 |
| Inter-report gap bug class — separate | C-9, OS-2 |
| Phase6 backstop (durable-enqueue recovery; out of MVP) | C-3, AC-E2E-8, OS-1, OS-2 |
| Test strategy (unit for scanner/decision/fail-open; integration for full E2E + dry-adjudication) | AC-2.1..AC-2.5, AC-3.1..AC-3.4, AC-4.1..AC-4.5, AC-6.1..AC-6.6, AC-7.1..AC-7.9, AC-9.1..AC-9.3, AC-10.1..AC-10.4, AC-11.1, AC-13.1, AC-13.2, AC-E2E-1..AC-E2E-8 |

---

## 2026-09-06 Amendments (append-only — historical rows above are NOT rewritten)

### FR-3 third-input amendment (additive — original deny semantics preserved)

The FR-3 deny predicate as ratified above (two-input: `pending_children == 0 AND queued_or_expected_wakeups == 0`) is EXTENDED by a THIRD, additive input `live_descendants` (counted by `InstanceManager.count_live_descendants(instance_id)` — BFS over permanent `instances.parent_id`, root excluded, status NOT IN {COMPLETED, TERMINATED, ERROR, FAILED}, capped at `LIVE_DESCENDANTS_BFS_CAP = 500`). The new FR-3 deny predicate (read in full) is:

- NOT attested, AND
- `pending_children == 0`, AND
- `queued_or_expected_wakeups == 0`, AND
- `live_descendants == 0` ← **third input, 2026-09-06**

Closes the 809e2a59 waiting_children false-deny incident class (transitive grandchild alive but `pending_children` and `queued_or_expected_wakeups` both 0 because the watcher lifecycle is per-turn — see `decisions.md` "2026-09-06" entry for full root-cause narrative). DO NOT TOUCH the watcher lifecycle; the third input reads the permanent INSTANCE tree, NOT `dependency_bus`. Decision enum is NOT changed (no new member) — `ALLOWED_LEGITIMATE_PENDING_WAKEUP` already covers the allow predicate; a non-zero `live_descendants` is the same allow predicate semantically.

### FR-10 schema amendment (additive — original 15 fields preserved, one appended)

The FR-10 canonical log schema as ratified above is EXTENDED by ONE additive field — `live_descendants: int` — appended to `CANONICAL_LOG_SCHEMA_FIELDS` (now 16 fields, exported at `daemon.services.attestation_gate.CANONICAL_LOG_SCHEMA_FIELDS`). The DB-seam fail-open path (`event=leader_completion_gate_db_error`) emits `live_descendants=-1` alongside the existing two `-1` sentinels (`pending_children=-1`, `queued_or_expected_wakeups=-1`). The scanner-fail-open path (`event=leader_completion_gate_error`) emits `live_descendants=-1` alongside the existing UNKNOWN sentinels. `docs/setup.md:545, :548, :576` updated to document the 16-field schema and the three-input deny predicate.

### FR-3 conditionality amendment (2026-09-06 — replace the unconditional deny with a delegation-gated deny)

The FR-3 deny predicate as ratified above (unconditional: "NOT attested AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` AND `live_descendants == 0`") is REPLACED by a CONDITIONAL predicate:

- `attestation_required == True` (computed by the new R0 input `delegation_since_last_user := send_message tool call in AIMessages at-or-after the last real user message), AND
- `NOT attested`, AND
- `pending_children == 0`, AND
- `queued_or_expected_wakeups == 0`, AND
- `live_descendants == 0` ← preserved from the 2026-09-06 third-input amendment.

When `attestation_required == False` (no `send_message` tool call since the last real user message), the gate ALLOWS without demanding `attest_completion` — a quick follow-up question, chart rendering, or other non-delegating turn completes normally. The decision value is `ALLOWED` (no new enum member; the schema field carries the verdict). The counter math is unchanged (a non-fire is NOT one of the four reset triggers per leader ruling 1).

DEGENERATE FALLBACK: when no real user message exists in the conversation (`is_real_user_message` returns `False` for every HumanMessage; `find_last_real_user_index` returns `-1`), the conditional scanner falls back to walking the WHOLE message list — `delegation_since_last_user` becomes `True` IFF any `send_message` tool call appears anywhere. Conservative, preserves the prior deny protection if the real-user anchor drifts.

SELF-REFERENCE TRAP (design trap, closed): the deny-path injection is a `HumanMessage` carrying `additional_kwargs["attestation_nudge"] == True`. The `is_real_user_message` predicate excludes this marker — the deny nudge does NOT shift the delegation anchor, so the gate stays ON across deny→nudge→next-turn-end cycles. Without this exclusion the feature would self-defeat on the first deny.

### FR-4 nudge amendment (2026-09-06 — system-context header + self-sufficient body)

The FR-4 in-graph nudge text as ratified above (the body starting with `"The work is not yet finished — check current progress ..."`) is AMENDED to:

1. Lead with a single non-blank header line `[SYSTEM CONTEXT: Completion Check Nudge]` (so the LLM parses it as system-origin at read time). The header is the system-origin marker; the underlying message role remains user-authored (R1 — checkpoint-durable). The header DOES NOT change the `additional_kwargs["attestation_nudge"] == True` marker that the scanner exclusion predicate reads.
2. Expand the body to RESTATE the conditional semantics ("This gate is CONDITIONAL on delegation: it fires ONLY when a child was dispatched (a `send_message` tool call happened) since the last real user message. Plain questions, chart requests, and other non-delegating turns do NOT trigger this gate. When you have dispatched a child this mission, the work is not complete until you attest.") — the runtime nudge is now self-sufficient because the prompts no longer teach the unconditional MUST-call contract.
3. PRESERVE the substance ("The work is not yet finished — check current progress (tasks/children status) and continue") and the two-step teaching ("FIRST deliver your full detailed final report as its own message; THEN call `attest_completion` ALONE as a subsequent step — never bundle the report into the attestation tool-call message").

All NUDGE_TEXT verbatim pin tests updated. The marker kwargs (`attestation_nudge: True`, `attestation_nudge_denied_count: int`) are UNCHANGED — back-compat with the scanner exclusion predicate. The text is pinned by `daemon.graph.ATTESTATION_NUDGE_TEXT` (the single source of truth) and imported by all tests via `from daemon.graph import ATTESTATION_NUDGE_TEXT as NUDGE_TEXT` (no per-file hardcoded copies).

CONVENTION EXCEPTION (appendix): per the App Architecture blueprint, `[SYSTEM NOTE: …]` frames are data-only. The user explicitly asked for a `[SYSTEM CONTEXT: Completion Check Nudge]` header on the deny-path HumanMessage so the LLM recognizes it as system-origin. This APPEND-ONLY exception REUSES the existing `[SYSTEM CONTEXT: ` prefix deliberately so the same `is_real_user_message` content-sentinel exclusion (step 5 of the exclusion ladder — body starts with `[SYSTEM CONTEXT:`) catches it symmetrically. No new prefix is introduced; no existing prefix convention is broken.

### FR-10 schema amendment #2 (2026-09-06 — schema grows 16→17 fields)

The FR-10 canonical log schema as extended above (16 fields) is FURTHER EXTENDED by ONE additive field — `attestation_required: bool` — appended to `CANONICAL_LOG_SCHEMA_FIELDS` (now 17 fields, exported at `daemon.services.attestation_gate.CANONICAL_LOG_SCHEMA_FIELDS`). The field is positioned AFTER `live_descendants` in the canonical tuple (grouped with the conditional/R2 input family). The DB-seam fail-open path (`event=leader_completion_gate_db_error`) and the scanner-fail-open path (`event=leader_completion_gate_error`) emit `attestation_required=False` (the safe fail-OPEN default — failing closed would deny un-attested completion on the scanner fault class, which the C3 fail-open ruling rejects).

SUPPLEMENTARY DIAGNOSTIC FIELDS (NOT in the canonical 17-field schema tuple, but emitted in the format-string log line for dry-mode soak and FR-3 conditionality audit): `delegation_since_last_user` (bool — mirror of the scanner verdict; the same exclusion-predicate semantics the gate uses), `last_real_user_found` (bool), `last_real_user_index` (int, -1 when no real user message exists), `first_delegation_after_last_user_index` (int, -1 when no `send_message` tool call at-or-after the last real user message), `delegation_tool_call_total` (int — normalized log shape). `docs/setup.md:545, :548, :576` updated to document the 17-field schema, the conditional predicate, and the deny-log diagnostic surface.

### NFR-6 nudge text (VER-6.4 — verbatim pin remains, content amended)

The NFR-6 verbatim pin of the in-graph nudge text remains in force — `tests/unit/test_attestation_nudge_inject.py::test_deny_injects_checkpoint_plain_dict_and_routes_to_agent` AND `tests/unit/test_attestation_nudge_inject.py::test_nudge_header_is_first_nonblank_line_and_marker_unchanged` pin the new content via `daemon.graph.ATTESTATION_NUDGE_TEXT` (the single source of truth, imported by all integration tests). The verbatim-pin convention is unchanged: integration tests import the canonical constant rather than hardcoding a per-file copy.

---

## Phase 6 fastfollow (2026-09-07) — Inline-LLM completion-report judge

### FR-12 — Inline LLM judge on the would-be-deny path

> **Stage-3 supersession (2026-09-17):** the historical Phase-6 judge contract below is superseded by **R-RES4-1..12** — the would-be-deny window judge retired with R7; the CONDITIONAL FUSED judge (`judge_fused_bundle_async`, `FUSED_JUDGE_SYSTEM_PROMPT`, `FusedJudgeResult`, `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048`, the `leader_completion_gate_fused_judge*` event family) is the sole judge surface. Text kept as the historical record.

**Requirement:** After the gate reaches `Decision.DENIED` (delegated mission, no `attest_completion` in window, nothing pending) and BEFORE injecting the in-graph nudge, the gate MUST call an inline LLM (direct chat completion — NOT an instance spawn) to judge "are the leader's last messages a REAL completion report?". If the judge says YES → complete normally WITHOUT the toolcall (decision=ALLOWED, no nudge). If the judge says NO (or the call errors / times out / returns unparsable JSON) → existing deny+nudge path unchanged.

**Implementation surface:**

* New module `daemon/services/attestation_report_judge.py` (the judge service — pure-callable, no global state).
* New module `daemon/services/attestation_judge_resolver.py` (Pattern C sibling resolver — kill-switch only).
* Extension to `daemon/services/attestation_resolver.py` (`emit_attestation_boot_log` extended with `llm_judge_enabled` + `llm_judge_model`).
* Extension to `daemon/services/attestation_gate.py` (`build_gate_config(...)` grows `llm_judge_enabled: bool = True` kwarg; `GATE_CONFIG_KEYS` extended).
* Extension to `daemon/graph.py::create_attestation_gate_node` (judge runs on the would-be-deny path BEFORE the counter increment; judge-yes → return END; judge-no / error / timeout / unparsable → fall through to existing deny+nudge).

**Judge system prompt contract:** strict, role-anchored, conservative, demands strict JSON output `{"is_complete_report": <bool>, "reason": "<one-sentence rationale>"}` (no markdown, no prose, no code fences, no commentary). The single source of truth is `JUDGE_SYSTEM_PROMPT` at `daemon/services/attestation_report_judge.py`.

**Fail-safe direction:** every error path (timeout, exception, unparsable JSON) returns `is_complete_report=False`. The judge NEVER raises. The judge emits `event=leader_completion_gate_judge_error` for wrapper-layer bugs and degrades to the existing deny+nudge path. The 3-deny escalation bound caps worst-case misfires.

**Bounds:** `JUDGE_TIMEOUT_S=10.0` seconds; `JUDGE_MAX_INPUT_CHARS=12,000` chars (per-message budget = `MAX / count`); `JUDGE_MAX_OUTPUT_CHARS=400` chars.

### FR-13 — Kill-switch (Pattern C, default ON)

**Requirement:** `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` (Pattern C restart-read resolver; default ON; `=0` / `=false` / `=no` / `=off` disables). When OFF → judge never invoked; gate's pre-feature byte-identical behavior is preserved.

**Surface:**
* `daemon/services/attestation_judge_resolver.is_llm_judge_enabled()` cached-global resolver.
* `daemon/services/attestation_judge_resolver.reset_llm_judge_resolver_for_tests()` test-only cache reset (also wired into `attestation_resolver.reset_attestation_resolver_for_tests`).
* `daemon/services/attestation_gate.build_gate_config(...)` grows `llm_judge_enabled: bool = True` kwarg (per-instance override — back-compat default True).
* The gate node reads BOTH sources: `is_llm_judge_enabled()` AND `gate_config.get("llm_judge_enabled", True)`. Either being False skips the judge.

**Boot log extension:** `Leader completion attestation resolved: … llm_judge_enabled=<true|false> llm_judge_model=<name>|<disabled> …` (the existing one-shot INFO line gains the judge state; O1 PASS/WARN tag preserved).

### FR-14 — Model resolution (honors OPENAI_MODEL_KEYWORDS with fallback)

**Requirement:** The judge MUST honor `OPENAI_MODEL_KEYWORDS` (= `config.llm.model_keywords`) with fallback to `OPENAI_MODEL` (= `config.llm.model`). The judge module exposes `resolve_judge_model(config) -> str` which mirrors `daemon/services/keyword_extraction.py` semantics verbatim. Setting `OPENAI_MODEL_KEYWORDS=quick` (or similar) pins the judge to a fast model; leaving unset inherits the main `OPENAI_MODEL`.

**Investigation outcome (per the dispatcher's pre-flight flag):** The actual semantics of `OPENAI_MODEL_KEYWORDS` are PLAIN MODEL NAME STRING, not a keyword-based selector. The LLMConfig field is declared at `daemon/config.py:130-137` (`model_keywords: str | None = None`); the `set_title_model_fallback` model_validator at `daemon/config.py:424-431` collapses empty `model_keywords` to `model`. There is NO keyword-to-model mapping logic anywhere in `daemon/config.py`. The operator docs at `config.yaml:22` and `.env.example:42` describe the field as a literal model name ("Set to 'quick' to mirror the explorer agent's llm_model"). The judge's implementation matches this straightforward reading.

### FR-15 — Observability (diagnostic extras outside the canonical 17-field tuple)

**Requirement:** The judge MUST emit one structured log line per call carrying the diagnostic extras `verdict` / `llm_judge_verdict` / `llm_judge_model` / `llm_judge_latency_ms` / `llm_judge_reason` / `llm_judge_error_class`. These fields are diagnostic extras — NOT in the canonical 17-field `leader_completion_gate` tuple (same pattern as the supplementary conditional-attestation fields). Operators grep `event=leader_completion_gate_judge` to surface judge outcomes.

**Surface:**
* `event=leader_completion_gate_judge` — emitted on every judge call (yes / no / error / timeout / unparsable).
* `event=leader_completion_gate_judge_error` — emitted for wrapper-layer bugs (defense-in-depth — the judge itself already converts all internal failures to JudgeResult with `is_complete_report=False`).
* The canonical 17-field tuple is UNCHANGED; existing test pins coherent.

### FR-16 — Nudge mermaid accuracy (ReportJudge decision node)

**Requirement:** The mermaid embedded in `ATTESTATION_NUDGE_TEXT` gains ONE new decision node between `"AttestRecent -- No"` and `"Nudged"` — the `ReportJudge` decision ("Did the gate's report judge confirm a real completion report?"). Yes → `FinishGate`; No → `Nudged`. The canonical byte-pin (`EXPECTED_NUDGE_TEXT_CANONICAL`) is updated in the same commit. All other `ATTESTATION_NUDGE_TEXT` import sites (lane artifacts in `.agents/tester/RESULTS/`, integration tests under `tests/integration/`) import the constant via `from daemon.graph import ATTESTATION_NUDGE_TEXT` — single source of truth, no per-file byte drift.

### Tests (acceptance suite — all required, all green at ship)

The acceptance matrix below is the spec-driven suite (per the dispatcher's contract). All 9 scenarios are pinned.

| # | Scenario | Pinned in |
|---|----------|-----------|
| (a) | judge-yes → ALLOWED, no nudge, no counter write | `tests/unit/test_attestation_judge_wiring.py::test_judge_yes_allows_without_nudge_no_counter_increment` |
| (b) | judge-no → deny+nudge, counter increments | `tests/unit/test_attestation_judge_wiring.py::test_judge_no_falls_through_to_deny_nudge_increments_counter` |
| (c) | judge-timeout → deny+nudge | `tests/unit/test_attestation_judge_wiring.py::test_judge_timeout_falls_through_to_deny_nudge` |
| (d) | judge-error → deny+nudge | `tests/unit/test_attestation_judge_wiring.py::test_judge_generic_error_falls_through_to_deny_nudge` |
| (e) | unparsable → deny+nudge | `tests/unit/test_attestation_judge_wiring.py::test_judge_unparsable_falls_through_to_deny_nudge` |
| (f) | kill-switch OFF (env=0 AND gate-config flag=False) → judge never called | `tests/unit/test_attestation_judge_wiring.py::test_judge_not_called_when_gate_config_flag_off` + `test_judge_not_called_when_env_kill_switch_off` |
| (g) | model fallback resolution (OPENAI_MODEL_KEYWORDS unset → falls back to OPENAI_MODEL; set → uses that) | `tests/unit/test_attestation_judge_wiring.py::test_model_fallback_to_main_when_keywords_empty` + `test_model_uses_keywords_when_set` |
| (h) | log fields present on judge paths (verdict, model, latency_ms, reason, error_class) | `tests/unit/test_attestation_judge_wiring.py::test_log_fields_present_on_judge_yes` + `test_log_fields_present_on_judge_error` |
| (i) | judge NOT called on non-deny paths (attested, not-required, pending-wakeup) | `tests/unit/test_attestation_judge_wiring.py::test_judge_not_called_on_attested_path` + `test_judge_not_called_on_unrequired_path` + `test_judge_not_called_on_pending_wakeup_path` |

Judge pure-function unit tests (33 cases in `tests/unit/test_attestation_report_judge.py`) cover `resolve_judge_model`, `_slice_judge_window`, `_format_window_for_judge`, `_parse_judge_response` (strict JSON, code-fence leakage, substring match, multi-object, non-object, missing field, wrong type, reason-length cap, empty input), `judge_completion_report_async` (yes/no/unparsable/code-fence/timeout/error/empty-messages/truncation), `judge_completion_report_sync` (same shape), and the constants (`JUDGE_TIMEOUT_S`, `JUDGE_MAX_INPUT_CHARS`, `JUDGE_MAX_OUTPUT_CHARS`, `JUDGE_DEFAULT_WINDOW`, `JUDGE_SYSTEM_PROMPT` shape).

### Files (Phase 6 fastfollow judge)

- `daemon/services/attestation_judge_resolver.py` (new) — Pattern C kill-switch resolver (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`).
- `daemon/services/attestation_report_judge.py` (new) — the judge service.
- `daemon/services/attestation_resolver.py` — `emit_attestation_boot_log` extended; `reset_attestation_resolver_for_tests` extended.
- `daemon/services/attestation_gate.py` — `build_gate_config(...)` grows `llm_judge_enabled: bool = True` kwarg; `GATE_CONFIG_KEYS` extended.
- `daemon/graph.py` — judge wiring in `create_attestation_gate_node`; `ATTESTATION_NUDGE_TEXT` mermaid extended.
- `tests/unit/test_attestation_report_judge.py` (new — 33 tests).
- `tests/unit/test_attestation_judge_wiring.py` (new — 16 tests, acceptance matrix).
- `tests/unit/test_attestation_nudge_inject.py` — `EXPECTED_NUDGE_TEXT_CANONICAL` updated for the new mermaid.
- `docs/setup.md` — new "Inline-LLM completion-report judge" section appended.

### Requirement (operator tuning — 2026-09-07) — Judge wall-clock cap env-tunable

**Context:** the prior hardcoded `JUDGE_TIMEOUT_S = 10.0` was timeslicing genuine-report quick-model calls. Tester live-LLM probe (2026-09-07, evidence commits `b42f7237..2a43904c` on branch `feature/leader-completion-attestation`, captured at `.agents/tester/RESULTS/2026-09-07-lca-judge-live-probe*`) measured real quick-model latencies: successes 2.6s–13.6s, with **4/8 calls >15s**. The 10.0s cap was firing on a substantial fraction of genuine-report calls and silently flipping the gate to the conservative deny+nudge path, defeating the feature's purpose.

**Requirement:** the judge wall-clock cap MUST be env-tunable via `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` (Pattern C restart-read resolver, sibling to the boolean kill-switch resolver at `daemon/services/attestation_judge_resolver.py` and the main tri-state mode resolver at `daemon/services/attestation_resolver.py`). Default `25.0` seconds (bumped from 10.0 — operator tuning decision 2026-09-07 grounded in the probe data). Minimum clamp `5.0` seconds — values below the clamp clamp to the floor with a one-shot WARN. The runtime value MUST flow to BOTH seams: the `asyncio.wait_for` cap + HA facade `wall_clock_cap_s` (the operator-visible wall-clock bound) AND the W1 `request_timeout` coupling (per-attempt HTTP timeout bound per the compaction-site precedent at `daemon/manager.py:398`; the per-attempt value is `min(resolved_timeout, config.llm.request_timeout or resolved_timeout)`).

**Acceptance criteria:**

* AC-T1: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S` unset / blank → resolved to `25.0`; no WARN.
* AC-T2: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=<positive-float-above-clamp>` → parsed as a float; no WARN; flows to both seams.
* AC-T3: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=<non-numeric>` (e.g. `abc`, `1.5x`) → resolved to `25.0` (fail-OPEN); one-shot WARN emitted.
* AC-T4: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=<non-positive>` (e.g. `0`, `-1`) → resolved to `25.0` (fail-OPEN); one-shot WARN emitted.
* AC-T5: `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S=<positive-but-below-clamp>` (e.g. `3`, `4.9`) → resolved to `5.0` (clamp); one-shot clamp WARN emitted.
* AC-T6: cached-global wins over mid-flight env mutation (Pattern C restart-read); `reset_judge_timeout_resolver_for_tests()` clears the cache + one-shot WARN flags so tests re-resolve under mutated env.
* AC-T7: the resolved value flows to BOTH the `asyncio.wait_for` cap (the `timeout_s` pass-through to `_invoke_judge_llm`) AND the W1 `request_timeout` coupling (`min(resolved, config.llm.request_timeout or resolved)`); pinned by `tests/unit/test_attestation_judge_wiring.py::test_resolved_timeout_flows_to_judge_call` + `test_resolved_timeout_default_when_env_unset` + `test_resolved_timeout_below_clamp_flows_clamp_to_seams` + `test_resolved_timeout_explicit_kwarg_overrides_resolver` + `test_resolved_timeout_w1_coupling_uses_min`.
* AC-T8: `emit_attestation_boot_log` carries `llm_judge_timeout_s=<resolved>` (e.g. `llm_judge_timeout_s=25.0`); pinned by the existing `test_boot_log_line_includes_judge_info` (which still passes — no new field pin required, the boot log is grep-readable) + the docs/setup.md example.
* AC-T9: `reset_attestation_resolver_for_tests` clears BOTH sibling resolver caches (kill-switch + timeout) so a test that flips either env sees the change on the next call.
* AC-T10: the kill-switch cache + timeout cache are independent — resetting one MUST NOT clobber the other (pinned by `test_sibling_resolver_caches_are_independent`).

**Files (this requirement):**

- `daemon/services/attestation_judge_timeout_resolver.py` (new) — Pattern C timeout resolver (env `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_TIMEOUT_S`, default `25.0`, min clamp `5.0`, fail-OPEN on invalid).
- `daemon/services/attestation_report_judge.py` — `JUDGE_TIMEOUT_S` constant bumped to `DEFAULT_JUDGE_TIMEOUT_S` (25.0; documented default reference only); the `_invoke_judge_llm` W1 coupling + `asyncio.wait_for` cap + failover `wall_clock_cap_s` now all read from the resolver; the public async + sync entry points use `timeout_s: float | None = None` and resolve inside.
- `daemon/services/attestation_resolver.py` — `emit_attestation_boot_log` extended with `llm_judge_timeout_s=<resolved>` + an additional env readout; `reset_attestation_resolver_for_tests` also clears the new timeout cache + one-shot WARN flags.
- `docs/setup.md` — the inline-LLM completion-report judge section updated (bounds line shows `JUDGE_TIMEOUT_S=25.0s` env-tunable; boot-log example updated to include `llm_judge_timeout_s=25.0`; new "Judge wall-clock cap" subsection documents the env name, default, min clamp, fail-OPEN policy table, and the 2026-09-07 tuning rationale).
- `tests/unit/test_attestation_judge_resolver.py` — extended with the timeout truth table (AC-T1..T6 + the WARN-emission discipline + AC-T10); 29 new tests, file total 46 tests.
- `tests/unit/test_attestation_judge_wiring.py` — extended with 5 new wiring tests (AC-T7); file total 26 tests.
- `tests/unit/test_attestation_report_judge.py` — `test_constants_pinned` updated to assert `JUDGE_TIMEOUT_S == 25.0` (was 10.0).

**Test-count truth-table discipline (grep-verified 2026-09-07):** the three touched unit files ship **106 tests total** (`pytest --collect-only -q`): `test_attestation_report_judge.py` 34, `test_attestation_judge_resolver.py` 46, `test_attestation_judge_wiring.py` 26. The full attestation matrix (40 files) collects the baseline + these new tests; see the Coder final report for the exact run numbers (any drift here is a doc-truth violation — the matrix run is ground truth).
---

**2026-09-08 user decision:** Completion Attestation prompt-contract sections removed from `agents/leader/rule.md` + `agents/leader/workflow.md`. The deny-time nudge is the sole teaching source (header + conditional semantics + two-step pattern + embedded mermaid); the LLM judge releases genuine reports. Rationale: a standing prompt section is redundant. Accepted cost: possibly one extra nudge cycle on delegated missions whose report the judge cannot confirm.
---

**2026-09-11 incident requirement (incident b08f40fe): `live_descendants` counts WORK-BEARING descendants only (two-set semantics).**

**Requirement:** `InstanceManager.count_live_descendants` (attestation-gate R2 third input) must not let dormant never-reporting descendants satisfy branch (5). Verified incident shape: leader `b08f40fe` completed 2026-09-11 14:50:10 UTC via `allowed_legitimate_pending_wakeup` with `live_descendants=4` where all four were IDLE-orphan grandchildren (spawned by tester `c6f57749`, never dispatched: 0 `message_queue` rows, 0 `message_metadata` rows, all dependency watchers FIRED). Branch (5)'s premise — "a child report will revive the leader" — is false for orphans; they must not look like delegation-in-flight.

**Acceptance criteria:**

* AC-L1: UNCONDITIONAL-live = `{RUNNING, WAITING, WAITING_CHILDREN, PAUSED}` — counted with no further checks; PAUSED-is-live unchanged.
* AC-L2: CONDITIONAL-live = `{IDLE, QUEUED}` — counted IFF (a) a not-yet-processed `message_queue` row targets the descendant (`PENDING`/`READY`/`PROCESSING`/`RETRYING`), OR (b) a not-yet-settled `job_queue_items` row targets it (`admission_state` QUEUED | ACTIVE — the `ACTIVE_ADMISSION_STATES` in-flight set). The job lane is mandatory (an IDLE instance with only a QUEUED job has no message row); DONE/DEAD never count.
* AC-L3: IDLE orphan with NO message and NO unsettled job is NOT live — the incident regression at the count level AND at the gate level (branch (5) must not fire; DENIED + nudge + counter increments; pinned by `tests/integration/test_attestation_idle_orphan_incident.py` and the count-level split in `tests/integration/test_attestation_live_descendants.py`).
* AC-L4: terminal set unchanged (`COMPLETED`/`TERMINATED`/`ERROR`/`FAILED` excluded); BFS cap, permanent-`parent_id` walk, and root exclusion preserved byte-for-byte in behavior.
* AC-L5: fail-open preserved — a DB error in the new conditional sub-checks propagates to the gate DB seam (`live_descendants=-1`, fail-open ALLOWED); it never fails toward orphan=not-live. Unwired lanes contribute no signal.
* AC-L6: after the orphans are terminated and `attest_completion` is in window → attested ALLOWED (counter reset); terminated-but-unattested still denies (original protection survives).
* AC-L7: gate-level test reconstructs the EXACT b08f40fe tree (8 terminal children + 4 IDLE-orphan grandchildren under the tester child, delegation anchored after the last real user message, mode=enforce) — named after the incident and referencing `b08f40fe` in its docstring.

**Files (this requirement):** `daemon/manager.py`; `daemon/repositories/message_queue/repository.py`; `tests/support/conftest.py`; `tests/integration/test_attestation_live_descendants.py`; `tests/integration/test_attestation_idle_orphan_incident.py` (new); `docs/setup.md`.

---

## Requirement (incident b08f40fe follow-up — 2026-09-11) — Mid-work marker scan trigger on ALLOW paths

**Context:** incident b08f40fe (2026-09-11) closed via the two-set `live_descendants` semantics (D-ENTRY 2026-09-11 above). The orphan-fix closes the IDLE-orphan subtree branch but the broader class — leader PROSE that reads mid-work while the R2 inputs are clean — is still undetected by any existing gate predicate. The leader's final AIMessage VERBATIM was "Awaiting final four: C12a/b/c + blame-worker. Then I aggregate and write RESULTS. Ending turn." With the orphan-fix applied (`live_descendants=0`), the natural decision is DENIED via the canonical R2 predicate; but on a delegated mission whose natural decision is ALLOWED_LEGITIMATE_PENDING_WAKEUP (real wakeup en route), no predicate detects the mid-work phrasing — the leader completes silently.

**Requirement:** the gate MUST detect mid-work phrasing on ALLOW paths (attested_allow is skipped; the conditional-OFF branch is INCLUDED) and route through the existing inline-LLM judge. Markers + judge-no + nothing pending → CONVERT TO DENY + nudge + counter increment. Markers + judge-no + real pending → ALLOW + checkpoint-durable hint (no counter, no deny, no re-route; the turn still ends so the wake-up can arrive). Markers + judge-yes → ALLOW normally. Markers + judge-error/timeout/unparsable → (a)-behavior if nothing pending, (b)-behavior otherwise (conservative fall-through).

**Acceptance criteria:**

* **AC-M1**: marker catalog is curated (12-18 patterns from the incident family: "ending turn", "ending my turn", "awaiting", "then i aggregate", "then i compile", "will write", "will aggregate", "not a completion report", "interim", "in progress", "not yet complete", "still pending", "to be continued", "will report back", "standby", "stand by").
* **AC-M2**: the verbatim b08f40fe line "Awaiting final four: C12a/b/c + blame-worker. Then I aggregate and write RESULTS. Ending turn." fires the marker scan.
* **AC-M3**: legitimate completion prose ("All work shipped. Done.", "Nothing pending, all shipped", "I delivered the report") does NOT fire the marker scan.
* **AC-M4**: case-insensitive match (uppercase, mixed-case, lowercase all fire on the same marker).
* **AC-M5**: window bounded — only the last `ENSEMBLE_LEADER_ATTESTATION_WINDOW` AIMessages are inspected; a marker in an older AIMessage is invisible.
* **AC-M6**: only AIMessages contribute to the scan (HumanMessage / ToolMessage / SystemMessage are invisible).
* **AC-M7**: hook on ALLOW paths only — `Decision.DENIED` / `TERMINAL_AFTER_BOUND` / `DRY_LOG` / meta-bypass are NOT scanned. Attested_allow is NOT scanned. Conditional-OFF (`attestation_required=False`) IS scanned.
* **AC-M8**: judge-not-complete + nothing pending → CONVERT TO DENY (existing nudge machinery: counter+1, ledger increment, nudge injection, route back to `agent`).
* **AC-M9**: judge-not-complete + real pending → ALLOW + checkpoint-durable hint (`HumanMessage` with `[SYSTEM CONTEXT: Completion Check Note]` header, construction-time `id` invariant via `_make_context_message` factory).
* **AC-M10**: judge-yes → ALLOW normally (no nudge, no counter, no hint).
* **AC-M11**: judge error / timeout / unparsable on marker path → (a)-behavior if nothing pending, (b)-behavior otherwise (log `judge_error`).
* **AC-M12**: kill-switch OFF (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` or `llm_judge_enabled=False` in gate-config) → markers logged, NO judge call, plain ALLOW in ALL marker cases.
* **AC-M13**: no markers in the message tail → no judge call (cost control).
* **AC-M14**: canonical `event=leader_completion_gate` log row carries the additive marker fields (marker_hit, marker_terms, marker_path, marker_judge_verdict, marker_judge_latency_ms, marker_judge_error_class). Marker-path judge call ALSO emits a separate `event=leader_completion_gate_marker_judge` log line.
* **AC-M15**: existing would-be-deny judge (`event=leader_completion_gate_judge` on `Decision.DENIED`) is SKIPPED when `decision.marker_path in {"a", "d"}` — the marker-path judge IS the disambiguator; double-judge must not fire.
* **AC-M16**: `Completion Check Note` text is a single-source-of-truth constant in `daemon/graph.py` (canonical home; NFR-6 parity with `ATTESTATION_NUDGE_TEXT`). The header `[SYSTEM CONTEXT: Completion Check Note]` reuses the existing prefix convention so the `is_real_user_message` predicate in `attestation_scanner.py` recognizes it as not-a-real-user-message.

---

## Requirement (user 2026-09-12) — Word-count trigger (< 150 words) for the allow-path judge (length trigger)

**Context:** User observation 2026-09-12 — mid-work ACKs are often SHORT with no marker words ("Understood, continuing." / "OK, waiting on the tester."). The marker catalog (AC-M1) catches phrasing ("ending turn", "awaiting"); the brevity class is INDEPENDENT signal that the marker-only catalog misses. Real completion reports the leader would put through this gate are normally detailed (hundreds of words); very-short prose on an ALLOW path is suspicious. This requirement adds the word-count trigger as the second half of the two-stage disambiguator.

**Requirement:** the gate MUST detect brevity-class prose on ALLOW paths and route through the existing inline-LLM judge. The length trigger composes with the marker scan via ``OR`` (`marker_hit OR length_trigger` → judge fires). Length trigger fires when the flattened LAST AIMessage word count is strictly less than `SHORT_REPORT_WORD_THRESHOLD` (150 words; module-level constant, NOT env-tunable). Attested allows still skip entirely. Cost control: NEITHER trigger fires ⇒ NO judge call. Same (a)/(b)/(c)/(d) routing as the marker path (judge verdict drives routing).

**Acceptance criteria:**

* **AC-L1**: `SHORT_REPORT_WORD_THRESHOLD = 150` is a module-level constant in `daemon/services/attestation_marker_scanner.py` (NOT env-tunable by design; one knob fewer; revisit at soak).
* **AC-L2**: word count is whitespace-split on the flattened LAST AIMessage content (mirrors `_flatten_ai_content` for list-of-blocks content). Pure function; no I/O.
* **AC-L3**: boundary pins — 149 words ⇒ trigger fires; 150 words ⇒ trigger does NOT fire (strict less-than); 151 words ⇒ trigger does NOT fire.
* **AC-L4**: a short AIMessage with no marker phrases ("Understood, continuing." — 2 words) triggers the length path → judge fires.
* **AC-L5**: a long detailed completion report (≥150 words) does NOT trigger the length path → no judge call (cost control preserved end-to-end).
* **AC-L6**: a short AIMessage with a marker phrase ("Understood, continuing. Ending turn." — both triggers fire) — `trigger_source="markers+length"` (the combined literal).
* **AC-L7**: a long AIMessage with a marker phrase (e.g., long report + "Ending turn." tail) — `trigger_source="markers"` (only markers fire).
* **AC-L8**: a long AIMessage with no marker phrases — `trigger_source=""` (cheap allow path; `marker_hit=False` and `length_trigger=False`; no judge call).
* **AC-L9**: degenerate empty / non-AI message tail — `length_trigger=False`, `final_word_count=0`, `messages_scanned=0` (no AIMessage to measure; mirrors the marker scanner's degenerate-tail contract).
* **AC-L10**: list-of-blocks content (LangChain text + reasoning blocks) is flattened to plain text BEFORE the word count is taken.
* **AC-L11**: only the LAST (newest) AIMessage is counted — the length trigger is a single-message signal, not an aggregate over the window.
* **AC-L12**: short AIMessage with real pending work → judge fires, judge-no → path (b) → ALLOW + checkpoint-durable Completion Check Note (no counter, no deny, no re-route; the turn still ends).
* **AC-L13**: short AIMessage containing a quick-answer artifact (e.g., "Here is the chart you asked for. <mermaid>" under 150 words) → judge fires, judge-yes → path (c) → ALLOW normally.
* **AC-L14**: dry mode + length trigger → log-only, NO judge call, plain ALLOW (side-effect-free; dry-mode `allow unconditionally` posture preserved end-to-end).
* **AC-L15**: kill-switch OFF (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` or `llm_judge_enabled=False` in gate-config) + length trigger → length signal logged, NO judge call, plain ALLOW. The existing `event=leader_completion_gate_marker_judge_disabled` row is emitted with `verdict=<skipped>` (the length trigger rides the same kill-switch as markers — single judge disable surface).
* **AC-L16**: canonical `event=leader_completion_gate` log row carries the three additive length-trigger fields (`length_trigger`, `final_word_count`, `trigger_source`). Format-string placeholder count grows 28 → 31 (drift pin in `test_length_log_placeholder_count_is_31`).
* **AC-L17**: graph-node marker-path judge wiring reads `decision.marker_hit or decision.length_trigger` (the OR-composition at the gate-node). The judge call is gated by the same kill-switch (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`); the same `(a)/(b)/(c)/(d)` routing applies. NO new event name is introduced — the existing `event=leader_completion_gate_marker_judge` and `event=leader_completion_gate_marker_judge_disabled` rows cover the length-trigger case.
* **AC-L18**: `trigger_source` derivation — `"markers+length"` when both halves fire, `"markers"` when only markers fire, `"length"` when only length fires, `""` when neither fires (logged as `<none>` for grep-disjointness with the canonical log format).
* **AC-L19**: unit test matrix covers: threshold pin, helper basics (whitespace-split semantics), 149/150/151 boundary pins, short-no-marker judge fires, long-no-marker no-judge cost control, short+complete artifact judge-yes allows, short real-pending hint, short+markers combined trigger_source, markers-only on long-with-marker, dry-mode log-only, kill-switch OFF log-only, log-row placeholder count 28→31 drift pin.

**DO NOT TOUCH (this requirement):**

* The 5-value canonical decision enum — unchanged.
* The marker catalog (16 patterns; 12-18 balance).
* The marker-path judge — unchanged, REUSED.
* The would-be-deny judge — unchanged, REUSED.
* The R2 inputs — unchanged.
* The counter-reset semantics — unchanged.
* The nudge/hint texts — unchanged.
* The bound/escalation semantics — unchanged.
* The idle-orphan two-set semantics — unchanged.

**Files (this requirement):** `daemon/services/attestation_marker_scanner.py` (length-trigger scanner + threshold constant); `daemon/services/attestation_gate.py` (`GateDecision` extended with three additive fields; trigger-site OR-composition; log format string 28→31); `daemon/graph.py` (gate-node judge wiring extended to read `decision.marker_hit or decision.length_trigger`); `tests/unit/test_attestation_marker_scanner.py` (16 new tests); `tests/unit/test_attestation_marker_wiring.py` (9 new tests + updated existing tests to use long completion reports so the cheap allow path stays green); `tests/unit/test_attestation_judge_wiring.py` (default final_text updated); `tests/unit/test_attestation_nudge_inject.py` (AIMessage updated); `tests/unit/test_attestation_conditional_gate_outcomes.py` (two AIMessages updated); `tests/integration/test_attestation_marker_routing_lca.py` (`_no_marker_mission` updated); `requirements.md` (this entry); `docs/setup.md` (runbook note).

## Requirement (2026-09-12) — Judge fallback-config import-typo fix + send_message continuation clauses (demo-testcase grounded)

* **R-IMP1**: both judge-seam fallback config resolutions in `daemon/graph.py` MUST import via `from .config import load_config` (ONE dot). The two-dot form raised `ImportError` inside the wrapper `try` and was swallowed into the fail-safe route (d) with the judge silently skipped — reachable only with a manager lacking `.config` (dead in prod, dead under MagicMock embeddings). Demonstrated via the `plan/mid-work-report-testcase` lane @ eed44334.
* **R-IMP2**: a config-less manager + healthy config source → the judge RUNS with the real `load_config()` product at BOTH seams (marker-path and would-be-deny); no `*_judge_error` log row; routing is judge-driven. Pinned by `test_configless_manager_marker_judge_fallback_config_is_healthy` and `test_configless_manager_would_be_deny_judge_fallback_config_is_healthy` (fail-on-base proven: both fail with the typo present).
* **R-IMP3**: a GENUINE fallback config-load failure MUST still degrade conservatively and observably: no raise, fail-safe ROUTE (d), judge skipped, and the log row carries the route (`decision=fail_safe_marker_d`, `marker-path d`). Pinned by `test_configless_manager_config_load_failure_still_failsafe_route_d`.
* **R-IMP4**: `ATTESTATION_NUDGE_TEXT` MUST name `send_message` as the continuation mechanism — one parenthetical clause: "(send_message to children/revive as needed)" appended to the continue sentence.
* **R-IMP5**: `COMPLETION_CHECK_NOTE_TEXT` MUST name `send_message` as the continuation mechanism — one parenthetical clause: "(check your children's status and continue or revive their work via send_message if needed)" inserted after "the wake-up actually comes".
* **AC-IMP1**: the canonical nudge byte pin (`EXPECTED_NUDGE_TEXT_CANONICAL`, `tests/unit/test_attestation_nudge_inject.py`) matches the amended constant byte-for-byte; all other constant references in the 14-file attestation test set are import-equality or header-prefix pins and pass unchanged (no length/char-count pins exist in the repo).
* **AC-IMP2**: the full attestation test matrix (unit + integration + migration; `find tests -name "*attestation*.py"`, excluding `__pycache__`; `tests/postgres` out of scope for this run) is green post-change.

**DO NOT TOUCH (this requirement):** the judge services (`attestation_report_judge.py`, judge resolver), the gate evaluator, the marker catalog, the R2 inputs, counter/reset/bound semantics, the dry-mode and kill-switch contracts, and every other byte of the two constants beyond the two clauses above.

**Files (this requirement):** `daemon/graph.py` (two import fixes; two clause additions); `tests/unit/test_attestation_nudge_inject.py` (byte pin re-pinned); `tests/integration/test_attestation_marker_routing_lca.py` (three regression tests + `_ConfigLessManagerStub`); `decisions.md` + `requirements.md` (this entry).

## Requirement (2026-09-12, user request) — LCA busy-descendant trigger suppression (false-positive hint fix)

**Context:** User observation 2026-09-12 — Route-(b) Completion Check Note hint fires on healthy waits. The user's repro: a leader awaiting a RUNNING child writes a short mid-work ACK ("Awaiting the tester reply. Ending turn, will continue.") — markers (`ending turn`, `awaiting`) AND length (short, < 150 words) BOTH fire on the ALLOW path → judge fires → judge-no (mid-work phrasing) → route (b) → ALLOW + checkpoint-durable Completion Check Note injected. Council-predicted W2 false-positive class: the hint appears on essentially every awaiting turn-end where the leader is doing the HEALTHY thing. Pre-fix a leader with 3 RUNNING children would inject a Completion Check Note on EVERY turn-end during a long-running mission. This requirement adds busy-descendant trigger suppression: the marker/length trigger is disarmed ENTIRELY when at least one descendant is in the unconditional-busy subset `{RUNNING, WAITING, WAITING_CHILDREN}`. No judge call, no route-(b) hint, plain allow. The marker/length signal STAYS RECORDED for observability.

**Requirement:** the gate MUST detect busy descendants (the unconditional-busy subset `{RUNNING, WAITING, WAITING_CHILDREN}`) and SUPPRESS the WHOLE marker/length trigger when at least one busy descendant exists. Suspect-pending shapes (PAUSED descendants, en-route-only work via IDLE/QUEUED + pending message OR unsettled job) MUST KEEP triggering — the suppression is for healthy waits, not stuck work. No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa — behavior fixes ship always-on). Dry-mode `allow unconditionally` posture is preserved end-to-end. The kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` continues to apply on non-suppressed paths exactly as before.

**Acceptance criteria:**

* **AC-BUSY-1**: `InstanceManager.count_busy_descendants(instance_id) -> int` is a new public method on `daemon/manager.py`. Counts descendants in the unconditional-busy subset `{RUNNING, WAITING, WAITING_CHILDREN}` ONLY. PAUSED is excluded (suspect, not healthy). Conditional-live dormant `IDLE`/`QUEUED` are excluded (no execution). Terminal `COMPLETED`/`TERMINATED`/`ERROR`/`FAILED` are excluded. Returns 0 when the root is not found OR no descendants exist OR every descendant is terminal/PAUSED/dormant. Bounded by `LIVE_DESCENDANTS_BFS_CAP`.
* **AC-BUSY-2**: shared private helper `InstanceManager._count_descendants_busy_and_live(instance_id) -> tuple[int, int]` does ONE BFS pass returning `(busy_count, live_count)`. Both `count_live_descendants` and `count_busy_descendants` are thin wrappers around this helper. From the gate, two BFS passes total (each bounded, each ~0.016ms P95, total ~0.032ms stays well under the 20ms budget).
* **AC-BUSY-3**: `GateDecision` extended with `busy_descendants` (int, default 0) and `trigger_suppressed_by` (str, default `""`). Both stamped on EVERY evaluation (the busy count is meaningful even when the trigger doesn't fire — log forensics).
* **AC-BUSY-4**: at the trigger site (`daemon/services/attestation_gate.py:993-1037`), if `busy_descendants > 0 AND trigger_fires` ⇒ the WHOLE trigger is SUPPRESSED ENTIRELY:
  - `trigger_source=""` (cleared — the cheap-allow signal)
  - `trigger_suppressed_by="busy_descendants"` stamped
  - `marker_hit`, `marker_terms`, `length_trigger`, `final_word_count` STAY RECORDED for observability
  - `marker_path=""` (no judge fires — no path to record)
* **AC-BUSY-5**: graph-node marker-path judge wiring (`daemon/graph.py:5161-5168`) adds `and not decision.trigger_suppressed_by` to the existing `decision.marker_hit or decision.length_trigger` check. When the gate has stamped a non-empty `trigger_suppressed_by` on the decision, the entire judge block short-circuits: NO `judge_completion_report_async` call, NO route-(b) hint injection, NO counter write, plain allow.
* **AC-BUSY-6**: PAUSED descendants + trigger ⇒ judge + route-(b) hint STILL fire (PAUSED is suspect, not healthy). Pinned by `test_paused_child_keeps_trigger_armed_no_suppression`.
* **AC-BUSY-7**: en-route-only work (IDLE + pending message OR IDLE + unsettled job) + trigger ⇒ STILL fires (dormant IDs are not busy). Pinned by `TestFacadeBfsCap::test_idle_with_unprocessed_message_counts_live` (existing — re-asserted post-change) and the new `_count_descendants_busy_and_live` helper's strict busy subset.
* **AC-BUSY-8**: boundary — busy=0 + markers + nothing pending ⇒ route (a) deny+nudge UNCHANGED. Busy suppression MUST NOT regress the existing deny path. Pinned by `test_deny_path_unchanged_when_busy_zero_no_markers_pending_nudge`.
* **AC-BUSY-9**: existing deny-path suite unchanged (route a/b/c/d). Pinned by `test_deny_path_suite_unchanged_existing_marker_a_still_works` and the existing marker-path routing tests still passing.
* **AC-BUSY-10**: log schema pin — the canonical `event=leader_completion_gate` log row carries `busy_descendants` AND `trigger_suppressed_by` on EVERY evaluation. Format-string placeholder count grows 31 → 33 (drift pin in `test_length_log_placeholder_count_is_33`). Operators can grep `event=leader_completion_gate trigger_suppressed_by=busy_descendants` to count suppressions; `marker_hit=True trigger_suppressed_by=busy_descendants` confirms the trigger would have fired but for busy suppression.
* **AC-BUSY-11**: dry-mode + busy suppression — log-only, NO judge call, NO hint, plain allow (dry-mode `allow unconditionally` posture preserved end-to-end). Pinned by `test_busy_suppression_dry_mode_log_only_no_judge_no_hint`.
* **AC-BUSY-12**: kill-switch `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0` + busy suppression — suppression disarms the trigger BEFORE the kill-switch check; dry-mode log-only posture preserved.
* **AC-BUSY-13**: fail-open contract — DB errors on `count_busy_descendants` propagate out of the helper; the gate's `except Exception` DB seam converts them to fail-open ALLOWED with `busy_descendants=-1` in the log row (the `-1` sentinel pattern, never a 0 default — 0 is a meaningful R2 value).
* **AC-BUSY-14**: no new `ENSEMBLE_*` env flags. The busy suppression is a behavior fix shipped always-on per the fix/flag policy (7d5285aa). The only kill-switch surface is the existing judge kill-switch (AC-BUSY-12).
* **AC-BUSY-15**: full attestation test matrix green post-change. Enumerated by `find tests -name "*attestation*.py" -not -path "*/postgres/*"` (47 files; 662 tests). Pre-existing failures are itemized separately; no new reds.

**DO NOT TOUCH (this requirement):**

* The 5-value canonical decision enum — unchanged.
* The marker catalog (16 patterns; 12-18 balance) — unchanged.
* The marker-path judge — unchanged, REUSED on non-suppressed paths only.
* The would-be-deny judge — unchanged, REUSED.
* The R2 inputs — unchanged.
* The deny path + the two-set live semantics — unchanged.
* The counter-reset semantics — unchanged.
* The nudge/hint texts — unchanged.
* The bound/escalation semantics — unchanged.
* The b08f40fe idle-orphan class — unchanged (idle orphans still NOT counted live, still NOT counted busy).
* No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa).

**Files (this requirement):** `daemon/manager.py` (new `count_busy_descendants` sibling + shared `_count_descendants_busy_and_live` helper); `daemon/services/attestation_gate.py` (`GateDecision` extended with two additive fields; trigger-site suppression; log format string 31 → 33); `daemon/graph.py` (gate-node judge wiring `and not decision.trigger_suppressed_by`); `tests/unit/test_attestation_marker_wiring.py` (11 new tests + `_make_node` extended with `busy_descendants` kwarg + drift pin 31 → 33); `tests/integration/test_attestation_marker_routing_lca.py` (`_ConfigLessManagerStub` extended); `tests/integration/test_attestation_live_descendants.py` (`_StubManager` extended + `TestFacadeBfsCap` + `TestCanonicalLogSchema` + DB-seam tests patched); `tests/integration/test_attestation_mid_work_report_testcase.py` (`test_scenario_a_children_out_allow_with_hint` + `test_scenario_a_live_judge` rewritten for the suppressed contract); `tests/integration/test_attestation_c2_both_branches.py` + `tests/integration/test_attestation_dry_mode.py` + `tests/integration/test_attestation_observability.py` + `tests/unit/test_attestation_dry_logging.py` + `tests/unit/test_attestation_gate.py` (`make_manager` / `_manager` helpers extended with `count_busy_descendants=0`); `tests/support/conftest.py` (`GraphTestManager` extended with `count_busy_descendants` + `_count_descendants_busy_and_live` delegation); `decisions.md` + `requirements.md` (this entry); `docs/setup.md` (runbook note).


---

## AC-JUDGE-RETRY-1 (2026-09-16): LCA judge unparsable retry + forensic logging + deny-path symmetry guard

**Acceptance criteria for the incident-98b59dd7 (class D judge false-negative) fix. All AC-JUDGE-RETRY-N are PINNED by the named tests in `tests/unit/test_attestation_report_judge.py` and `tests/unit/test_attestation_gate.py`.**

* **AC-JUDGE-RETRY-1**: ONE retry on unparsable. When the model RESPONDED with a body AND `_parse_judge_response` returned `None`, the judge service retries ONCE with the same input + same config + same per-attempt `timeout_s` (a fresh call, no prompt mutation, no model swap, no backoff). Pinned by `test_judge_async_unparsable_retries_and_succeeds_on_attempt_2` (parse-success on retry path) and `test_judge_async_unparsable_exhaust_retry_keeps_conservative` (unparsable on both attempts path).
* **AC-JUDGE-RETRY-2**: NO retry on timeout. On `verdict="timeout"` (attempt 1 raised `asyncio.TimeoutError`), `attempt=1`, `first_unparsable_excerpt=None`, NO retry fires. Pinned by `test_judge_async_timeout_does_not_retry` (asserts `counter.count == 1` and `attempt == 1`).
* **AC-JUDGE-RETRY-3**: NO retry on generic error. On `verdict="error"` (attempt 1 raised any non-timeout exception), `attempt=1`, `first_unparsable_excerpt=None`, NO retry fires. Pinned by `test_judge_async_generic_error_does_not_retry` (asserts `counter.count == 1` and `attempt == 1`).
* **AC-JUDGE-RETRY-4**: NO retry on success. On `verdict="yes"` or `verdict="no"` (attempt 1 parsed cleanly), `attempt=1`, `first_unparsable_excerpt=None`, NO retry fires. Pinned by `test_judge_async_yes_does_not_retry` and `test_judge_async_no_does_not_retry`.
* **AC-JUDGE-RETRY-5**: Reason is no longer empty on unparsable. On the retry-exhausted path (`verdict="unparsable", attempt=2`), `reason` carries `"judge_response_unparsable on both attempts: <excerpt-head>"` (incident 98b59dd7 root cause closure). On the retry-then-timeout/error path (`attempt=2`, attempt 2 was a transport failure), `reason` carries the dual-shape descriptor. Pinned by `test_judge_async_unparsable_response_is_conservative` (asserts `reason != ""` and `"judge_response_unparsable" in reason`) and `test_judge_async_first_unparsable_then_timeout_records_both`.
* **AC-JUDGE-RETRY-6**: First-unparsable excerpt is captured on retry. On any 2-attempt outcome (parse-success OR unparsable OR timeout/error), `first_unparsable_excerpt` carries the redacted + truncated raw response of attempt 1, capped at `JUDGE_EXCERPT_MAX_CHARS=400` chars, whitespace-normalized, with secrets redacted. Pinned by the same tests as AC-JUDGE-RETRY-1 + AC-JUDGE-RETRY-5 + `test_redact_secrets_redacts_bearer_token` + `test_redact_secrets_redacts_api_key_shapes` + `test_redact_secrets_redacts_secret_shapes` + `test_truncate_excerpt_caps_at_max_chars` + `test_shape_unparsable_excerpt_composes_redact_then_truncate`.
* **AC-JUDGE-RETRY-7**: Secret redaction is conservative but not over-broad. Bearer / api-key / token / secret-shaped strings are redacted with `[REDACTED]` sentinel; short prose words like "token economy" or "bearer of good news" (no shaped secret after the keyword) are NOT redacted (false-positive guard). Pinned by `test_redact_secrets_preserves_short_tokens`.
* **AC-JUDGE-RETRY-8**: Excerpt cap + whitespace normalization. Excerpt is capped at exactly `JUDGE_EXCERPT_MAX_CHARS` chars with a `[truncated]` tail marker (single source of truth — no env-tunable knob); whitespace runs (newlines, tabs, multiple spaces) are collapsed to a single space. Pinned by `test_truncate_excerpt_caps_at_max_chars` + `test_truncate_excerpt_collapses_whitespace` + `test_truncate_excerpt_handles_empty_input`.
* **AC-JUDGE-RETRY-9**: Deny-path symmetry guard (i). Long-form final AIMessage (>150 words, `final_word_count >= SHORT_REPORT_WORD_THRESHOLD`) + unparsable on attempt 1 + unparsable on attempt 2 (retry exhausted) → conservative fail-safe. The judge service returns `is_complete_report=False, attempt=2`; the gate sees this AFTER retry exhaustion and the existing deny+nudge machinery fires. The retry gates the nudge on retry exhaustion, NOT on single-attempt unparsable. Pinned by `test_deny_symmetry_long_form_unparsable_then_unparsable_nudge_after_retry`.
* **AC-JUDGE-RETRY-10**: Deny-path symmetry guard (ii). Long-form final AIMessage (>150 words) + unparsable on attempt 1 + parse-success on attempt 2 → `is_complete_report=True, attempt=2` → gate flips to ALLOWED → NO nudge fires. The retry RECOVERS the false-positive class from incident 98b59dd7. Pinned by `test_deny_symmetry_long_form_unparsable_then_parse_success_no_nudge`.
* **AC-JUDGE-RETRY-11**: JudgeResult shape — 8 canonical fields in EXACT order. `[is_complete_report, verdict, reason, model, latency_ms, error_class, attempt, first_unparsable_excerpt]`. Frozen + hashable. Defaults: `error_class=None`, `attempt=1`, `first_unparsable_excerpt=None`. Backward compatible — existing positional-construction call sites (using first 6 fields positionally) still work without changes. Pinned by `test_judge_result_is_frozen_and_error_class_defaults_none`.
* **AC-JUDGE-RETRY-12**: KB-trap pin — on DENIED rows, `marker_hit` / `length_trigger` / `final_word_count` / `marker_path` stay at dataclass defaults (False / False / 0 / "") because the marker/length scanner NEVER runs on the deny path. Operators / future readers MUST NOT interpret those fields as measurements of the final AIMessage content on a DENIED row. Pinned by `TestDeniedRowsHaveDefaultMarkerFields` (4 tests).
* **AC-JUDGE-RETRY-13**: Worst-case latency bound. The retry uses its OWN per-attempt timeout window (`timeout_s`). Total worst-case wall-clock = `2 × timeout_s`. With the default 25.0s cap, worst-case = 50.0s. The HA facade's `wall_clock_cap_s` and `asyncio.wait_for` bounds still apply per-attempt (the retry does NOT stack timeouts across attempts). Documented in the module docstring + the runbook (`docs/setup.md`).
* **AC-JUDGE-RETRY-14**: Kill-switch OFF = zero judge calls (including zero retries). The kill-switch (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED=0`) is checked at the graph layer in `daemon/graph.py:5483-5486` BEFORE the judge is called. When the kill-switch is OFF, the judge is never called → zero retries by construction. The existing pin `test_judge_not_called_when_env_kill_switch_off_real_resolver` (asserts `calls == []`) covers this; no new test was added because the architecture guarantees it.
* **AC-JUDGE-RETRY-15**: Log row additions. Both `event=leader_completion_gate_judge` (would-be-deny path, `daemon/graph.py:5576-`) and `event=leader_completion_gate_marker_judge` (marker-path, `daemon/graph.py:5347-`) gain 2 additive placeholders: `llm_judge_attempt=%s` and `llm_judge_first_unparsable_excerpt=%s`. Operators grep these to see whether the verdict came from a fresh call or a retry, AND what the first attempt returned if the retry was triggered (incident 98b59dd7 forensic surface).
* **AC-JUDGE-RETRY-16**: No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa). The retry is shipped always-on per the policy. The only kill-switch surface is the existing judge kill-switch (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`).

## DO NOT TOUCH (this requirement):

* The marker catalog (16 patterns; 12-18 balance) — unchanged.
* `SHORT_REPORT_WORD_THRESHOLD` value (150) — unchanged. The symmetry-guard long-form detector REUSES this constant; no duplicate.
* Busy suppression (`busy_descendants` + `trigger_suppressed_by`) — unchanged.
* The 5-value canonical decision enum — unchanged.
* Routing semantics (a)/(b)/(c)/(d) — unchanged.
* Timeout default (25.0s) — unchanged.
* Nudge/hint texts — unchanged.
* Kill-switch behavior (`ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED` default ON; =0 disables judge ENTIRELY, retries included) — unchanged.
* The 33-placeholder pin on the canonical `event=leader_completion_gate` log row — unchanged.
* No new `ENSEMBLE_*` env flags (fix/flag policy 7d5285aa).

## Files (this requirement):

- `daemon/services/attestation_report_judge.py` — `JudgeResult` extended; helpers added; retry refactor; module docstring updated.
- `daemon/graph.py` — two log rows extended with 2 additive fields each.
- `tests/unit/test_attestation_report_judge.py` — 18 new tests + 2 updated tests + 1 new constants pin.
- `tests/unit/test_attestation_gate.py` — new `TestDeniedRowsHaveDefaultMarkerFields` class with 4 tests.
- `decisions.md` + `requirements.md` (this entry); `docs/setup.md` (runbook note).

---

## Incident 6a0d60c9 fix cycle (2026-09-16) — FIX-1 marker-path bound enforcement + FIX-2 answer-gate blindness + FIX-3 nudge id stability

* **AC-6A0D-1 (FIX-1)**: Shared helper `deny_bound_exceeded(denied_count, bound)` in `daemon/services/attestation_gate.py` is the SINGLE bound predicate; `decide()` step (6) uses it (semantics unchanged: `denied_count + 1 > bound`).
* **AC-6A0D-2 (FIX-1)**: Marker-path (a) conversion (judge verdict=no, nothing pending) consults the shared predicate. At `denied_count == bound` it produces `Decision.TERMINAL_AFTER_BOUND`, `next_denied_count = 0`, NO nudge; ledger `set_escalated_and_reset` fires; `event=leader_completion_gate_terminal_after_bound` is emitted; the END is allowed. Pinned by `tests/integration/test_attestation_marker_bound_enforcement_lca.py::test_path_a_at_bound_escalates_without_nudge`.
* **AC-6A0D-3 (FIX-1)**: Marker-path (d) conversions — BOTH the post-call timeout/error/unparsable route and the wrapper-fault `except` route — consult the shared predicate with the same terminal outcome. Pinned by `test_path_d_timeout_at_bound_escalates_without_nudge` + `test_path_d_wrapper_fault_at_bound_escalates_without_nudge`.
* **AC-6A0D-4 (FIX-1)**: Below the bound the marker path still denies + nudges (no premature escalation). Pinned by `test_path_a_below_bound_still_denies` + `test_path_d_below_bound_wrapper_fault_still_denies`.
* **AC-6A0D-5 (FIX-1, the 6a0d60c9 regression shape)**: Synthetic marker-triggered turn-ends with the counter climbing 0→3 produce EXACTLY 3 nudges, then terminal_after_bound with ZERO further nudges. Pinned by `test_path_a_exactly_three_nudges_then_terminal`.
* **AC-6A0D-6 (FIX-2)**: `decide()` gains keyword-only `user_answer_pending: bool = False`; the arm (3.b) returns `ALLOWED_LEGITIMATE_PENDING_WAKEUP` with the counter UNCHANGED and `should_inject_nudge=False`. It fires BEFORE the attested check (no attested-allow reset while an answer is pending) and AFTER the conditional-off check (which keeps its plain-`allowed` enum). It blocks the bound escalation and the deny. Pinned by `tests/unit/test_attestation_user_answer_pending_decide.py::TestUserAnswerPendingArm` (8 tests).
* **AC-6A0D-7 (FIX-2)**: At gate evaluation with an open awaiting-answer handle: zero marker scan, zero judge calls, zero nudges, zero hints, zero counter movement; the canonical row carries `user_answer_pending=True`. Pinned by `tests/integration/test_attestation_user_answer_pending_lca.py::test_answer_pending_plain_allows_zero_judge_zero_nudge` (judge monkeypatch raises if called) + `test_answer_pending_counter_never_moves_across_repeats`.
* **AC-6A0D-8 (FIX-2)**: Delegated mission (attestation_required=True) + nothing pending + open answer handle → plain allow, NOT a deny. Pinned by `test_answer_pending_suppresses_delegated_denial`.
* **AC-6A0D-9 (FIX-2, detection mechanism)**: DB-backed source of truth `TaskRepository.has_open_answer_handle_for_gate` — `suspension_reason='awaiting_answer'` AND `resume_target_turn_id IS NOT NULL` AND `status='paused'` (the `answer_gate_existing_turn` resume selector's handle shape). Exposed via `InstanceManager.has_open_user_answer` (propagating contract, mirrors `count_busy_descendants`). NOT the in-memory question pack keying (that keying remains in-memory-only — noted risk, deliberately not relied upon).
* **AC-6A0D-10 (FIX-2, clearing)**: The detection self-clears when the answer is consumed — `ResumeTurn` flips `status='paused' → 'pending'` and nulls the handle columns in ONE atomic guarded UPDATE; a consumed handle reads False (no stale-allow). Pinned by `test_consumed_handle_reads_false_no_stale_allow`.
* **AC-6A0D-11 (FIX-2, false-positive guard)**: Freshness guard — the handle must be the instance's NEWEST task row (autoincrement id); a leaked pre-revive handle expires when any newer turn exists (no permanent allow bypass). Ambiguity (>1 open handles) refuses the bypass. Non-`awaiting_answer` suspension reasons never arm the bypass. Duck-typing guard — only the literal `True` arms it; truthy non-bool / missing-facade reads as False (deny path reachable). DB-error rides the existing whole-eval fail-open with `user_answer_pending=False` on the row. Pinned by `TestHasOpenAnswerHandleForGate` (6 tests) + `test_truthy_non_bool_facade_value_never_arms_the_bypass` + `test_missing_facade_reads_as_not_pending` + `test_facade_db_error_degrades_to_not_pending` + `test_no_answer_pending_deny_path_still_reachable`.
* **AC-6A0D-12 (FIX-3)**: The deny nudge id is `_stable_id_for("attestation_nudge", instance_id=...)` = `attestation_nudge:{instance_id}`; `additional_kwargs` unchanged. ALL deny producers (decide-path + marker (a)/(d), single construction site) mint the SAME id. Pinned by `tests/integration/test_attestation_nudge_supersede_lca.py::test_nudge_mints_stable_per_instance_id`.
* **AC-6A0D-13 (FIX-3)**: Consecutive denies supersede to ONE nudge block via the REAL `langgraph add_messages` reducer; the surviving block carries the LATEST deny's counter stamp; distinct instances do not supersede each other. Pinned by `test_consecutive_denies_supersede_to_one_block_real_add_messages` + `test_distinct_instances_do_not_supersede_each_other`.
* **AC-6A0D-14 (log schema)**: `user_answer_pending` is the 18th canonical field (17→18; format string 33→34 placeholders). Drift pins updated: `test_attestation_conditional_gate_outcomes.py::TestConditionalSchemaPin`, `test_attestation_live_descendants.py::TestCanonicalLogSchema.test_canonical_schema_has_18_fields`, `test_attestation_marker_wiring.py::test_length_log_placeholder_count_is_34`.
* **No new `ENSEMBLE_*` env flags** (fix/flag policy 7d5285aa) — FIX-1/2/3 ship always-on. `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND` remains the existing tuning-only knob, now enforced on all deny producers.

## Files (this requirement):

- `daemon/services/attestation_gate.py`, `daemon/graph.py`, `daemon/manager.py`, `daemon/repositories/task/repository.py`, `daemon/services/context_messages.py` (see decisions.md D-ENTRY 2026-09-16 for per-file detail).
- New tests: `tests/integration/test_attestation_marker_bound_enforcement_lca.py`, `tests/integration/test_attestation_user_answer_pending_lca.py`, `tests/unit/test_attestation_user_answer_pending_decide.py`, `tests/integration/test_attestation_nudge_supersede_lca.py`.
- Updated pins: `test_attestation_conditional_gate_outcomes.py`, `test_attestation_live_descendants.py`, `test_attestation_marker_wiring.py`, `test_attestation_bound_escalation.py`.
- Docs: `docs/setup.md` (runbook), `decisions.md` + `requirements.md` (this entry).


# CTD — Child-Terminal Contradiction Detection (2026-09-16, SUPERSEDED-IN-PART 2026-09-18)

The complementary bug class to the LCA leader-side gate: a CHILD instance that emits a final report promising future work ("Then I aggregate and write RESULTS. Ending turn.") and then transitions to terminal. The promised "next report" never arrives — the parent wakes up on the completion signal and trusts the report as if the work were done. This is the original hallucination bug class, caught at the SOURCE rather than at the parent's gate.

The detector lives in the same directory as the LCA scanner (`daemon/services/attestation_marker_scanner.py`) but uses a SEPARATE catalog (`CHILD_TERMINAL_PROMISE_MARKERS`, 17 entries) with a SEPARATE function (`scan_child_terminal_report_for_promises`). It is invoked from the child→parent terminal-report delivery seam (`daemon/services/child_reports.py::_process_child_completion_db_sync`). The detector is zero-LLM (pure substring scan) and ships always-on (no new `ENSEMBLE_*` env flag).

**SUPERSEDED-IN-PART 2026-09-18 (D-CTD-7, user decision).** The advisory ``[SYSTEM CONTEXT: Child Report Check]`` note mint is REMOVED; the catalog and the scanner function are KEPT (catalog byte-identical, 17 entries; user explicitly declined tightening — one extra quick judge call vs. broader real-child-lie coverage). The LLM judge now subsumes the gate role. Pin below for the surviving catalog; the note-mint ACs (CTD-3, CTD-6, CTD-7, CTD-9, CTD-10) are SUPERSEDED — see decisions.md D-CTD-7 for the full rationale and the resurrection-loud pins.

## Acceptance criteria

* **CTD-1 (catalog membership) — KEPT**: `CHILD_TERMINAL_PROMISE_MARKERS` holds 10–18 entries; every spec seed phrase is present (`ending turn`, `awaiting`, `then i`, `to be continued`, `in progress`, `still pending`, `not yet complete`, `will report back`). Pinned by `tests/unit/test_child_terminal_contradiction.py::TestChildTerminalPromiseScan.test_catalog_size_in_range` and `test_canonical_seed_phrases_all_present`. Catalog size pinned to exactly 17 by `tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins.test_catalog_byte_identical_after_removal`.

* **CTD-2 (stable-id format) — SUPERSEDED 2026-09-18 (D-CTD-7)**: the ``_stable_id_for("child_report_check", ...)`` branch is REMOVED from `daemon/services/context_messages.py` (no remaining callers after the mint deletion). The ``CONTEXT_KIND_CHILD_REPORT_CHECK`` enum constant IS KEPT so the resolver-side ``_is_child_report_check_note`` detector (in `daemon/services/attestation_resolver_activation.py`) can still recognize note-shaped messages — that surface is the A-signal path the user pinned as untouched (D-CTD-7 (ii)).

* **CTD-3 (hook fires on canonical positive — note attached) — SUPERSEDED 2026-09-18 (D-CTD-7)**: the note mint is REMOVED. No advisory ``[SYSTEM CONTEXT: Child Report Check]`` ``MessageQueue`` row is created on child-terminal. The ``event=leader_completion_gate_child_report_check_fired`` log row no longer fires. Operators who ``grep`` for either will see zero hits post-activation; this is expected.

* **CTD-4 (hook stays quiet on clean completion) — KEPT**: clean completions (`"Done, 5/5, merged abc123"`) STILL stay quiet — they never produced a note and still don't (catalog's FP-tight near-FP posture preserved). Pinned by `tests/unit/test_child_terminal_contradiction.py::TestChildTerminalPromiseScan.test_clean_completion_does_not_fire` and `test_other_clean_reports_do_not_fire`.

* **CTD-5 (near-FP adjudication) — KEPT (catalog pin only)**: the catalog still contains `awaiting` and the FP behavior is preserved. The NOTE is no longer minted on the FP — but the catalog's near-FP behavior is unchanged. Pinned by `tests/unit/test_child_terminal_contradiction.py::TestChildTerminalPromiseScan.test_near_fp_awaiting_merge_decision_fires`.

* **CTD-6 (supersede on repeat) — SUPERSEDED 2026-09-18 (D-CTD-7)**: no note to supersede; mint gone.

* **CTD-7 (depth-agnostic) — KEPT (catalog pin only)**: the catalog + scanner function apply to ANY agent's terminal report — the detector shape is agent-agnostic. The hook surface is gone; the catalog's depth-agnostic posture is unchanged (a future re-attach point inherits the same shape).

* **CTD-8 (zero LLM calls) — KEPT (catalog pin only)**: the catalog + scanner function is pure substring scan (no LLM call). The hook-surface pin is gone with the mint; the catalog's zero-LLM posture is unchanged.

* **CTD-9 (timing unchanged) — SUPERSEDED 2026-09-18 (D-CTD-7)**: no second INSERT in the SAME transaction; report delivery timing is back to the pre-CTD single-INSERT shape.

* **CTD-10 (dead-parent skip) — SUPERSEDED 2026-09-18 (D-CTD-7)**: no note INSERT to suppress on dead parents.

* **CTD-11 (no new env flag)**: The detector ships always-on. Zero new `ENSEMBLE_*` env reads in the touched files. Pinned by `tests/unit/test_child_terminal_contradiction.py::TestSourcePins.test_no_new_env_flag_added` (AST walk over the three touched modules; any new `os.environ` / `os.getenv` call mentioning `CHILD_REPORT_CHECK` raises AssertionError).

* **CTD-12 (no LLM gating of the child path)**: The detector is COMPLETELY independent of the LCA leader gate (`decide()`, judge service, marker/length triggers, busy suppression, deny-bound escalation). LCA kill-switches (`ENSEMBLE_LEADER_ATTESTATION_MODE`, `ENSEMBLE_LEADER_ATTESTATION_LLM_JUDGE_ENABLED`, `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`) do not affect the child-terminal contradiction detector. The two subsystems share the `leader_completion_gate_*` event-namespace prefix (operability) but the runtime contract is independent.

## Files (this requirement):

- `daemon/services/attestation_marker_scanner.py` — catalog + scan function (CTD-1; KEPT after D-CTD-7; reused by D-CTD-8 transcript scan).
- `daemon/services/context_messages.py` — `CONTEXT_KIND_CHILD_REPORT_CHECK` kind constant KEPT (resolver detector depends on it; A-signal path untouched per D-CTD-7 (ii); legacy detector kept as defense-in-depth per D-CTD-8); `_stable_id_for` child_report_check branch REMOVED (D-CTD-7).
- `daemon/services/child_reports.py` — mint block REMOVED (D-CTD-7); finalizer count-guard predicate hardening KEPT (defense-in-depth for any future task-less row producer); preservation comment at the mint-site line explains what was deleted.
- `daemon/services/attestation_resolver_activation.py` — D-CTD-8 transcript-scan additions: constants ``_INTERNAL_REPORT_SOURCE_PREFIX``, ``_CONTRADICTION_MARKERS``, ``_SHORT_REPORT_WORD_THRESHOLD`; helpers `_is_child_report_message`, `_extract_child_id_from_source`, `_word_count`, `_build_evidence_from_report_message`; ``collect_source_a_signals`` updated to scan the ``internal_report:``-stamped ``HumanMessage`` rows (in addition to the legacy note detector, kept as defense-in-depth). NO changes to: `activation_predicate`, `_build_a_section`, `assemble_fused_bundle`, BUNDLE caps, judge service, MID_WORK_MARKERS, tree-status / Source C, predicate shapes, count-guard predicates, judge service.
- Tests:
  - `tests/unit/test_child_terminal_contradiction.py` (14 — pure-function matrix + source-level pins; hook + stable-id + mint+delivery tests deleted with the mint; 10-needle mint-resurrection negative pin stays green).
  - `tests/unit/test_attestation_resolver_activation.py::TestDCTD7ASignalPathPins` (5 — A-signal path preservation pins: 4 from D-CTD-7 + 1 functional pin from D-CTD-8).
  - `tests/unit/test_attestation_resolver_activation.py::TestSourceACollection` (12 — 5 legacy note-detection tests + 7 new live-transcript-scan tests).
  - `tests/unit/test_attestation_stage3_census.py` (21 — ledger-(c) marker-write seam tests removed with the mint).
- Docs: `decisions.md` (D-CTD-1..8, D-U1), `requirements.md` (this file; CTD-2/3/6/9/10 marked SUPERSEDED 2026-09-18 (D-CTD-7); CTD-13 added 2026-09-18 (D-CTD-8); R-RES-8 supersession note in-place 2026-09-18 (D-CTD-8)), `docs/setup.md` (runbook entry updated to drop the note mint description; see runbook update 2026-09-18).

---

## R-RES — Stage 1 parallel-dry shadow acceptance criteria (2026-09-16, unified 3-source resolver)

Origin: `resolver-unification.md` §4 (Stage 1) + user-locked decisions Δ1–Δ4 approved / Δ2 A-not-busy-suppressed / DP-5 rejected / R1–R8 Stage-3-only / no new flags / zero LLM. Tests: `tests/unit/test_attestation_resolver_activation.py` (63).

* **R-RES-1 (R4/D10 mirror, TDD-first)**: `¬attestation_required` (no-delegation mission) ⇒ suspicion sources A/B are NEVER evaluated — the predicate short-circuits BEFORE the A/B provider calls. The invariant test was written FIRST and failed on import before implementation. Pinned by `TestR4ShortCircuitInvariant` (incl. attested + user-answer-pending meta-bypass rows and the delegated-mission positive control).

* **R-RES-2 (Term 0)**: `¬attestation_enabled ∨ ¬scope_applicable ∨ mode="off"` ⇒ not fired, nothing evaluated (outermost). Dry/enforce compute normally. Pinned by `TestTerm0ScopeMode`.

* **R-RES-3 (§4.2 predicate semantics)**: `c_quiet := pending=0 ∧ wakeups=0 ∧ live=0`; `b_fires := (marker_hit ∨ length_trigger) ∧ busy=0`; `a_suspicion := advisory ∨ contradiction ∨ phrase ∨ word-count-below`; `activation := c_quiet ∨ b_fires ∨ a_suspicion`; band precedence deny > marker > a_suspicion. Pinned by `TestPredicateMatrix`.

* **R-RES-4 (Δ2)**: Source A fires ALONE while `busy_descendants>0` (markers busy-muted, tree not quiet) ⇒ `band=a_suspicion`. Source B busy-muted entirely. Pinned by `test_delta2_a_band_fires_alone_while_busy` + `test_busy_mutes_source_b`.

* **R-RES-5 (C-read failure)**: a raising C provider ⇒ whole-eval fail-open plain-allow (`fail_open=True`, `would_allow`). Pinned by `TestCReadFailureFailOpen`; wiring parity pinned by `test_db_error_emits_fail_open_shadow_row`.

* **R-RES-6 (§10.2 structural pin)**: `busy>0 ⇒ ¬c_quiet` guarded at the facade status-set source — busy statuses ⊆ unconditional-live statuses in `InstanceManager._count_descendants_busy_and_live`; PAUSED live-not-busy. Pinned by `TestSourceSetPins` (source-level subset extraction).

* **R-RES-7 (would-be-outcome mapping)**: §4.3 no-judge mapping — deny band → `would_deny_nudge` / `would_terminal` at the shared bound; marker/A bands → `would_hint` on route-(b) pending, else `would_allow`; not-fired/fail-open → `would_allow`. Old-decision mapping + agreement (would_hint never agrees at the evaluate() seam). Pinned by `TestWouldBeOutcomeMapping`.

* **R-RES-8 (Source A = transcript-scan contract, supersession of the Stage-0 note-shape contract)**: post-D-CTD-8 (2026-09-18) the A-band scans the leader's in-context child-report ``HumanMessage`` rows (the ``internal_report:<child_iid>`` stamp emitted by ``daemon/graph.py:6950-6967`` + ``daemon/services/instance_messaging.py:525``) for the 17-pattern catalog **at gate-evaluation time**, in the same window the A/B/C reads already use; the legacy note-detector (``_is_child_report_check_note``) is KEPT as defense-in-depth for any historical checkpoint state. The 4-field Source-A OR (``advisory_present ∨ contradiction_flag ∨ phrase_match ∨ word_count_below_threshold`` → ``a_suspicion = bool(...)``) is preserved verbatim — D2 SEMANTICS STAY (A-band not busy-suppressed, activates alone). The contradiction sub-signal is raised when any matched_terms item is in ``_CONTRADICTION_MARKERS`` (``still pending`` / ``not yet complete`` / ``interim``); the word-count sub-signal is raised when any catalog-hit child-report's raw word count is below 150 (the Source B ``length_trigger`` mirror). The 17-pattern catalog is BYTE-IDENTICAL to the deleted Stage-0 producer (existing ``test_catalog_byte_identical_after_removal`` pin stays green). Delivery-time machinery is gone FOREVER — no note, no SAVEPOINT, no ``PROCESS_MESSAGE`` Task mint, no notify, no mint-side log row. **SUPERSEDED-IN-PART 2026-09-18 (D-CTD-8, user standing decision Q2)**: the original A-band contract (D-CTD-7 supersession marked Source A dormant; the Stage-0 note-shape produced ``advisory_present ≡ phrase_match`` with ``contradiction_flag`` / ``word_count_below_threshold`` always False) is REPLACED by the transcript-scan shape on this same AC row. See decisions.md **D-CTD-8** for the field mapping, the compaction question, and the closure FP specimens (coder bd5f7b2d + reviewer 9abfcb33 self-referential FP class). Pinned by ``tests/unit/test_attestation_resolver_activation.py::TestSourceACollection`` (7 new live-path tests) + ``TestDCTD7ASignalPathPins::test_functional_pin_internal_report_message_produces_a_signal`` (the literal-must-pass functional pin) + the existing ``test_activation_predicate_a_suspicion_term_intact`` (the 4-field OR identity wire pin). **SUPERSEDED-IN-PART 2026-09-20 (dual-autopsy B1, decisions.md "dual-autopsy B1: stale-A never-clear defect; 5-part fix")**: the scan is now NEWEST-REPORT-ONLY per child (each child's LATEST ``internal_report:`` message is the only one scanned; superseded reports drop wholesale — an earlier report's contradiction the newest does not repeat does NOT resurface); operator-action sentence hits (rebuild/restart/redeploy in the hit's sentence) are EXCLUDED from ``contradiction_flag``; and when the tree-rows provider is wired (the real ``evaluate_resolver_activation`` path, fetch-once cached), advisories from a child whose tree status is ``completed`` are SUPPRESSED unless that child's newest report carries a genuine (non-operator-scoped) hit — the later-contradiction exception (a completed child that lied at the end still surfaces). Band structure + the 4-field OR wire unchanged. Pinned by ``TestNewestReportOnlyPerChild`` / ``TestOperatorScopedHits`` / ``TestCrossResolveAgainstTreeRows`` (incl. the flagship child-lie pin ``test_child_lie_completed_without_delivery_still_fires``).

* **R-RES-9 (bundle, Δ1+Δ3)**: A child-report evidence present; C first-10 rows + `+N more` suffix + scalar counts; per-section caps (3000/6000/3000) and total ≤12000; UUID id-redaction; stable sha256 + size witnesses. Pinned by `TestFusedBundle`. **SUPERSEDED-IN-PART 2026-09-20 (dual-autopsy B1, decisions.md "dual-autopsy B1: stale-A never-clear defect; 5-part fix")**: the B-section per-message clip is raised 1500 → 2500 chars (`_B_MESSAGE_CLIP`, pinned by ``TestBSectionClip2500`` — incident acbf5627's final report lost its evidence tail + activation note at 1500/2372); the B-section external cap (≤6000) and the total budget (≤14000 with U additive per R-RESU) are UNCHANGED; section A evidence now carries the newest-report-only + cross-resolution + operator-scoping semantics of the R-RES-8 B1 supersession above.

* **R-RES-10 (zero-LLM sentinel)**: full gate evaluation with would_fire=true ⇒ judge/LLM client NEVER invoked; `STAGE2_JUDGE_SEAM` defaults None and is provably inert; the event row carries `judge_invoked=False`. Pinned by `TestStage2SeamInert`.

* **R-RES-11 (parallel-log row shape)**: ONE structured `event=leader_completion_resolver_eval` row per canonical gate evaluation with stable field names; agreement true/false cases; NO row on meta-bypass/off; dry-mode row shape; fail-open row on the DB-error seam. Pinned by `TestShadowEventRowShape`.

* **R-RES-12 (exception isolation)**: a resolver-side crash logs `event=leader_completion_resolver_eval_error` and NEVER propagates into gate control flow (old decision unchanged). Pinned by `test_shadow_error_never_breaks_the_gate`.

* **R-RES-13 (tree-rows provider)**: enumerates descendants via the public `get_tree_ids_permanent` facade (root excluded, fetch-capped), returns `[]` on any failure (never raises), no-repo → empty. Pinned by `TestTreeRowsProvider`.

* **R-RES-14 (no new env flags)**: zero new `os.environ`/`os.getenv` reads in the Stage-1 module. Pinned by `TestSourcePins.test_no_new_env_flag_reads` (AST walk).

* **R-RES-15 (old family unchanged)**: the existing attestation test family passes byte-identical (no behavior change; grep guards: old-path files touched only at the additive shadow seam; no new LLM/judge call sites; nudge/hint/note text constants untouched). Verified by the full-family run in the Stage-1 verification report.

---

## R-RES2 — Stage 2 flip acceptance criteria (2026-09-16, unified 3-source resolver)

* **R-RES2-1 (ONE judge call site)**: the fused bundle reaches a REAL LLM invocation through exactly ONE site — `judge_fused_bundle_async`, called only from the graph node's fused block; `_invoke_judge_llm` is the shared transport seam (`system_prompt` additive kwarg, legacy default byte-identical). Pinned by `TestSharedTransportSeam` + `TestOldSitesDeadButPresent`.
* **R-RES2-2 (retry-once carries)**: retry fires ONLY on responded-but-unparsable, never on timeout/error; `attempt=2` + `first_unparsable_excerpt` preserved (98b59dd7 contract). Pinned by `TestFusedJudgeRetryOnceOnUnparsable`.
* **R-RES2-3 (judge_invoked derived)**: the eval row's `judge_invoked` derives from `FusedJudgeResult.invoked` (a real invocation record) — no literals on any path. Pinned by `TestJudgeInvokedDerivation`.
* **R-RES2-4 (R7-1 — DP-5 rejected)**: judge error/timeout/unparsable×2 → deny+nudge bound-enforced on the deny band, NEVER allow; marker-band path-(d)-with-pending → hint. Pinned by `TestR7PinJudgeErrorNeverAllows`.
* **R-RES2-5 (R7-2 — Q1 parity)**: kill-switch OFF → deny+nudge WITHOUT judge on the un-attested-quiet band (zero HTTP attempts spied); plain allow on marker AND A bands. Pinned by `TestR7PinKillSwitchPerBandMapping`.
* **R-RES2-6 (R7-3 — bound/escalation parity)**: at-bound TERMINAL + `set_escalated_and_reset` + operator event from the fused path exactly as the old deny path; below-bound increments; EXACTLY-3-nudges loop. Pinned by `TestR7PinBoundEscalationFromFusedPath`.
* **R-RES2-7 (budget sentinel)**: ≤1 LOGICAL invocation per evaluation on every band (deny/marker/A/rescue); the retry = 2 HTTP attempts WITHIN one invocation (documented reading, pinned as entries==1 ∧ attempts==2); 0-LLM rows (D10 meta-bypass, dry) never invoke. Pinned by `TestBudgetGuardSentinel`.
* **R-RES2-8 (old sites dead-but-present)**: zero deletions; both legacy judge entries unreachable while `_LCA_STAGE2_RESOLVER_FLIP` is True (legacy event families silent; `judge_completion_report_async` zero calls across deny+marker evaluations). Pinned by `TestOldSitesDeadButPresent`.
* **R-RES2-9 (Δ4 hint citation)**: the Completion Check Note gains `evidence_cited` + `advisory_note_text` from the verdict when present (canonical prefix byte-identical; stable supersede id unaffected); byte-identical note when the verdict cites nothing. Pinned by `TestD4HintEvidenceCitation`.
* **R-RES2-10 (incident-class E2E)**: b08f40fe (Δ1 evidence visible in the judge payload + deny+nudge), 98b59dd7 (genuine report → allow, no nudge), 6a0d60c9 (answer-pending plain allow + bound-enforced sibling), ORIGINAL child-lie (3-eval arc: A-band Δ2 row → D4 hint → quiet deny+nudge → attested allow + reset). Pinned by the four `TestIncident*` classes in `tests/unit/test_attestation_resolver_stage2.py`.
* **R-RES2-11 (byte-identical surfaces)**: attested allow / answer-gate allow / mode off+dry semantics / stamps / nudge text unchanged; zero new env flags under `daemon/` (AST pin green); bound/escalation/nudge/note texts untouched beyond the D4 citation.

---

## R-RES4 — Stage-3 retirement acceptance criteria (2026-09-17, FINAL STATE)

The Stage-3 retirement (resolver-unification §7 R1–R8, user-approved Appendix A; ledger items a–e) EXECUTED on branch `feature/lca-resolver-stage3`. The final-state acceptance criteria supersede the Stage-2 interim pins listed above where noted.

* **R-RES4-1 (single completion path)**: EXACTLY ONE completion path exists — activation predicate (`attestation_resolver_activation.activation_predicate`, semantics UNTOUCHED) → conditional fused judge (`judge_fused_bundle_async`, the sole judge entry point) → the existing outcome machinery. The `_LCA_STAGE2_RESOLVER_FLIP` constant, both legacy judge call sites in `daemon/graph.py` (~604 LoC), and the legacy judge service surface (`judge_completion_report_async`/`_sync`, `JudgeResult`, `_parse_judge_response`, window slicing/formatting helpers, legacy caps) are DELETED. Pinned by `tests/unit/test_attestation_stage3_census.py` + `TestLegacySitesDeleted`.
* **R-RES4-2 (supersedes R-RES2-8)**: the "old sites dead-but-present" Stage-2 contract is retired — the legacy sites are now DELETED, and the re-contracted pins assert deletion + fused-judge service for both legacy families (deny band + marker band).
* **R-RES4-3 (R7 conditional neutrality — MUST survive on the single path)**: (1) judge error/timeout/unparsable×2 → deny+nudge, BOUND-ENFORCED, path-(d)-exact incl. the at-bound edge (error at bound ⇒ `terminal_after_bound`, not a free deny) — NEVER fail-safe allow; (2) judge kill-switch OFF → deny band deny+nudge WITHOUT judge (Q1 parity), marker/A bands plain-ALLOW. Pinned by the R7 invariant classes (unchanged from Stage 2) + `TestF2FailOpenTargetPin` (ledger b).
* **R-RES4-4 (R4/D10 delegation gate wording — FR-3 amendment)**: FR-3's conditionality (`attestation_required`) is the unified predicate's OUTERMOST TERM (Term 1: `¬attestation_required ∨ attested ∨ user_answer_pending` → not fired, NOTHING evaluated). `decide()` no longer carries a delegation arm; `evaluate()`'s composition layer mirrors the bypass (plain ALLOW, counter untouched) and — the D10 mirror — SKIPS the marker/length scans entirely on non-delegated missions (suspicion signals are not even evaluated; log rows on that branch carry the False/0 defaults). Pinned by `TestR4CensusDelegationArmDeleted` + the quick-question family.
* **R-RES4-5 (R2 meta-flag enforcement point)**: the C2 master flag and D3 leader scope are enforced at GRAPH-BUILD time (gate node wired only for in-scope leaders; `mode=off` ⇒ not wired) and encoded as the predicate's Term 0; the gate-config no longer carries `attestation_enabled`/`scope_applicable` keys; `decide()` takes no meta params. `evaluate()` keeps the defensive Term-0 early return (byte-equivalent OFF baseline).
* **R-RES4-6 (R3 dry as a resolver property)**: dry mode = the mode layer in `evaluate()` — predicate + enforce-tree decision computed & logged, fused node skipped, zero side effects, counter frozen at input. `decision=dry_log` rows keep their shape (gain nothing, lose the retired keys).
* **R-RES4-7 (R5 busy-mute as a predicate term)**: busy_descendants mutes ONLY the marker band (inside `b_fires`); Source A is deliberately NOT busy-suppressed (Δ2). The `trigger_suppressed_by` log key is retired; the suppression is observable via `busy_descendants>0` + zero judge rows + zero hints.
* **R-RES4-8 (R1 log schema)**: `CANONICAL_LOG_SCHEMA_FIELDS` = 17 (the log-only outside-window diagnostic retired). Canonical format-string placeholders = 27.
* **R-RES4-9 (R8 single judge event family)**: `leader_completion_gate_fused_judge` / `_fused_judge_disabled` / `_fused_judge_error` only; the `*_marker_judge*` names and the bare `_judge` row are gone. Dashboards must migrate off the legacy names.
* **R-RES4-10 (ledger a — B-redaction)**: ALL id-bearing bundle text redacted (A structural fields, B leader-prose excerpts, C tree rows) per the 98b59dd7 boundary; the f926de24 scope-note workaround replaced by the real fix.
* **R-RES4-11 (ledger c — marker-write seam)**: the child-terminal promise-scanner import is module-top (hoisted out of the completion transaction hot path); the note-INSERT catch splits ImportError (loud, deploy-bug) from runtime errors (advisory-lost).
* **R-RES4-12 (kept machinery — the DO-NOT-DELETE inventory)**: bound/escalation/ledger machinery, the four counter resets, hint/nudge channels (Completion Check Note, ATTESTATION_NUDGE_TEXT, should_inject_nudge), ALL knobs/modes/kill-switches (tri-state mode, judge kill-switch, judge timeout, WINDOW/DENY_BOUND), retry-once-on-unparsable, promotion metrics + O1 boot assert + gate_exception_seen, the attestation tool contract, `decide()`'s R2-input logging shape, the 16-pattern scanner catalog + length threshold, the activation module + predicate semantics, advisory-note timing (Child Report Check note at child-terminal time) — ALL unchanged.

### FR-3 wording amendment (Stage 3, per R4)

The FR-3 conditionality amendment (2026-09-06) text "the gate ALLOWS without demanding `attest_completion`" remains the CONTRACT; the mechanism note is updated: the `attestation_required` computation is the unified activation predicate's Term 1 (outermost), mirrored at `evaluate()`'s composition layer — it is no longer a separate arm inside `decide()`, and the marker/length suspicion scans are skipped entirely on non-delegated missions (the D10 mirror invariant, now literal).

---

## R-U1 — Fused-judge user-intent section (section U, incident 4dfded83, 2026-09-18)

The fused bundle MUST carry the user's original request so the completion judge can score INTENT-FULFILLMENT, not report-shape (incident: leader answered the user's Q1 completely and informally at step 297; the judge — shown no user request — judged not_complete and the gate nudged; epoch 7e8a6323).

* **FR-U1-1 (section U)**: `assemble_fused_bundle` renders the last real user message's CONTENT under `=== SOURCE U: the user's original request for this mission ===`; capped `BUNDLE_U_SECTION_MAX=2000`, id-redacted (`slot_hint="user"`). Anchor = the delegation scanner's already-computed `last_real_user_index` (gate passes the message CONTENT through `evaluate_resolver_activation` — no re-walk, no new DB reads); the builder re-checks with `is_real_user_message` and fails closed to omission.
* **FR-U1-2 (fallback)**: no real user message → U omitted entirely; `FusedBundle.user_message_included=False`/`u_chars=0`; eval row logs `user_message_included=False bundle_u_chars=0` (Python-bool row convention).
* **FR-U1-3 (budget)**: `BUNDLE_TOTAL_MAX` 12000→14000 with U additive (raise = exactly the U cap; A/B/C caps + pins untouched; final hard clip unchanged).
* **FR-U1-4 (prompt)**: `FUSED_JUDGE_SYSTEM_PROMPT` enumerates four sections (U first) + the intent-fulfillment instruction (genuine answer ⇒ completion report regardless of formality; formal report ignoring the ask ⇒ NOT complete). Conservative default, single-line strict-JSON contract, retry-once-on-unparsable, `FUSED_JUDGE_MAX_OUTPUT_CHARS=2048` unchanged; ≤1 logical judge invocation per evaluation (U = input enrichment at the single call site).
* **Pinned by** `tests/unit/test_attestation_resolver_user_intent.py` (intent-match / intent-mismatch / anchor-absent / caps+redaction / witnesses / row fields / prompt+cap identity).


# SUPERSESSION ENTRY (2026-09-23) — Completion Check Note (b)/(d)-with-pending → LOG-ONLY

The OLD contract for the (b)/(d)-with-pending route was: ALLOW +
checkpoint-durable ``[SYSTEM CONTEXT: Completion Check Note]`` HumanMessage
injection, stable-id ``completion_check_note:{instance_id}`` minted via
``_stable_id_for`` (the F1 Shape A supersede pattern), ``context_kind=task_context``,
content = ``COMPLETION_CHECK_NOTE_TEXT`` byte-for-byte (+ the D4 evidence-citation
suffix when the fused judge verdict carried ``evidence_cited`` /
``advisory_note_text``). This was the marker-path allow-with-hint surface
introduced 2026-09-11 (incident b08f40fe) and refined through F1 Shape A
(2026-09-12) + the LCA busy trigger suppression (2026-09-12) + the D4
evidence citation (2026-09-16).

This contract is RETIRED end-to-end 2026-09-23 (incident b2f4dae9). The
NEW contract is: the (b)/(d)-with-pending route resolves to ALLOW
log-only — NO message is injected, NO context_kind is minted, NO
stable-id is required. The route STILL EXISTS as a logged decision:
the resolver_eval row carries ``resolver_outcome=allow_hint`` and the
``[AttestationGate]`` log line carries ``would_be_route=allow_hint`` so
operators can distinguish (b)/(d)-with-pending from plain (c) allow.
The full evidence chain must remain log-reconstructible (the
``leader_completion_gate_fused_judge`` row carries the verdict + reason
+ band + terms + judge_invoked flag).

Superseded ACs (2026-09-23):
* **AC-M9**: judge-not-complete + real pending → ALLOW + checkpoint-durable
  hint (the canonical ``HumanMessage`` with
  ``[SYSTEM CONTEXT: Completion Check Note]`` header, construction-time
  ``id`` invariant via ``_make_context_message`` factory). The "hint"
  clause is RETIRED; the "ALLOW" outcome is unchanged.
* **AC-M16**: ``Completion Check Note`` text is a single-source-of-truth
  constant in ``daemon/graph.py`` (canonical home; NFR-6 parity with
  ``ATTESTATION_NUDGE_TEXT``). The text constant
  ``COMPLETION_CHECK_NOTE_TEXT`` is RETIRED along with the entire hint
  surface (the constant home is the canonical witness, the text is
  deleted). The NFR-6 parity is preserved with the surviving
  ``ATTESTATION_NUDGE_TEXT`` + ``ATTESTATION_FINAL_REPORT_REMINDER`` +
  ``ATTESTATION_BUNDLED_REMINDER`` constants.
* **AC-L12**: short AIMessage with real pending work → judge fires,
  judge-no → path (b) → ALLOW + checkpoint-durable Completion Check
  Note (no counter, no deny, no re-route; the turn still ends). The
  "Completion Check Note" clause is RETIRED; the ALLOW + no-counter +
  no-deny + no-re-route outcome is unchanged. The turn still ends;
  the route label survives on logs only.
* **R-IMP5**: ``COMPLETION_CHECK_NOTE_TEXT`` MUST name ``send_message``
  as the continuation mechanism — the parenthetical clause
  "(check your children's status and continue or revive their work via
  send_message if needed)". The constant is RETIRED; the parenthetical
  clause is no longer a contract anchor.

Acceptance criteria (NEW, 2026-09-23):
* **AC-LCA-NOTE-RM-1**: the (b)/(d)-with-pending route resolves to
  ALLOW log-only — ZERO injected messages (assert
  ``"messages" not in result``), NO counter movement, NO deny, NO
  re-route. The turn still ends. Pinned by
  ``tests/unit/test_attestation_lca_note_removed.py::test_b2f4dae9_regression_pin_healthy_busy_a_band_lexical_fp_allow_log_only``
  (the b2f4dae9 incident shape: Source A suspicion + busy descendants
  + judge-no → log-only ALLOW).
* **AC-LCA-NOTE-RM-2**: the route label survives on logs — the
  ``[AttestationGate]`` log line carries
  ``would_be_route=allow_hint`` AND the resolver_eval row carries
  ``resolver_outcome=allow_hint`` AND the
  ``leader_completion_gate_fused_judge`` row carries the verdict +
  reason + band + terms + judge_invoked flag. Pinned by the b2f4dae9
  regression pin (same test as AC-LCA-NOTE-RM-1) — the three log-row
  assertions are the canonical log-fidelity contract.
* **AC-LCA-NOTE-RM-3**: suspect-pending shapes (PAUSED + en-route-only)
  STILL hit the full gate (deny path intact) — the protection lives
  in the deny path, not the hint. NO hint is injected on the
  suspect-pending case; the fused judge still fires; the
  (b)/(d)-with-pending route resolves to ALLOW log-only. Pinned by
  ``tests/unit/test_attestation_lca_note_removed.py::test_suspect_pending_paused_child_completion_still_hits_full_gate``
  + ``tests/unit/test_attestation_lca_note_removed.py::test_suspect_pending_en_route_only_completion_still_hits_full_gate``.
* **AC-LCA-NOTE-RM-4**: whole-tree negative census pin — ZERO
  ``Completion Check Note`` references in ``daemon/`` (the
  production-code surface) AND ZERO references to the five retired
  SYMBOLS (``COMPLETION_CHECK_NOTE_TEXT``,
  ``_make_completion_check_note_message``,
  ``_COMPLETION_CHECK_NOTE_TITLE``, ``_fused_hint_citation``,
  ``marker_hint_message``) in ``daemon/``. The
  ``_stable_id_for`` table rejects the ``"completion_check_note"``
  kind (the supported-kinds list shrunk to 4 rows:
  ``project``, ``shared_meta_kv``, ``attestation_nudge``,
  ``attestation_final_report_reminder``). Pinned by the three
  negative census tests in
  ``tests/unit/test_attestation_lca_note_removed.py``.

Files (this supersession): ``daemon/graph.py`` (deleted constant +
factory + citation helper + title + injection site + emit site);
``daemon/services/attestation_gate.py`` (deleted
``marker_hint_message`` field); ``daemon/services/context_messages.py``
(deleted table row + kind branch; updated docstring);
``docs/setup.md`` (retirement notes + updated References);
``tests/unit/test_attestation_marker_wiring.py`` (re-anchored 3 tests
to log-only contract — ``test_completion_check_note_stable_id_*`` and
``test_completion_check_note_compaction_seam_hoists_once`` RETIRED);
``tests/unit/test_attestation_marker_supersede_lca.py`` (entire file
retired — replaced by single retirement-witness test);
``tests/unit/test_attestation_resolver_stage2.py`` (D4 hint class
retired — replaced by ``TestD4HintEvidenceCitationRetired`` stub);
``tests/unit/test_attestation_resolver_user_intent.py`` (false-rescue
test re-anchored to log-only);
``tests/unit/test_attestation_stage3_census.py`` (replaced
kept-list assertion with explicit RETIRED pin);
``tests/unit/test_attestation_attest_first_contract.py`` (updated
NFR-6 docstring);
``tests/unit/test_attestation_lca_note_removed.py`` (NEW FILE — 6
tests: b2f4dae9 regression + 2 suspect-pending + 3 negative census);
``tests/integration/test_attestation_marker_routing_lca.py``
(re-anchored scenario-(b) and (d2) tests to log-only contract);
``tests/integration/test_attestation_mid_work_report_testcase.py``
(removed unused ``COMPLETION_CHECK_NOTE_TEXT`` import);
``tests/integration/test_attestation_stage2_failopen.py``
(re-anchored 4 tests to log-only contract);
``tests/integration/test_lcan_childlie_e2e.py`` (re-anchored
s3_a_band_fires_alone test to log-only contract). NO other test
files were modified.

# SUPERSESSION ENTRY (2026-09-19) — Attest-first pure-toolcall-turn contract

The acceptance criteria for the 2026-09-06 conditional-attestation teaching
(suppression rule + concise tool description) are NOT superseded — they
remain in force and are enforced by ``daemon/services/attestation_gate.py``
term 1 (``attestation_required``) + ``agents/leader/rule.md`` + the
``daemon/tools/attestation.py`` docstring. What IS superseded is the
ordering teaching: the OLD "deliver the report FIRST, then call
``attest_completion``" is replaced by the NEW "call ``attest_completion``
ALONE in a PURE TOOLCALL TURN (empty content), THEN deliver the full
detailed final report as a SUBSEQUENT standalone AI message" — enforced
system-side via the HOLD-state gate branch (D-entry 2026-09-19, this
file). See the D-entry for the full rationale + the verification matrix.

Superseded ACs (2026-09-19):
* Any AC that pinned the OLD order (report-first-then-attest) is
  superseded by the new order (attest-first-then-report). The OLD
  contract's "deliver the report FIRST, then attest" prose is gone
  from the leader prompt (``agents/leader/rule.md``) and from the
  tool docstring (``daemon/tools/attestation.py``). The new contract
  is taught in BOTH places + enforced by the gate.
