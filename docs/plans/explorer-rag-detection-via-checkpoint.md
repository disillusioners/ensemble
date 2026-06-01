# Explorer RAG Detection via Checkpoint Inspection

**Date**: 2026-06-02
**Status**: Draft (Revised — addressing reviewer feedback)
**Scope**: `daemon/utils.py`, `daemon/tools/knowledge_tools.py`, `agents/explorer/*`

## Problem

The explorer agent currently self-reports whether it queried RAG via a `## Did you query RAG: yes/no` heading in its response. This relies on the LLM correctly understanding and outputting the flag — which is unreliable. The LLM may forget, misinterpret, or hallucinate the flag value.

We need a **100% deterministic** way to detect if `rag_query_data` or `rag_get_graph` was actually called during the explorer's execution.

## Current Flow

```
explore() in knowledge_tools.py
    │
    ├─ invoke_agent_and_wait() ──> spawns child explorer instance
    │                                    │
    │                                    ├─ agent runs, may call rag_query_data / rag_get_graph
    │                                    ├─ agent writes "## Did you query RAG: yes/no" heading
    │                                    └─ LangGraph checkpointer stores full message history
    │
    ├─ _parse_rag_queried(result) ──> parses heading from response text (UNRELIABLE)
    │
    └─ if rag_queried: _save_explorer_result(...)
```

## Proposed Flow

```
explore() in knowledge_tools.py
    │
    ├─ invoke_agent_and_wait(return_instance_id=True)
    │       ──> ALWAYS returns (content, child_instance_id) tuple  [C1 FIX]
    │
    ├─ _check_rag_queried_via_checkpoint(checkpointer, child_instance_id)
    │       ──> inspects checkpoint messages for rag_query_data / rag_get_graph tool calls
    │       ──> returns True/False (deterministic, no LLM dependency)
    │       ──> runs REGARDLESS of error in content  [C2 FIX]
    │
    └─ if rag_queried: _save_explorer_result(...)
```

## Implementation Phases

This plan is split into **two phases** to allow production validation before full removal of the old heading-based system [S1 + W4]:

- **Phase 1**: Add checkpoint detection as primary method. Keep heading parsing as fallback for log comparison. Remove heading from prompts only after Phase 2 validation.
- **Phase 2** (after production validation): Remove heading parsing code, remove heading stripping, delete old tests, remove prompt heading instructions.

---

## Phase 1: Add Checkpoint Detection (Primary) + Keep Heading as Fallback

### Step 1: Add `return_instance_id` parameter to `invoke_agent_and_wait`

**File**: `daemon/utils.py:490-583`

- Add `return_instance_id: bool = False` parameter (line ~497)
- **[C1 FIX]** When `return_instance_id=True`, ALL return paths return `tuple[str, str]` = `(content, instance_id)`. This includes all 3 error paths:
  - Timeout path (line 560-566): return `(error_string, instance_id)`
  - Agent error path (line 568-570): return `(error_string, instance_id)`
  - Exception path (line 575-578): return `(error_string, instance_id)`
  - Success path (line 572-573): return `(result.content or "", instance_id)`
- Update return type annotation: `) -> str | tuple[str, str]:`
- Update docstring to specify tuple behavior on `return_instance_id=True`

**Key constraint**: The `instance_id` is already generated at line 529 before any branching, so it's always available in all return paths. No structural change needed — just wrap each return in a tuple when the flag is `True`.

```python
async def invoke_agent_and_wait(
    manager,
    agent_id: str,
    message: str,
    project_id: str | None = None,
    instance_name: str | None = None,
    parent_id: str | None = None,
    timeout: float = 300.0,
    return_instance_id: bool = False,
) -> str | tuple[str, str]:
```

Implementation pattern (apply to each return statement):

```python
# Helper at top of try block:
def _return(value: str) -> str | tuple[str, str]:
    return (value, instance_id) if return_instance_id else value

# Then replace each return:
# return "Error: ..." → return _return("Error: ...")
# return result.content → return _return(result.content or "")
```

Backward compatible — `return_instance_id=False` is default, all existing callers get same `str` return.

**Caller not in scope**: `daemon/mcp/kb_server.py:263` also calls `invoke_agent_and_wait()` but uses default parameters (no `return_instance_id`). It is unaffected. No changes needed there. [W1]

### Step 2: Add `_check_rag_queried_via_checkpoint()` helper

**File**: `daemon/tools/knowledge_tools.py` (add after `_parse_rag_queried` at ~line 66)

New async function that queries the LangGraph checkpointer to inspect actual tool calls:

```python
RAG_TOOL_NAMES = frozenset({"rag_query_data", "rag_get_graph"})


async def _check_rag_queried_via_checkpoint(
    checkpointer,
    instance_id: str,
) -> bool:
    """Check if RAG tools were actually called by inspecting checkpoint messages.

    Queries the LangGraph checkpointer for the agent's message history
    and looks for rag_query_data or rag_get_graph tool calls.

    Args:
        checkpointer: AsyncSqliteSaver instance from manager.
        instance_id: The child agent's instance ID (used as thread_id).

    Returns:
        True if any RAG tool was called, False otherwise.
    """
    try:
        config = {"configurable": {"thread_id": instance_id}}
        state = await checkpointer.aget(config)
        if not state:
            logger.debug("Checkpoint inspection: no state found for %s", instance_id[:8])
            return False

        messages = state.get("channel_values", {}).get("messages", [])
        scanned = 0
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name in RAG_TOOL_NAMES:
                        logger.info(
                            "Checkpoint inspection: RAG tool '%s' found (scanned %d messages)",
                            name, scanned + 1,
                        )
                        return True
            scanned += 1

        logger.info("Checkpoint inspection: no RAG tools found (scanned %d messages)", scanned)
        return False
    except Exception:
        logger.debug("Failed to check RAG tool calls from checkpoint", exc_info=True)
        return False
```

**[S2]** Logs number of messages scanned and whether RAG tools were found on success path.

**Note on shared utility [S3]**: The checkpoint message retrieval pattern (`checkpointer.aget(config)` → `state.get("channel_values", {}).get("messages", [])`) is also used in `persistence.py:get_instance_messages()` and `manager.py:_has_checkpoint()/_get_message_count()`. While extracting a shared `get_checkpoint_messages()` utility would reduce duplication, this is a **separate refactor** — not in scope for this plan. The pattern here is simple enough (2 lines) that duplication is acceptable. File a follow-up issue if desired.

### Step 3: Update `explore()` to use checkpoint-based detection

**File**: `daemon/tools/knowledge_tools.py:366-416`

This is the core change. Two critical fixes from the reviewer:

**[C1 FIX]** Use direct tuple unpacking — `invoke_agent_and_wait` with `return_instance_id=True` always returns a tuple. No `isinstance()` guard needed.

**[C2 FIX]** Check checkpoint REGARDLESS of whether the result is an error. The child instance was created and may have called RAG tools before failing.

Replace lines 366-416 with:

```python
        # Invoke explorer agent — always returns (content, child_instance_id) tuple
        result, child_instance_id = await invoke_agent_and_wait(
            manager=manager,
            agent_id="explorer",
            message=explorer_message,
            project_id=pid,
            parent_id=current_instance_id,
            instance_name=f"explore-{query[:30]}",
            timeout=300.0,
            return_instance_id=True,
        )

        # Handle error results — but still check checkpoint BEFORE returning
        # (child may have called RAG tools before failing)
        is_error = result is None or (isinstance(result, str) and result.startswith("Error:"))
        if is_error and result is None:
            result = "Explorer agent timed out or failed. Try a simpler query."

        # Deterministic RAG detection via checkpoint (runs for BOTH success and error paths)
        rag_queried_checkpoint = False
        if child_instance_id and hasattr(manager, 'checkpointer') and manager.checkpointer:
            rag_queried_checkpoint = await _check_rag_queried_via_checkpoint(
                manager.checkpointer, child_instance_id
            )

        # [Phase 1 only] Keep heading-based detection for log comparison
        rag_queried_heading = _parse_rag_queried(result) if isinstance(result, str) else False

        if rag_queried_checkpoint != rag_queried_heading:
            logger.info(
                "RAG detection mismatch: checkpoint=%s, heading=%s, instance=%s",
                rag_queried_checkpoint, rag_queried_heading, child_instance_id[:8] if child_instance_id else "N/A",
            )

        # Use checkpoint result as source of truth
        rag_queried = rag_queried_checkpoint

        # Return early if error (AFTER checkpoint inspection)
        if is_error:
            return result

        # ... rest of existing processing (should_update_kb, heading stripping, auto-save)
```

**Key behavioral changes**:
1. The `try/except` around `invoke_agent_and_wait` (old lines 366-377) is removed — errors are no longer returned early before checkpoint inspection.
2. The `if result is None` check (old line 379) is absorbed into `is_error` handling above.
3. Checkpoint inspection runs for ALL outcomes (success, timeout, agent error, exception).
4. Phase 1 logs mismatches between heading and checkpoint for production validation.

### Step 4: Update `explore()` auto-save section

**File**: `daemon/tools/knowledge_tools.py:416-460` (approximate, after Step 3 changes)

The auto-save block (`if rag_queried: _save_explorer_result(...)`) remains unchanged functionally — it just now uses the checkpoint-based `rag_queried` instead of heading-based.

No code changes needed here in Phase 1; the variable name `rag_queried` carries the checkpoint result from Step 3.

### Step 5: Add tests for Phase 1 changes

**Files**:
- `tests/unit/tools/test_knowledge_tools.py` — Update ALL existing `invoke_agent_and_wait` mocks
- `tests/unit/test_explorer_auto_save.py` — Keep existing `_parse_rag_queried` tests (still used in Phase 1)

**[W3 FIX]** All existing tests mock `invoke_agent_and_wait` with `return_value=explorer_response` (a string). After this change, when `explore()` uses `return_instance_id=True`, the mock must return a tuple.

**Required change pattern** — update every mock in `test_knowledge_tools.py`:

```python
# Before:
with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
           new_callable=AsyncMock, return_value=explorer_response):

# After:
with patch("daemon.tools.knowledge_tools.invoke_agent_and_wait",
           new_callable=AsyncMock, return_value=(explorer_response, "test-child-id")):
```

This affects approximately 25+ mock sites in `test_knowledge_tools.py` (all lines matching `invoke_agent_and_wait`).

**New tests to add in `tests/unit/tools/test_knowledge_tools.py`**:

Add a new test class `TestCheckRagQueriedViaCheckpoint` with async tests:

| Test | Description |
|------|-------------|
| `test_rag_tool_found` | Returns `True` when checkpoint messages contain `rag_query_data` tool call |
| `test_rag_get_graph_found` | Returns `True` when checkpoint messages contain `rag_get_graph` tool call |
| `test_no_rag_tools` | Returns `False` when no RAG tool calls in messages |
| `test_checkpoint_exception` | Returns `False` when checkpointer raises exception (graceful degradation) |
| `test_checkpoint_none` | Returns `False` when checkpoint state is `None` |
| `test_empty_messages` | Returns `False` when checkpoint has valid state but empty messages list [S5] |
| `test_multiple_tools_one_rag` | Returns `True` when checkpoint has many tool calls including one RAG call |

**New test: `invoke_agent_and_wait` with `return_instance_id=True`**:

| Test | Description |
|------|-------------|
| `test_return_instance_id_success` | Returns `(content, instance_id)` on success |
| `test_return_instance_id_timeout` | Returns `(error_string, instance_id)` on timeout |
| `test_return_instance_id_exception` | Returns `(error_string, instance_id)` on exception |
| `test_no_return_instance_id_default` | Returns plain `str` when flag is `False` (backward compat) |

**New test: `explore()` checkpoint integration**:

| Test | Description |
|------|-------------|
| `test_explore_checkpoint_rag_found_saves` | Checkpoint says RAG was called → save triggered |
| `test_explore_checkpoint_rag_not_found_skips` | Checkpoint says no RAG → save skipped |
| `test_explore_error_still_checks_checkpoint` | Agent errors but checkpoint shows RAG → save triggered [C2 validation] |
| `test_explore_checkpoint_mismatch_logged` | Mismatch between heading and checkpoint → logged at INFO |

**Test file for `test_explorer_auto_save.py`**: No changes needed in Phase 1 — `_parse_rag_queried` is still used as the heading-based fallback. Its tests remain valid.

---

## Phase 2: Remove Heading-Based Detection (After Production Validation)

> **Gate**: Phase 2 proceeds only after Phase 1 has run in production with no unexpected mismatches for at least 1 release cycle. If mismatches reveal bugs, fix them first.

### Step 6: Remove heading-based code and update prompt files

**Order of operations** (update imports/dependencies BEFORE deleting code):

**6a. Update `explore()` in `knowledge_tools.py`**:
- Remove `rag_queried_heading` variable and the mismatch log block
- Remove `_parse_rag_queried(result)` call entirely
- Remove `_RAG_QUERIED_PATTERN.sub("", result).strip()` heading stripping (line ~415)

**6b. Remove dead code from `knowledge_tools.py`**:
- Delete `_RAG_QUERIED_PATTERN` regex constant (lines 38-41)
- Delete `_parse_rag_queried()` function (lines 60-65)

**6c. Update explorer prompt files** (remove heading instructions):

| File | Lines to Remove/Modify | What to Change |
|------|----------------------|----------------|
| `agents/explorer/workflow.md:170` | Line 170 | Change "BOTH `## Confidence:`, `## Need Update KB:`, and `## Did you query RAG:` headings are MANDATORY" → Remove `## Did you query RAG:` from this sentence |
| `agents/explorer/workflow.md:177` | Line 177 | Remove `## Did you query RAG: {yes\|no}` from response template |
| `agents/explorer/workflow.md:209` | Line 209 | Remove `## Did you query RAG: yes` from example response |
| `agents/explorer/workflow.md:236-237` | Lines 236-237 | Delete both bullet points about when to set yes/no |
| `agents/explorer/workflow.md:240` | Line 240 | Remove `## Did you query RAG:` from "headings MUST appear first" list |
| `agents/explorer/soul.md:27` | Line 27 | Delete "Honest Reporter" bullet point entirely |
| `agents/explorer/rule.md:35` | Line 35 | Remove `## Did you query RAG:` from non-negotiable headings list |
| `agents/explorer/rule.md:39` | Line 39 | Delete "RAG Query Signal" bullet point entirely |

**6d. Remove heading stripping in response processing** (`knowledge_tools.py`):
- Remove line: `result = _RAG_QUERIED_PATTERN.sub("", result).strip()`
- Keep `_SHOULD_UPDATE_KB_PATTERN.sub("", result)` (that heading is unrelated)

### Step 7: Replace `_parse_rag_queried` tests with checkpoint tests

**File**: `tests/unit/test_explorer_auto_save.py`

**[W2 FIX]** The old `TestParseRagQueried` class (lines 520-598) tested `_parse_rag_queried()` which is being DELETED. These tests cannot be renamed — they must be REPLACED entirely.

- Delete the entire `TestParseRagQueried` class
- Delete the `from daemon.tools.knowledge_tools import _parse_rag_queried` import
- The replacement tests already exist in `test_knowledge_tools.py` (added in Phase 1, Step 5)

---

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How to get child instance_id | Add `return_instance_id` param to `invoke_agent_and_wait` | Minimal API change, backward compatible |
| How to inspect tool calls | Query checkpointer `aget` + scan messages | LangGraph already stores this; no new infrastructure needed |
| Fallback on checkpoint failure | Default to `False` (no save) | Safe default — better to miss a save than save duplicates |
| Error-path checkpoint behavior [C2] | Always check checkpoint, even on error | Child may have called RAG tools before erroring |
| Return type with `return_instance_id=True` [C1] | Always `tuple[str, str]`, all paths | Eliminates need for `isinstance()` guard, simplifies caller |
| Keep heading as fallback [S1] | Phase 1: dual detection for log comparison | Allows production validation before full removal |
| Remove heading from prompts | Phase 2 only, after validation | Two-phase approach gives rollback safety |
| MCP KB server (`kb_server.py:263`) [W1] | Not in scope — uses default params, backward compatible | No changes needed, it ignores `return_instance_id` |
| Rollback strategy [W4] | Phase 1 keeps old code alongside new; Phase 2 gated on validation | If checkpoint detection has issues, old heading detection still works |
| Shared checkpoint utility [S3] | Not extracted in this plan | 2-line pattern, not worth the refactor scope. File follow-up if desired |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Checkpoint not written before we query it | Low — completion is signaled after graph finishes, which writes checkpoint | Return `False` on error (safe default) |
| `aget` performance overhead | Negligible — single SQLite read of already-written data | Async, non-blocking |
| Race condition: cleanup deletes checkpoint before we read it | Very low — cleanup runs on a timer (hourly), not on completion | N/A |
| Breaking `invoke_agent_and_wait` callers | None — `return_instance_id=False` is default | All existing callers unaffected |
| Checkpoint `messages` empty on valid state [S5] | Low — would indicate LangGraph bug | Return `False` (safe default, logged) |
| Heading/checkpoint mismatch in Phase 1 | Possible — this is why we validate | Logged at INFO for monitoring; checkpoint wins |
| Phase 2 premature removal | Low — gated on production validation | Keep Phase 1 logging as evidence base |

## Rollback Strategy [W4]

**Phase 1 rollback**: Trivial — remove the `return_instance_id=True` argument from `explore()`. Old heading-based detection continues to work since it was never removed.

**Phase 2 rollback**: If issues are discovered after heading removal:
1. Revert the prompt file changes (git revert)
2. Re-add `_parse_rag_queried()` and `_RAG_QUERIED_PATTERN` from git history
3. Re-add the heading stripping line in `explore()`
4. Explorer agent will resume self-reporting the heading

Alternatively, introduce a config flag `EXPLORER_CHECKPOINT_RAG_DETECTION=true` (default `true`) that falls back to heading-based detection when `false`. This is heavier and only recommended if production issues require a runtime toggle.

## Files Changed

| File | Phase | Change |
|------|-------|--------|
| `daemon/utils.py` | 1 | Add `return_instance_id` param to `invoke_agent_and_wait`; all return paths return tuple when `True` |
| `daemon/tools/knowledge_tools.py` | 1 | Add `_check_rag_queried_via_checkpoint()`, `RAG_TOOL_NAMES`; update `explore()` to use tuple unpacking + checkpoint detection + mismatch logging |
| `tests/unit/tools/test_knowledge_tools.py` | 1 | Update all `invoke_agent_and_wait` mocks to return tuples; add `TestCheckRagQueriedViaCheckpoint` class; add checkpoint integration tests |
| `agents/explorer/workflow.md` | 2 | Remove `## Did you query RAG:` from template, example, and instructions (lines 170, 177, 209, 236-237, 240) |
| `agents/explorer/soul.md` | 2 | Delete "Honest Reporter" trait (line 27) |
| `agents/explorer/rule.md` | 2 | Remove `## Did you query RAG:` from headings list (line 35); delete "RAG Query Signal" (line 39) |
| `daemon/tools/knowledge_tools.py` | 2 | Remove `_RAG_QUERIED_PATTERN`, `_parse_rag_queried()`, heading stripping |
| `tests/unit/test_explorer_auto_save.py` | 2 | Delete `TestParseRagQueried` class and its import |

## Success Criteria

- [ ] `_check_rag_queried_via_checkpoint()` correctly detects RAG tool calls from checkpoint state
- [ ] `_check_rag_queried_via_checkpoint()` returns `False` gracefully for all error/edge cases (no state, empty messages, exception)
- [ ] `explore()` always inspects checkpoint, even when agent returns an error
- [ ] `invoke_agent_and_wait(return_instance_id=True)` returns `tuple[str, str]` from ALL code paths (success, timeout, error, exception)
- [ ] All existing tests pass after mock return value update (string → tuple)
- [ ] New tests cover all edge cases for checkpoint inspection
- [ ] Phase 1 production logs show no unexpected heading/checkpoint mismatches
- [ ] Phase 2: heading code and prompt instructions cleanly removed
- [ ] MCP KB server (`kb_server.py`) continues working unchanged
