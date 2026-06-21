# E2E Workflow Tests — Created Not Run

## Date
2026-06-21

## Context
User requested 3 critical E2E test cases simulating the most frequent user workflows against the live daemon (dev.sh at http://localhost:8079).

## What Was Created
- **File**: `tests/e2e/test_e2e_workflows.py` (742 lines, 3 tests)
- **Commits**: `e03b0aa2` (test file), `e9f56b7e` (ensure.md update)

## The 3 E2E Tests

### 1. `test_parent_child_workflow_happy_path`
- Spawns leader, sends "ask coder to say hello" message
- Waits for coder child to spawn (polls GET /api/instances/{id} for `children` field)
- Waits for leader to reach terminal status
- Verifies assistant message exists in history
- Cleanup in finally block

### 2. `test_pause_after_spawn_then_resume`
- Same workflow but pauses after coder child spawns
- Verifies both leader and coder are "paused"
- Waits 5s to verify no processing happens
- Resumes, verifies workflow completes
- Cleanup in finally block

### 3. `test_terminate_after_spawn_then_revive`
- Same workflow but terminates after coder child spawns
- Verifies termination succeeds (hard assertion)
- Sends "continue" message — documents actual behavior (soft assertion)
- Polls 30s to observe status changes
- Cleanup in finally block

## Key Design Decisions

### Instance Response Schema
- `InstanceInfo` model uses `children: list[str]` for child instance IDs
- Code defensively checks `child_ids` and `child_instances` too (in case schema evolves)

### Terminal Status Set
- `{completed, terminated, error, failed}` — from `InstanceStatus` enum
- Excluded non-terminal statuses: `paused`, `queued`, `running`, etc.

### Test Markers
- `pytest.mark.integration` — excluded from default runs (matches project convention)
- `pytest.mark.skipif(not _daemon_running())` — skips gracefully when daemon not running

### Timeouts (generous — real LLM calls)
- `SPAWN_TIMEOUT = 60s` — wait for child to spawn
- `COMPLETION_TIMEOUT = 120s` — wait for workflow completion
- `POLL_INTERVAL = 3s`

## NOT RUN — Pending enqueued_at Fix
Tests were created but NOT run yet. The user stated the enqueued_at bug fix is still in progress (Phase D dependency_watchers column bug). Tests should be run after the fix lands.

## Run Command
```bash
# Requires daemon running via ./dev.sh
python -m pytest tests/e2e/test_e2e_workflows.py -v -m integration
```

## Gotchas
- These tests use REAL LLM calls — not mocked. The leader actually processes the message with an LLM.
- The test message "ask coder to say hello, this is a test workflow, coder dont need do anything" is specifically designed to trigger the leader→coder spawn workflow.
- All cleanup must be in finally blocks — never leave instances running.
- The daemon MUST be running before these tests execute (skipped otherwise via skipif marker).
