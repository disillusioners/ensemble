# Architecture Decisions: User Message Injection

This document records the 6 critical decisions (C1-C6) plus warnings addressed during plan revision. These decisions are binding for all phases.

---

## C1. Thread InstanceManager/LiveEventHub via Factory Closure

**Problem**: `create_agent_node()` and `build_instance_graph()` do NOT have access to InstanceManager or LiveEventHub. They run in isolated LangGraph execution context.

**Decision**: Thread an injection slot handle + live_hub reference through `build_instance_graph()` → `create_agent_node()`, following the existing `compactor`/`graph_ref` factory-closure pattern.

**Pattern**:
```python
def build_instance_graph(..., injection_slot=None, live_hub=None):
    # injection_slot is a callable: (instance_id) -> dict|None
    # OR a lightweight object wrapping InstanceManager methods
    
    def create_agent_node(...):
        # injection_slot and live_hub accessible via closure
        ...
```

**Rejected alternative**: Module-level singleton access — breaks test isolation, creates hidden coupling.

**Affected phases**: Phase 1 (wiring), Phase 2 (SSE emission uses live_hub from closure)

---

## C2. Persist Injected HumanMessage to Checkpoint

**Problem**: Agent node currently returns only `{'messages': [response]}`. The injected HumanMessage is used for the LLM call but never written back to LangGraph state. This causes:
- Crash recovery loses the message
- GET /messages history misses the user turn
- Conversation coherence breaks (AIMessage with no preceding HumanMessage)

**Decision**: Return BOTH messages so the `add_messages` reducer persists both:

```python
if injected_msg is not None:
    return {'messages': [injected_msg, response]}
return {'messages': [response]}
```

**Marker flag**: Use `additional_kwargs={"injected_message": True}` on the HumanMessage, following the same pattern as `language_check_reminder` at graph.py:520-530. This flag is used by:
- C3: Compaction paths to skip injected messages from summarization
- Future tooling to identify injected vs normal user messages

**Affected phases**: Phase 1 (agent node return value)

---

## C3. Fix Both Compaction Paths to Preserve Injected Messages

**Problem**: Two compaction paths lose the injection:

1. **Reactive** (graph.py:641-684) — ContextLengthExceededError handler reads from checkpoint (injection not persisted there yet if compaction triggers before return), compacts without it, re-invokes without it
2. **Proactive** (`_maybe_compact_context` in instance_messaging.py:523) — Compaction runs before agent node, may summarize away injected messages from prior turns

**Decision**:

1. **Reactive path fix**: In the ContextLengthExceededError handler, after building `compact_messages`, re-append the injected message before re-invoking the LLM. The injected message is available as a local variable in the agent_node closure.

2. **Proactive path fix**: In `compaction.py`, add a check for `additional_kwargs.get("injected_message")` to skip injected messages from summarization. This follows the exact same pattern as `language_check_reminder` — messages with this flag are preserved verbatim and not included in the compaction summary.

**Affected phases**: Phase 1 (reactive fix in graph.py + proactive fix in compaction.py)

---

## C4. Do NOT Change PAUSED Behavior of send_message

**Problem**: Original plan proposed returning 409 Conflict for PAUSED instances. This would break existing auto-resume behavior — a load-bearing code path that supports vision image propagation through PAUSED cascade resume.

**Decision**: Keep send_message API state-aware but do NOT change PAUSED behavior:

| Instance Status | Behavior | Return Code |
|----------------|----------|-------------|
| RUNNING | Set RAM injection slot | 202 |
| WAITING_CHILDREN | Set RAM injection slot | 202 |
| PAUSED | Existing auto-resume (resume_instance_cascade + resume_processing_job) | 200 |
| IDLE | Normal enqueue_message | 200 |
| Terminal | Normal enqueue_message | 200 |

**Clarification**: "If user press pause, clear this message" (constraint #3) means: when pause is triggered, clear the RAM injection slot if one exists. It does NOT mean rejecting send_message to PAUSED instances.

**Affected phases**: Phase 2 (API routing — PAUSED branch unchanged), Phase 4 (no PAUSED rejection test)

---

## C5. ensure.md Renamed to testing-guide.md

**Problem**: The file was renamed from `ensure.md` to `testing-guide.md`.

**Decision**: Update ALL Phase 4 references from `ensure.md` to `testing-guide.md`.

**Affected phases**: Phase 4

---

## C6. Add `canInject` Computed, Don't Modify `isInstanceRunning`

**Problem**: Original plan proposed modifying `isInstanceRunning()` to exclude QUEUED. This would break the Pause button for QUEUED instances.

**Decision**: Add a separate `canInject` computed signal:

```typescript
readonly canInject = computed(() => {
    const status = this.instanceStatus();
    return status === 'running' || status === 'waiting_children';
});
```

**Button visibility logic**:
- `isInstanceRunning()` (RUNNING + WAITING_CHILDREN + QUEUED) → controls Pause button visibility (UNCHANGED)
- `canInject()` (RUNNING + WAITING_CHILDREN only) → controls Send button + text input visibility (NEW)
- `isInstancePaused()` → controls Resume button (UNCHANGED)
- IDLE/other → controls Send button (UNCHANGED)

When RUNNING/WAITING_CHILDREN: BOTH Pause button AND Send+text input are visible simultaneously.

**Affected phases**: Phase 3 (message-input component)

---

## W1. Injection Cleanup in ALL 5 Paths

All instance lifecycle cleanup paths must clear `_pending_injections`:

| # | Path | Location | Status |
|---|------|----------|--------|
| 1 | `_release_cached_instance` | manager.py:1756 | ✅ Already in plan |
| 2 | `pause_instance_cascade` | instance_lifecycle.py:1478 | ✅ Already in plan |
| 3 | `terminate_instance` | instance_lifecycle.py:1124 | ❌ ADD |
| 4 | `clear_all_instances` | instance_lifecycle.py:1992 | ❌ ADD (clear entire dict) |
| 5 | Project cascade delete | routers/projects.py | ❌ ADD |

**Recommendation**: Consider a centralized `_cleanup_instance_state(instance_id)` helper on InstanceManager that pops from `_graph_tasks`, `_request_registry`, AND `_pending_injections` in one call. This prevents future drift when new instance-level dicts are added.

**Affected phases**: Phase 1

---

## W3. Add 6th E2E Test for WAITING_CHILDREN

Add E2E test: inject into WAITING_CHILDREN instance, wait for child completion + parent resume, verify injection consumed.

**Affected phases**: Phase 4

---

## W5. Reuse `stream_message()` Instead of New `stream_injection()`

**Decision**: Do NOT create a new `stream_injection()` method. Reuse the existing `stream_message()` method by passing an `event_type` parameter (e.g., `event_type="injection_pending"`, `event_type="injection_consumed"`, `event_type="injection_cleared"`).

**Affected phases**: Phase 2 (SSE emission)

---

## W6. Fix Test Race Condition in `test_injection_cleared_on_pause`

**Fix**: After sending injection, verify `pending=true` BEFORE pausing. After pause, verify the message does NOT appear in conversation (was cleared, not consumed).

**Affected phases**: Phase 4

---

## W7. Update E2E Test Count

There are **5 existing E2E tests**, not 4:
1. `test_parent_child_workflow_happy_path`
2. `test_pause_after_spawn_then_resume`
3. `test_terminate_after_spawn_then_revive`
4. `test_wave_spawn_with_defer_queue`
5. `test_pause_blocks_defer_queue`

**Affected phases**: Phase 4

---

## S1. TTL Sweeper for Orphaned Injection Slots

**Suggestion (incorporated)**: Add a background sweeper that cleans up injection slots older than 1 hour. These can accumulate if an instance crashes without proper cleanup or if the agent node never runs again (e.g., stuck in WAITING_CHILDREN forever).

**Implementation**: Add to InstanceManager's existing periodic cleanup (e.g., alongside `_cleanup_cached_instances` which runs on a 4-hour TTL). Check `_pending_injections` entries for timestamps older than 1 hour and remove them.

**Affected phases**: Phase 1

---

## S4. Validate Empty Content in Injection API

**Suggestion (incorporated)**: Validate that injection content is not empty:

```python
if not message_data.content or not message_data.content.strip():
    raise HTTPException(status_code=400, detail="Message content cannot be empty")
```

Apply this validation to the injection path (and optionally to all send_message paths).

**Affected phases**: Phase 2

---

## S9. Use Long-Response E2E Test Prompts

**Suggestion (incorporated)**: Design E2E test prompts that generate long responses AND trigger tool calls, keeping the instance RUNNING long enough to reliably inject mid-execution.

**Affected phases**: Phase 4
