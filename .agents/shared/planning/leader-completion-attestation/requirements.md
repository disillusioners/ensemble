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
| **Nudge** | The act of injecting a checkpoint-durable `HumanMessage` into the same leader execution when the gate denies. The nudge is the leader's continuation signal: "The work is not yet finished — check current progress and continue." It is injected in-graph (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`) and routes the execution back to the `agent` node — no `manager.enqueue_message` is called on deny, no instance is revived (the leader is RUNNING throughout). |
| **Bounded retry** | A per-instance counter (`denied_count`, row-scoped instance column) tracking consecutive denied completions. After the bound is reached, the gate allows the next would-be END AND emits a `gate_terminal_after_bound` observability event AND sets a persistent `completion_gate_escalated=true` flag on the instance row. Reset to 0 on every allow (row survives revive, unlike the loop-breaker in-memory `_loop_breaker_state`). |
| **Mode** | A single tri-state env var `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce` (default `dry` at ship). Replaces the previous two-env-pair design (one env for the on/off state plus a separate env for a dry-run toggle) because the pair admits an inconsistent combination with no well-defined meaning. Restart-read via Pattern C resolver (`daemon/services/instance_messaging.py:114-191` precedent). `off` ⇒ gate does not run (legacy behavior preserved); `dry` ⇒ gate evaluates and emits `leader_completion_gate` decision-log entries with `decision: "dry_log"` (canonical enum per Phase 4 task 4.5 / CR-4) and scanner diagnostics, but allows every END (zero side effects); `enforce` ⇒ gate denies per FR-3. Promote to `enforce` only after ≤2-week soak with adjudicated dry-log false-positive rate (computed from `dry_log_deny_predicate_total / dry_log_total` — replaces the previous fuzzy `would_have_denied` metric per CR-4). |
| **Attempt ledger** | The per-instance persistence of `denied_count` (counter) and `completion_gate_escalated` (escalation marker flag), stored as **row-scoped instance-row columns** (NOT an in-memory dict, NOT a side table). PG+SQLite-safe migration (fresh-SQLite boot trap is a live hazard). Counter resets to 0 on every allow (architect ruling — `daemon/manager.py:3734/:3798/:8548` is the in-memory precedent only, not applicable to DB columns). |
| **Fail-open (gate exception path)** | When the scanner or gate raises an unexpected exception (programming error, transient I/O), the gate MUST allow completion and emit a structured error log. This is the **gate-level** fail-OPEN (W4 precedent `graph.py:2663-2688`, narrow exception set). It is distinct from the scanner's **cannot-prove ⇒ deny** rule (compaction summary encountered first ⇒ deny, since the gate cannot prove attestation). The narrow bootstrap exception set deliberately does NOT cover SQLAlchemy `OperationalError` (the `denied_count` ledger DB seam) — that path must surface as a structured error log. |

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
6. **The deny injects a checkpoint-durable HumanMessage nudge into the same execution ("The work is not yet finished — check current progress and continue.") and routes back to the `agent` node.** No `manager.enqueue_message`, no revive. If the leader LLM had pending children or queued wakeups at step 5, the gate would have allowed the END without attestation (legitimate delegation turn-end, R2).

The feature fixes step 6 by requiring an explicit attestation tool call between step 4 and step 6; missing attestation in a no-pending state triggers an in-graph nudge rather than terminal write.

---

## Functional Requirements

| ID | Requirement | Rationale | Priority | Theme |
|----|-------------|-----------|----------|-------|
| FR-1 | The leader agent MUST have access to a new attestation tool whose name, signature, and category are stable. The tool is a no-op except for recording the call into the instance's message history. | The attestation signal must be observable in the LangGraph message stream (tool_calls list of the most recent AIMessage) and must not depend on the LLM's text output. | Must | Attestation tool |
| FR-2 | When an instance's completion gate evaluates, the system MUST scan the last N messages (configurable) of the leader's `state.values['messages']` for an AIMessage whose `tool_calls` includes the attestation tool name. | Tool-call introspection of AIMessages is the only deterministic way to confirm a real tool was invoked (vs. the LLM claiming it was). N is configurable per requirement NFR-2. | Must | Attestation check |
| FR-3 | When the in-graph gate (a) observes a leader turn-end (the `attestation_gate` node is reached), (b) sees no attestation tool call within the window per FR-2, (c) sees `pending_children == 0` AND `queued_or_expected_wakeups == 0`, the gate MUST deny the completion. A deny MUST NOT write the terminal status. The deny MUST trigger the in-graph nudge per FR-4, NOT `manager.enqueue_message` and NOT instance revival. | Per R2, this is the bug fix: a hallucinated completion followed by zero pending state. Legitimate delegation turn-ends (children active / wakeups pending) are allowed without attestation — the R2 input kills "nudge-flood". Without this denial, hallucinated completions continue to strand missions. | Must | Hallucination recovery |
| FR-4 | The deny path MUST inject a checkpoint-durable `HumanMessage` directly into the leader's graph state (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`), with content *"The work is not yet finished — check current progress and continue."* The injection lands in the SAME execution, the leader remains RUNNING, the graph routes back to the `agent` node. NO `manager.enqueue_message` call on deny, NO instance revival. The durable-enqueue recovery injector (and its facade-forwarding / JAFP tests) is RELOCATED to phase6 (`phase6-fastfollow-plan.md`) as a C backstop, post-soak. | Durability comes from LangGraph checkpointing (no RAM-only injection); the in-state nudge survives node-boundary checkpoints because it IS a checkpointed message. Same-execution routing avoids the observer-vs-revive race that a deferred enqueue would reintroduce. The phase6 backstop only matters if the in-graph nudge later proves insufficient (e.g., for OS-2 / parent-cascade no-leader-turn completions). | Must | Hallucination recovery (in-graph nudge) |
| FR-5 | The leader agent's prompt (workflow.md / rule.md / dispatch prompts) MUST contain a contract that the leader MUST call the attestation tool before declaring itself done. This contract is documented in `agents/leader/rule.md` under a canonical `### Must` block (house style). | Without a prompt-side obligation, the leader LLM has no reason to call the tool. The house-style `### Must` block placement ensures prompt hygiene and reviewer visibility. | Must | Prompt contract |
| FR-6 | The system MUST track a per-instance denied-completion counter and bound it at a configurable maximum (default proposed: 3). When the bound is exceeded, the system MUST allow the instance to terminate normally AND emit an escalation observability event AND set a persistent flag on the instance row indicating "completion gate denied N times before terminal." The counter MUST reset to 0 on every allow (and reset on terminal-after-bound). | Prevents infinite loops if the leader LLM is pathologically incapable of producing an attestation. Keeps missions from being permanently stuck. The reset-on-allow rule is critical because instance-row columns survive revive — without reset, a revived leader starts its next mission pre-burdened. | Must | Loop safety |
| FR-7 | The attestation window size N MUST be configurable at boot via the restart-read resolver pattern (env → yaml → default) and MUST default to a sane value (architect to confirm; proposed default: 3). At startup the resolver MUST assert `N ≤ min_recent_window` (currently 3 — `daemon/compaction.py`), raising a one-time boot WARN if the constraint is violated; the gate continues running regardless, but the violation is operator-visible. | Different mission classes may have different message densities; hardcoding N creates brittleness. The boot assert (per O1) is the operator-facing guard for the known failure mode where raising WINDOW above the compaction floor causes false-positive folding-attestation denies. | Must | Configurability |
| FR-8 | The feature's mode MUST be set via a single tri-state env `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce`, resolved at boot by the restart-read Pattern C resolver (cached global + one-time boot log), defaulting to `dry` at ship. When mode=`off`, the gate MUST behave as if no attestation is required — i.e., the system behaves exactly as today. When mode=`dry`, the gate evaluates and emits structured decision-log entries with `decision: "dry_log"` (canonical enum per Phase 4 task 4.5 / CR-4) and scanner diagnostics (`pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`), but every END is allowed (zero side effects). When mode=`enforce`, the gate denies per FR-3. The legacy single-bool env var (under a different name) is NOT a supported surface — the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE`, and the resolver raises `ResolverError` if any non-tri-state key is set. | Operators need a tri-state mode for incident response: instant revert (off), observable soak (dry), production enforcement (enforce). Promoting dry → enforce is operator-driven after a ≤2-week soak with adjudicated dry-log false-positive rate. | Must | Mode (kill-switch replacement) |
| FR-9 | The attestation tool category MUST be opt-in via `agents/leader/meta.json` `tools.allow` (the agent's existing authz path), MUST be added to a category list whose membership is fail-closed, and MUST be discoverable by the existing tool registry drift tests (`tests/unit/tools/test_upgrade_registration.py`). | Mirrors the existing authz discipline (absent category = unavailable). Drift tests catch silent-registration omissions. | Must | Authorization |
| FR-10 | The system MUST emit an observability event for each gate decision (allowed / denied / terminal-after-bound / scanner-allowed-on-exception) carrying: instance_id, gate_decision, attestation_present (bool), denied_count, gate_location, leader_prompt_version, mode, pending_children, queued_or_expected_wakeups, attest_seen_outside_window (any attestation call observed beyond the window — diagnostic for the R2 threshold), messages_scanned (int, >0 in healthy dry runs), scanned_window_size. The event MUST be persisted to the system log via the standard structured logging path. In `dry` mode every gate evaluation MUST emit a decision-log entry regardless of the deny/allow choice. | Post-incident diagnosis requires knowing what the gate did and why. The expanded schema (vs the original FR-10) is required for W5 dry-run measurability and for adjudicating the R2 input (how often is `attest_seen_outside_window` true in dry logs?). | Should | Observability |
| FR-11 | The feature MUST be scope-aware: it covers the leader agent by default. Whether it also covers any non-leader "parent" instance is an **OPEN scoping decision** (see Gaps & Ambiguities §G1). The recommended default is **leader-only** for v1; the architect decides. | Avoids over-scoping before the bug is reproduced in non-leader parents. | Should | Scope |
| FR-12 | The system MUST log a one-time boot line announcing the resolved effective values: (mode, attestation_window N, denied_completion_bound, gate_locations_active, boot_assert_N_le_min_recent_window (PASS/WARN)). | Operators need a single line they can grep to confirm config at boot. The boot line is the same pattern as the WC-wake resolver (`instance_messaging.py:114-191`). The N-vs-min_recent_window assert is part of the boot line so a misconfigured WINDOW is operator-visible. | Should | Observability |
| FR-13 | The gate MUST fail-OPEN on any scanner/gate exception (try/except, W4 precedent `graph.py:2663-2688`). On exception the gate MUST allow completion, MUST emit a structured `gate_exception` log entry with exception type and stack-trace summary, and MUST set a transient `gate_exception_seen=true` flag on the instance row for operator visibility. The narrow bootstrap exception set deliberately does NOT cover SQLAlchemy `OperationalError` raised by the `denied_count` ledger DB seam — that path emits a `gate_ledger_db_error` log and the gate behavior is implementation-defined (it MUST NOT silently inflate the counter; idempotency is per-denial-epoch). | An unhandled scanner exception on the routing path would error every leader mission, which is D2's outage class. The W4 precedent shows a narrow try/except set does NOT cover DB OperationalError, so this FR codifies the explicit exclusion. | Must | Resilience (fail-open) |

### Theme: Attestation Tool

**FR-1:** A new tool category — proposed name `attestation` (architect to confirm) — is registered with a single tool method whose canonical name is proposed as `attest_completion`. The tool:
- **Rationale:** must be unambiguously introspectable in `state.values['messages']` via AIMessage.tool_calls.
- **Priority:** Must.
- **Notes:**
  - The tool body is a no-op aside from returning a structured success payload (e.g. `{"attested": true, "timestamp": "<iso>"}`). It does not mutate any persistent state by itself — attestation is recorded in the message history by virtue of the tool call existing there.
  - Registration MUST follow the three-step + statics discipline (`daemon/tools/_tool_registry.py:454-493`; `DYNAMIC_TOOL_NAMES`; `KNOWN_TOOL_NAMES` drift test; decorator-only registration is silently invisible).
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
1. Increments the per-instance `denied_count` (row-scoped column; DB survives revive).
2. If counter < bound (FR-6), the gate **denies** by injecting a checkpoint-durable `HumanMessage` directly into the leader's graph state with content `"The work is not yet finished — check current progress and continue."`, and routing back to `agent`. The leader remains RUNNING throughout. There is **no** `manager.enqueue_message` call, **no** instance revival, **no** cross-turn delivery.
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

> *When your work for this mission is genuinely complete and you are about to be done, you MUST call the `attest_completion` tool. Do not declare done in plain text. If you receive a HumanMessage in the conversation containing "The work is not yet finished — check current progress and continue.", treat it as a real user instruction: review your current progress, complete the remaining work, and only then call `attest_completion`.*

- **Rationale:** without an explicit prompt contract, the leader LLM has no reason to call the tool. The HumanMessage framing in the contract matches the actual delivery shape (in-state `HumanMessage`, not a `MessageQueue` enqueue).
- **Priority:** Must.
- **Notes:**
  - House style per `agents/leader/rule.md` is mandatory `### Must` blocks under `## Must`. Follow exactly.
  - The text above is a draft; the architect + reviewer may refine it.

### Theme: Loop Safety

**FR-6:** Per-instance `denied_count` persisted as a **row-scoped instance-row column** (NOT an in-memory dict, NOT a side table). Bound is configurable (default proposed: 3, env `ENSEMBLE_LEADER_ATTESTATION_DENY_BOUND`). When the counter reaches the bound, the gate MUST allow the next would-be END AND emit a `gate_terminal_after_bound` observability event carrying: instance_id, denied_count, bound, last_denial_reason. A persistent `completion_gate_escalated=true` flag MUST be set on the instance row.
- **Rationale:** prevents pathological infinite loops while preserving mission forward progress. The DB column survives instance revival (terminal→RUNNING via `enqueue_message` to terminal instance at `instance_messaging.py:1867-1909`); without reset-on-allow, a revived leader starts its next mission pre-burdened.
- **Priority:** Must.
- **Notes:**
  - **Reset semantics:** `denied_count` resets to 0 on every allow (FR-3 → attested path) AND on every terminal_after_bound. Reset is on the same tx as the allow write, so the counter never inflates across instance reuse.
  - **Migration:** PG+SQLite-safe (fresh-SQLite boot trap from migration `20260714_000001` is a live hazard — known project risk).
  - **Failure mode on the ledger DB seam:** if `denied_count` increment raises `OperationalError` (DB unavailable), the gate MUST NOT silently inflate or double-count; idempotency is per-denial-epoch (a documented per-increment nonce or upsert). See FR-13.
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
| NFR-7 | Security | Attestation tool MUST be fail-closed — agents without the category in their `tools.allow` MUST NOT be able to invoke it (regardless of how the LLM phrases a request). | Authorization correctness. | Leader-only invocation; non-leader parents cannot invoke | Unit test: instantiate non-leader agent, assert tool absent; integration test: assert non-leader parent completion is NOT attestation-gated (per FR-11 OPEN scope decision). |
| NFR-8 | Observability | Each gate decision MUST emit a structured log entry conforming to the canonical schema at Phase 4 task 4.5 (verbatim pointer — do not restate): `decision` is one of the canonical `Decision` enum values `allowed | denied | terminal_after_bound | dry_log | allowed_legitimate_pending_wakeup`; all canonical schema fields are present (`event`, `decision`, `instance_id`, `attestation_present`, `denied_count`, `gate_location`, `leader_prompt_version`, `pending_children: int`, `queued_or_expected_wakeups: int`, `attest_seen_outside_window`, `messages_scanned`, `scanned_window_size`, `mode`, `scanner_window_truncated`, `scanner_summary_seen`). In `dry` mode every evaluation MUST emit a decision-log entry (zero-side-effect) with `decision: "dry_log"` (canonical enum) and the R2-deny predicate derivable from the canonical schema fields (`pending_children: int == 0 AND queued_or_expected_wakeups: int == 0 AND attestation_present: bool == False` ⇒ R2-deny predicate satisfied). Exception-path evaluations (C3 fail-open) emit the mode-appropriate canonical decision (`decision: "allowed"` under enforce, `decision: "dry_log"` under dry) and the exception detail is recorded in the diagnostics layer via the C3 error log (`event=leader_completion_gate_error` or `event=leader_completion_gate_db_error`, with `error_class: str` per Phase 4 task 4.5) — the canonical `Decision` enum does not include a separate exception-path value; fail-open is a PATH, not a decision value. | Log signature completeness. | All decisions logged with full schema | Integration test asserts log entries match schema; log search asserts all required keys present. |
| NFR-9 | Observability | Boot log line MUST announce effective resolved values for the three config knobs plus the boot-assert result for `N ≤ min_recent_window`. | Operator visibility. | One log line at boot with all keys | Boot the daemon with each env var set/unset; assert the resolved value matches expectation. |
| NFR-10 | Compatibility | The feature MUST NOT alter behavior when `ENSEMBLE_LEADER_ATTESTATION_MODE=off`. | Backward compatibility. | Existing behavior preserved when off | Integration test: run completion path with mode=off, assert byte-equivalent behavior to a pre-feature baseline reference. |
| NFR-11 | Compatibility | The feature MUST NOT break: normal leader completion WITH attestation; mission finalize path; revive semantics (COMPLETED → RUNNING on `enqueue_message`); WC-wake routing lanes (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF — instance_messaging.py:114-191); existing report-delivery recovery sweeps (`ReportDeliveryRecoveryService`, `WaitingChildrenWatchdog`); the report-injection claim state machine PENDING→INJECTED rowcount-guard (graph.py:416-490); the in-state `HumanMessage` reminder precedent (`daemon/graph.py:2666-2685`). | Must-not-break surface list. | All listed surfaces behave identically across all three mode values | Integration test suite parameterized over `mode ∈ {off, dry, enforce}` asserting no behavior delta on the must-not-break list. |
| NFR-12 | Compatibility | The feature MUST NOT introduce a defer-starvation footgun: if the gate ever emits a `gate_deferred` return that is not re-armed or finalized, the related job strands in `admission_state='active'` indefinitely. | Job-strand avoidance. | Any `gate_deferred` path either re-arms or finalizes | Code review + integration test asserts job state after denied completion. |
| NFR-13 | Maintainability | The attestation tool name, mode, window N, bound, and dry-log emission MUST be configurable, NOT hardcoded. | Configurability. | All values come from the resolver, not literals | Code review + drift test on hardcoded constant grep. |
| NFR-14 | Maintainability | The gate decision logic MUST be unit-testable in isolation from the graph (pure function over `(attestation_present, pending_children, queued_or_expected_wakeups, denied_count, bound, mode, scope)` → `{allow, deny, terminal_after_bound}`). | Testability. | Pure function exists and is unit-tested | Unit test file exercises the pure decision function over a full input matrix. |
| NFR-15 | Resilience | On ANY unhandled exception in the scanner or gate decision logic, the gate MUST allow completion AND emit a structured `gate_exception` log entry with exception type and a stack-trace summary AND set a transient `gate_exception_seen=true` flag on the instance row. The exception set MUST include the bootstrap precedent `except Exception` (W4, `graph.py:2663-2688`) and MUST NOT include SQLAlchemy `OperationalError` raised by the `denied_count` ledger DB seam — that path emits `gate_ledger_db_error` and is implementation-defined within idempotency constraints. | Fail-open continuity. | Scanner exception ⇒ allow; ledger DB exception ⇒ controlled, observable | Unit test injects scanner exception and asserts allow; integration test simulates `OperationalError` on increment and asserts idempotency + structured log. |
| NFR-16 | Observability | The dry-mode decision log MUST include all R2 inputs (`pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`) and a `messages_scanned` count > 0 on healthy evaluations. The schema MUST be sufficient to adjudicate: (a) what fraction of dry evaluations would have denied under enforce, (b) how often `attest_seen_outside_window=true` (the signal that the R2 input is firing correctly), (c) how often `pending_children>0` allows a would-be-deny. | Dry-run promotion gate data. | Adjudicated dry-log false-positive rate is measurable before promote-to-enforce | Integration test replays a recorded session with mode=dry and asserts the schema is sufficient to derive all three rates; operator runbook references this NFR by ID. |

---

## Constraints

| ID | Type | Description | Source | Impact |
|----|------|-------------|--------|--------|
| C-1 | Technical | The feature MUST integrate via the existing tool registration three-step + statics discipline; decorator-only registration is silently invisible and is forbidden. | Architecture blueprint | Any tool implementation must follow the registry pattern or it won't be discoverable. |
| C-2 | Technical | Attestation tool category MUST go through the existing `tools.allow` opt-in pattern in `meta.json`; there is no other authorization path. | Architecture blueprint | Categories not listed are unavailable — fail-closed. |
| C-3 | Technical | The deny path MUST inject an in-state `HumanMessage` directly into the leader's graph (mirroring the `language_check` reminder precedent at `daemon/graph.py:2666-2685`); the durable-enqueue recovery path (`manager.enqueue_message`) is RELOCATED to `phase6-fastfollow-plan.md` as a C backstop. The MVP path MUST NOT call `manager.enqueue_message` on deny and MUST NOT revive the leader. | R1 (architecture recommendation §3) | Enqueue on deny would reintroduce the observer-vs-revive race B is meant to eliminate, and would double-deliver once the leader does attest. |
| C-4 | Technical | The gate's deny input MUST require `attestation_present == false` AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` simultaneously. If any of the three is non-zero, the gate allows without attestation. | R2 (architecture recommendation §3) | Without the pending-wakeup input the gate would nudge-flood legitimate delegation turn-ends. |
| C-5 | Business | The tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE` default (`dry` at ship) and the promote-to-`enforce` criterion (≤2-week soak on dry-log false-positive rate) are RESOLVED per architect ruling D2. | Architect | The default governs the initial rollout; the promote-runbook entry must ship with the gate. |
| C-6 | Business | Scope (leader-only vs. all parents) is an **OPEN decision**; recommended default is leader-only for v1. | Architect | Determines which instances are gated (D3 OPEN). |
| C-7 | Technical | The gate's `except Exception` (W4 precedent `graph.py:2663-2688`) MUST NOT cover SQLAlchemy `OperationalError` raised by the `denied_count` ledger DB seam — that path emits `gate_ledger_db_error` and is implementation-defined within idempotency constraints. | Architecture recommendation §4 | If the wider `except Exception` covered `OperationalError`, the leader counter could silently inflate on transient DB failures, causing spurious escalation. |
| C-8 | Technical | The feature MUST coexist with the WC-wake variant (`ENSEMBLE_WC_WAKE_ENQUEUE` default OFF, module-level env resolver + cached global + one-time boot log — `instance_messaging.py:114-191`). The new tri-state MODE env resolver MUST use the same Pattern C shape (cached global + one-time boot log). | Architecture blueprint + critical notes | Both envs and both boot logs must not interfere; orthogonal concerns. |
| C-9 | Technical | The feature MUST coexist with the existing known blind spot: during the inter-report gap (previous child report processed, next not arrived), bus gate and pending-tasks gate can BOTH pass — premature finalize window. This feature addresses the leader's hallucinated case; the inter-report gap case is **out of scope** (see Out of Scope §OS-2). The C fast-follow (phase6) addresses the parent-cascade no-leader-turn path (OS-2) and ships only after the in-graph gate soak data is adjudicated. | Research / incident analysis | Other completion paths remain vulnerable to a different bug class; do not promise a fix for that class in the MVP. |
| C-10 | Technical | The pre-call compaction and other pre-completion middleware MUST run before the gate evaluates, so the gate sees the final message stream state. Order of middleware evaluation is an architect concern. The boot assert (FR-7 / NFR-9) MUST verify `WINDOW ≤ min_recent_window` to avoid the `WINDOW=5` while compaction fold floor is `min_recent_window=3` failure mode. | Architecture blueprint (D10(b)) | Wrong order or misconfigured WINDOW → gate makes decisions on stale messages or false-positives folded-attestation denies. |
| C-11 | Technical | The `denied_count` column MUST reset to 0 on every allow (FR-3 attested path) AND on `terminal_after_bound`. Without the reset, a revived leader instance (terminal→RUNNING per `instance_messaging.py:1867-1909`) starts its next mission pre-burdened. Reset is implementation-defined (idempotent per-denial-epoch upsert, OR a documented single-tx UPDATE) but MUST be observable in the gate decision log. | Architecture recommendation D5 | O2 architect ruling. |
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
- **Given:** the gate reaches the `attestation_gate` node, `mode=enforce`, scope applicable (leader), AND `pending_children == 0` AND `queued_or_expected_wakeups == 0` AND `scanner_result.attested == False` AND `denied_count < bound`.
- **When:** the gate evaluates the would-be END.
- **Then:** the terminal-status write is NOT performed AND a checkpoint-durable `HumanMessage` with content `"The work is not yet finished — check current progress and continue."` is appended to `state['messages']` AND the graph routes back to the `agent` node AND `denied_count` increments AND a `denied` decision-log entry is emitted (carrying `pending_children`, `queued_or_expected_wakeups`, `attest_seen_outside_window`, `messages_scanned`). NO `manager.enqueue_message` call is made; no instance revival occurs.
- **Test type:** integration.

**AC-3.2** (allow path)
- **Given:** the gate evaluates a leader instance.
- **When:** `scanner_result.attested == True`.
- **Then:** the terminal-status write proceeds normally AND no nudge is injected AND `denied_count` is reset to 0 on the same tx AND an `allowed` decision-log entry is emitted.
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
- **Then:** a `HumanMessage` with content `"The work is not yet finished — check current progress and continue."` is appended to `state['messages']` with `additional_kwargs={"attestation_nudge": True}` (exact mirror of `language_check` reminder precedent at `daemon/graph.py:2666-2685`) AND the graph returns control to the `agent` node. NO `MessageQueue` row is created, NO `Task` row is created, NO `worker_pool.notify_work()` is called, NO `manager.enqueue_message` is invoked.
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
- **Then:** it matches the verbatim constant `"The work is not yet finished — check current progress and continue."` exactly — no LLM-supplied or user-supplied fragments are concatenated. (NFR-6.)
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
- **Given:** `denied_count = k` and `bound = 3`.
- **When:** a deny fires (AC-3.1 path).
- **Then:** `denied_count` becomes `k+1`.
- **Test type:** unit.

**AC-6.2** (allow at bound)
- **Given:** `denied_count = bound = 3`.
- **When:** the next completion-eligible evaluation occurs.
- **Then:** the gate allows terminal AND emits `gate_terminal_after_bound` event AND sets `completion_gate_escalated=true` on the instance row AND resets `denied_count` to 0.
- **Test type:** integration.

**AC-6.3** (no infinite loop)
- **Given:** a leader LLM that NEVER calls the attestation tool.
- **When:** the gate runs 10 consecutive denial cycles (more than any reasonable bound).
- **Then:** the gate terminates the instance after `bound` denials and emits the escalation event exactly once.
- **Test type:** integration (replay from a recorded session).

**AC-6.4** (counter resets on new mission)
- **Given:** a leader instance that has been escalated (`completion_gate_escalated=true`).
- **When:** a new mission is dispatched to a fresh leader instance.
- **Then:** the fresh instance starts with `denied_count = 0`.
- **Test type:** unit.

**AC-6.5** (counter resets on every allow — O2)
- **Given:** the leader has `denied_count = 2`, `mode=enforce`.
- **When:** the leader calls `attest_completion` before END and the gate re-evaluates with `attested=True`.
- **Then:** `denied_count` is reset to 0 on the same tx as the allow write (or as a same-DB-session UPDATE). Crucially, this MUST happen — without reset, a revived leader starts the next mission pre-burdened.
- **Test type:** unit + integration.

**AC-6.6** (ledger DB OperationalError ⇒ structured log, NOT silent inflation — C7)
- **Given:** the gate would increment `denied_count`, but the SQLAlchemy UPDATE raises `OperationalError` (DB unavailable).
- **When:** the exception is raised.
- **Then:** the gate emits a `gate_ledger_db_error` log entry AND does NOT silently inflate `denied_count` AND does NOT silently allow-completion; idempotency is per-denial-epoch (a documented per-increment nonce OR per-instance monotonic counter that the gate logic uses for the next evaluation). Implementation choice between "drop the increment, deny anyway" and "retry the increment with backoff" is developer-decision; both are acceptable if observable.
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

**AC-7.9** (forbid legacy single-bool surface — C-5/C-12)
- **Given:** a legacy single-bool env (under any prior canonical name) is set in the daemon environment, with any single-bool value (`0`, `1`, `true`, `false`, `off`, or `on`).
- **When:** the resolver parses the environment.
- **Then:** **the resolver raises a `ResolverError` at boot** (fail-CLOSED — the legacy key is rejected, not silently ignored). The legacy key is NOT honored; the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE`. The test target is unambiguous: the resolver raises, the daemon refuses to start (mirrors the WC-wake resolver's fail-closed posture for typo'd keys per `daemon/services/instance_messaging.py:114-191`).
- **Test type:** unit.

### FR-13: Fail-open on scanner/gate exceptions

**AC-13.1** (scanner exception ⇒ allow)
- **Given:** the scanner raises an unexpected exception (e.g., `AttributeError` from a malformed message).
- **When:** the gate catches the exception via the W4 precedent try/except.
- **Then:** the gate allows completion, emits a `gate_exception` log entry with exception type and stack-trace summary, AND sets `gate_exception_seen=true` on the instance row.
- **Test type:** unit.

**AC-13.2** (OperationalError is NOT in the bootstrap exception set — C7)
- **Given:** the gate would increment `denied_count` and the SQLAlchemy UPDATE raises `OperationalError`.
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

**AC-10.3** (dry-mode would-have-denied schema)
- **Given:** `mode=dry`, leader mission that would have denied.
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
  3. `denied_count` increments from 0 to 1.
  4. In-graph `HumanMessage` with content `"The work is not yet finished — check current progress and continue."` is appended to `state['messages']`.
  5. Graph routes back to `agent` (no enqueue, no revive).
  6. Terminal-status write is NOT performed.
  7. Leader resumes, sees the in-state `HumanMessage` as a fresh user instruction.
  8. Leader's prompt contract instructs it to continue; leader completes remaining work and calls `attest_completion`.
  9. Gate re-evaluates, scanner returns `attested=True`; `denied_count` resets to 0 on the same tx (AC-6.5).
  10. Terminal-status write proceeds; mission finalizes.
  11. Log entries: 1× `denied` (with all NFR-8 keys), 1× `allowed`. No `gate_terminal_after_bound`. No `MessageQueue` / `Task` rows for the nudge.
- **Test type:** end-to-end integration (record/replay or scripted LLM mock).

**AC-E2E-1b** (pending-wakeup delegation turn-end allows without attestation — R2)
- **Given:** a leader instance with `pending_children > 0` (a child is in WAITING_CHILDREN or ACTIVE) when the leader LLM emits a final assistant message and routes END.
- **When:** the leader LLM does NOT call `attest_completion`.
- **Then:**
  1. The gate evaluates, scanner returns `attested=False`.
  2. `pending_children > 0` (R2 satisfied).
  3. The gate allows the END without attestation. NO nudge is injected. `denied_count` is NOT incremented. `denied_count` resets to 0 if it was non-zero (because this is an allow).
  4. Terminal-status write proceeds (or the gate defers normally if the leader has children to process first — that is the pre-existing behavior).
  5. Decision log entry: `decision=allowed_legitimate_pending_wakeup` with R2 inputs (AC-3.3 schema).
- **Test type:** integration.

**AC-E2E-2** (bound-exceeded escalation)
- **Given:** a leader LLM that cannot be induced to call `attest_completion`.
- **When:** the leader attempts completion `bound + 1` times in a no-pending state.
- **Then:**
  1. First `bound` attempts produce `denied` log entries and in-state nudges (per AC-3.1/AC-4.1).
  2. `(bound + 1)`-th attempt produces a `gate_terminal_after_bound` event and the instance transitions to COMPLETED.
  3. The instance row carries `completion_gate_escalated=true`; `denied_count` is reset to 0 (AC-6.2).
  4. Mission finalizes; downstream state sees a terminal leader.
- **Test type:** integration.

**AC-E2E-3** (normal attested completion unaffected)
- **Given:** a leader LLM that properly calls `attest_completion` before END.
- **When:** the gate evaluates.
- **Then:**
  1. Scanner returns `attested=True`.
  2. Terminal-status write proceeds normally.
  3. No nudge is injected.
  4. `denied_count` resets to 0 (or stays at 0 from prior allow).
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
- **Then:** the decision-log corpus is sufficient to compute: (a) `dry_log_deny_predicate_total` fraction (replaces the previous fuzzy "would-have-denied fraction" name; per CR-4 the canonical metric is `dry_log_deny_predicate_total`, the count of dry evaluations where the R2-deny predicate is satisfied — i.e. `attestation_present == false AND pending_children == 0 AND queued_or_expected_wakeups == 0`), (b) `attest_seen_outside_window=true` rate, (c) `pending_children>0`-allows rate (canonical `allowed_legitimate_pending_wakeup` decision per Phase 4 task 4.5 / CR-4). Promote-to-enforce is operator-decision per the runbook; the metric source is reproducible. **Recorded-corpus ownership** (resolves the yellow-note ambiguity): the recorded leader-mission dataset lives at `tests/fixtures/recorded_leader_missions/` (test fixture location, checked into the repo) and is owned by the Phase 5 test author. The fixture loader is a deterministic emitter that replays each mission's AIMessage sequence through the gate and captures the canonical log schema (Phase 4 task 4.5) into a JSONL file at `tests/fixtures/recorded_leader_missions/_runs/<mission_id>.jsonl`. The corpus is versioned alongside the test fixtures; reproducibility requires the corpus to be committed. The replay driver is `tests/support/recorded_corpus_replay.py` (new — Phase 5 task 5.16 owns this file).
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
| G1 | Scope: should the gate apply only to the `leader` agent, or to any parent instance? | Architect to confirm. **Recommended default: leader-only for v1** (least surface area; matches the incident report; non-leader parents are not yet observed to hallucinate this way). | High |
| G2 | ~~Kill-switch default: ON or OFF at first deploy?~~ | **RESOLVED → C5 + FR-8:** `ENSEMBLE_LEADER_ATTESTATION_MODE` defaults to **`dry`** at ship; promote to `enforce` after ≤2-week soak on adjudicated dry-log false-positive rate. The legacy single-state-mode env (under any prior name) is not a supported surface. | — (closed) |
| G3 | Attestation tool canonical name: `attest_completion` vs. `mark_done` vs. `complete_mission` vs. other. | Architect to confirm. Recommendation: `attest_completion` — descriptive and unambiguous. | Medium |
| G4 | Attestation tool argument shape: no-arg, or accept an optional mission id / notes field? | Architect to confirm. Recommendation: no required args; optional `notes` field for diagnostics. | Medium |
| G5 | Default attestation window N: 3? 5? 1? | **Tied to compaction floor**: FR-7 requires `N ≤ min_recent_window` (currently 3 in `daemon/compaction.py`) at boot — N=3 is the default and matches the floor; raising above the floor fires the boot WARN per AC-7.8. | Medium |
| G6 | Default denied-completion bound: 3? 5? | Architect to confirm. Recommendation: 3 — low enough to flag pathological behavior quickly, high enough to survive noisy turns. | Medium |
| G7 | Where does the gate live? `should_continue` (graph.py:2462-2533), child_reports._process_child_completion_db_sync (root_completed path :2566), job_feedback_observer._finalize_job_db_sync (:3703-3758), or multiple? | **RESOLVED → D1=B**: in-graph `end_candidate` interception, wired under its own flag in BOTH branches of `create_should_continue(language_check_enabled)` (the `language_check=on` AND `language_check=off` paths — `:2707` has two paths; piggybacking on language_check wiring silently disables the gate for most instances). Graph-build-time `agent_id == 'leader'` check keeps non-leader graphs untouched. | — (closed by D1) |
| G8 | Where is `denied_count` persisted? Instance row, side ledger, or LangGraph state? | **RESOLVED → D5**: instance-row column (`denied_count`, `completion_gate_escalated`). PG+SQLite-safe migration. `denied_count` resets to 0 on every allow AND on terminal-after-bound (architect ruling: rows survive revive). | — (closed by D5) |
| G9 | Does the recovery message always use the same verbatim text, or should it include mission-specific context (instance id, mission id, denied_count)? | **Moot per R1**: the in-graph nudge is a server-authored constant; mission context is emitted to the dry-log schema, not into the message. | — (closed) |
| G10 | Does the recovery message pre-emptively raise the leader's `denied_count` so the leader sees its own count? | **Moot per R1**: there is no separate recovery message; the nudge is the in-state `HumanMessage`; the leader's `denied_count` is row-stored and not surfaced to the LLM. | — (closed) |
| G11 | When the gate denies, does the leader's existing checkpoint get reused (revive) or does a new turn start fresh? | **RESOLVED → R1**: the leader is RUNNING throughout; no revive is involved. The in-graph nudge is appended to existing `state['messages']`; LangGraph checkpoints at node boundaries. | — (closed by R1) |
| G12 | Should the gate also evaluate when the instance is being TERMINATED by operator (not hallucinating)? | Architect to confirm. Recommendation: NO — operator termination bypasses the gate; operator action is intentional. | Medium |
| G13 | Should the recovery message have a fixed `priority`? | **Moot per R1**: no separate recovery message; the in-state nudge is ordered by LangGraph checkpoint append (no `priority` field). | — (closed) |
| G14 | Boot assert (O1): should the gate hard-fail or merely WARN when `WINDOW > min_recent_window`? | **RESOLVED → FR-7**: WARN-only. The gate continues running; the violation is operator-visible in the boot log (per AC-7.7, AC-7.8). Hard-fail would block Phase 2 deployment for misconfigured WINDOW values, which is operationally brittle. | — (closed) |
| G15 | Fail-open exception set (C7): does the bootstrap `except Exception` cover SQLAlchemy `OperationalError`? | **RESOLVED → C7**: NO. The W4 precedent (`graph.py:2663-2688`) narrow set does NOT cover `OperationalError` raised by the `denied_count` ledger DB seam; that path emits `gate_ledger_db_error` (per AC-6.6) and is implementation-defined within idempotency constraints. | — (closed) |

---

## Resolved Decisions (Reference)

These were OPEN at the planner stage and are resolved by the architect's review and the leader rulings. Listed here so the spec layer reflects the post-reconciliation shape.

| # | Decision | Resolution | Source |
|---|----------|------------|--------|
| **R1** | Deny path semantics | **In-graph checkpoint-durable HumanMessage nudge (R1)**. NO `manager.enqueue_message` on deny, NO revive. Durable-enqueue recovery injector relocated to `phase6-fastfollow-plan.md` (C backstop, post-soak). | `architecture-recommendation.md` §3 C5 interpretation fork |
| **R2** | Gate deny input | **Pending-wakeup input required**: deny ONLY when `attestation_present == false` AND `pending_children == 0` AND `queued_or_expected_wakeups == 0`. Legitimate delegation turn-ends are allowed un-attested. | `architecture-recommendation.md` §3 |
| **D1** | Gate placement | **B (in-graph `end_candidate` interception)** under its own flag in both `create_should_continue` branches, leader-only by graph-build-time `agent_id` check. | `architecture-recommendation.md` §1 D1 |
| **D2** | Mode env (kill-switch replacement) | **Tri-state `ENSEMBLE_LEADER_ATTESTATION_MODE=off\|dry\|enforce`**, default `dry` at ship. The single-state-mode env shape is NOT supported on any legacy key; the canonical key is `ENSEMBLE_LEADER_ATTESTATION_MODE`. | `architecture-recommendation.md` §1 D2 |
| **D5** | Retry bound + ledger semantics | `denied_count` (row-scoped instance column, PG+SQLite-safe migration), bound 3, reset to 0 on every allow AND on terminal-after-bound, escalation flag `completion_gate_escalated`. | `architecture-recommendation.md` §1 D5 |
| **D6** | Recovery source value | **RELOCATED to phase6** (D6 status: DEFERRED-to-phase6). The MVP deny path is in-graph only; the durable-enqueue path is the C backstop, post-soak. | `architecture-recommendation.md` §1 D6 |
| **D7** | Tool semantics | `attest_completion`, no-arg, idempotent (any call in window counts), short confirmation ToolMessage return, NOT privileged. | `architecture-recommendation.md` §1 D7 |
| **D8** | Dry-run / observability | The tri-state `dry` mode IS the dry-run (no separate pre-Phase-2 activity). Dry lines carry scanner diagnostics (window truncated, summary-seen) so dry→enforce promotion is adjudicated on data, not conjecture. NFR-16 codifies promotion-gate data. | `architecture-recommendation.md` §1 D8 |
| **O1** | Boot assert (N ≤ min_recent_window) | WARN-only at boot (FR-7 / AC-7.8). Violation is operator-visible; gate continues running. | `architecture-recommendation.md` §1 D10(b) |
| **O2** | Reset semantics | `denied_count` reset on every allow + reset on terminal_after_bound + documented reset triggers. The earlier in-memory-dict cleanup precedent (named at the planner stage) is DROPPED — row-scoped DB columns need no per-instance in-memory cleanup hooks (the precedent applies to the loop-breaker counter, not to DB columns). | `architecture-recommendation.md` §4 + §5 phasing adjustments |
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
| Scope (leader-only vs all parents) — OPEN | FR-11, C-6, G1, AC-11.1 (D3 OPEN) |
| Tool canonical name — OPEN | G3 (D7 RESOLVED: `attest_completion`, recommend; canonical name confirmation pending) |
| Window N default — OPEN | G5 (architect to confirm default; FR-7 boot-assert ties it to compaction floor) |
| Bound default — OPEN | G6 (architect to confirm; D5 recommends 3) |
| Per-instance attempt ledger storage — RESOLVED → D5 | FR-6, AC-6.5, C-11 |
| Operator termination bypass — OPEN | G12 |
| Inter-report gap bug class — separate | C-9, OS-2 |
| Phase6 backstop (durable-enqueue recovery; out of MVP) | C-3, AC-E2E-8, OS-1, OS-2 |
| Test strategy (unit for scanner/decision/fail-open; integration for full E2E + dry-adjudication) | AC-2.1..AC-2.5, AC-3.1..AC-3.4, AC-4.1..AC-4.5, AC-6.1..AC-6.6, AC-7.1..AC-7.9, AC-9.1..AC-9.3, AC-10.1..AC-10.4, AC-11.1, AC-13.1, AC-13.2, AC-E2E-1..AC-E2E-8 |
