# Test Report: Resume Message Appends Instead of Replacing

**Date:** 2026-05-26T22:52
**Commit:** `ab23b16` — fix: resume message now appends instead of replacing first message
**Branch:** `latest`

## Summary

| Category | Result | Details |
|----------|--------|---------|
| New Unit Tests | ✅ 8/8 PASS | `tests/unit/test_resume_message_append.py` |
| API Regression | ✅ 43/43 PASS | `tests/test_api.py` |
| Core Regression | ✅ 156/157 PASS | `tests/unit/` (1 pre-existing) |
| Frontend | ✅ 723/723 PASS | 18 suites, 4.4s |
| ensure.md | ✅ PASS | dev.sh stable 30s |

## Bug Fixed

**Root cause:** `resume_processing_job()` reused the paused job's `message_id`. LangGraph's `add_messages` reducer replaces messages with matching IDs, so the resume message replaced the original first message.

**Fix:** Changed `message_id=message_id or str(uuid.uuid4())` → `message_id=str(uuid.uuid4())` in `daemon/manager.py:1843`.

## New Tests Created

| Test | Purpose | Status |
|------|---------|--------|
| `test_resume_generates_unique_message_id` | Resume generates fresh UUID ≠ old message_id | ✅ |
| `test_resume_always_generates_fresh_uuid` | Multiple resumes each get unique IDs | ✅ |
| `test_resume_message_content_preserved` | Default and custom message text preserved | ✅ |
| `test_resume_silent_mode_no_message_id_reuse` | Silent mode still uses fresh UUID | ✅ |
| `test_resume_with_no_existing_message_id_in_metadata` | Edge: None metadata still works | ✅ |
| `test_resume_does_not_reuse_old_message_id` | Core regression: old ID never reused | ✅ |
| `test_resume_returns_old_message_id_in_result` | Result dict contains old ID for completion | ✅ |
| `test_resume_with_empty_metadata_still_works` | Edge: empty metadata dict | ✅ |

## Pre-existing Issue (Not Related)

- `tests/unit/services/test_title_generation_trigger.py::test_send_message_triggers_title_on_cancelled_error`
- `CancelledError` mocking issue in async context — existed before this fix
- Last modified in commit `fb8f5a0` — unrelated to resume changes

## Quick Fixes Applied

None needed — all tests passed on first run.

## ensure.md Validation

- dev.sh ran for full 30 seconds without crash
- Exit code 124 (timeout) — expected
- Clean startup, MCP servers warmed up, 4 workers healthy

## Overall Status

✅ **READY** — Resume message append fix verified with 8 new targeted tests. Zero regressions across API, core, and frontend test suites. dev.sh stable.
