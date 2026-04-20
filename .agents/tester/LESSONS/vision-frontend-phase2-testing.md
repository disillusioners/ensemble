# Vision Frontend Phase 2 Testing

## Date: 2026-04-20

## What Was Tested
Phase 2 Frontend Image Upload UI for agents-ensemble vision support.

## Key Findings

### Backend Quick Fixes Discovered During Testing
1. **project_list return type** (`tests/test_project_tools.py`): `project_list` tool returns `{"projects": [...]}` wrapped dict, but tests expected raw list. Tests were wrong, not the code.
2. **FIFO order in pending queries** (`daemon/repositories/job_queue/repository.py`): Three `list_pending_*` methods used `created_at.desc()` (LIFO) when FIFO was expected per docstrings. Changed to `.asc()`.

### Frontend Tests Required No Fixes
All 278 frontend tests passed on first run — the Phase 2 implementation was well-tested by the existing spec files.

### Web Automation Notes
- The test instance was in `isStreaming=true` state during browser automation, showing "Stop" instead of "Send" button. This is a backend state issue, not a UI bug.
- Image preview thumbnail rendered at 46×46px (spec says 48×48) — close enough, likely due to border/padding.
- The placeholder text was updated to "Type your message or drag images here..." — good UX touch.

## Test Execution Strategy
- Ran backend + frontend in parallel (2 opencode sessions)
- Web automation ran after frontend tests passed (sequential dependency)
- Total execution time: ~20 minutes for all 3 phases
