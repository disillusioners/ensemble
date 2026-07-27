# Quick Fix: Pre-existing test_send_message_success mock+assertion drift

**Date:** 2026-07-27
**Worker:** b1-api-autostart (f145d190)
**Branch:** feature/vscode-autostart-editor-ui
**Commits:** `e5c351ba` (fix 1) + `5e6b9cc3` (fix 2)
**Pack:** api_unit_test (`tests/test_api.py`)

## Problem
The pre-existing test `test_send_message_success` (NOT one of the 4 new auto-start tests) was failing due to two sequential mock/assertion drift issues:

### Bug 1: Missing `queued` field on Mock fixture (`tests/test_api.py:81`)
- **Root cause:** The `enqueue_message_job` mock return value lacked the `queued` field. When router code called `getattr(result, "queued")`, the Mock auto-generated a child Mock object, which Pydantic then rejected as non-bool.
- **Fix:** Added `queued=False` to the Mock fixture (1 line).
- **Commit:** `e5c351ba`

### Bug 2: Stale `assert_called_once_with` kwargs (`tests/test_api.py:858`)
- **Root cause:** The assertion was missing the `queue_id=None` kwarg that the router actually passes via `queue_id=message.queue_id`. The router code had evolved to pass `queue_id` but the test assertion was never updated.
- **Fix:** Added `queue_id=None` to expected kwargs (1 line).
- **Commit:** `5e6b9cc3`

## Root Cause Summary
Both bugs are **mock/assertion drift** — the router's actual behavior evolved (`queued` field on results, `queue_id` kwarg passed to `enqueue_message_job`) but the test's mock fixture and assertions were not kept in sync. These are pre-existing bugs unrelated to the auto-start feature.

## Lessons
1. **Sequential failures in one test** — the first re-run surfaced the second bug after fixing the first. A single test can have multiple layered failures.
2. **Mock fixtures need all fields** that production code reads via `getattr` — missing fields cause Mock to auto-generate non-type-safe children.
3. **`assert_called_once_with` must match actual call signatures** — any new kwarg added to the production call site needs to be added to the assertion.
