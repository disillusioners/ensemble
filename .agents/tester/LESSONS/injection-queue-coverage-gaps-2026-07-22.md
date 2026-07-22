# Coverage Gaps Found: Injection Queue Multi-Message Behavior

**Date:** 2026-07-22
**Branch:** `feature/injection-queue`
**Commits:** `2ec1099a`, `41c59c4c`, `85097179`

## Context

The injection feature changed from single-slot replace to append-list (FIFO queue) semantics. Coverage analysis identified gaps in multi-message scenarios for code paths that loop over the new `injected_msgs` list instead of handling a single scalar.

## Gaps Found & Resolved

### ✅ GAP 1: Reactive Compaction C3 Multi-Message (FIXED)

**Root cause:** `test_injection_re_appended_after_reactive_compaction` only tested single-message re-append. The new code loops `for inj in injected_msgs: compact_messages.append(inj)` but no test verified multiple messages survive.

**Fix:** Added `test_multi_entry_injection_re_appended_after_reactive_compaction` — 3-entry queue triggers ContextLengthExceededError, asserts all 3 markers survive in FIFO order. Committed at `85097179`.

**Result:** All 3 messages correctly re-appended — no bug found.

### ✅ GAP 2: LoopBreaker C3 Multi-Message Dedup (FIXED)

**Root cause:** `test_injected_message_re_appended_after_repair` only tested single-message re-append. The new code has a `msg.id is None` short-circuit at graph.py ~line 1185 that is load-bearing — without it, messages 2+ with None IDs get silently dropped.

**Fix:** Added `test_multiple_injected_messages_re_appended_after_repair` — 3-entry queue triggers loop repair, asserts all 3 markers survive. Committed at `85097179`.

**Result:** All 3 messages correctly re-appended — the `msg.id is None` guard works. No bug found.

## Remaining Gaps (Not Yet Resolved)

### 🔴 E2E `test_injection_replacement` — Tests OLD Semantics

**Root cause:** The e2e test still asserts old replace semantics (expects SECOND_MARKER only, FIRST absent). Under append-list, BOTH markers should appear.

**Status:** Not fixed — E2E requires daemon (not running). Must be rewritten before merging.
**Recommendation:** Rewrite as multi-message consumed test.

### 🟡 No E2E Multi-Message Test

**Root cause:** No e2e test sends 2+ injections to RUNNING and verifies both consumed end-to-end.
**Status:** Deferred (daemon not available).

## Lesson Learned

When a data structure changes from scalar to list (single-slot → queue), every code path that loops over the new list must have a multi-entry test variant. The single-message tests all passed but didn't exercise the loop logic. Systematic gap analysis (git diff → code paths → test coverage matrix) is essential for catching these.
