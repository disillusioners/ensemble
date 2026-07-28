# Plan Improvement Tracking: Context Injection Restructure

## Iteration 001 — REJECTED
**Date**: 2026-07-28 02:45
**Verdict**: REJECTED

### Blocking Issues

#### 1. Phase 4 Ephemerality Mechanism is Fundamentally Flawed (add_messages is APPEND-only)

**Description**: Phase 4 (ADR-6) proposes filtering context messages from checkpoint by returning a filtered message list at `agent_node` return. The plan states: "Filter context messages at agent_node return — injected messages are stripped before state is written to checkpoint."

**Root Cause**: LangGraph's `add_messages` reducer (used by `SessionState(MessagesState)`) is an **APPEND-only reducer**. Returning `{'messages': filtered_list}` from `agent_node` does NOT replace the state — it APPENDS the returned messages to existing state. Context messages that entered via graph input (`_build_graph_input()`) are ALREADY in checkpoint state by the time `agent_node` returns. Filtering the return value only controls what's ADDED, not what's REMOVED.

**Proof from codebase**:
- `daemon/graph.py:764-767`: "RemoveMessage sentinels must come BEFORE the repair message so the `add_messages` reducer processes removals before appending the summary (LangGraph processes the list left-to-right)."
- `daemon/graph.py:850-873`: `_build_removal_list()` exists specifically to build `RemoveMessage` sentinels because the reducer cannot delete by filtering — it needs explicit removal markers.
- `daemon/compaction.py:696-712`: Compaction uses `RemoveMessage(id=msg.id)` to delete messages — confirming the append-only model.
- `daemon/graph.py:2390-2410`: `agent_node` return already shows `add_messages` append semantics — it returns `{'messages': [response]}` and the response is APPENDED, not replacing the list.

**Impact**: The entire Phase 4 design is built on a false assumption. If implemented as designed, context messages WOULD be checkpointed (the opposite of the goal). This invalidates ADR-1, ADR-6, and the core value proposition ("Checkpoint DB stays lean").

**Expected**: A mechanism that actually prevents checkpointing — e.g., `RemoveMessage` sentinels at `agent_node` return, OR NOT injecting context messages into the graph input at all (inject them in `full_messages` local variable only, like the existing RAM-queue injection pattern at graph.py:2009-2023).

**Found**: A filter-at-return approach that cannot work with the `add_messages` reducer.

#### 2. Contradiction Between Phase 4 (Ephemerality) and Phase 6 (Compaction Survival)

**Description**: Phase 4 makes context messages ephemeral (NOT in checkpoint). Phase 6 ensures context messages survive compaction. These are contradictory.

- Phase 4 Deliverable: "Context messages NOT in checkpoint DB"
- Phase 6 Objective: "Ensure context messages survive LangGraph compaction correctly"
- Phase 6 Task 4: "Verify: context messages preserved verbatim"

**Root Cause**: If context messages are filtered from checkpoint (Phase 4 goal — even if the mechanism worked), compaction operates on CHECKPOINT state via `graph.aget_state()` (graph.py:2332-2333). Compaction never sees context messages if they're not checkpointed. Therefore "ensuring they survive compaction" is either meaningless (they're not there) or contradictory (they must be in checkpoint for compaction to see them).

**Impact**: Phase 6 is logically incoherent given Phase 4. At minimum, the relationship needs to be resolved.

**Expected**: Clear statement of whether context messages are in checkpoint or not, and how compaction interacts with them.
**Found**: Contradictory requirements across phases.

#### 3. Phase 7 "Per-Turn Freshness" Mischaracterizes Current Mechanism

**Description**: Phase 7 Objective states: "Current problem: System prompt is FROZEN at graph-compile time (closure capture at graph.py:1983)." And the plan-overview.md says context is "frozen at spawn."

**Found in code**: The project context and shared context metadata injection (mechanism #3, instance_messaging.py:1806-2002) uses **once-per-instance flags** (`project_injected`, `shared_context_injected`) that inject on the FIRST message only. This is NOT "frozen at spawn" — it's "injected once on first message." The distinction matters because:
- The current mechanism injects fresh-at-first-message data (not spawn-time data)
- The proposed change to per-turn is a behavioral change from "once" to "every turn"
- The plan's framing ("stale until instance respawn") is inaccurate — it's "stale after first message"

**Impact**: Minor — doesn't block, but mischaracterizes the improvement and could lead to underestimating the test changes needed (tests that assume once-per-instance injection semantics).

### Notes (Non-blocking)

- The `_build_graph_input()` function reference is ACCURATE — it exists at instance_messaging.py:83, returns `{"messages": [skill_msg?, user_msg]}`.
- The line references for string prepending (1856, 1888, 1909-2002) are ACCURATE.
- The appender functions and `_apply_post_cache_appends()` at the referenced line numbers are ACCURATE.
- The `additional_kwargs={"injected_message": True}` pattern at graph.py:2019, 2158 is ACCURATE.
- The compaction functions `_is_injected_message()` and `_partition_injected_messages()` at compaction.py:74-119 are ACCURATE.
- Escalation point #1 in decisions.md correctly identifies the risk, but it's not just an escalation point — it's a fundamental design flaw that should be resolved before implementation begins.

### Recommended Fix Direction

Instead of filtering at `agent_node` return (Phase 4), follow the EXISTING pattern used for RAM-queue injections (graph.py:2009-2023):
- Context messages should be injected into `full_messages` (the LOCAL variable passed to `llm.invoke()`), NOT into the graph input (`_build_graph_input()` / `state['messages']`).
- This means context messages never enter the `add_messages` reducer and are never checkpointed — true ephemerality without needing a filter.
- This makes Phase 6 (compaction survival) a non-issue — compaction doesn't see them, and they're rebuilt fresh each turn.
- This resolves the async/sync boundary concern (escalation point #4) — context assembly happens inside `agent_node` (already async), not in the sync `_build_graph_input()`.

---

## Iteration 002 — REJECTED
**Date**: 2026-07-28 03:24
**Verdict**: REJECTED

### Assessment of v1 → v2 Corrections

All three iteration-001 blockers were correctly resolved:
1. ✅ ADR-2 injects into LOCAL `full_messages` inside `agent_node` — verified against graph.py:1984, 2009-2023. The `add_messages` append-only problem is eliminated.
2. ✅ Old Phase 4 (filter) + Phase 6 (compaction survival) DELETED. C3 re-append consolidated in Phase 3 Task 5.
3. ✅ Non-blocking, remains minor.

The v2 architecture (ADR-2 local injection) is architecturally sound and correctly resolves the v1 flaw. The following 3 issues are all Phase 3 WIRING GAPS — not architectural problems. They are straightforward to fix without changing the architecture.

### Blocking Issues

#### 1. Loop-Breaker Repair Path Drops Context Messages (CONSENSUS — both councilors + independent verification)

**Description**: The plan's Phase 3 explicitly addresses compaction re-append (Task 5, C3 analog at graph.py:2344-2358) but completely OMITS the loop-breaker repair path. `_maybe_repair_loop` at graph.py:1344 rebuilds `full_messages` from scratch:
```python
full_messages = [SystemMessage(content=system_prompt)] + list(messages)
```
Only `injected_msgs` (RAM-queue) is re-appended inside `_maybe_repair_loop` (lines 1338-1343). Report msgs are re-appended in `agent_node` after the loop-breaker call (lines 2284-2287) via identity-based dedup. Context messages would be silently dropped on every loop-breaker repair.

The plan's Phase 3 code sketch (lines 86-156) OMITS the `_maybe_repair_loop` call entirely — it jumps from "Report injections" directly to the LLM call.

**Why blocking**: Same class of bug the C3 pattern was designed to prevent. An LLM stuck in a loop needs context most during repair — context would be silently lost exactly then.

- Expected: Context messages survive loop-breaker repair (re-appended via C3 pattern, parallel to lines 2284-2287 for report msgs)
- Found: Loop-breaker rebuild drops context messages; plan never mentions this path

**Fix**: Add C3-style re-append for `context_msgs` after the `_maybe_repair_loop` call returns (parallel to lines 2284-2287). Add a Phase 3 task citing graph.py:2253-2266 (loop-breaker call site) and graph.py:1344 (rebuild site). Update the Phase 3 code sketch to show `_maybe_repair_loop` in its actual position.

#### 2. ContextSlot Reachable from Messaging Path Is Unimplementable as Designed (independently verified)

**Description**: The plan's Phase 3 "Skill Injection Threading" section shows `context_slot.set_skill_injection_result(skill_result)` being called from `_process_message_with_tracking()` in instance_messaging.py. But the messaging path obtains only the compiled graph via `manager.get_instance()` (manager.py:5340) — it does NOT have a reference to the `ContextSlot` instance, which is captured inside the `agent_node` closure.

`InjectionSlot` works because it delegates to a MANAGER-LEVEL dict (`_pending_injections`, manager.py:747, 1909-1937): the messaging path calls `manager.set_injection()`, the slot reads via `manager.get_injection()`. The plan's `ContextSlot.set_skill_injection_result()` stores on `self._skill_injection_result` — unreachable from the messaging path.

- Expected: Messaging path can pre-set skill search result on ContextSlot before graph invocation
- Found: ContextSlot stores on self, messaging path cannot reach it — plan's stated approach is unimplementable

**Fix**: Mirror InjectionSlot's manager-level indirection. Add a manager-level store (e.g., `_pending_skill_results: dict[str, tuple]`) with `set`/`get`/`clear` methods. Have `ContextSlot.assemble()` read from the manager rather than `self._skill_injection_result`. Update Phase 3 ContextSlot design and escalation #4.

#### 3. Skill Injection Lost on Retry (is_retry=True) — Behavioral Regression (verified plausible)

**Description**: Skill search is gated behind `if not is_retry:` in the messaging path. Today, the first attempt's skill message enters the checkpoint via `_build_graph_input` → `add_messages`. On retry, the search is skipped, but the LLM still sees skill context in checkpoint history.

After this plan, skills become ephemeral (never checkpointed). On retry, search is skipped → `skill_injection_result is None` → `assemble_context_messages()` receives nothing. The checkpoint has no skill history to fall back on. The LLM loses all skill context on every retry.

Phase 1's signature hedges this ("If None, the builder may do the search itself or skip skills") but Phase 3 never picks a side.

- Expected: Success criteria states "Skill injection works via both auto-search and `<meta>` explicit tag" — must work on retries too
- Found: Retry path would silently lose all skill context

**Fix**: Specify and test one of:
1. `assemble_context_messages()` MUST re-run skill search when `skill_injection_result is None` and `skill_injection` is enabled, OR
2. The retry branch in instance_messaging.py re-runs `inject_skills()` (lifting the `if not is_retry:` gate for the search).
Add an explicit retry-path test to Phase 3/6 matrix.

### Notes (Non-blocking)

- **ADR-8 auto-migration**: Legacy `context_injection: true` → `human_messages` means opted-in agents flip behavior on deploy. Contradicts "zero-risk canary" framing. Document explicitly.
- **Risk register overstates append_auto_load_skills DB-write bug**: persistence.py:514 already passes `disable_auto_load_tracking=True`, so that write is already suppressed.
- **Diagram inaccuracy**: plan-overview "Desired Final Format" lists user request before history (reverse-chronological), but actual code ordering is oldest→newest. Phase 3 code sketch is correct.
- **Phase 4 GET /messages skill latency**: BM25/embedding search on every poll may exceed 50ms. Recommend caching last per-instance search result.
- **ContextSlot agent_meta staleness**: agent_meta captured at spawn; if meta.json changes mid-session, stale mode resolution. Acceptable (agents don't change mid-session), but document.

---

## Iteration 003 — APPROVED
**Date**: 2026-07-28 04:10
**Verdict**: APPROVED

### Assessment of v2 → v3 Corrections

All three iteration-002 wiring gaps (B1/B2/B3) were correctly resolved. Each fix was independently verified against actual source code:

#### B1: Loop-Breaker Repair Path Drops Context — ✅ FIXED
- **Verified**: `_maybe_repair_loop` exists at graph.py:1148 (function def), called at graph.py:2253.
- **Verified**: Report-msg re-append (the pattern to mirror) at graph.py:2284-2287 using object identity check.
- **Verified**: Repair rebuild at graph.py:1344: `full_messages = [SystemMessage(content=system_prompt)] + list(messages)` — drops all local injections.
- **Plan fix (Phase 3 Task 6)**: C3-style re-append for `context_msgs` after line 2287. Correct pattern, correct location.

#### B2: ContextSlot Cannot Reach Messaging Path Results — ✅ FIXED
- **Verified**: `manager._pending_injections` dict at manager.py:747 (InjectionSlot's indirection pattern).
- **Verified**: Messaging path holds compiled graph via `manager.get_instance()`, no ContextSlot reference — original design was unimplementable.
- **Plan fix (Phase 3 Task 2/11)**: `manager._context_skill_results` dict at manager.py:747, write via `set_context_skill_result()`, read via `get_context_skill_result()`. Correct mirror of existing pattern.

#### B3: Skill Injection Lost on Retry — ✅ FIXED
- **Verified**: `_skill_injection_msg` at instance_messaging.py:1789, initialized to `None`, set only inside `if not is_retry:` (line 1807).
- **Plan fix (Phase 3 Task 7)**: Messaging path pre-computes skill result → stores in manager → agent_node reads on retry; if missing, runs search itself. Deterministic.

### Council Findings (independently evaluated, not adopted)

Council returned 7 "blocking issues." Independent assessment:

| # | Council Claim | Verdict | Reason |
|---|---|---|---|
| 1 | Per-turn keying for `_context_skill_results` | Non-blocking | Over-engineering — messaging path writes fresh every non-retry turn |
| 2 | Snapshot skill results to DB | Rejected | Directly contradicts ADR-2 (ephemerality). Alternative design, not a defect |
| 3 | Mode-flip guard | Non-blocking | Already handled — mode resolved from `agent_meta` at spawn; can't flip mid-session |
| 4 | Compaction re-build site (NEW gap) | Rejected | Already in plan — Phase 3 Task 8 + pseudocode at phase3-plan.md:184-199. Council missed existing content |
| 5 | Cleanup parity for `_context_skill_results` | Non-blocking (valid note) | Needs 5-path cleanup mirroring `_pending_injections`. Mechanical, non-architectural |
| 6 | Defensive return-path assertion | Non-blocking | Over-engineering — ephemerality by construction |
| 7 | Serialization round-trip for `additional_kwargs` | Moot | Context never enters checkpoint (ADR-2) |

### Notes (Non-blocking)

- **Cleanup parity (council #5)**: Implementer should enumerate the 5+ termination paths for `_context_skill_results` cleanup, matching `_pending_injections` parity. See manager.py lines 2202, 2211, 2281, 2309, 2688, 4602 for reference. Mechanical work.
- **All previous non-blocking notes from iter-002 carry forward**: auto-migration documentation, append_auto_load_skills DB-write risk register accuracy, diagram ordering, GET /messages latency caching.
- **Line references verified accurate**: graph.py:1148 (function), 2253 (call), 2284-2287 (report re-append), 1344 (rebuild); manager.py:747 (_pending_injections); instance_messaging.py:1789 (_skill_injection_msg), 1807 (is_retry gate).

### Approval Rationale

The plan is internally consistent, complete, feasible, and safe:
- Architecture (local injection inside agent_node) is proven by existing RAM-queue and report injection patterns
- All three message-rebuild sites (loop-breaker, compaction, return) explicitly handled
- Default mode = legacy = byte-identical behavior (backward compatible)
- Per-agent feature flag with canary rollout
- Scope correctly bounded (opencode path excluded per ADR-13)
- All prior blockers resolved with code-verified fixes
