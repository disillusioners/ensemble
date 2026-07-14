# Lesson: Terminal Checkpoint Guard in _maybe_compact_context

Date: 2026-07-14
Branch: feature/instance-status-reuse-bug
Commit: 52133a14

## Bug
When reusing a completed child instance via `send_message` (2nd+ time), the instance status did not show "running" because the graph returned instantly.

## Root Cause
`_maybe_compact_context` in `instance_messaging.py` unconditionally called `graph.aupdate_state(config, {...}, as_node='agent')` on ALL non-retry turns. On terminal checkpoints (state.next is empty), this call clears the checkpoint's `next=()`, causing `astream()` to return immediately without running the graph.

## Fix
Added `if not state.next: return` guard before any compaction logic (instance_messaging.py:553). This skips compaction on terminal checkpoints but preserves it for active (non-terminal) turns.

## Test Strategy
1. **8 unit tests** (`test_instance_messaging_compaction_guard.py`) — test the guard directly (terminal vs non-terminal, edge cases)
2. **11 multi-reuse scenario tests** (`test_multi_reuse_lifecycle.py`) — verify the original bug scenario end-to-end across 3+ reuse cycles
3. **198 compaction regression tests** — ensure existing compaction behavior unaffected
4. **32 messaging regression tests** — ensure instance messaging hooks unaffected
5. **21 orchestration regression tests** — ensure lifecycle unaffected

## Key Insight
`graph.aupdate_state(as_node='agent')` is a known footgun on terminal checkpoints — it corrupts the checkpoint state. Always guard terminal checkpoints before calling it.

## Before/After
- **Before**: COMPLETED → send_message → RUNNING → COMPLETED in <100ms (frontend never sees RUNNING)
- **After**: COMPLETED → send_message → RUNNING (stays RUNNING while graph executes) → COMPLETED (full cycle)
