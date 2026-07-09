# Test Report: system_* info tools

**Date:** 2026-07-09 14:21 UTC  
**Branch:** `feature/system-info-tools-gaia`  
**Commit:** `9f90f78e`  
**Sessions:** `system-tools-test` (ses_0b8c15d5), `tool-wiring-verify` (ses_0b8c15d4)

---

## Summary

| Check | Status |
|-------|--------|
| System tools tests (69) | ✅ PASS (69/69) |
| Gaia agent tests | ⚠️ 37 passed, 7 failed (pre-existing, NOT caused by this commit) |
| Broader tools regression | ⚠️ 521 passed, 66 failed (pre-existing, NOT caused by this commit) |
| Tool wiring integration (5 checks) | ✅ PASS (5/5) |
| **Overall** | ✅ **PASS — READY TO MERGE** |

**Quick Fixes Applied:** None needed  
**Code Changes:** None (no fixes required)

---

## 1. System Tools Tests — ✅ PASS (69/69)

```
$ python -m pytest tests/unit/tools/test_system_tools.py -v
collected 69 items
======================== 69 passed, 3 warnings in 1.15s ========================
```

All key behaviors verified:

### system_env
- ✅ Prefix filtering works correctly
- ✅ Secret masking enabled by default (values replaced with `[REDACTED]`)
- ✅ `nomask=True` returns real values
- ✅ No full `os.environ` dump — curated allowlist only (`_TRACKED_ENV_EXACT` + `_TRACKED_ENV_PREFIXES`)

### system_config
- ✅ Section filtering works
- ✅ Recursive nested masking (deeply nested dicts/lists masked)
- ✅ Dynamic section list derived from `Config.model_fields`

### system_health
- ✅ Returns version, db_type, rag_enabled status, platform info, PID

### Connection String Masking
- ✅ Password in URLs is masked (`[REDACTED]`)
- ✅ Host/port/db visible (not masked)

### Masking Primitives
- ✅ Recursive dict masking
- ✅ List masking
- ✅ Tuple masking
- ✅ URL password masking
- ✅ Primitive values → placeholder

---

## 2. Gaia Agent Tests — ⚠️ 37 passed, 7 failed (PRE-EXISTING)

**Failures are NOT caused by commit `9f90f78e`.** All 7 failures are in `TestGaiaScriptAccessibility`:

| Test | File | Error |
|------|------|-------|
| `test_scripts_directory_exists` | `test_gaia_agent.py:535` | `FileNotFoundError: agents/gaia/scripts` |
| `test_npx_script_exists` | `test_gaia_agent.py` | Same |
| `test_npx_script_is_readable` | `test_gaia_agent.py` | Same |
| `test_npx_script_has_content` | `test_gaia_agent.py` | Same |
| `test_scripts_directory_listable` | `test_gaia_agent.py:539` | Same |
| `test_no_symlinks_in_scripts` | `test_gaia_agent.py:548` | Same |
| `test_scripts_are_markdown_files` | `test_gaia_agent.py:555` | Same |

**Root Cause:** Commit `9102e620 "improving gaia"` deleted `agents/gaia/scripts/npx.md` (203 lines removed) but never updated the `TestGaiaScriptAccessibility` tests added in commit `04a6f653`. Identical failures confirmed on the parent state — **NOT caused by this PR**.

**The meta.json change (replacing "context" with "system" in tools.allow) is correctly verified** — the Gaia tool filter and registry tests pass.

---

## 3. Broader Tools Regression — ⚠️ 521 passed, 66 failed (PRE-EXISTING)

All 66 failures confirmed pre-existing by reproducing against parent state. **None caused by changes to `_tool_registry.py` or `instance.py` in this commit.**

**Failure clusters:**

| Cluster | Count | Root Cause | In scope? |
|---------|-------|------------|-----------|
| `TestInnerSoul*` (compound, redirect, rejection) | 54 | `daemon/tools/inner_soul.py:1381` — `re.search` on `MagicMock` (fixture issue) | ❌ No |
| `TestAccessMemoryArchive` | 5 | Archive "Access denied" vs expected response | ❌ No |
| `Test*Memory*EdgeCases` | 7 | Archive access + compound classification fixtures | ❌ No |

**None of these failures reference `daemon/tools/system.py`, `daemon/tools/_tool_registry.py`, or `daemon/tools/instance.py`** — the files modified in this PR.

---

## 4. Tool Integration Wiring — ✅ PASS (5/5)

Manual source code verification confirmed all integration points:

### Check 1: CATEGORY_MODULES entry — ✅ PASS
`daemon/tools/_tool_registry.py:205`:
```python
"system": "daemon.tools.system",
```

### Check 2: Assembly in create_instance_tools() — ✅ PASS
`daemon/tools/instance.py`:
- Line 117: `from .system import create_system_tools`
- Lines 1029-1030:
  ```python
  system_tool_list = create_system_tools(manager, current_instance_id)
  tools.extend(system_tool_list)
  ```

### Check 3: Gaia meta.json updated — ✅ PASS
`agents/gaia/meta.json:12-14`:
```json
"tools": {
  "allow": ["bash", "filesystem", "help", "mcp", "system"]
}
```
`"system"` present, `"context"` correctly removed.

### Check 4: Factory function exists — ✅ PASS
`daemon/tools/system.py:319-568`:
```python
def create_system_tools(manager, current_instance_id) -> list:
    ...
    return [system_env, system_config, system_health]
```

### Check 5: Tool decorators + docstrings — ✅ PASS
All 3 tools have `@register_tool_category("system")` + `@tool` + docstrings + `_full_doc_`:
- `system_env`: decorator at line 339
- `system_config`: decorator at line 402
- `system_health`: decorator at line 479

---

## Pre-existing Issues Noted

**Recommend follow-up ticket for test rot (separate from this PR):**
1. `TestGaiaScriptAccessibility` (7 tests) — stale tests referencing deleted `agents/gaia/scripts/npx.md`
2. `TestInnerSoul*` + memory/archive tests (66 tests) — `MagicMock` fixture bypass in `inner_soul.py:1381`

These failures exist on the parent commit and are out of scope for the system info tools PR.

---

## Overall Status

- **System Tools Tests:** ✅ PASS
- **Tool Wiring Integration:** ✅ PASS
- **Pre-existing Failures:** 73 tests (documented, out of scope, not regressions)
- **Quick Fixes Applied:** None (none needed)
- **Code Changes:** None (no fixes required)
- **Verdict:** ✅ **READY TO MERGE** — system info tools are correctly implemented, tested, and wired
