# LESSON: Slug regex hex-only bug in context_tools.py

**Date:** 2026-08-08
**Feature:** Instance Lifecycle Hooks
**Severity:** 🟡 Important (non-blocking but produces noisy slugs)
**Found by:** E2E validation worker (instance a0d12998)

## Problem

`_build_filename` in `daemon/services/context_tools.py:50` writes `instance_id[:8]` as the filename suffix. But `_TIMESTAMP_PATTERN` at line 43 requires the suffix to be **hex-only**:

```python
_TIMESTAMP_PATTERN = re.compile(r"_\d{8}_\d{6}(?:_[a-f0-9]{8})?\.md$")
```

When `instance_id[:8]` contains non-hex chars (e.g., `test-ins`), the regex doesn't match, so the timestamp + suffix leak into the extracted slug:

- **Expected slug:** `distributed-consensus-algorithms`
- **Actual slug:** `distributed-consensus-algorithms_20260808_132236_test-ins`

## Impact

- Doesn't break the heuristic matcher (substring scoring still works — `"consensus"` scores 1.0)
- Makes slugs longer/noisier than intended
- Could cause issues in downstream code relying on clean slugs
- The existing unit tests use hex instance_ids (e.g., `aaaa1111`, `bbbb2222`) so this bug is not caught by them

## Fix

One regex char-class change:

```python
# Before
_TIMESTAMP_PATTERN = re.compile(r"_\d{8}_\d{6}(?:_[a-f0-9]{8})?\.md$")

# After
_TIMESTAMP_PATTERN = re.compile(r"_\d{8}_\d{6}(?:_[A-Za-z0-9_-]{1,32})?\.md$")
```

The same pattern exists in `context_injection.py:134` and should be updated there too for consistency.

## Test Gap

The `TestSlugParserCompat` tests use hex instance_ids, so they never exercise the non-hex path. A test with a non-hex instance_id (e.g., `test-ins`) would catch this.

**Status:** Reported to developer, not fixed by tester (production code change, outside quick-fix scope).
