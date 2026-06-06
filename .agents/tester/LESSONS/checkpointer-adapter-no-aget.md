# Lesson: CheckpointerAdapter does not expose .aget()

**Date:** 2026-06-06
**Bug:** `docs/bugs/context-dir-empty-after-rag-call.md`
**Fix commit:** `7c9ebbe` (branch `fix/rag-checkpoint-detection`)

## The gotcha

`daemon.persistence.CheckpointerAdapter` is a wrapper introduced in commit `8c76247` for PostgreSQL support. It exposes only:
- `list_thread_ids`, `get_checkpoint_ids`, `delete_checkpoints_excluding`, `delete_writes_excluding`, `adelete_thread`, `find_excess_checkpoint_groups`, `close`
- the `raw_saver` property

It does **NOT** implement `.aget()`. The underlying saver (e.g., `MemorySaver`, Postgres saver) exposes `.aget()` via `raw_saver`.

## Why unit tests missed it

All existing tests of code that takes a checkpointer use `MagicMock` with `aget` monkey-patched directly:
```python
mock_checkpointer.aget = AsyncMock(return_value={...})
```
`MagicMock` auto-creates any attribute, so the tests never construct a real `CheckpointerAdapter`. The `AttributeError` path was never exercised.

## Pattern to use when calling checkpointer.aget()

```python
from daemon.persistence import CheckpointerAdapter

if isinstance(checkpointer, CheckpointerAdapter):
    saver = checkpointer.raw_saver
else:
    saver = checkpointer
state = await saver.aget(config)
```

This pattern is already used at `daemon/persistence.py:280`. Always use it when calling `.aget()` / `.aput()` / `.alist()` etc. on a checkpointer that came from `InstanceManager._checkpointer` or any production wiring.

## Defensive logging matters

The original bug was caught by `except Exception` and logged at **DEBUG** — invisible at default `LOG_LEVEL=INFO`. This made a silent always-False return look like "RAG was not queried". When swallowing exceptions in auto-save / context-injection paths, prefer `logger.warning(..., exc_info=True)` so future regressions are visible.

## Regression test pattern

Use the real `SqliteCheckpointerAdapter` (a concrete `CheckpointerAdapter`) wrapping a mocked saver — that's the production shape:
```python
from daemon.checkpoint_adapter import SqliteCheckpointerAdapter
raw_saver = MagicMock()
raw_saver.aget = AsyncMock(return_value={...})
adapter = SqliteCheckpointerAdapter(raw_saver)
result = await _check_rag_queried_via_checkpoint(adapter, "inst-123")
```
This catches the AttributeError class of bug that pure-MagicMock tests miss.
