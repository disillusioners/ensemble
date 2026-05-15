# Plan: Stopped Instance Memory Leak Mitigation

## Problem Statement

`stop_instance_cascade()` intentionally preserves in-memory instances for fast resumption. However, if an instance is stopped and never resumed or explicitly terminated, it remains in memory indefinitely, causing a memory leak.

### Current Behavior

When `stop_instance_cascade()` is called on an instance:

1. **In-memory object retained** — Instance stays in `manager.instances` dict
2. **Project locks retained** — Job lock is NOT released
3. **DB status set to IDLE** — Instance can be resumed
4. **Graph object retained** — Full conversation history kept in memory

```python
# From instance_lifecycle.py:457-461
# NOTE: Unlike terminate_instance, we do NOT:
# - Remove from instances dict (instance stays in memory, resumable)
# - Release project locks (job continues)
# - Mark jobs as cancelled
```

### Impact Analysis

| Issue | Severity | Description |
|-------|----------|-------------|
| Memory growth | High | Each stopped instance retains full graph with conversation history |
| Blocked spawns | High | Stopped instances count toward `max_instances` limit |
| Lock contention | Medium | Project locks held by abandoned instances prevent other jobs |
| No auto-cleanup | High | No TTL, LRU, or any eviction mechanism |

### Failure Scenario

1. User spawns 10 instances for a project
2. Each performs long-running work with large conversation context
3. User stops all 10 instances (e.g., to pause work)
4. User spawns 10 new instances elsewhere
5. `max_instances` reached (e.g., limit is 20)
6. Original 10 stopped instances are never resumed or terminated
7. **Result**: 10 abandoned instances consuming memory forever, blocking new spawns

## Proposed Solutions

### Solution 1: TTL-Based Auto-Termination

Add a configurable TTL for stopped instances. After the timeout expires, the instance is automatically terminated.

**Pros:**
- Simple to implement and understand
- Provides predictable memory bounds
- Configurable per deployment

**Cons:**
- May terminate instances the user intended to keep
- Requires background cleanup task or lazy evaluation
- TTL value is deployment-specific (hard to tune)

**Configuration:**
```yaml
limits:
  max_instances: 20
  stopped_instance_ttl_minutes: 60  # New: auto-terminate after 1 hour idle
```

---

### Solution 2: LRU Eviction on Max Instances

When `max_instances` is reached during spawn, evict the oldest stopped instance before rejecting the new spawn.

**Pros:**
- Defers cleanup until necessary
- Preserves stopped instances when memory is available
- Simple change to spawn logic

**Cons:**
- Doesn't provide proactive memory management
- Eviction is unpredictable (depends on spawn order)
- Still retains abandoned instances indefinitely if under limit

**Implementation point:**
```python
# In spawn_instance() before the max_instances check:
if current_instance_count >= self._config.limits.max_instances:
    # Try to evict oldest stopped instance
    oldest_stopped = find_oldest_stopped_instance()
    if oldest_stopped:
        await self.terminate_instance(oldest_stopped.id)
    else:
        raise ValueError(f"Max instances limit reached...")
```

---

### Solution 3: Explicit Cleanup Path (Documentation Only)

Document that `stop` is NOT a cleanup operation and users MUST call `terminate` to release resources.

**Pros:**
- No code changes required
- Clear user expectation

**Cons:**
- Relies on user discipline
- Easy to forget and cause production issues
- Doesn't solve the root problem

---

### Solution 4: Hybrid: TTL + Manual Override

Combine TTL-based auto-termination with a flag to mark instances as "pinned" (exempt from TTL eviction).

**Pros:**
- Flexible — most instances auto-cleanup, important ones can be preserved
- Balances automatic cleanup with user control

**Cons:**
- More complex implementation
- Requires new API to pin/unpin instances
- UI implications if frontend needs to show pin status

**Metadata field:**
```python
instance_metadata["pinned"] = True  # Exempt from TTL eviction
```

---

## Recommendation

**Implement Solution 1 (TTL-Based Auto-Termination)** as the primary mitigation, with Solution 4 (Hybrid) as a future enhancement if needed.

### Rationale

1. **Predictable memory bounds** — TTL ensures abandoned instances don't grow unbounded
2. **Simple to reason about** — Instance lives for N minutes after stopping, then auto-cleans
3. **Low implementation complexity** — Can reuse existing `terminate_instance()` logic
4. **Configurable** — Different deployments can tune TTL to their needs

### Implementation Sketch

1. Add `stopped_instance_ttl_minutes` to `limits` config
2. Store `stopped_at` timestamp on instance metadata when stopped
3. Background task or lazy evaluation:
   - Query instances with `status=IDLE` and `stopped_at < now - TTL`
   - Call `terminate_instance()` on each
4. Consider `warn_on_stopped_instances_threshold` for monitoring

### Backward Compatibility

- Default TTL can be `None` (no auto-cleanup) to preserve current behavior
- Existing stopped instances continue as-is until first TTL check
- Migration path: users can set TTL to合理的值 and rely on auto-cleanup

## Open Questions for Team

1. Should TTL check be background task (periodic) or lazy (on spawn/access)?
2. What should the default TTL be? (建议: 60 minutes)
3. Should we emit metrics/events when instances are auto-terminated?
4. Do we need admin API to list/view/cull stopped instances manually?
5. Should stopped instances have a separate `status` (e.g., `STOPPED`) distinct from `IDLE`?

## Files to Modify (Implementation Phase)

- `daemon/config.py` — Add new config field
- `daemon/models/instance.py` — Add `stopped_at` field to metadata
- `daemon/services/instance_lifecycle.py` — Record `stopped_at` on stop, implement cleanup
- `tests/` — Add tests for TTL behavior
- `config.yaml` — Document new field

## References

- Current `stop_instance_cascade` implementation: `daemon/services/instance_lifecycle.py:387-495`
- `terminate_instance` cleanup behavior: `daemon/services/instance_lifecycle.py:285-385`
- `max_instances` limit enforcement: `daemon/services/instance_lifecycle.py:148-153`
