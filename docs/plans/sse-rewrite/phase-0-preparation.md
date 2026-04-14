# Phase 0: Preparation — Extract `parse_think_tags`

> **Isolated PR**: This phase must be committed separately before any other SSE changes.

---

## Goal

Move `parse_think_tags()` and `_THINK_PATTERN` from `manager.py` to a new `daemon/utils.py` file. This prevents circular import issues when `serialize_message()` (added in Phase 1) needs to call `parse_think_tags()`.

---

## Steps

1. Create `daemon/utils.py` with:
   - `_THINK_PATTERN` constant
   - `parse_think_tags()` function (copy from `manager.py`)

2. Update `manager.py` import:
   ```python
   from .utils import parse_think_tags
   ```

3. Update `persistence.py` import:
   ```python
   from daemon.utils import parse_think_tags
   ```

4. Verify:
   ```bash
   grep -rn "parse_think_tags" daemon/ --include="*.py"
   ```
   Should show imports from `.utils`

---

## Verification

```bash
# Should NOT exist yet
ls daemon/utils.py 2>/dev/null && echo "EXISTS - remove/rename first" || echo "OK - will create"

# After changes, verify imports
grep -rn "parse_think_tags" daemon/ --include="*.py"
```

Expected output after completion:
- `daemon/utils.py` contains the function
- `daemon/manager.py` imports from `.utils`
- `daemon/persistence.py` imports from `daemon.utils`

---

## Phase 0.5: LangGraph Stream Format Verification (MANDATORY)

> **Hard gate**: Phase 1 cannot start until this passes.

Add diagnostic logging to verify LangGraph's actual `astream()` format matches assumptions:

```python
# In manager.py, add before streaming loop
async for event in graph.astream(graph_input, config, stream_mode=["updates", "messages"]):
    if isinstance(event, tuple):
        mode, data = event
    else:
        mode, data = "updates", event
    
    logger.info(f"LangGraph stream event: mode={mode}, "
                f"data_keys={data.keys() if isinstance(data, dict) else type(data)}")
    # Continue with existing logic
```

**Abort condition**: If stream format differs significantly from expected, reassess the checkpoint-based approach entirely. Document findings and create new plan before proceeding.

**Note**: LangGraph version is locked in `pyproject.toml`. Format verification is valid only for the current version. Future LangGraph upgrades require separate verification.

### Phase 0.5 Additional Verification (Do before Phase 4)

Before Phase 4 cleanup, verify that `ResponseDispatcher` in `daemon/sources/dispatcher.py` correctly filters checkpoint events:

```bash
grep -rn "event_type" daemon/sources/dispatcher.py | grep -i "filter\|checkpoint\|completed"
```

Expected: Dispatcher should only process `event_type='completed'`, ignoring `event_type='checkpoint'`. If the filter is missing, add it before Phase 4.
