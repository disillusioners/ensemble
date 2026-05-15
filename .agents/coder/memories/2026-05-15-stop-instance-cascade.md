# Stop Instance with Child Cascade — Implementation Notes

## Feature Summary
Enhanced `POST /instances/{id}/stop` to cascade to all child instances recursively (DFS).

## Architecture
- **Service method:** `InstanceLifecycleService.stop_instance_cascade()` in `daemon/services/instance_lifecycle.py`
- **Manager delegation:** `InstanceManager.stop_instance_cascade()` delegates to lifecycle service
- **Route:** `POST /instances/{id}/stop` in `daemon/routers/instances.py` calls manager method

## Key Patterns
1. **DFS cascade:** Same pattern as `terminate_instance` — recurse to children first, then stop self
2. **Soft stop only:** Cancels requests via `request_registry.cancel_by_instance()`, updates DB status to `idle`. Does NOT remove from memory, release locks, or clean up watches
3. **Instance.children field:** Stored as JSON string in DB, but `_enrich_instances()` converts to list on read. Always check with `if meta and meta.children:`
4. **prefetched_meta optimization:** `_stop_single()` helper accepts pre-fetched metadata to avoid redundant DB lookups for the root instance
5. **CancellationReason.USER_STOPPED:** The appropriate reason for user-initiated stops (vs SESSION_TERMINATED for terminate)

## Response Format
```json
{
  "stopped": true,
  "stopped_ids": ["parent-id", "child1-id"],
  "skipped_ids": ["already-idle-child-id"]
}
```

## Test Coverage
- 8 unit tests for lifecycle service (single, children, grandchildren, idle, mixed, not-found, cancellation reason)
- 2 API tests (404, success with cascade response)
- 1 manager delegation test
- Total: 11 tests, all passing

## Commit
- Branch: `feature/stop-instance-cascade`
- Hash: `19f66e2`
