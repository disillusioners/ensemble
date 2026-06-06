# Test Report: fix/rag-checkpoint-detection (RAG checkpoint unwrap fix)

**Date:** 2026-06-06
**Branch:** `fix/rag-checkpoint-detection`
**Commit:** `7c9ebbe` — fix: unwrap CheckpointerAdapter.raw_saver in RAG checkpoint detection
**Bug Doc:** `docs/bugs/context-dir-empty-after-rag-call.md`
**Sessions:**
- `ens/rag-fix-tests` — pytest scope expansion (ses_1650e43d9ffezBr9cuk1AIaueF)
- `ens/rag-fix-ensuremd` — ensure.md validation (ses_1650e43ffffeLzXkqSAbVE0F7Z)

---

## Summary

| Scope | Tests | Passed | Failed | Skipped | Errors | Verdict |
|-------|-------|--------|--------|---------|--------|---------|
| `tests/unit/tools/test_knowledge_tools.py` | 84 | 84 | 0 | 0 | 0 | ✅ PASS |
| `tests/unit/tools/` | 402 | 402 | 0 | 0 | 0 | ✅ PASS |
| `tests/unit/` | 2642 | 2642 | 0 | 0 | 0 | ✅ PASS |
| ensure.md (`./dev.sh` 30s) | — | — | — | — | — | ✅ PASS |

**Overall: ✅ ALL PASS — 0 regressions, 0 quick fixes needed.**

---

## Step 1 — Targeted: test_knowledge_tools.py
- **Command:** `uv run pytest tests/unit/tools/test_knowledge_tools.py -v`
- **Result:** 84 passed, 0 failed, 0 skipped, 0 errors (2.78s)
- **New regression tests (both PASS ✓):**
  - `TestCheckRagQueriedViaCheckpoint::test_unwraps_raw_saver_when_checkpointer_is_checkpointer_adapter`
  - `TestCheckRagQueriedViaCheckpoint::test_unwraps_raw_saver_returns_false_when_no_rag_tool_calls`
- These tests use a real `SqliteCheckpointerAdapter` wrapping a `MagicMock` saver, which is the exact shape that triggered the bug. The mock saver's `aget` is awaited on the unwrapped `raw_saver`, not on the adapter.

## Step 2 — Broader: tests/unit/tools/
- **Command:** `uv run pytest tests/unit/tools/ -v`
- **Result:** 402 passed, 0 failed, 0 skipped, 0 errors (8.68s)

## Step 3 — Broader still: tests/unit/
- **Command:** `uv run pytest tests/unit/ -v`
- **Result:** 2642 passed, 0 failed, 0 skipped, 0 errors (69.02s)
- Warnings: 154 pre-existing deprecation warnings, all unrelated to this fix (pydantic.v1/Python 3.14, `datetime.utcnow()`, `asyncio.iscoroutinefunction`).

## ensure.md Validation
- **dev.sh exists & executable:** Y
- **Port 8079 free:** Y (port 8088 = tester infrastructure, untouched)
- **Run:** `timeout 30s bash ./dev.sh` → exit code 124 (clean timeout)
- **Log inspection:** 0 error markers (no `Traceback|Error|CRITICAL|FATAL|Exception|raise`)
- **Startup normal:** uvicorn on `0.0.0.0:8079`, RAG auto-test passed, PostgreSQL engine created, checkpointer adapter ready, MCP warmup pool started.
- **Shutdown normal:** graceful teardown on timeout signal (the `Worker did not stop within 0s` WARNING is a known cosmetic message).
- **Cleanup:** port 8079 freed, no leftover processes.

**ENSURE.MD: PASS**

---

## Quick Fixes Applied
None. No code changes were required. Working tree remains clean.

---

## Fix Verification — Does it actually address the bug?

The fix correctly addresses the documented root cause:

1. **`isinstance(checkpointer, CheckpointerAdapter)` check** — matches the production shape where `InstanceManager._checkpointer` is a `CheckpointerAdapter` (since commit `8c76247`). Pattern matches `daemon/persistence.py:280`.
2. **`.raw_saver` unwrap** — calls `.aget()` on the actual saver (e.g., `MemorySaver` or PostgreSQL checkpointer), which exposes `aget`. Previously, calling `.aget()` on the adapter raised `AttributeError`.
3. **Fall-through for tests** — when the caller passes a plain mock (no `raw_saver`), the `isinstance` check fails and the original path is preserved. This is why all 82 pre-existing tests still pass.
4. **DEBUG → WARNING log promotion** — previously the swallowed `AttributeError` was invisible at default LOG_LEVEL=INFO; now failures are visible. This will surface future regressions of the same class.

The 2 new regression tests prove the unwrap path works end-to-end with a real adapter, closing the gap that allowed the bug to ship undetected.

---

## Code Changes Summary
No code changes were made during testing. Working tree clean.

---

## Overall Status
- Unit Tests: ✅ PASS (2642/2642)
- ensure.md: ✅ PASS (dev.sh stable 30s)
- **Testing Complete: ✅ READY** — the fix is verified and safe to merge.
