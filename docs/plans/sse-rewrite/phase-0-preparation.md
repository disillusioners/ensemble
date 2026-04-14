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
