# Instance Hard Delete Feature — Testing Session
Date: 2026-07-13

## Feature
DELETE /api/instances/{id}?hard_delete=true — tree-aware cascade hard-delete across 10 DB tables.

## Key Findings

### 1. Tree IDs MUST be snapshotted before terminate_instance()
The existing test suite (tests/test_instance_hard_delete.py) already covers this critical architectural point: `_terminate_instance_db_sync` Step 5 deletes `instance_hierarchy WHERE parent_id = :iid`, which would cause `get_tree_ids()` to return only root after termination. The hard_delete_instance() implementation correctly snapshots tree_ids BEFORE calling terminate_instance(). Confirmed by TestEmptyTreeFallback.

### 2. FK-safe cascade order is correct and validated
The 10-table cascade order (job_locks → job_queue_items → job_watchers → dependency_watchers → instance_mappings → tasks → events → message_queue → instance_hierarchy → instances) was validated by TWO independent test approaches:
- TestFKCascadeOrder: proves naive deletion violates JobWatcher FK, hard_delete_tree succeeds
- test_real_fk_relationships_do_not_raise (new mock test): seeds rows with REAL FKs and confirms cascade completes without IntegrityError

### 3. Checkpoint cleanup is truly best-effort
test_checkpoint_failure_does_not_block_db_cascade mocks CheckpointerAdapter.adelete_thread to raise. The DB cascade still completes and failed thread IDs surface in checkpoint_errors. This confirms the design intent: checkpoint cleanup failures do NOT block the destructive cascade.

### 4. Hard-delete is NOT gated on instance status
test_terminated_instance_with_dependents_hard_deletes_clean confirms that instances with status='terminated' can still be hard-deleted. The cascade is not conditional on instance state.

### 5. No production bugs found
All 7 mock test scenarios + 12 existing unit tests + 66 concurrency tests + 35 regression tests passed against production code. No quick fixes were needed.

## Test Architecture Notes
- The mock tests use real in-memory SQLite with StaticPool (cross-thread safety) and FK enforcement, mirroring tests/test_instance_cascade.py fixtures.
- Frontend dialog tests use a TestableXxxComponent pattern (hand-rolled mirror, no TestBed) — consistent with mcp-server-dialog.component.spec.ts pattern.
- Two new pack scripts created (hard_delete_unit_test.sh, hard_delete_mock_test.sh) following the project's established pack convention.

## Commits
- 3618566b — test(packs): add hard_delete_unit_test pack
- 12973395 — test(hard-delete): add 3-level tree cascade + checkpoint cleanup mock pack
- Frontend spec committed by frontend-pack session
