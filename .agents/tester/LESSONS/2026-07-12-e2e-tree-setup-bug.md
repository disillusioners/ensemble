# E2E Test Tree Setup Bug — Pre-existing, Found During Message-Body Injection Testing

**Date**: 2026-07-12
**Branch**: `feature/shared-context-message-injection`
**Commit (fix)**: `5c0195d0`
**Severity**: Test bug (pre-existing, not production)

## Root Cause

The test `TestSharedContextE2E::test_kv_written_via_repo_round_trips_into_injection_fence` in `tests/integration/test_shared_context_e2e.py` had a malformed tree setup:

1. Created `parent_id` as a root Instance (with `parent_id=None`)
2. Called `instance_repo.update(parent_id, parent_id=root_id)` to set the parent's parent to `root_id`
3. But **never created `root_id` as an Instance row**

When `get_tree_root_id(parent_id)` walked the tree:
- Found `parent_id` → its `parent_id` is `root_id`
- Looked up `root_id` → NOT FOUND (no Instance row exists)
- Fell back to `parent_id` itself (the fallback logic when tree walk misses)

But the KV was written under `root_id`'s partition, so the lookup returned empty and the injection returned the prompt unchanged — causing the assertion `<shared_context_metadata> in composed` to fail.

## Why It Wasn't Caught Before

The test was added in commit `5020a27f` (original shared-context-metadata branch) but was always SKIPPED because:
1. The pack script gates on `OPENAI_API_KEY` (module-level `skipif`)
2. Previous test runs didn't have the key set / propagated

When we ran the E2E pack with the key explicitly exported, the test actually ran and exposed the bug.

## Fix

Create `root_id` as a real root Instance FIRST, then create `parent_id` with `parent_id=root_id`. Drop the now-redundant `update()` call.

**Before** (11 lines, broken):
```python
instance_repo.create(
    instance_id=parent_id,
    agent_id="developer",
    agent_dir="/tmp/test/developer",
    parent_id=None,
    project_id="default",
    metadata={"title": "parent"},
)
instance_repo.update(parent_id, parent_id=root_id)
```

**After** (16 lines, correct):
```python
instance_repo.create(
    instance_id=root_id,
    agent_id="developer",
    agent_dir="/tmp/test/developer",
    parent_id=None,
    project_id="default",
    metadata={"title": "root"},
)
instance_repo.create(
    instance_id=parent_id,
    agent_id="developer",
    agent_dir="/tmp/test/developer",
    parent_id=root_id,
    project_id="default",
    metadata={"title": "parent"},
)
```

## Lesson

1. **E2E tests with skipif guards can hide bugs** — if the guard condition is rarely met, the test never runs. Always verify E2E tests actually execute.
2. **Tree setup in tests must create ALL nodes** — using `update()` to retroactively set parent_id without creating the parent row causes silent fallback in `get_tree_root_id`.
3. **The sibling test (`test_tree_root_resolution_via_real_instance_repo`) builds the tree correctly** — it creates root → parent → child in order. The broken test should have followed the same pattern.
