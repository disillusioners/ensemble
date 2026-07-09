# Todo Comment Edge Cases — Testing Findings

**Date**: 2026-07-09
**Feature**: Todo comment + refresh (branch: feature/todo-comment-refresh)
**Commit**: 5958dcb0

## Key Findings

### 1. Existing Test Coverage Was Already Strong
The original feature commit (5c15ebcb) already included tests for 13 of 17 scenarios. The hardening commit (5958dcb0) added more. Gaps were only in:
- Concurrent access (filled by TestConcurrentSetComment)
- Special characters in comments (filled by TestSpecialCharactersInComment)
- SSE emission on comment edge cases (filled by TestRouterSSEOnCommentEdgeCases)

### 2. TodoManager MAX_COMMENT_LENGTH=1000
The implementation includes a defense-in-depth length limit of 1000 characters enforced by `set_comment()`. Tests must keep comment strings under this limit to avoid ValueError.

### 3. threading.Lock (not asyncio.Lock)
TodoManager uses `threading.Lock` for all state mutations. This is by design — the tools layer handles async wrapping. Concurrent tests should use real threads, not asyncio tasks.

### 4. update() Reminder Format
When a comment is non-empty and status is updated to "done", the reminder prepends:
```
User commented:
---
{comment}
---
```
This format uses triple-dash fences, so comments containing `---` are safe (data, not parsed as markdown by the manager).

### 5. Pre-existing Failures (70 tests)
The broader test suite has 70 pre-existing failures in unrelated modules:
- `test_inner_soul_rejection.py` (~11 failures)
- `test_memory_edge_cases.py` (~10 failures)
- `test_job_queue_proxy_phase*.py` (~7 failures)
- `test_archive_lifecycle.py`, `test_inner_soul_compound.py`, `test_inner_soul_redirect.py`

These are NOT caused by the todo comment feature. Verification: grep for `todo`/`TodoManager`/`set_comment` in failing files → zero matches.

### 6. Frontend Race Guards
The hardening commit added race guards in `todo-list.component.ts` that check `instanceId()` after async responses to prevent stale updates when switching instances. This pattern is applied symmetrically to both refresh and comment-save paths.
