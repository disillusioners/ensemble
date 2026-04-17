# Phase 4 Quick Fixes — Pre-existing Test Sync Issues

**Date:** 2026-04-17
**Commit:** `14d204c`
**Session:** phase4-core

## Issue
Two test files in `tests/message_queue_redesign/` had assertions out of sync with config defaults after Phase 4 changes:

1. **`test_timeout_retry_e2e.py:550,574`** — Expected `task_timeout_minutes == 45.0` but actual default is `60.0`
2. **`test_worker_timeout.py:222-228,263-269`** — Used deprecated `event_bus=None` parameter on `ProcessMessageProcessor`

## Fix
- Updated assertions to match current default (60.0)
- Removed deprecated `event_bus=None` parameter (now passed via constructor defaults)

## Pattern
When config defaults change, test assertions in other test directories may still reference old values. Always check cross-directory test files after config changes.
