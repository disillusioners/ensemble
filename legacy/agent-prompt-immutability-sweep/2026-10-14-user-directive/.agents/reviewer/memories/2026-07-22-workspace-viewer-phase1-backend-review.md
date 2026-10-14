# Review: Workspace Viewer Phase 1 Backend (e70f997f)

**Date**: 2026-07-22
**Commit**: `e70f997f` — `feat: add workspace viewer backend API`
**Branch**: `feature/workspace-viewer`
**Verdict**: 🔴 **REJECTED** — Critical security vulnerability must be fixed before merge

## Deep-Review Triggers
- Data Integrity / Security (path traversal, arbitrary file read)
- Cross-Cutting Changes (modified shared filesystem.py)
- Complex Concurrency (thread-based file monitor + asyncio)
- Architecture (new trust-boundary pattern)

## Findings Summary
- 🔴 Critical: 1 (temp directory path traversal bypass)
- 🟡 Warning: 7
- 🟢 Suggestion: 4

## Key Findings

### 🔴 CRITICAL: resolve_strict() allows temp directory reads
- `resolve_strict()` calls `_contains()` which exempts system temp dirs
- Attacker can read `/tmp/secret.txt`, `/tmp/db.sqlite`, etc. via HTTP
- **Fix**: Use `_normed_contains(self.workdir, target)` instead of `_contains()`

### 🟡 Null bytes cause uncaught ValueError → HTTP 500
- `Path().resolve()` raises ValueError on embedded null bytes
- `_resolve_target` only catches OSError/RuntimeError, not ValueError
- **Fix**: Add ValueError to except clauses

### 🟡 Invalid project workdir → HTTP 500
- WorkspaceGuard constructor raises ValueError if workdir doesn't exist
- Endpoints don't catch this
- **Fix**: Wrap in try/except, return 404

### 🟡 Git working_content/head_content no size limit
- `read_text()` and `git show` output not size-checked
- Can cause memory exhaustion on large files
- **Fix**: stat() before read, use MAX_FILE_SIZE_BYTES

### 🟡 _subscribers dict iteration race
- `_emit()` iterates dict from watchdog thread while async modifies it
- RuntimeError: dictionary changed size during iteration
- **Fix**: `list(self._subscribers.values())` snapshot

### 🟡 _instances dict TOCTOU race
- Concurrent get_or_create can create orphaned observer threads
- **Fix**: Add threading.Lock

## Method
3 parallel opencode sessions (security, concurrency, compat). Council mode attempted but hook didn't deliver; fell back to standard parallel sessions.
