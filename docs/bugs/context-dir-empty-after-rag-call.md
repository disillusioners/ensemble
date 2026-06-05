# Bug: Context Directory Always Empty After Explorer Calls RAG

**Date:** 2026-06-06
**Status:** Investigated (pending fix)
**Severity:** Medium (silent failure - shared context for sibling instances never populated)

---

## Summary

When the `explorer` agent calls a RAG tool (e.g. `rag_query_data`, `rag_get_graph`), the auto-save logic in `daemon/tools/knowledge_tools.py` is supposed to persist the result into a shared context directory at:

```
$TEMP/ensemble/context/<tree_root_id_or_instance_id>/
```

In practice, the directory is **never created** and **never written to** — it stays empty (or absent) even when RAG was definitely called. As a result, downstream sibling instances reading from this context dir via the auto-inject path get nothing, and the explorer's findings are lost to the rest of the tree.

---

## Root Cause

| Location | Type |
|----------|------|
| `daemon/tools/knowledge_tools.py:80` | Calls `.aget()` on a `CheckpointerAdapter`, which does not implement `aget` |
| `daemon/tools/knowledge_tools.py:101` | Bare `except Exception` swallows the `AttributeError` and logs at DEBUG |
| `daemon/tools/knowledge_tools.py:425-429` | Call site passes `manager._checkpointer` (the adapter, not the raw saver) |

### The buggy call

```python
# daemon/tools/knowledge_tools.py:59-103
async def _check_rag_queried_via_checkpoint(checkpointer, instance_id) -> bool:
    try:
        config = {"configurable": {"thread_id": instance_id}}
        state = await checkpointer.aget(config)   # <-- BUG
        if not state:
            return False
        messages = state.get("channel_values", {}).get("messages", [])
        ...
    except Exception:
        logger.debug("Failed to check RAG tool calls from checkpoint", exc_info=True)
        return False
```

`manager._checkpointer` is a **`CheckpointerAdapter`** (a wrapper introduced in commit `8c76247` for PostgreSQL support). The adapter exposes only:

- `list_thread_ids`, `get_checkpoint_ids`, `delete_checkpoints_excluding`, `delete_writes_excluding`, `adelete_thread`, `find_excess_checkpoint_groups`, `close`
- the `raw_saver` property

It does **not** implement `.aget()`. The correct call is `await checkpointer.raw_saver.aget(config)`.

### Downstream effect

```python
# daemon/tools/knowledge_tools.py:425-429
rag_queried = False
if child_instance_id and hasattr(manager, "_checkpointer") and manager._checkpointer:
    rag_queried = await _check_rag_queried_via_checkpoint(
        manager._checkpointer, child_instance_id
    )
```

1. `checkpointer.aget(config)` raises `AttributeError: 'CheckpointerAdapter' object has no attribute 'aget'`
2. The bare `except Exception` catches it and logs at **DEBUG** (invisible at default `LOG_LEVEL=INFO`)
3. The function returns `False`
4. `rag_queried` stays `False`
5. The auto-save block (lines 466-486) is skipped — **`_save_explorer_result()` is never called**
6. The only place in the codebase that does `dir_path.mkdir(parents=True, exist_ok=True)` for this context dir lives inside `_save_explorer_result()` — so the directory is **never created at all** (not even as an empty dir)

### Why the context dir exists in this report

The path in question is `/var/folders/3m/p68dltbj1xv7hjkdxr_gqnc00000gn/T/ensemble/context/2b573a46-3a5a-455a-872b-1e3e6638ec2c`. This matches the formula at `daemon/tools/knowledge_tools.py:294, 469-470`:

```python
Path(tempfile.gettempdir()) / "ensemble" / "context" / context_key
```

(With `context_key = get_tree_root_id(current_instance_id) or current_instance_id or "default"`.)

So the user's observed empty path is consistent with the auto-save path being created *only* by `_save_explorer_result()`, which is exactly the function that never runs.

---

## Why Unit Tests Miss It

`tests/unit/tools/test_knowledge_tools.py` has 21 references to `aget`, all of which use `MagicMock` with `aget` monkey-patched directly onto the mock object:

```python
mock_checkpointer.aget = AsyncMock(return_value={...})
# or
mock_manager._checkpointer.aget = AsyncMock(return_value={...})
```

`MagicMock` auto-creates any attribute access, so the tests never construct a real `CheckpointerAdapter` and never exercise the `AttributeError` path. There is no test where a real `CheckpointerAdapter` (or any non-mocked wrapper that lacks `aget`) is passed to `_check_rag_queried_via_checkpoint`.

---

## The RAG Tools Themselves Are Fine

`daemon/tools/rag_tools.py` (`rag_query_data`, `rag_get_graph`, etc., lines 371-642) are pure *query* tools — they return strings to the LLM. They are not expected to write to the context dir. The bug is solely on the read-back side that decides whether the auto-save should fire.

The RAG tool calls are correctly persisted in the checkpoint by LangGraph (the `AIMessage.tool_calls` are stored in `channel_values.messages` keyed by `thread_id == instance_id`); the bug is only in reading that state back through the wrong API.

---

## Culprit Commits

| Commit | Title | Effect |
|--------|-------|--------|
| `3cccdee` (2026-06-02) | feat: add checkpoint-based RAG detection for explorer agent (Phase 1) | Introduced `_check_rag_queried_via_checkpoint()` and its call site. Written *after* the `CheckpointerAdapter` abstraction (commit `8c76247`) but used the raw-saver-style `checkpointer.aget(...)` API on the adapter object. |
| `72d5362` (2026-06-02) | refactor: remove heading-based RAG detection, keep checkpoint-only (Phase 2) | Deleted the heading-based fallback (`_parse_rag_queried`). After this, the broken checkpoint call became the **only** signal that could set `rag_queried=True`. |

---

## Established Correct Pattern in Codebase

The same operation is performed correctly elsewhere — these should be used as the reference fix shape:

| Location | Code |
|----------|------|
| `daemon/manager.py:1899, 1920` | `await self.checkpointer.raw_saver.aget(config)` |
| `daemon/persistence.py:280, 285` | `saver = checkpointer.raw_saver if isinstance(checkpointer, CheckpointerAdapter) else checkpointer` then `await saver.aget(config)` |
| `daemon/services/instance_messaging.py:247-258` | Property: `return adapter.raw_saver if adapter is not None else None` (docstring: *"services that need the raw saver (aget / alist) reach it via raw_saver"*) |

A related broken-but-accidentally-working call exists at `daemon/services/instance_messaging.py:369, 383` (`await self._checkpointer.aget(config)`); it works only because a property in that module unwraps the adapter first. `knowledge_tools.py:80` does not have that property indirection — it receives the raw adapter directly.

---

## Fix Options

### Option 1: Unwrap `raw_saver` at the call site (Recommended, minimal)

```python
# daemon/tools/knowledge_tools.py:80
state = await checkpointer.raw_saver.aget(config)
```

If `checkpointer` is not always a `CheckpointerAdapter` (e.g. in some tests), use the pattern from `daemon/persistence.py:280`:

```python
saver = checkpointer.raw_saver if hasattr(checkpointer, "raw_saver") else checkpointer
state = await saver.aget(config)
```

### Option 2: Accept the raw saver from the caller

Change the call site at `daemon/tools/knowledge_tools.py:427` to pass the raw saver:

```python
rag_queried = await _check_rag_queried_via_checkpoint(
    manager._checkpointer.raw_saver, child_instance_id
)
```

### Option 3: Add a helper on `CheckpointerAdapter`

Add a proper `aget()` (and `alist()`) pass-through on the adapter itself, mirroring the methods it already wraps. This is the most invasive option but eliminates future bugs of this kind.

### Follow-up (all options)

- Promote the swallowed `AttributeError` to at least `logger.warning(...)` so this class of failure is visible at default `LOG_LEVEL=INFO`.
- Add a unit test that constructs a real `CheckpointerAdapter` (or any object that lacks `aget` but exposes `raw_saver.aget`) and asserts `_check_rag_queried_via_checkpoint` returns the expected value rather than silently returning `False`.
- Consider restoring the heading-based fallback (or another orthogonal signal) so that a single broken read-back path cannot silently disable auto-save for the entire explorer flow.

---

## Verification Steps (after fix)

1. Start the daemon and trigger a flow that spawns the explorer agent.
2. Confirm the explorer invokes a RAG tool (visible in its message log).
3. Check `$TEMP/ensemble/context/<root_or_instance_id>/` — it should now exist and contain a saved result file.
4. Confirm a downstream sibling instance receives the injected context.
5. Run `pytest tests/unit/tools/test_knowledge_tools.py -v` — new regression test should pass and the existing 21 mock-based tests should continue to pass.
