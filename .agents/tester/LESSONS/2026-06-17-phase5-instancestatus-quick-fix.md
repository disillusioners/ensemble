# Phase 5 Quick Fix: InstanceStatus Test for Canonical 10-Value Enum

**Date:** 2026-06-17
**Commit:** `65058a4d`
**File:** `tests/test_models.py`

## What Was Fixed

Phase 5 (`8f4b46f7`) eliminated the duplicate `InstanceStatus` definition by canonicalizing at `daemon/repositories/instance/models.py`. The canonical enum has **10 values** (added `WAITING` to the canonical location), while the old duplicate at `daemon/models/instance.py` had only 8.

The test in `tests/test_models.py` still asserted 8 values. Fixed to expect 10.

## Root Cause

Phase 5 architectural cleanup: the `WAITING` status was previously only in the duplicate definition, not the canonical one. Phase 5 migrated it to canonical. This is a test-only fix — the new values (`WAITING`, `QUEUED`, `FAILED`) are actively used in production code (`job_recovery_service.py`, `job_feedback_observer.py`, `manager.py`).

## Lesson

When a refactor eliminates duplicate definitions and canonicalizes an enum/class:
1. Check all tests that assert the count or presence of enum values
2. The test asserting the old count is testing the wrong thing — it should test the canonical definition
3. This is a common pattern in deduplication refactors
