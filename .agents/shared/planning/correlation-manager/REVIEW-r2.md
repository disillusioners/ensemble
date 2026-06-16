# CorrelationManager Migration Plan — Re-Review (Round 2)

**Date**: 2026-06-16
**Verdict**: 🟢 ALL FIXED — 2 blocking issues + 2 constraints resolved
**Reviewer**: Reviewer agent (council deep-review mode, round 2)
**Previous Verdict**: 🟡 NEEDS REVISION (2 remaining blocking issues)

## Summary

All issues from Round 1 and Round 2 are now resolved. The plan is ready for implementation.

## Per-Issue Verification

| Issue | Round 1 Severity | Final Status | Details |
|-------|-----------------|-------------|---------|
| C1 | 🔴 Critical | ✅ Fixed (Rev 2) | Hook points correct; rebuild now uses real UUIDs from message_queue (N1 fix) |
| C2 | 🔴 Critical | ✅ Fixed (Rev 1) | Direct callback bypasses EventBus DB persistence |
| C3 | 🔴 Critical | ✅ Fixed (Rev 1) | Direct callback eliminates queue overflow risk |
| C4 | 🔴 Critical | ✅ Fixed (Rev 1) | Per-parent Lock + MainLoopBridge marshals all callers to event loop |
| C5 | 🔴 Critical | ✅ Fixed (Rev 2) | TOCTOU eliminated + `had_error` flag makes conservative error policy workable (N2 fix) |
| C6 | 🟡 Warning | ✅ Fixed (Rev 1) | 3 retries, [0.5, 1, 2]s backoff, recursive counter passed correctly |
| N1 | 🔴 Blocking | ✅ Fixed | Rebuild queries `message_queue` for real `message_id` UUIDs |
| N2 | 🔴 Blocking | ✅ Fixed | `ParentCorrelation.had_error` set before pop; `_determine_terminal_status` reads flag |
| N3 | 🟡 Constraint | ✅ Documented | Phase 1 constraints section: all CM callers MUST be on main event loop |
| N4 | 🟡 Constraint | ✅ Documented | Phase 2 constraints section: callback MUST NOT re-enter CM for same parent_id |
