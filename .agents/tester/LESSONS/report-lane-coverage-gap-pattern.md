# Lesson: Report-Lane Decoupling — Test Coverage Gap Pattern

**Date:** 2026-06-24
**Feature:** report-lane decoupling
**Tags:** test-coverage-gap, claim_pending_task, cross-system-guard, meaningful-tests

## The Pattern (Recurring)

When a feature's core contract is a SQL-level behavior change (e.g., "type X bypasses guard Y"), tests that only exercise the **data setup** (creating rows of type X) but never call the **actual method under test** (`claim_pending_task`) are **trivially passing** — they prove nothing about the guard logic.

## What Happened

The Phase 2 test suites (`test_report_lane_phase2.py` SQLite + `_pg.py` PostgreSQL) had `TestIndependentTurn` classes named to suggest they verify the "report lane decoupling". They only created `PROCESS_REPORT` Task rows and asserted they exist. **They never called `claim_pending_task`.** So the single most important invariant — PROCESS_REPORT bypasses the cross-system guard while PROCESS_MESSAGE remains blocked — was untested on BOTH databases.

## How to Detect This

When reviewing tests for a SQL-behavior change, ask:
1. Does the test actually CALL the method whose SQL changed? (`claim_pending_task`, not just `repo.create`)
2. Does the test exercise the contrast case? (type X passes, type Y blocked — proving the scoping is correct)
3. Does the test create the GUARD's precondition? (e.g., a PROCESSING MESSAGE job for the cross-system guard to evaluate)

## Fix Applied

Added `TestReportLaneGuard` / `TestReportLaneGuardPG` classes that:
1. Create a PROCESSING MESSAGE job (the guard's precondition)
2. Create a PROCESS_REPORT task with a DIFFERENT message_id
3. Assert `claim_pending_task` returns the report task (bypass works)
4. Contrast: create a PROCESS_MESSAGE task with non-matching message_id → assert blocked (scoping correct)

Commits: 82c8f2ec (SQLite), afbab690 (PG).

## Related
- Also found a misleading test name: `test_pg_concurrent_claims_only_one_wins` runs sequentially (sync method, no asyncio.gather), so it does NOT prove PG row-level locking under READ COMMITTED. Name overstates what it proves.
