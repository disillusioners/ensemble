# Explorer RAG Detection via Checkpoint Inspection

**Date**: 2026-06-02
**Status**: Draft
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
    │       ──> returns (content, child_instance_id)
    │
    ├─ _check_rag_queried_via_checkpoint(checkpointer, child_instance_id)
    │       ──> inspects checkpoint messages for rag_query_data / rag_get_graph tool calls
    │       ──> returns True/False (deterministic, no LLM dependency)
    │
    └─ if rag_queried: _save_explorer_result(...)
```

## Implementation Steps

### Step 1: Add `return_instance_id` parameter to `invoke_agent_and_wait`

**File**: `daemon/utils.py`

- Add `return_instance_id: bool = False` parameter
- When `True`, return `(content, instance_id)` tuple instead of just `content`
- Backward compatible — existing callers get same behavior (they don't pass the parameter)

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

Return value changes:
- `return_instance_id=False` (default): returns `str` (unchanged)
- `return_instance_id=True`: returns `tuple[str, str]` = `(content, instance_id)`

### Step 2: Add `_check_rag_queried_via_checkpoint()` helper

**File**: `daemon/tools/knowledge_tools.py`

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
            return False

        messages = state.get("channel_values", {}).get("messages", [])
        for msg in messages:
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                for tc in msg.tool_calls:
                    name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", "")
                    if name in RAG_TOOL_NAMES:
                        return True
        return False
    except Exception:
        logger.debug("Failed to check RAG tool calls from checkpoint", exc_info=True)
        return False
```

### Step 3: Update `explore()` to use checkpoint-based detection

**File**: `daemon/tools/knowledge_tools.py`

Changes to the `explore()` tool function:

1. Call `invoke_agent_and_wait(..., return_instance_id=True)` to get both content and instance_id
2. After successful completion, call `_check_rag_queried_via_checkpoint()` with the child instance_id
3. Use the checkpoint result for `rag_queried` (overrides the heading-based detection)
4. Remove the `_parse_rag_queried` heading parsing (or keep as fallback)

```python
# Before:
result = await invoke_agent_and_wait(
    manager=manager,
    agent_id="explorer",
    ...
)
...
rag_queried = _parse_rag_queried(result)

# After:
invoke_result = await invoke_agent_and_wait(
    manager=manager,
    agent_id="explorer",
    ...
    return_instance_id=True,
)
if isinstance(invoke_result, tuple):
    result, child_instance_id = invoke_result
else:
    result = invoke_result
    child_instance_id = None

...
# Deterministic RAG detection via checkpoint
rag_queried = False
if child_instance_id and manager.checkpointer:
    rag_queried = await _check_rag_queried_via_checkpoint(
        manager.checkpointer, child_instance_id
    )
```

### Step 4: Update prompt files to remove the self-reporting heading

**Files**:
- `agents/explorer/workflow.md` — Remove `## Did you query RAG: {yes|no}` from response format
- `agents/explorer/soul.md` — Remove "Honest Reporter" trait related to the heading
- `agents/explorer/rule.md` — Remove "RAG Query Signal" section

The heading `## Did you query RAG:` is no longer needed since detection is now deterministic.

### Step 5: Update tests

**Files**:
- `tests/unit/test_explorer_auto_save.py` — Rename `TestParseRagQueried` → update tests for new `_check_rag_queried_via_checkpoint` function
- `tests/unit/tools/test_knowledge_tools.py` — Update `TestExploreAutoSave` tests to mock checkpoint inspection instead of heading parsing
- Add new tests for `_check_rag_queried_via_checkpoint()`:
  - Returns `True` when checkpoint messages contain `rag_query_data` tool call
  - Returns `True` when checkpoint messages contain `rag_get_graph` tool call
  - Returns `False` when no RAG tool calls in messages
  - Returns `False` when checkpointer raises exception (graceful degradation)
  - Returns `False` when checkpoint state is None
- Add test for `invoke_agent_and_wait` with `return_instance_id=True`
- Update existing tests that mock `invoke_agent_and_wait` to handle new parameter

### Step 6: Clean up removed code

- Remove `_RAG_QUERIED_PATTERN` regex constant
- Remove `_parse_rag_queried()` function
- Remove `## Did you query RAG:` heading stripping from the response processing

## Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| How to get child instance_id | Add `return_instance_id` param to `invoke_agent_and_wait` | Minimal API change, backward compatible |
| How to inspect tool calls | Query checkpointer `aget` + scan messages | LangGraph already stores this; no new infrastructure needed |
| Fallback on checkpoint failure | Default to `False` (no save) | Safe default — better to miss a save than save duplicates |
| Remove heading from prompts | Yes, after checkpoint detection is confirmed working | The heading was the source of unreliability |
| Keep heading as fallback | No — remove completely | Two systems doing the same thing creates confusion; one deterministic system is cleaner |

## Risk Assessment

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| Checkpoint not written before we query it | Low — completion is signaled after graph finishes, which writes checkpoint | Return `False` on error (safe default) |
| `aget` performance overhead | Negligible — single SQLite read of already-written data | Async, non-blocking |
| Race condition: cleanup deletes checkpoint before we read it | Very low — cleanup runs on a timer (hourly), not on completion | N/A |
| Breaking `invoke_agent_and_wait` callers | None — `return_instance_id=False` is default | All existing callers unaffected |

## Files Changed

| File | Change |
|------|--------|
| `daemon/utils.py` | Add `return_instance_id` param to `invoke_agent_and_wait` |
| `daemon/tools/knowledge_tools.py` | Add `_check_rag_queried_via_checkpoint()`, update `explore()`, remove heading parsing |
| `agents/explorer/workflow.md` | Remove `## Did you query RAG:` heading from response format |
| `agents/explorer/soul.md` | Remove "Honest Reporter" trait |
| `agents/explorer/rule.md` | Remove "RAG Query Signal" section |
| `tests/unit/test_explorer_auto_save.py` | Update tests for new detection method |
| `tests/unit/tools/test_knowledge_tools.py` | Update tests for new detection method |
