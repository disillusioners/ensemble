# Quick Fix: Operator Precedence Bug in child_reports.py

**Date**: 2026-05-25
**File**: `daemon/services/child_reports.py:658`
**Commits**: 3b8fa74 + e7e9f0d

## Problem
Python operator precedence bug: `await coroutine[0]` — the subscript `[0]` was applied to the coroutine object BEFORE `await`, causing `TypeError: 'coroutine' object is not subscriptable`.

This caused child completion reports to silently fail, leaving leader instances permanently stuck in `waiting_children` state.

## Root Cause
```python
# BROKEN — subscript happens before await
if not await self._should_send_completion_report(...)[0]:
```

## Fix
```python
# FIXED — await first, then subscript
should_send = await self._should_send_completion_report(...)
if not should_send[0]:
```

## Impact
- **Critical**: Without this fix, the entire agent-to-agent communication flow was broken
- Leader instances would get stuck in `waiting_children` forever
- Child instances would complete but their reports would never be processed
- This was the root cause of multiple previous test failures with stuck instances

## Lesson
In Python async code, `await expr[index]` is dangerous. Always store the await result first:
```python
result = await some_async_fn()
value = result[index]
```

## Verification
- E2E test passed after fix: leader → completed, coder → terminated
- No orphan warnings
- Proper state transitions confirmed
