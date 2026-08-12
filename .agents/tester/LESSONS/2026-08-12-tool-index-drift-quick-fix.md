# Lesson: Hardcoded Tool Index Drift When Inserting New Tools

**Date:** 2026-08-12
**Context:** P0 Job Visibility Tools (`job_messages`, `job_tree`) added to `daemon/tools/job_queue.py`
**Severity:** 🟠 Important (breaks existing tests, but is test-code only)

## Root Cause

The `create_job_tools()` factory in `daemon/tools/job_queue.py` returns a list of tools. Tests in `tests/test_job_queue_tools.py` access individual tools by **hardcoded index** (e.g., `tools[13]` for `watch_job`).

When 4 new P0 tools (`job_messages`, `job_tree`, `job_progress`, `job_inject`) were **inserted** into the list (not appended), all subsequent tool indices shifted:
- `watch_job`: `tools[13]` → `tools[17]` (shifted +4)
- `watch_jobs`: `tools[16]` → `tools[20]` (shifted +4)

This caused 7 test failures — all `assert` mismatches because `tools[13]` now resolved to `job_messages` instead of `watch_job`.

## Impact
- 7 tests in `tests/test_job_queue_tools.py` broke
- All failures were false negatives — production code was fine
- Quick fix: updated 6 index references (test code only, < 20 lines)

## Pattern to Watch For

**When adding new tools to any `create_*_tools()` factory:**
1. Check if existing tests reference tools by **hardcoded index**
2. If the new tools are inserted *before* existing tools (not appended), update all subsequent index references
3. Better long-term fix: tests should resolve tools by **name lookup** rather than hardcoded index:
   ```python
   # Fragile:
   watch_job = tools[13]
   
   # Robust:
   watch_job = next(t for t in tools if t.name == "watch_job")
   ```

## Affected Files
- `tests/test_job_queue_tools.py` — 6 edits: `tools[13]→[17]` (5x), `tools[16]→[20]` (1x)
- Production code: **NOT modified**

## Quick Fix Applied
- Worker `e1493955` applied the fix during test execution
- Test code only, < 20 lines, no architecture change
- Not committed — leader handles commits
