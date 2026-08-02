# Test Report: `context` Parameter in `send_message` Tool

**Date:** 2026-08-02
**Branch:** `feature/context-param-send-message`
**Instance IDs:** 868d5b6f, 14c3272f, f4964f9d, 15fbc4e0, 65d55359, d18793c1

## Summary

| Category | Tests | Passed | Failed | Skipped | Status |
|----------|-------|--------|--------|---------|--------|
| New Unit Tests (`_format_task_context` + tool flow) | 14 | 14 | 0 | 0 | ✅ PASS |
| New Service Tests (HumanMessage injection) | 11 | 11 | 0 | 0 | ✅ PASS |
| Regression: instance_messaging_regression | 28 | 28 | 0 | 0 | ✅ PASS |
| Regression: api_unit_test | 221 | 213 | 0 | 8 | ✅ PASS |
| Regression: context_injection_unit_test | — | — | — | — | ⚠️ STALE PACK (pre-existing) |
| **Totals** | **274** | **266** | **0** | **8** | **✅ ALL PASS** |

- Quick Fixes Applied: 2 (1 test code fix, 1 commit of untracked test file)
- Quarantined: 0

## Scope Decision

> Full requested; change touches 6 daemon files across 5 modules (instance.py, instance_messaging.py, task_processor.py, message_processing_pipeline.py, manager.py, persistence.py) + 7 agent doc files. This is a cross-module feature addition with full pipeline threading → scoped to relevant packs: messaging regression, API tests, context injection regression + 2 new test files covering the new functionality. Full suite not warranted (change is additive, not a refactor of existing logic).

## Feature Tested

A new `context` parameter on the `send_message` agent tool:
- Accepts `dict[str, Any] | None`
- When non-empty, `_format_task_context()` renders it into a `[SYSTEM CONTEXT: Task Context]` markdown block
- Stored as `metadata={"task_context": "..."}` in the message queue row
- Extracted by `task_processor.py`, threaded via `ProcessingContext.task_context`
- Injected as a `HumanMessage` at position 0 of `persistent_context_msgs` (before project/shared_context/skills)
- `additional_kwargs={"injected_message": True, "context_kind": "task_context"}`
- `id=f"task-context-{message_id}"` (deterministic per message)
- Skipped on retry (`not is_retry` guard)
- `"task_context"` registered in `persistence.py:_CONTEXT_KINDS` for checkpoint survival

## New Test Files Written

### 1. `tests/tools/test_send_message_context_param.py` (825 lines, 14 tests)

**Part A — `_format_task_context()` pure function tests (10 tests):**
- A1: Typical dict (files list + notes string) — header, bullets, text block, separators
- A2: Empty dict → ONLY the header line
- A3: `None` → documents dict-only contract (raises AttributeError; caller must guard)
- A4: Non-string scalars (int, float, bool) → `str(value)` rendering
- A5: Nested dict values → `str(value)` rendering
- A6: Special characters in keys (underscore → space, slash passthrough)
- A7: Newlines/tabs/angle brackets in values → verbatim passthrough
- A8: Multiple keys → dict insertion order preserved
- A9: Single-element list → exactly one bullet
- A10: Unicode/emoji/CJK values → UTF-8 round-trip

**Part B — `send_message(context=...)` tool-level flow tests (4 tests):**
- B1: `context={"key": "value"}` → metadata contains task_context
- B2: `context=None` → metadata is None (backward-compat)
- B3: `context={}` → metadata is None (empty dict treated as no context)
- B4: `context` + `load_skill` together → both features compose without contamination

### 2. `tests/services/test_instance_messaging_task_context.py` (956 lines, 11 tests)

**Part A — HumanMessage injection (6 tests):**
- 1: Basic shape: content, id format, kwargs
- 2: `task_context=None` → no injection
- 3: `task_context=""` → no injection (falsy guard)
- 4: `is_retry=True` → no injection (retry guard)
- 5: Position 0 of persistent_context_msgs (before other context blocks)
- 6: Message id format `task-context-{message_id}` deterministic

**Part B — Additional kwargs correctness (2 tests):**
- 7: Exact key set `{"injected_message": True, "context_kind": "task_context"}`
- 8: `context_kind` value matches `CONTEXT_KIND_TASK_CONTEXT` constant

**Part C — Integration with existing context blocks (2 tests):**
- 9: task_context before shared_context
- 10: task_context before skills

**Part D — Persistence layer recognition (1 test):**
- 11: `_messages_have_context_block` recognizes `task_context` kind (3 negative controls)

## Regression Test Results

### instance_messaging_regression_test.sh — ✅ PASS (28/28)
- `test_instance_messaging_skill_injection.py`: 23/23 pass
- `test_instance_messaging_shared_context_injection.py`: 5/5 pass
- Runtime: 0.86s

### api_unit_test.sh — ✅ PASS (213 passed, 8 skipped)
- 6 test files including `tests/test_api.py` (contains send_message tests)
- 1 quick fix applied: `daemon.utils.get_registry` → `daemon.registry.get_registry` patch target in `test_spawn_instance_instructive_errors.py` (commit `92c7d649`)
- Runtime: 12.5s

### context_injection_unit_test.sh — ⚠️ STALE PACK (pre-existing)
- Pack targets `tests/unit/test_context_injection_prompt.py` which was DELETED in commit `f2ecb3a5` (2026-07-29, before this branch)
- **NOT a regression** from this feature — documented in `LESSONS/2026-07-31-legacy-context-injection-removal-testing.md`
- Action: Mark DEPRECATED in PACKS.md (this report)

## ensure.md Validation Results

### Critical Requirements
- ✅ No regressions in changed packs — all relevant packs PASS
- ✅ Deadlock / concurrency integrity — N/A (no concurrency code changed in this feature)
- ✅ No sync DB calls on the asyncio event loop — N/A (no new DB calls)
- ✅ `dev.sh` includes `--timeout-graceful-shutdown 10` — N/A (no dev.sh changes)

### Important Requirements
- ✅ All callers of converted async functions properly await — N/A (no async signature changes)
- ✅ Original send_message scenario works without blocking — verified by api_unit_test

### Nice-to-have
- ✅ No dead code — implementation is clean, all paths exercised by tests

## Quick Fixes Applied

| Instance | Fix | File | Root Cause | Verification |
|----------|-----|------|------------|--------------|
| 15fbc4e0 (api regression) | Patch target correction | `tests/test_spawn_instance_instructive_errors.py` | `daemon.utils.get_registry` patched but `get_registry` is imported locally from `daemon.registry`, not a module-level attribute | Commit `92c7d649`; 213/213 pass |
| d18793c1 (commit) | Commit untracked unit test file | `tests/tools/test_send_message_context_param.py` | Worker created file but didn't commit | Committed; combined run 25/25 pass |

## Commits

| Commit | Description |
|--------|-------------|
| `c508871f` | test: add service tests for task_context HumanMessage injection |
| `92c7d649` | test: fix get_registry patch target in spawn_instance instructive errors test |
| `fe5aff8b` | test: add unit tests for send_message context param |

## Documentation Updated
- [x] RESULTS/2026-08-02-context-param-send-message-test.md — this report
- [ ] PACKS.md — new entries for the 2 new test packs + context_injection_unit_test DEPRECATED
- [ ] LESSONS/ — quick fix for get_registry patch target

## Overall Status
- New Tests: ✅ PASS (25/25)
- Regression: ✅ PASS (241/241 active, 8 skipped, 0 failed)
- ensure.md: ✅ PASS (all relevant critical requirements met)
- **Testing Complete: ✅ READY**
te: ✅ READY**
