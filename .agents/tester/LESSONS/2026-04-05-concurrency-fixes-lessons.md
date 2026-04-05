# Phase 1 Concurrency Fixes — Testing Lessons

## Date: 2026-04-05

### Bug Found During Review: Unwrapped `match_by_keywords` DB Call
- **File**: `daemon/manager.py:916`
- **Issue**: `self.project_store.match_by_keywords(keywords)` was a synchronous SQLite call not wrapped with `asyncio.to_thread()`
- **Impact**: Blocks the event loop during project keyword matching in `_process_queue()`
- **Fix**: Wrapped with `await asyncio.to_thread(self.project_store.match_by_keywords, keywords)`
- **Commit**: `07baab1`
- **Lesson**: When wrapping DB calls with `asyncio.to_thread()`, check ALL repository methods — some use different access patterns (e.g., `self.project_store` vs `self._project_repository`) that may be easy to miss.

### Pattern: Check for Multiple Repository References
- `_project_repository` and `project_store` may point to different objects/instances
- Both need to be checked for blocking calls
- Search for all `self.*repository` and `self.*store` patterns when auditing

### Testing Approach Used
- Two parallel opencode sessions: one for runtime (tests/imports), one for code review
- Code review with specific evidence requirements (line numbers, code snippets) produced thorough results
- Quick fix workflow worked well: found issue → fixed → re-tested → committed, all in same session
