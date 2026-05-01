# Instance Title Generation Fix — 2026-05-01

## Bug
Frontend instance list showed shortened UUIDs instead of generated titles.

## Root Cause
Title generation service (`daemon/services/title_generation.py`) was **fully implemented but never called**. The method `_generate_and_broadcast_title` had zero callers in the entire codebase.

## Data Flow (all correct, just missing the trigger)
- **Backend Model**: `instance_metadata["title"]` property exists on Instance model
- **Pydantic Schema**: `InstanceInfo.title: str | None` exists
- **API Endpoint**: `/instances` returns title in response
- **Frontend**: Template uses `instance.title || getInstanceIdShort(...)` — correct fallback

## Fix
Added `_trigger_title_generation()` helper in `daemon/services/child_reports.py` that:
1. Gets the original user message content from queue repository
2. Uses `MainLoopBridge.run_async_no_wait()` to fire-and-forget the title generation
3. Called in 3 mutually-exclusive instance completion code paths (no parent, tool invocation, child with parent)

## Key Design Points
- **Fire-and-forget**: Non-blocking, uses existing MainLoopBridge pattern
- **Idempotent**: `_generate_and_broadcast_title` already checks if title exists before generating
- **No circular imports**: MainLoopBridge is a standalone service
- **Error handling**: Exception logging via `_log_exception` callback

## Commit
`f74f6fb` on `fix/instance-list-title`
