# Quick Fix: Module-level import breaks mock patches in title_generation tests

**Date**: 2026-06-06
**Commits**: `d132139` (test_title_generation_trigger.py), `e49eba8` (test_manager.py)
**Refactor**: `refactor/clean-llm-config-helper` (commit `79408c9`)

## Problem

The `clean_llm_config` refactor moved `from ..graph import ThinkingChatOpenAI` from function-local imports to **module-level imports** in `daemon/services/title_generation.py`.

Tests that patched `daemon.graph.ThinkingChatOpenAI` via `@patch("daemon.graph.ThinkingChatOpenAI")` relied on the function-local import re-resolving the attribute at call time. With module-level imports, the name `ThinkingChatOpenAI` is already bound at import time — the patch on `daemon.graph` no longer affects it.

**Symptom**: Some tests were making real HTTP calls to OpenAI with fake `test-key` credentials (getting 401s). Others passed by accident via early returns or because the 401 was interpreted as an expected error.

## Root Cause

Module-level import binds the name at import time:
```python
# Before (function-local): patch worked
def generate_title():
    from ..graph import ThinkingChatOpenAI  # re-resolved at call time
    llm = ThinkingChatOpenAI(**config)

# After (module-level): patch doesn't work  
from ..graph import ThinkingChatOpenAI  # bound at import time

def generate_title():
    llm = ThinkingChatOpenAI(**config)  # uses original, not patched
```

## Fix

Update mock patch paths to target the consumer module, not the source module:

```python
# Before (broken):
@patch("daemon.graph.ThinkingChatOpenAI")

# After (fixed):
@patch("daemon.services.title_generation.ThinkingChatOpenAI")
```

## Lesson

**When refactoring imports from function-local to module-level, mock patch paths must be updated to target the consumer module.** This is a common Python gotcha — module-level imports bind names at import time, making patches on the source module ineffective.

**Always check**: When changing import style, grep for `@patch` patterns referencing the old import path.

## Affected Files
- `tests/unit/services/test_title_generation_trigger.py` (4 patches)
- `tests/test_manager.py` (5 patches)
