# Quick Fix: get_registry Patch Target in spawn_instance Instructive Errors Test

**Date:** 2026-08-02
**Branch:** `feature/context-param-send-message`
**Commit:** `92c7d649`
**Instance:** 15fbc4e0 (api regression worker)

## Problem

`tests/test_spawn_instance_instructive_errors.py` failed with:
```
AttributeError: <module 'daemon.utils'> does not have the attribute 'get_registry'
```

The test patched `daemon.utils.get_registry` using `@patch("daemon.utils.get_registry")`, but `get_registry` is imported **locally** inside `validate_agent_id()` (daemon/utils.py:454) — it is never a module-level attribute of `daemon.utils`.

## Root Cause

The patch target was wrong. `get_registry` lives in `daemon.registry`, not `daemon.utils`. The test used the wrong module path for the mock.

## Fix

Corrected the patch target from `daemon.utils.get_registry` → `daemon.registry.get_registry` in all 6 occurrences (5 were masked by `@pytest.mark.skip`, only 1 was active).

## Impact

- Test-only change — no production behavior affected
- 213/213 tests pass after fix (was 212 pass, 1 fail before)
- This is a pre-existing issue that was masked by `@pytest.mark.skip` on 5 of 6 occurrences

## Lesson

When patching imports that use local (function-level) imports, the patch must target the **source module** (`daemon.registry.get_registry`), not the module where the function is called (`daemon.utils`). Local imports don't bind the name at module level, so `@patch("daemon.utils.get_registry")` silently fails to find the attribute.
