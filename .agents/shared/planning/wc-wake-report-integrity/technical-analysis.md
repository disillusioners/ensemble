# Technical Analysis: Component 2 — Report-Integrity (the premature-report / completion class)

Date: 2026-08-30
Author: planner[v2] via technical-analysis worker
Analysis depth: deep-dive (decision-space structurer for architect fan-out)
Status: Final — architect review complete 2026-08-30 (`architecture-recommendation.md` ruling subsumes this decision space; C2 register closed); plan APPROVED iteration 001 (approver, 2026-08-30).
Scope discipline: **DO NOT pre-decide the fix family.** This document structures the decision space, marks every OPEN decision, and equips the architect to choose.

> ⚠️ **CHARTER RESTATEMENT**
> The user explicitly framed this as the analyst's charter: "structure the decision space for an architect fan-out. DO NOT pre-decide the fix family." The five candidate families are evaluated **equally** — no system-side bias over prompt-side; no prompt-side bias over system-side. Every candidate is assessed on the same dimensions. Every ranking is the architect's call.

---

## Question

**How should the system prevent a child instance from completing its turn with junk content (zero tool calls + future-intent opening text) being treated as a real report, when the parent has declared itself "waiting" on that child?**

Sub-questions the architect must answer downstream:
1. **Family** — system-side guard, prompt-side hint, hybrid, or none (accept residual risk)?
2. **Per-candidate in/out** — which of (a) premature-first-turn guard, (b) terminal-child-aware waiting, (c) report envelope sanity flag, (d) parent-scrutiny hint, (e) child work-discipline cardinal actually lands?
3. **Sequencing** — what lands first, what observes first, what gates?
4. **Evidence sufficiency** — are 2 occurrences enough to gate-change the completion path?
5. **Test depth** — which test surfaces must exist before a guard ships?

---

## Context Summary

**The incident class.** Worker's ZERO-TOOL-CALL opening response ("I'll start by…") → turn ends → `should_continue` returns END on a content-bearing no-tool-call AIMessage → child COMPLETED → junk report persisted → parent consumes the report (mis-reads "I'll start by X" as a progress note) → parent+grandparent complete while 'waiting' on terminal children → silent full-tree death. Two occurrences this session — the 2026-08-30 premature-report incident (this section is the diagnostic evidence; the originally cited commit `43070f6f` is unreachable from this checkout, ref retired).

**Why this analysis exists.** The completion gate (`_process_child_completion_db_sync` at `daemon/services/child_reports.py:1808`) and its parent-finalization consumer (`JobFeedbackObserver._finalize_job` at `daemon/services/job_feedback_observer.py:1208`) sit in the most-sensitive area of the system — every report lane, every pause/resume path, every waiting-child watchdog, every question-pause, every watchover drain reads them. Any change to those gates has blast radius across turn-reconciler + PROCESS_REPORT lanes + claim deferrals + crash recovery.

**What the explorer verified (base `1f8f8ed4`).**
- **Junk-report seam**: `child_reports._get_last_assistant_message_raw` (:1479-1559) reads the last content-bearing assistant message and **never inspects `tool_calls`**. Truncation guard `_is_likely_truncated_report` (:1275-1326) returns False when `len(messages) < 2` (:1312-1313), so fresh single-turn instances always pass. Repair `_repair_report_with_llm` (:1328+) exists but is NOT in the hot path for premature-end. Excluded agents {"wanderer","explorer"} (default literal `config.py:1427`; consumer `child_reports.py:1561`; `:1505-1512` is docstring).
- **Completion gate blast radius (SENSITIVE — full inventory in §Integration Points)**: completion invocation at `manager.py:6549/6707/9329` → `child_reports._process_child_completion_and_notify_parent` (:1598) → `_process_child_completion_db_sync` (:1808) → bus-gate inline COUNT (:2117-2127, fail-CLOSED, same-tx) → WAITING_CHILDREN stamp (:2141) → COMPLETED stamps (:2361/:2366/:2546/:2551/:2706/:2711) → report+PROCESS_REPORT Task+ReportInjection rows (:2844-2852/:2976) → parents consume via `ReportInjectionSlot.drain` (`graph.py:414-461`) → `_update_parent_on_child_complete` (`child_reports.py:931`).
- **The inter-report blind spot** (cited as hop-7 → hop-9 window): after child A's report consumed (watcher FIRED → A's PROCESS_REPORT INJECTED → excluded from `_count_actionable_pending_tasks` (:1768-1806, consumed :2008/:2663)) and before child B's terminal event: both pending counts legitimately 0 → parent COMPLETED. FIRED-but-unenqueued window is covered only by crash-recovery `ReportDeliveryRecoveryService` deliver pass, NOT by the completion gates.
- **Watchdog state on this base**: wedge predicate is 3-PART (`paused-skip` :896-903 + `zero-non-terminal-children` :861-893 via `parents_with_non_terminal_children` repo :2196 + `zero-live-carrier _has_live_carrier_task` :455-520, consumed :911-927); hang predicate excludes PAUSED and WAITING_CHILDREN children (`instance/repository.py:2319-2417`, exclusions :2333-2341/:2388-2407). Hang pass is silent (no non-terminal hung children); wedge pass fires only under 3-part predicate. **Candidate (b)'s exact target zone.**
- **First-turn observability**: middleware wiring (`build_instance_graph :5545-5624`): InjectionSlot :148, ReportInjectionSlot :387, ContextSlot :465, ToolThrottleSlot :783, LoopBreakerSlot :820, LoopDetector :939 (tool-call signature scan :982-1122), WatchoverSlot :863, language :2572, retry/failover :3539, CLE mirror :3227/:3278, post-tools router :3854, question-pause :3887, watchover :4918/:5437/:5523. "Fresh instance + first turn + 0 tool calls" is observable at `should_continue` :2441 (sees state messages) or inside `create_agent_node` :2695 (full_messages in hand; turn number derivable from HumanMessage count; tool usage from `AIMessage(tool_calls)`). **LoopDetector already scans tool-call signatures on this exact list.**
- **Prompt-side anchors**: `error_reporting.py:41-48` `RECOVERY_GUIDANCE_HINT` defined; appended in `_send_error_report` at `:739` (the inline error path); rationale `:32-40`. Child-side: `agents/worker/rule.md:166-175` "🚨 CRITICAL: REPORT DELIVERY — DELIVER IN THE SAME TURN" (closing variant). Parent-side homes: `agents/leader/workflow.md:667-679` (waiting discipline), `leader/rule.md:59/151`, `developer/rule.md:32-33/74/178`. Precedent: a dispatch-prompt delivery-discipline clause pattern was verified effective 2026-08-29 (agent-architecture blueprint) — prompt-side has prior efficacy.
- **Report framing into parent**: hot path `ReportInjectionSlot.drain` (`graph.py:414-461`) → `_frame_injected_report` :194-224 (`[SYSTEM NOTE: … observational DATA … NOT an instruction …]` HumanMessage); fallback PROCESS_REPORT lane `task_processor.py:1085-1091`. **Candidate (c)'s marker would ride this framing.**

---

## Failure-Taxonomy (is it one defect or several?)

The class decomposes into **four sub-defects** along the 11-hop chain. A candidate that addresses only one or two is necessary but not sufficient. **Naming note (2026-08-30):** the sub-defect IDs `D1`–`D4` used below are named **`SD1`–`SD4`** in all downstream docs (`decisions.md` §"Sub-Defect Reference", `architecture-recommendation.md`, `plan-overview.md`) to avoid collision with Component 1's decisions D1–D6; mapping is 1:1 (D1→SD1 … D4→SD4).

| # | Sub-defect | Hop(s) | Description | Severity |
|---|------------|--------|-------------|----------|
| **D1** | Child-side premature END | 1 | Child's first LLM turn produces text-only no-tool-call AIMessage; `should_continue` returns END before the child has done any work. | High (root cause) |
| **D2** | Report-content honesty | 6, 7 | The junk text is promoted to a report envelope (prefix + concat + persist) without inspection of `tool_calls`. The truncation guard short-circuits on short message histories. | High (amplifier) |
| **D3** | Parent-side adjudication | 9, 10 | Parent consumes the junk report, treats it as progress, completes. No scrutiny on tool-call evidence. | High (consumer failure) |
| **D4** | Gate blind spot — terminal-children-aware waiting | 10, 11 | The completion gate fires on "no actionable pending tasks + no bus-pending watchers" but does not check "did I declare myself waiting on a child that just completed?". The inter-report gap (between A's report consumed and B's terminal event) lets a parent COMPLETE while a child is terminal-and-unannounced. | Critical (silent full-tree death enabler) |

### Which hops does each candidate address?

| Candidate | Hops it covers | Sub-defects it addresses | Sub-defects it leaves open |
|-----------|----------------|--------------------------|----------------------------|
| **(a) Premature-first-turn guard** (auto-continue "execute now" or flag `[interim — no work performed]`) | 1 (intercept), 6 (flag in envelope) | D1, partial D2 | D3, D4 |
| **(b) Terminal-child-aware waiting** (parent ends turn while declared-waiting child is terminal → inject notice forcing adjudication turn instead of silent COMPLETED) | 9, 10, 11 | D3, D4 | D1, D2 (doesn't prevent the junk; prevents the silent-death propagation) |
| **(c) Report envelope sanity flag** (terminal reports from ≤2-msg/no-tool histories carry warning marker) | 6, 7, 9 (parent sees flag) | D2, partial D3 | D1, D4 |
| **(d) Parent-scrutiny prompt hint** (RECOVERY_GUIDANCE_HINT-style — "if mid-report shows no work, scrutinize and call `send_message` to verify") | 9, 10 (adjudication) | D3 | D1, D2, D4 (advisory, not blocking) |
| **(e) Child work-discipline cardinal** (worker cardinal "deliver in same turn" exists; complement "do not end turn without doing work" — no preamble-only endings) | 1 (LLM-side pressure), 2 (turn boundary) | D1 (via prompt) | D2, D3, D4 (advisory, not enforced) |

**Key finding for the architect:** candidates (a) and (c) cover hops 1–7 (prevent/flag the junk). Candidates (b) and (d) cover hops 9–11 (catch/adjudicate the silent-death propagation). **No single candidate covers the full chain.** The architect's fix-family decision is partly a sequencing-and-coverage decision: which hops do you accept as uncovered?

---

## 11-Hop Premature-Completion Chain (Evidence Backbone)

Each hop cites the file:line that participates. Architect uses this to verify each candidate's coverage claim.

```
1.  Child's first LLM call produces text-only no-tool-call AIMessage
    → should_continue returns END                                       daemon/graph.py:2441-2512
2.  Turn ends, node-boundary checkpoint persists                        daemon/graph.py:3471
3.  manager dispatches child completion                                  daemon/manager.py:6549
    → _process_child_completion_and_notify_parent                        daemon/services/child_reports.py:1598
    → _process_child_completion_db_sync                                 daemon/services/child_reports.py:1808
4.  Gates zero: pending_tasks=0 (:2008, :2663) and bus_pending=0 (:2117-2127)
    → fail-CLOSED same-tx; COMPLETED eligible
5.  COMPLETED stamps propagate                                           child_reports.py:2361/2366/2546/2551/2706/2711
6.  Junk content read via _get_last_assistant_message_raw               child_reports.py:1479-1559 (returns :1528-1536)
    Truncation guard short-circuits on short histories                   child_reports.py:1312-1313
7.  Envelope+persist: _get_instance_report_prefix + content concat      child_reports.py:672-704 / :1264-1271
    Task row + ReportInjection row persisted                              child_reports.py:2844-2852 / :2976
8.  Watchers fired; last fire re-triggers _finalize_job                  job_feedback_observer.py:1208
    (DependencyBus.emit_terminal :551 → emit_terminal_for_child_instance :721 → fire_for_terminated_target :1100)
9.  Parent consumes ReportInjectionSlot.drain OR PROCESS_REPORT lane     graph.py:414-461 / task_processor.py:1085-1091
    Framing: _frame_injected_report `[SYSTEM NOTE: … DATA … NOT instruction …]`  graph.py:194-224
10. Parent completes: _update_parent_on_child_complete                   child_reports.py:931
    bus-active branch :1045-1101; inline fallback :1132-1137
    _finalize_job_db_sync → COMPLETED                                     job_feedback_observer.py:2822
11. Grandparent repeats hop-10 → no wake signal for
    "declared-waiting-parent-with-terminal-children"
    (hang pass silent — instance_messaging/repository exclusions ;
     wedge pass fires only under 3-part predicate —
     waiting_children_watchdog.py:861-893/:896-903/:911-927)
```

**Hot spots** (architect's mental anchors):
- The junk originates at hop-1 with **zero system-side signal** that the child's turn did no work.
- The junk survives hop-6 because `_get_last_assistant_message_raw` is content-only.
- The junk survives hop-7 because the envelope is "prefix + concat" with no content-integrity check.
- The junk reaches the parent at hop-9 framed as `[SYSTEM NOTE: … DATA …]` — the framing explicitly says "NOT an instruction" so a scrutiny hint injected at hop-9 has to be carefully worded.
- The silent-death enabler is hop-11: the parent+grandparent chain has no wake signal.

---

## Per-Candidate Analysis

Five candidates. Each gets equal rigor. Format: **mechanism / seam / does-NOT-catch / false-positive risk / testability / blast radius / reversibility / evidence-implicates-hop**.

---

### Candidate (a) — Premature-First-Turn Guard

> **Mechanism.** When a child instance is fresh (no prior turns) and its first LLM response is content-bearing AND `tool_calls == []`, intercept at `should_continue` (or inside `create_agent_node :2695`) and either: (a1) auto-continue with a directive "execute now — begin the actual work" appended to the conversation, or (a2) auto-flag the eventual report as `[interim — no work performed]`. Variant (a1) is forceful; (a2) is permissive.
>
> **Seam.** `daemon/graph.py:2441-2512` (`should_continue` returning END) OR `daemon/graph.py:2695` (`create_agent_node` after the first LLM call, before END decision). The LLM-side signal needed: count of `HumanMessage` (= turn number) and presence of `AIMessage(tool_calls)` (= work done). `LoopDetector` already scans tool-call signatures at `daemon/graph.py:939` (scan :982-1122) — same data is available here.
>
> **Does NOT catch.**
> - D3 (parent-side adjudication) — the parent's first-turn junk completion is uncovered unless symmetrically guarded.
> - D4 (terminal-children-aware waiting) — guard fires on the child's first turn, not on the parent's "waiting on terminal children" condition.
> - Hop-9 hop-10 hop-11 propagation — silent full-tree death still possible if the parent also opens with zero-tool text or if a different junk path appears.
> - Subsequent-turn junk (the second turn also zero-tool) — needs a "first turn" predicate. Architect must decide: do we generalize to "any turn with zero tool calls after LLM-side future-intent opener"? That's a wider predicate and a wider blast radius.
>
> **False-positive risk.** Legitimate single-turn agents whose real work IS one message: (i) `wanderer`/`explorer` already excluded from `_repair_report_with_llm` (literal `config.py:1427`; consumer `child_reports.py:1561`) — would need parallel exclusion in any premature-first-turn logic. (ii) Some agents may legitimately emit a "task understood, here is my plan" text opener then END on a structured ask where the parent expects "plan acknowledged" as the report. (iii) Directive tools (T1, T7 enums) that classify the task without invoking tools. (iv) Sleep/acknowledge agents that wait for user input after a one-message acknowledgement. Each of these must be enumerated before (a) ships — see §Testability.
>
> **Testability.**
> - Test shape: spin fresh instance, mock LLM to return text-only no-tool-call AIMessage; assert (a1) auto-continue fires or (a2) flag appears in envelope.
> - Extend `tests/unit/services/test_child_reports.py` for (a2); new unit test for (a1) at `tests/unit/graph/test_should_continue.py` (or extension of existing graph tests).
> - Red-green feasible: yes, current behaviour allows junk through; new behaviour intercepts. Both directions observable in checkpoint dump.
> - Red-flag: exclusion list {"wanderer","explorer"} is hand-maintained — any new agent category needs an explicit exclude or it false-positives.
>
> **Blast radius.** Touches the graph path that EVERY turn traverses (entry+exit of `create_agent_node` / `should_continue`). Touches report framing at hop-7 only for variant (a2). Does NOT touch the completion gate blast radius (turn-reconciler, PROCESS_REPORT lanes, pause/resume, watchdog, question-pause, watchover). Variant (a1) auto-continue adds an extra LLM call per zero-tool first turn — latency + cost.
>
> **Reversibility.** High. Both variants are local to `should_continue`/`create_agent_node` and gated by a config flag. Rollback = env-disable.
>
> **Evidence implicates hop?** Yes, directly. Hop-1 is the origin. The 2-occurrence pattern ("ZERO-TOOL-CALL opening response") is exactly the signal (a) reads. But: (a) addresses only the child side; if the parent is the one completing silently (hop-10/11), (a) does nothing. Evidence is **partial** — it implies hop-1 but does NOT prove hop-10/11 don't need their own coverage.

---

### Candidate (b) — Terminal-Child-Aware Waiting

> **Mechanism.** At parent completion: before stamping COMPLETED, scan the parent's declared-waiting set (the dependency_bus watchers for `target_instance_id = parent_id`) and check whether any watched child is in a TERMINAL state (COMPLETED / FAILED / ERROR / TERMINATED). If yes: instead of stamping COMPLETED, inject a notice (similar to watchdog wedge-notice shape at `waiting_children_watchdog.py:263-295`) forcing an adjudication turn. The notice says: "you declared yourself waiting on child X; that child has terminated with status Y; here is its report; adjudicate or escalate."
>
> **Seam.** Add a new check inside `_process_child_completion_db_sync` (before the COMPLETED stamp at `child_reports.py:2361/2366/2546/2551/2706/2711`) AND inside `_finalize_job_db_sync` (`job_feedback_observer.py:2822`) AND inside `_update_parent_on_child_complete` (`child_reports.py:931/:1045-1101/:1132-1137`). All three touch the parent-completion decision. Also relevant: `ReportInjectionSlot.drain` (`graph.py:414-461`) — if the parent's turn has zero tool calls AND declared-waiting watchers with terminal children, the post-drain turn-end is a candidate for adjudication injection.
>
> **Does NOT catch.**
> - D1 (child-side premature END) — the junk still happens; (b) only catches the propagation.
> - D2 (report-content honesty) — the report is still junk; (b) forces the parent to look at it, not fix it.
> - Inter-report gap (hop-9→hop-11) — by the time (b) fires, the parent has already consumed A's report and is processing B's terminal event. The "declared-waiting child is terminal" predicate covers B, but A is gone. (b) might be exactly the right gate to break the "parent COMPLETED while children still terminal" cycle, but the architect should verify against the actual incident that both A and B reached terminal before the parent COMPLETED, OR that A's junk report was the trigger and B's terminal event came later but the parent already COMPLETED on A's report alone.
> - Hop-1 zero-tool origin — (b) is downstream; the LLM-side pressure to do real work is untouched.
>
> **False-positive risk.** Legitimate "parent completed and children also completed" — the gate must distinguish "parent truly done" from "parent done but children still terminal-and-unnoticed." The predicate needs to verify: parent declared itself waiting on the child AT SOME POINT (dependency_bus had a watcher). This is a transient-in-memory signal in some paths and a DB row in others (`DependencyWatcher` table). Need a robust predicate. Architect must decide: ephemeral signal from `bus.had_parent_error` / `bus.get_generation` is wrong (those track errors, not declarations); the right source is the bus's `pending_watchers` set at the time of the parent completing.
>
> **Testability.**
> - Test shape: spawn parent with declared-waiting child; child terminates; parent attempts to complete; assert notice-injected-OR-COMPLETED-blocked.
> - Extend `tests/unit/services/test_child_reports.py` (completion-time injection test) and `tests/test_dependency_bus.py` (watcher-pending-state-fixture test). Add a regression test mirroring the incident: parent declared-waiting, child completes with junk, parent attempts COMPLETED, assert adjudication-notice injected.
> - Red-green feasible: yes. Hard part is the "declared-waiting" predicate — need a test that demonstrates the predicate fires in the incident path and stays dormant in healthy paths.
>
> **Blast radius.** **HIGH.** This is the most-sensitive area of the system:
>   - `turn-reconciler.reconcile_turn_mirror` (authoritative)
>   - `PROCESS_REPORT` lane (`task_processor.py:1067-1098` + dedup `:255-284`)
>   - `ReportDeliveryRecoveryService` lanes 1-4 (`config.py:866-934`; deliver pass `instance_lifecycle.py:4356-4371`)
>   - pause/resume (resume calls completion at `manager.py:9329`; claim defers PAUSED-target at `instance_messaging.py:1494-1503`)
>   - watchdog (`api.py:534-603`; `waiting_children_watchdog.py:524/:831-954`)
>   - question-pause deferred marker (`graph.py:3887-3958`)
>   - watchover deferred drain (`instance_messaging.py:918-979`; `:1141-1148/:3794-3801`)
>   - All four fail-OPEN defense-in-depth gates (`job_feedback_observer.py:_bus_count_pending_for_target_sync :344-424`; `_count_pending_tasks_for_instance_sync :426+`)
>   Every one of these reads the parent's "can I complete?" decision. (b) adds a new predicate to that decision.
>
> **Reversibility.** Medium. The predicate is a new check before the COMPLETED stamp; gating the gate by a config flag keeps rollback viable. But the gate's fail-OPEN semantics mean: if (b) is broken in production, parents may NOT complete when they should — a stricter failure mode than today's silent completion. Architect must decide: fail-OPEN (don't block, just inject notice — parent gets the message, can still complete) vs fail-CLOSED (block until adjudicated). Default should be fail-OPEN — same semantics as the existing defense-in-depth gates — and the architect should explicitly choose otherwise.
>
> **Evidence implicates hop?** Yes — directly. Hop-10/11 are the silent-death enabler. The incident's defining feature is "parent+grandparent complete while waiting on terminal children" — (b)'s exact predicate. Evidence is **strong** for (b)'s necessity, but does NOT prove (a)/(c) aren't also necessary.

---

### Candidate (c) — Report Envelope Sanity Flag

> **Mechanism.** In `_get_last_assistant_message_raw` (`child_reports.py:1479-1559`), inspect the returned message's `tool_calls`. If the message is being used as a terminal report AND `tool_calls == []` AND the message history is short (≤2 non-synthetic messages per the truncation-guard short-circuit at `:1312-1313`), prepend a warning marker to the envelope. Marker rides through `_get_instance_report_prefix` (`:672-704`) + concat (`:1264-1271`) → into the framed report via `ReportInjectionSlot.drain` (`graph.py:414-461`) / `_frame_injected_report` (`graph.py:194-224`) → parent's view. Marker reads e.g. `[REPORT SANITY: zero tool-call evidence; treat as interim, not completion]` or a structured prefix the parent LLM can pattern-match.
>
> **Seam.** `daemon/services/child_reports.py:1528-1536` (where the last message is selected) OR `:1264-1271` (envelope concat) OR `:672-704` (prefix builder). Most natural is the envelope concat — the marker travels with the report naturally.
>
> **Does NOT catch.**
> - D1 — does not prevent the junk; flags it downstream.
> - D4 — terminal-child-aware waiting is independent; the marker is informational, not blocking.
> - The parent may ignore the marker — advisory only unless paired with (d) (parent-scrutiny prompt hint).
> - Repair-with-LLM path (`_repair_report_with_llm :1328+`) and excluded agents {"wanderer","explorer"} (consumer `child_reports.py:1561`) — the flag should NOT mark excluded-agent reports (they're already designed to be text-only).
>
> **False-positive risk.** Low. The flag is additive (adds a warning marker, does not change report content). Healthy reports (with tool calls) are unaffected. Excluded agents' reports are unaffected if excluded-list is consulted. Risk: a legitimate single-message tool-using agent whose tool was invoked inline (tool_calls already populated) is not flagged — good. Risk: a single-turn agent with one bash call that's in fact junk work — the flag won't fire because tool_calls is non-empty. That's a **coverage gap** (junk that has tool calls is not flagged), not a false-positive.
>
> **Testability.**
> - Test shape: produce terminal report from a zero-tool history; assert marker present in envelope. Produce from a 3+ message history with tool calls; assert marker absent.
> - Extend `tests/unit/services/test_child_reports.py`. Existing fix: any test that currently asserts on envelope content needs the marker-aware assertion.
> - Red-green feasible: yes. Cheap to test; cheap to ship behind a config flag.
>
> **Blast radius.** **LOW.** The marker is additive on the report envelope path only. Does NOT touch the completion gate, the turn-reconciler, the PROCESS_REPORT lane, or any of the other blast-radius consumers. The marker rides inside the framed `[SYSTEM NOTE: … DATA …]` message — the parent's LLM sees it but the parent's own framing code does not change.
>
> **Reversibility.** High. A config flag (default ON for new code; OFF if issues) and a versioned envelope format would make rollback trivial.
>
> **Evidence implicates hop?** Indirectly. Hop-7 is the envelope, but the flag is only useful if combined with (d) (parent-scrutiny hint) or another enforcement. As a standalone, (c) is **observability instrumentation** — it surfaces the junk but does not act on it.

---

### Candidate (d) — Parent-Scrutiny Prompt Hint (RECOVERY_GUIDANCE_HINT pattern)

> **Mechanism.** Add a new hint pattern (similar to `RECOVERY_GUIDANCE_HINT` at `error_reporting.py:41-48`, appended in `_send_error_report` at `:739`) for **mid-reports that look incomplete**. The hint tells the parent: "if a child's report shows zero tool-call evidence AND no concrete output artifact, treat it as interim; verify by calling `send_message` to the child OR escalating to the user." Inject at the parent's turn-start as a system-side instruction (NOT in the framed `[SYSTEM NOTE: … DATA …]` payload — that payload is explicitly observational) OR ride inside the parent-side workflow.md guidance (no system change at all).
>
> **Seam.** Two homes:
>   - **System-side**: a new constant `PARENT_SCRUTINY_HINT` in `daemon/services/error_reporting.py` (or new file `daemon/services/report_hints.py`), appended in the report framing path (`graph.py:_frame_injected_report :194-224`) when the incoming report carries the (c)-style marker. This makes (d) the consumer of (c).
>   - **Prompt-side**: edit `agents/leader/workflow.md:667-679` (waiting discipline section) and `agents/developer/rule.md:32-33/74/178` to add scrutiny guidance.
>
> **Does NOT catch.**
> - D1, D2 — the hint is downstream. The junk still happens; the hint only changes parent behaviour on junk.
> - D4 (terminal-child-aware waiting) — same as (c); advisory, not blocking.
> - LLM compliance — the hint relies on the parent LLM following the instruction. Empirically the dispatch-prompt delivery-discipline pattern was effective 2026-08-29 (agent-architecture blueprint) — precedent is favourable but not deterministic.
>
> **False-positive risk.** None at the system level — the hint is advisory. False-positive at the LLM level: parent may over-scrutinize legitimate reports. Mitigation: hint's text quality and the (c)-marker-conditioned trigger (only fires when marker present).
>
> **Testability.**
> - System-side hint: extend `tests/unit/test_error_report_recovery_hint.py` (existing suite at `:180-290`) with parallel test for `PARENT_SCRUTINY_HINT`. Prompt-side: hard to test in unit; relies on integration / regression in real sessions.
> - Red-green feasible for system-side; not directly testable for prompt-side.
>
> **Blast radius.** **LOW.** Prompt edits to two files; one new constant + one append site in error_reporting.py or a new hints module. No gate changes, no completion path changes.
>
> **Reversibility.** High. Both homes are additive.
>
> **Evidence implicates hop?** Indirectly. (d) is only useful when paired with (c) (the marker) or with a self-evident junk pattern. As standalone, (d) is generic guidance and won't catch this class reliably.

---

### Candidate (e) — Child Work-Discipline Cardinal

> **Mechanism.** The worker rule already has "🚨 CRITICAL: REPORT DELIVERY — DELIVER IN THE SAME TURN" at `agents/worker/rule.md:166-175` — this prohibits the closing variant (ending with "I have enough evidence…" without delivering). The complement "do not end turn without doing work" — no preamble-only endings, no future-intent openers followed by END — is the OPENING variant and is currently uncovered. Add a new cardinal: "do not end the turn with a future-intent opening text and zero tool calls; that pattern will be detected and flagged as junk."
>
> **Seam.** `agents/worker/rule.md:166-175` (extending the existing cardinal section). Optionally mirror in `agents/worker/soul.md:3/20/102/136` persona files. Optionally extend `agents/developer/rule.md:32-33/74/178` and other delivery-disciplined agents.
>
> **Does NOT catch.**
> - The model emitting the future-intent opener anyway. LLM-side pressure is advisory, not enforcement.
> - D3, D4 — prompt-only fix doesn't help the parent or the gate.
> - Wanderer/explorer exclusion list — they emit text-only by design; the cardinal must exclude them or be worded to allow one-message acknowledgements.
>
> **False-positive risk.** Low — cardinal is a "do not" rule; healthy flows (single-message ack, plan-acknowledgement) need to be explicitly allowed in the cardinal text. Architect must write the cardinal carefully.
>
> **Testability.**
> - Hard at the unit level (LLM behaviour). At integration level: spawn worker, prime with junk-producing prompt, observe if the cardinal reduced junk rate.
> - Empirical evaluation only — needs a measure of "junk opener rate" before/after.
>
> **Blast radius.** **MINIMAL.** Two-to-three .md files.
>
> **Reversibility.** Trivial — revert the .md files.
>
> **Evidence implicates hop?** Indirect. The cardinal is a probabilistic pressure on hop-1 behaviour. Without (a), (c), or (b) as a backstop, the prompt-only fix is unreliable.

---

## Interaction Matrix

How the candidates compose, conflict, or duplicate.

| Pair | Relationship | Notes |
|------|--------------|-------|
| (a) + (b) | **Complementary.** (a) intercepts hop-1; (b) catches hop-9/10/11. Different sub-defects; no overlap. | Strongest composition for full chain coverage. |
| (a) + (c) | **Conflict (partial).** Both touch the report envelope at hop-6/7. (a)'s (a2) variant auto-flags; (c) flags every ≤2-msg no-tool report. (a2) becomes redundant if (c) ships. | If (a) ships as (a1) (auto-continue), (c) is the residual-flag for reports that slip past (a1). |
| (b) + (d) | **Complementary.** (b) injects a notice at the gate; (d) tells the parent LLM how to interpret a marker. (b) is enforcement; (d) is comprehension. | Pair (b)+(d) without (c): notice uses watchdog-style directive text (already proven in `waiting_children_watchdog.py:263-295`). Pair (b)+(d)+(c): full chain — flag arrives, hint explains, gate blocks. |
| (c) + (d) | **Symbiotic.** (d) is useless without a marker to react to; (c) is silent without a consumer. (c) emits the marker; (d) reads it. | Almost always shipped together. |
| (a) + (e) | **Redundant.** Both target hop-1; (a) is enforcement, (e) is prompt-side pressure. (a) makes (e) unnecessary; (e) without (a) is unreliable. | Pick one. If (a) ships, (e) is optional. |
| (b) + (e) | **Independent.** (b) catches the gate; (e) reduces the upstream rate. Both could ship; neither requires the other. | Reduce-then-catch pattern. |
| (c) + (e) | **Weak.** (c) is observability; (e) is prevention. Together they reduce junk and surface residual junk. | Cheap combo for "ship fast, observe later." |
| (a1) + (a2) | **Mutually exclusive.** Pick one variant. (a1) is forceful; (a2) is permissive. | Architect decides based on false-positive tolerance. |

### Architectural harmonies and conflicts

- **(b) and the existing fail-OPEN defense-in-depth gates** (`job_feedback_observer.py:_bus_count_pending_for_target_sync :344-424`; `_count_pending_tasks_for_instance_sync :426+`) — both read the same "can parent complete?" question. Adding (b) means the question now has THREE gates (bus, tasks, declared-waiting). Architect must decide gate ordering and which is authoritative. Fail-OPEN defense-in-depth means (b) failure should NOT block completion — but the question is which gate gets the "last word." Recommendation (architect decides): bus (fail-CLOSED, same-tx) > tasks (fail-OPEN, defense-in-depth) > (b) (fail-OPEN, advisory, last gate).
- **(b) and the watchdog's wedge predicate** — both target the same silent-death class. The watchdog fires periodically (5-min cadence per `config.py:858-865` drift reconciler; report_delivery sweep `config.py:866-934`); (b) fires at completion time. They overlap; (b) reduces the watchdog's wedge-fire rate. Architect should verify the wedge predicate at `waiting_children_watchdog.py:861-893/:896-903/:911-927` is consistent with (b)'s declared-waiting predicate — if (b) blocks, the wedge never needs to fire for the same instance.
- **(a)/(c) and the `wanderer`/`explorer` repair exclusion** (literal `config.py:1427`; consumer `child_reports.py:1561`) — any new logic must consult the same exclusion list to avoid false-positives on agents designed to be text-only.
- **Prompt hints and any system guard** — does a prompt fix undermine the case for a guard? NO. (d) is comprehension; (b)/(a) are enforcement. They reinforce. A guard without a hint may misfire on the parent side; a hint without a guard may be ignored. The two are complementary, not substitutes.

---

## Decision Axes for the Architect

These are the dimensions the architect must weight. Each axis is structured to be evaluated; the weight is the architect's call.

### Axis 1: Latency-to-Land vs Depth-of-Fix

| Approach | Latency | Depth |
|----------|---------|-------|
| (c)+(d) alone (marker + scrutiny hint) | **Days.** Two small files; existing test patterns. | Shallow. Catches the surface; relies on (b)-less gate. |
| (a)+(c)+(d) (auto-continue/flag + marker + hint) | **1–2 weeks.** Touches graph.py, child_reports.py, error_reporting-style module, three test files. | Medium. Hop-1–7 covered; hop-10/11 untouched. |
| (a)+(b)+(c)+(d) (full chain — guard + gate + marker + hint) | **3–4 weeks.** Touches graph.py, child_reports.py, job_feedback_observer.py, error_reporting.py, new tests, integration suite. | Deep. Full 11-hop coverage. |
| (e) only (prompt cardinal) | **Days.** Three .md files. | Shallowest. Untestable in unit; relies on LLM compliance. |

### Axis 2: Blast-Radius Tolerance

| Candidate | Blast radius | Severity |
|-----------|--------------|----------|
| (a) | Graph path (every turn). Low — local to `should_continue`/`create_agent_node`; gated by config. | Medium — turns affected: all fresh-instance first turns. |
| (b) | **Completion gate blast radius — highest in system.** Turn-reconciler, PROCESS_REPORT, ReportDeliveryRecovery, pause/resume, watchdog, question-pause, watchover, defense-in-depth gates. | **High** — gate change with system-wide effect. |
| (c) | Report envelope path only. | Low — additive marker. |
| (d) | Two .md files + one constant + append site. | Low. |
| (e) | Two-to-three .md files. | Lowest. |

**Architect's call:** does the gate change in (b) earn the silent-death prevention, or is the blast radius too high for 2-occurrence evidence?

### Axis 3: False-Positive Cost

| Candidate | Worst false-positive | Mitigation |
|-----------|---------------------|------------|
| (a) | Auto-continue forces a second LLM call on a legitimate single-message ack. Cost: latency + token spend; no behavioural break if "execute now" directive is permissive. | Exclusion list parallel to wanderer/explorer; config-gated. |
| (a2) | Flag added to a legitimate zero-tool final message — parent reads warning, may over-scrutinize. | Same exclusion list; flag is advisory. |
| (b) | **Block parent completion when parent is actually done.** Worst case: parent stuck waiting for an adjudication turn that never comes. | Fail-OPEN by default; gate-by-config; inject-notice instead of block. |
| (c) | Flag a legitimate report as suspicious. Parent ignores marker. | None needed — marker is informational. |
| (d) | Parent over-scrutinizes legitimate reports. | Hint text quality. |
| (e) | Cardinal constrains legitimate "plan-acknowledged" patterns. | Cardinal text must allow explicit one-message ack. |

### Axis 4: Evidence Sufficiency — 2 Occurrences Enough for a Gate Change?

The 2-occurrence evidence (the 2026-08-30 premature-report incident, §1 above; pattern is generalisable to any child instance) is consistent with a real class. But:
- The two incidents may share a non-evident common cause (specific LLM model behaviour? specific agent persona? specific task shape?).
- The gate blast radius means even a "correct" guard can break unrelated flows.
- Reverse: 2 occurrences is 2 too many for silent full-tree death; the cost of NOT guarding may exceed the cost of guarding.

**Architect's call:** is 2 occurrences + generalisability = "enough," or do we wait for one more? Recommend (architect decides): if (b) ships behind a kill-switch and fail-OPEN, the cost of waiting for one more is silent death in the interim. If (b) ships fail-CLOSED, the cost of being wrong is workflow disruption.

### Axis 5: Test-Scaffold Cost

| Candidate | Test surface | Scaffold cost |
|-----------|--------------|---------------|
| (a) | `tests/unit/services/test_child_reports.py` + new graph test for `should_continue`/`create_agent_node`. | Medium — need fixture for fresh-instance zero-tool first turn. |
| (b) | `tests/unit/services/test_child_reports.py` + `tests/test_dependency_bus.py` + new `tests/integration/test_completion_gate_block.py` (incident repro). | **High** — need declared-waiting-state fixture, terminal-child-state fixture, gate-blocking assertion, fail-OPEN assertion, defense-in-depth interaction test. |
| (c) | `tests/unit/services/test_child_reports.py` envelope assertion. | Low — single test extension. |
| (d) | `tests/unit/test_error_report_recovery_hint.py` extension. | Low. |
| (e) | Empirical LLM behaviour test — no clean unit. | Highest uncertainty — can't unit-test prompt adherence. |

---

## Sequencing Options (framed, not chosen)

Three sequencing shapes; architect picks.

### Option Seq-A: **Prompt-side first, observe, then guard**
1. Land (e) — child work-discipline cardinal — at minimal risk.
2. Land (d) — parent-scrutiny hint — at minimal risk.
3. Land (c) — envelope flag — at low risk.
4. **Observe** junk rate in production over N days (architect chooses N; 7d reasonable).
5. If junk rate not reduced to acceptable threshold, escalate to (a) and (b).

**Strengths.** Lowest initial risk; data-driven escalation. Builds empirical evidence for the (b) gate change.
**Weaknesses.** Silent-death window remains open during observation. If junk rate is rare, observation window may not see enough events.

### Option Seq-B: **Guard-first**
1. Land (b) behind kill-switch (config: `WC_WAKE_B_GATE_ENABLED=0` by default → flip to 1 in next deploy).
2. Land (a) behind config flag.
3. Land (c) + (d) together (marker + scrutiny hint).
4. (e) is optional once (a) ships.

**Strengths.** Full chain coverage from day 1. Silent-death window closes immediately.
**Weaknesses.** Highest upfront risk; (b)'s blast radius means the kill-switch is doing real work. Need the kill-switch to be revert-fast.

### Option Seq-C: **Cheapest-instrument-first (envelope flag as canary)**
1. Land (c) — envelope flag — as pure instrumentation (no consumer yet).
2. **Observe** flag frequency in production.
3. If flag fires more than expected: ship (a) and/or (b). If rare: ship (d) for parent-side consumption of the flag.

**Strengths.** Lowest blast; fastest data. The flag itself is diagnostic.
**Weaknesses.** Silent-death window still open. (c) does not block or fix; only observes.

---

## Flag-Only — Defer-Admission Latency (out of scope for this component, scope candidate for the plan)

**One short paragraph — flag-only, NOT committed.**

The 94-min pickup-while-idle surface is a separate class from premature-completion. It composes of: drift reconciler (300s cadence per `config.py:858-865`), `ReportDeliveryRecoveryService` (300s + 10-min-age + 100-batch per `config.py:866-934`), observer requeue (900s ±90 jitter per `docs/retry-architecture.md:256`), PAUSED-target claim deferral (`instance_messaging.py:1494-1503`), `WorkerPool` claim loop (`job_feedback_observer.py:1742-1753`), and the by-design `wait_for_idle` deferral. This is a candidate for the larger `wc-wake-report-integrity` plan but is NOT a sub-defect of the premature-completion class. Architect decides whether to fold it in. Reference for context: `docs/retry-architecture.md`.

---

## OPEN Decisions Register Seed (lift-able into decisions.md)

> The architect's decisions.md seed. Each item is OPEN. The architect closes them.

```
DECISION-ID  | STATUS | CANDIDATE / AXIS             | QUESTION
-------------+--------+------------------------------+----------------------------------------
D2.1         | OPEN   | Fix family                   | Which fix family lands?
             |        |                              | (system-side only / prompt-side only /
             |        |                              |  hybrid / none — accept residual)
D2.2         | OPEN   | (a) variant                  | Auto-continue (a1) or auto-flag (a2)?
D2.3         | OPEN   | (a) gate flag                | Default ON or OFF behind config?
D2.4         | OPEN   | (a) scope                    | First-turn only, or generalize to
             |        |                              |  "any zero-tool turn"?
D2.5         | OPEN   | (b) ship or defer            | (b) lands in this component, or
             |        |                              |  deferred behind (c)/(d) observability?
D2.6         | OPEN   | (b) default fail-mode        | Fail-OPEN (inject notice, allow complete)
             |        |                              |  or fail-CLOSED (block until adjudicated)?
D2.7         | OPEN   | (b) declared-waiting source  | Bus pending_watchers set at parent
             |        |                              |  completion, or persistent marker?
D2.8         | OPEN   | (b) interaction with         | Last gate, or merged with bus/tasks
             |        |  defense-in-depth            |  defense-in-depth? Ordering?
D2.9         | OPEN   | (c) ride-along with (b)      | (c) ships with (b) for marker+block,
             |        |                              |  or (c) ships standalone as instrument?
D2.10        | OPEN   | (d) home                     | System-side hint constant
             |        |                              |  (error_reporting.py-style) or
             |        |                              |  prompt-side workflow.md edit,
             |        |                              |  or both?
D2.11        | OPEN   | (e) include                  | (e) ships at all, given (a) covers
             |        |                              |  hop-1? Or as zero-cost belt-and-
             |        |                              |  braces?
D2.12        | OPEN   | Sequencing                   | Seq-A (prompt-observe-guard) /
             |        |                              |  Seq-B (guard-first) /
             |        |                              |  Seq-C (instrument-first)?
D2.13        | OPEN   | Evidence bar                 | Is 2 occurrences + generalisability
             |        |                              |  enough for (b)'s gate change, or
             |        |                              |  require one more incident first?
D2.14        | OPEN   | Test depth                   | Per-candidate test depth at ship
             |        |                              |  time: unit only / +integration /
             |        |                              |  +canary-flag observability?
D2.15        | OPEN   | Exclusion list               | Extend {"wanderer","explorer"} for
             |        |                              |  any new logic, or build a generic
             |        |                              |  opt-out mechanism?
D2.16        | OPEN   | Defer-admission latency      | In-scope for this component, or
             |        |                              |  scope-candidate for the plan but
             |        |                              |  NOT committed here?
D2.17        | OPEN   | Repair-with-LLM backstop     | Should the existing
             |        |                              |  _repair_report_with_llm (:1328+)
             |        |                              |  be invoked as a backstop in
             |        |                              |  addition to (a)/(c), or is
             |        |                              |  repaired junk still junk?
D2.18        | OPEN   | Carrier-vs-completion        | Distinguish "child's carrier Task
             |        |                              |  completed but no work done"
             |        |                              |  (signal: zero tool calls on
             |        |                              |  completing Task) from "child
             |        |                              |  said something junk" — both are
             |        |                              |  possible, and the guard predicate
             |        |                              |  may need to differ.
```

---

## Open Questions

For the architect (or the next planner pass):

1. **Carrier-vs-completion.** The completion gate fires when the carrier Task completes, regardless of whether the child did real work. Is the right guard predicate "carrier completed AND zero tool calls in the carrier's window" or "any tool_calls on the last AIMessage"? These are different signals — (a) uses AIMessage tool_calls, (b) uses carrier completion, (c) uses content-only. Architect must pick a canonical signal.

2. **Excluded-agent contract.** Today `{"wanderer","explorer"}` is a hand-maintained exclusion list for `_repair_report_with_llm`. As more agents become text-only by design, this list grows. Should the architect propose a generic opt-out mechanism (per-agent config flag)?

3. **The inter-report gap (hop-9 → hop-11).** After A's report consumed and before B's terminal event, both pending counts legitimately 0. The architect's gate predicate for (b) must include the case "parent already consumed A's report but A's junk is the only evidence." Does (b) re-check A's report content for junk, or does it only fire on B's terminal event? This is the subtle core of the gate.

4. **Repair-with-LLM (`:1328+`).** The system already has an LLM-driven report-repair path. Should the repair fire before the report reaches the parent (cost: latency) or only when (c)-marker is present (cost: junk still arrives)? The architect should decide if repair is a backstop or a coequal path.

5. **Watchdog's wedge predicate vs (b).** The wedge predicate at `waiting_children_watchdog.py:861-893/:896-903/:911-927` already covers "parent WAITING_CHILDREN with no non-terminal children AND no live carrier." Does (b) duplicate this? If (b) ships, the wedge may be unreachable for the same class. Architect should align the two predicates.

6. **Turn-reconciler impact.** `reconcile_turn_mirror` is the authoritative mirror writer. If (b) injects a notice that re-opens the parent's turn, the mirror must reflect the new turn. Architect must verify turn-reconciler compatibility — the inline check at `child_reports.py:2396` is the seam where any change must thread through.

7. **What does "terminal" mean for the declared-waiting predicate?** COMPLETED/FAILED/ERROR/TERMINATED — all terminal-but-different. The gate should distinguish: child TERMINATED (operator-killed, possibly parent-fault) vs child ERROR (child-fault, report may be error-context not junk) vs child COMPLETED (the most common path). Each has a different adjudication playbook.

---

## Technical Debt — Items Affecting This Analysis

| # | Debt Item | Impact on Recommendation | Severity | File:Line |
|---|-----------|--------------------------|----------|-----------|
| 1 | **Phase 4 deprecation** — `WAITING_CHILDREN` is deprecated as a control-flow status; display/log only. The bus is authoritative. The watchdog's wedge predicate still reads `WAITING_CHILDREN` (`waiting_children_watchdog.py:861-893`). | (b)'s declared-waiting predicate must consult the bus, not `WAITING_CHILDREN` status. Architect must wire the right source. | Medium | `child_reports.py:1151`; `:2228`; `:2272`; `:2297` |
| 2 | **Inter-report gap** — after A's report consumed (FIRED-but-unenqueued) and before B's terminal event, counts are legitimately 0. The completion gate is silent during this window. | (b)'s predicate must include "A's report was junk" OR "B's terminal event arrived after A's report was consumed"; else the gap stays. | High | `child_reports.py:2008`; `:2663` |
| 3 | **Repair-with-LLM is repair, not prevention** — `_repair_report_with_llm` is downstream of report emission. Junk still reaches the parent before repair. | (c) is a cheaper alternative to repair; (c) does not block. | Low | `child_reports.py:1328+` |
| 4 | **Hard-coded exclusion list** — `{"wanderer","explorer"}` at `config.py:1427` (consumer `child_reports.py:1561`) is hand-maintained. | New guards must consult this list; future growth needs a generic mechanism. | Low | `config.py:1427`; `child_reports.py:1561` |
| 5 | **Truncation guard short-circuits on short histories** — `_is_likely_truncated_report` returns False when `len(messages) < 2` (`:1312-1313`). | This is exactly the junk-detection gap. (c) must NOT replicate this guard's short-circuit. | High | `child_reports.py:1312-1313` |
| 6 | **Dead code: `_should_send_completion_report`** at `:825-929` (superseded by inline check at `:2396`). | No impact on (a)-(e); just clutter. | Low | `child_reports.py:825-929`; inline at `:2396` |
| 7 | **Carrier Task race** — TaskType.PROCESS_REPORT is INJECTED and skipped in `task_processor.py:284`; carrier dedup `:255-284`. | If (b) re-opens the parent's turn, the carrier-Task race window needs verification. | Medium | `task_processor.py:255-284`; `:1067-1098` |
| 8 | **Watchdog wedge predicate overlaps with (b)** — `waiting_children_watchdog.py:861-893` + `_has_live_carrier_task :455-520`. | If (b) ships, the wedge may be unreachable for the same class. Architect must align. | Medium | `waiting_children_watchdog.py:861-893`; `:455-520` |

## Items NOT Affecting This Analysis

- The drift-reconciler (300s cadence) — affects only the defer-admission-latency paragraph, out of scope for this component.
- The `ReportDeliveryRecoveryService` lanes 1-4 — same as above.
- The observer requeue timing — same as above.
- OpenCode abort/reset vs destroy semantics — orthogonal.

---

## References

- `daemon/services/child_reports.py:1479-1559` — `_get_last_assistant_message_raw` (the junk seam)
- `daemon/services/child_reports.py:1275-1326` — `_is_likely_truncated_report` (short-circuit on short histories)
- `daemon/services/child_reports.py:672-704` — `_get_instance_report_prefix`
- `daemon/services/child_reports.py:1264-1271` — envelope concat
- `daemon/config.py:1427` — excluded-agents default literal; `daemon/services/child_reports.py:1561` — consumer read
- `daemon/services/child_reports.py:1328+` — `_repair_report_with_llm`
- `daemon/services/child_reports.py:825-929` — `_should_send_completion_report` (DEAD CODE, superseded)
- `daemon/services/child_reports.py:1598` — `_process_child_completion_and_notify_parent`
- `daemon/services/child_reports.py:1808` — `_process_child_completion_db_sync`
- `daemon/services/child_reports.py:2008`; `:2663` — `_count_actionable_pending_tasks` (consumed)
- `daemon/services/child_reports.py:2117-2127` — inline bus-pending COUNT (fail-CLOSED, same-tx)
- `daemon/services/child_reports.py:2141` — WAITING_CHILDREN stamp (deprecated as control-flow; display/log only)
- `daemon/services/child_reports.py:2361/2366/2546/2551/2706/2711` — COMPLETED stamps
- `daemon/services/child_reports.py:2396` — inline check (C2 refactor of `_should_send_completion_report`)
- `daemon/services/child_reports.py:2844-2852`; `:2976` — Task+ReportInjection row persistence
- `daemon/services/child_reports.py:931`; `:1045-1101`; `:1132-1137` — `_update_parent_on_child_complete`
- `daemon/graph.py:148` — `InjectionSlot`
- `daemon/graph.py:194-224` — `_frame_injected_report`
- `daemon/graph.py:387` — `ReportInjectionSlot`
- `daemon/graph.py:413-461` — `ReportInjectionSlot.drain`
- `daemon/graph.py:939` — `LoopDetector` (tool-call signature scan :982-1122)
- `daemon/graph.py:2441-2512` — `should_continue` (END decision)
- `daemon/graph.py:2672` — `create_should_continue`
- `daemon/graph.py:2695` — `create_agent_node` (full_messages in hand)
- `daemon/graph.py:3471` — node-boundary checkpoint
- `daemon/graph.py:3887-3958` — question-pause deferred marker
- `daemon/graph.py:4918`; `:5437`; `:5523` — watchover slots
- `daemon/graph.py:5545-5624` — `build_instance_graph` wiring order
- `daemon/services/job_feedback_observer.py:1208` — `_finalize_job`
- `daemon/services/job_feedback_observer.py:2377` — `_finalize_instance`
- `daemon/services/job_feedback_observer.py:2749` — `_finalize_instance_db_sync`
- `daemon/services/job_feedback_observer.py:2822` — `_finalize_job_db_sync`
- `daemon/services/job_feedback_observer.py:344-424` — `_bus_count_pending_for_target_sync` (fail-OPEN)
- `daemon/services/job_feedback_observer.py:426+` — `_count_pending_tasks_for_instance_sync` (fail-OPEN)
- `daemon/services/job_feedback_observer.py:1742-1753` — `WorkerPool` claim loop
- `daemon/services/dependency_bus.py:551-720` — `emit_terminal`
- `daemon/services/dependency_bus.py:721` — `emit_terminal_for_child_instance`
- `daemon/services/dependency_bus.py:935` — `pending_watchers`
- `daemon/services/dependency_bus.py:973` — `count_pending_for_target`
- `daemon/services/dependency_bus.py:1025` — `cancel_for_target`
- `daemon/services/dependency_bus.py:1100` — `fire_for_terminated_target`
- `daemon/services/waiting_children_watchdog.py:524`; `:831-954` — main sweep
- `daemon/services/waiting_children_watchdog.py:861-893` — wedge predicate (zero-non-terminal-children)
- `daemon/services/waiting_children_watchdog.py:896-903` — wedge predicate (paused-skip)
- `daemon/services/waiting_children_watchdog.py:911-927` — wedge predicate (zero-live-carrier, consumed)
- `daemon/services/waiting_children_watchdog.py:455-520` — `_has_live_carrier_task`
- `daemon/services/waiting_children_watchdog.py:263-295` — `_build_wedge_notice` (text shape to mirror)
- `daemon/repositories/instance/repository.py:2196` — `parents_with_non_terminal_children`
- `daemon/repositories/instance/repository.py:2319-2417`; `:2333-2341`; `:2388-2407` — hang predicate exclusions
- `daemon/services/error_reporting.py:41-48` — `RECOVERY_GUIDANCE_HINT` definition
- `daemon/services/error_reporting.py:32-40` — rationale
- `daemon/services/error_reporting.py:84` — `class ErrorReportingService`
- `daemon/services/error_reporting.py:399` — `_send_error_report`
- `daemon/services/error_reporting.py:739` — `RECOVERY_GUIDANCE_HINT` append site
- `daemon/services/task_processor.py:255-284` — PROCESS_REPORT dedup
- `daemon/services/task_processor.py:1067-1098` — PROCESS_REPORT lane
- `daemon/services/task_processor.py:1085-1091` — fallback inject
- `daemon/services/instance_messaging.py:918-979`; `:1141-1148`; `:3794-3801` — watchover deferred drain
- `daemon/services/instance_messaging.py:1486-1510` — `send_message` revive semantics
- `daemon/services/instance_messaging.py:1494-1503` — PAUSED-target claim deferral
- `daemon/services/instance_lifecycle.py:4356-4371` — deliver pass
- `daemon/services/job_recovery_service.py:1297-1468` — Pattern-e dead-letter
- `daemon/manager.py:6549`; `:6707`; `:9329` — child-completion dispatch sites
- `daemon/manager.py:2878` — `spawn_executor` (referenced for skill anchor, not for this analysis)
- `daemon/config.py:858-865` — drift_reconcile_interval_seconds (default 300s)
- `daemon/config.py:866-934` — `ReportDeliveryRecoveryService` settings
- `daemon/config.py:870` — `report_delivery_recovery_enabled`
- `daemon/config.py:973` — `drift_reconcile_min_orphan_age_seconds` (default 900s)
- `daemon/config.py:1023` — `waiting_children_watchdog_hang_threshold_seconds`
- `agents/worker/rule.md:166-175` — "🚨 CRITICAL: REPORT DELIVERY — DELIVER IN THE SAME TURN" (closing variant)
- `agents/worker/soul.md:3`; `:20`; `:102`; `:136` — worker persona
- `agents/leader/workflow.md:667-679` — waiting discipline
- `agents/leader/rule.md:59`; `:151` — leader cardinals
- `agents/developer/rule.md:32-33`; `:74`; `:178` — developer guidance
- `docs/retry-architecture.md:256` — observer requeue timing (900s ±90 jitter)
- 2026-08-30 premature-report incident — incident pattern evidence (§1; original commit ref `43070f6f` unreachable from this checkout)
- Commit `2026-08-29` (agent-architecture blueprint) — dispatch-prompt delivery-discipline pattern (precedent for prompt-side efficacy)

---

## Architecture Diagram (decision-flow)

```mermaid
flowchart TB
    subgraph Hop1-7 ["Hop 1-7: Child emits junk; gate sees no tool calls"]
        C1[Child first LLM call: text-only no tool_calls] --> SC[should_continue graph.py:2441]
        SC -->|END| End1[Turn ends, checkpoint:3471]
        End1 --> Comp[_process_child_completion_db_sync:1808]
        Comp --> GateBus[Bus-pending COUNT fail-CLOSED same-tx:2117-2127]
        Comp --> GateTasks[Task-pending COUNT:2008/:2663]
        GateBus -->|both 0| Stamp[COMPLETED stamps:2361+:2706]
        GateTasks --> Stamp
        Stamp --> Junk[Junk read via _get_last_assistant_message_raw:1479-1559]
        Junk --> Guard[Truncation guard short-circuits:1312-1313]
        Guard --> Env[Envelope+persist:672-704/:1264-1271]
        Env --> Task[Task+ReportInjection rows:2844-2852]
    end

    subgraph Hop8-11 ["Hop 8-11: Parent consumes; silent full-tree death"]
        Task --> Fire[DependencyBus fire:551-720/:721/:1100]
        Fire --> Finalize[_finalize_job:1208]
        Finalize --> Drain[Parent ReportInjectionSlot.drain:413-461]
        Drain --> Frame[_frame_injected_report:194-224]
        Frame --> ParComplete[_update_parent_on_child_complete:931/:1045-1101]
        ParComplete --> ParFinalize[_finalize_job_db_sync:2822]
        ParFinalize -->|silent| Dead[Grandparent also COMPLETED: silent full-tree death]
    end

    Candidate_a[(a) Premature-first-turn guard] -.->|intercepts| SC
    Candidate_a -.->|flags| Junk
    Candidate_b[(b) Terminal-child-aware waiting] -.->|gates| Stamp
    Candidate_b -.->|gates| ParComplete
    Candidate_c[(c) Report envelope sanity flag] -.->|rides| Env
    Candidate_d[(d) Parent-scrutiny hint] -.->|reads marker| Frame
    Candidate_e[(e) Child work-discipline cardinal] -.->|prompt pressure| C1

    style Candidate_a fill:#fdd
    style Candidate_b fill:#fdd
    style Candidate_c fill:#fdd
    style Candidate_d fill:#fdd
    style Candidate_e fill:#fdd
    style Dead fill:#900,color:#fff
```

The diagram shows candidate placement, not candidate choice. Dead is the terminal failure mode all candidates attempt to prevent.

---

**End of analysis.** Architect's decisions register seed is in §OPEN Decisions Register Seed. Every item is OPEN. No recommendation is made here — the analyst's job was to structure the decision space; the architect's job is to decide.