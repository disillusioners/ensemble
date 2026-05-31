# MaintenanceService + CheckpointCleanup — Testing Lessons

## Date: 2026-05-31
## Branch: feature/checkpoint-cleanup

## Quick Fix: Config Field Rename
- **File**: 7 files (config.yaml, conftest.py, test_config.py, test_manager.py, test_progressive_dispatch.py, test_spawn_limit_edge_cases.py, test_mcp_cold_load_race.py)
- **Commit**: `09f7853`
- **Root cause**: Feature renamed `checkpoint_max_count` to `max_instance_history` in PersistenceConfig but didn't update YAML and test fixtures
- **Lesson**: When renaming model fields, always grep for old name across ALL files (config, tests, docs)

## Architecture Notes
- MaintenanceService is a generic background job runner with idle detection
- CheckpointCleanupJob registers 4 operations (A: orphaned threads, B: TTL expired, C: history cap, D: per-thread pruning)
- Service integrates with daemon lifecycle (start in initialize(), stop in shutdown())
- Idle check considers both job queue state and active LLM requests

## Edge Case Gotchas
1. **No explicit checkpointer init check** — Code relies on generic try/except instead of explicit `checkpointer.conn is not None` check
2. **Pagination race in orphan detection** — `_get_all_instance_ids()` paginates but total count could change during iteration
3. **Mid-cleanup stop** — Task cancellation is handled but partial SQL transactions are possible
