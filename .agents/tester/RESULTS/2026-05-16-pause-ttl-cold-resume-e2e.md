# Pause TTL + Cold Resume E2E Test Report

**Date**: 2026-05-16
**Test Type**: E2E (Mock/Integration)
**Script**: `test/packs/pause_ttl_cold_resume_e2e_test.py`
**Result**: ✅ PASS

## Summary
All 9 test steps passed. The Pause TTL + Cold Resume flow works correctly.

## Test Scenario
1. Start daemon, create instance, send initial message
2. Pause the instance via API
3. Verify `paused_at` is set in SQLite DB
4. Stop daemon (simulates TTL expiry — graph removed from memory)
5. Restart daemon (fresh process — no in-memory graphs)
6. Send message to the paused instance (triggers cold resume)
7. Verify status transitions: `paused → running → completed`
8. Verify `paused_at` is cleared (NULL) after resume

## Detailed Results

| Step | Description | Result | Evidence |
|------|-------------|--------|----------|
| 1 | Start Daemon | ✅ PASS | PID=67251, health check passed |
| 2 | Create Instance | ✅ PASS | instance_id=b0d00b23-..., status=idle |
| 3 | Send Initial Message | ✅ PASS | message accepted, LLM processed |
| 4 | Pause Instance | ✅ PASS | paused_ids=['b0d00b23...'], API status=paused |
| 5 | Check paused_at in DB | ✅ PASS | paused_at=2026-05-16T08:25:02.248615, status=paused |
| 6 | Stop Daemon | ✅ PASS | Daemon killed (PIDs cleaned up) |
| 7 | Restart Daemon | ✅ PASS | PID=68863 (new PID = fresh process), health OK |
| 8 | Cold Resume via Message | ✅ PASS | Message accepted, LLM invoked with 4 messages (restored from checkpoint) |
| 9 | Verify Final Status | ✅ PASS | status=completed, paused_at=None (cleared) |

## Cold Resume Evidence
- **Daemon restart**: New PID (68863 vs 67251) confirms fresh process
- **Checkpoint restoration**: LLM invoked with 4 messages (restored from DB checkpoints, not from initial 1-message state)
- **Status transition**: paused → completed (not stuck at paused)
- **paused_at cleared**: NULL in DB after resume

## Code Path Verified
1. `POST /api/instances/{id}/pause` → `pause_instance_cascade()` → sets `paused_at` in DB
2. Daemon restart → all in-memory graphs lost (simulates TTL expiry)
3. `POST /api/instances/{id}/messages` → `enqueue_message()` → updates status from paused→running
4. Worker picks up message → `_process_message_internal()` → calls `get_instance()`
5. `get_instance()` → not in memory → `_restore_instance()` → rebuilds graph from DB metadata + LangGraph checkpoints
6. LLM processes message → instance completes → status=completed, paused_at=NULL

## Configuration
- **Port**: 8079 (dev.sh default)
- **Agent**: leader (simple agent for testing)
- **DB**: `data_dev/instances.db` (SQLite)
- **TTL**: PAUSED_INSTANCE_TTL_MINUTES=30 (not actually waited — simulated via daemon restart)
- **LLM**: glm-5 (configured model)

## Notes
- The test uses daemon restart to simulate TTL expiry rather than waiting 30 minutes
- This is a valid simulation because daemon restart has the same effect as TTL expiry: graph removed from memory
- The `_restore_instance()` path is the same regardless of how the graph was removed from memory
