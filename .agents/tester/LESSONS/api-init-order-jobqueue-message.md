# Lesson: API Init Order Bug in JobQueue Message Handler

**Date**: 2026-05-25
**Feature**: Route HTTP API Messages Through JobQueue
**Commit**: daf846e

## Issue
`daemon/api.py` called `job_processor.setup_message_job_handler()` BEFORE `job_processor` (a `JobProcessor` instance) was defined. This caused a `NameError` at runtime when the app started with the new feature.

## Root Cause
The setup call was placed at the wrong location during Phase 2 wiring. It was placed near the top of the app factory function, but `JobProcessor` was instantiated later.

## Fix
Moved `job_processor.setup_message_job_handler()` to after the `JobProcessor` initialization block.

## Lesson
When wiring new handlers in `daemon/api.py`, always verify that the target object exists at the point of call. The file has specific initialization ordering that must be respected.

## Also Found
Missing DB migration for `job_queue_items.job_type` column. The Phase 1 code added `job_type` to the Pydantic model but no SQL migration was created, causing `dev.sh` to crash on startup.
