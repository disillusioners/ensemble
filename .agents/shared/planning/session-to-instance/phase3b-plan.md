# Phase 3b: Infrastructure — Events & Queue (SSE, Message Queue, Circuit Breaker)

## Objective
Rename all session references in `daemon/events.py` and `daemon/queue.py`. These are the core infrastructure for SSE event routing per agent instance and per-instance message delivery with circuit breaker protection.

## Context
- **Phase 3a completed**: InstanceManager renamed, spawn_instance/terminate_instance methods renamed
- `events.py` and `queue.py` are independent of manager imports — they use `session_id` as a dict key for routing, not as a typed model reference
- Both are called by `manager.py` (Phase 3a) and `api.py` (Phase 6)
- This phase can run in parallel with Phases 3c, 4, and 5

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename daemon/events.py** (~427 lines) | Rename dataclass field: `Event.session_id`→`Event.instance_id`. Rename methods: `get_queue(session_id)`→`get_queue(instance_id)`, `cleanup_session(session_id)`→`cleanup_instance(instance_id)`, `get_events_since(session_id)`→`get_events_since(instance_id)`, `clear_queue(session_id)`→`clear_queue(instance_id)`, `get_stats(session_id)`→`get_stats(instance_id)`. Update all internal dict keys: `_queues[session_id]`→`_queues[instance_id]`, `_subscribers[session_id]`→`_subscribers[instance_id]`, etc. | `daemon/events.py` (~427 lines) |
| 2 | **Rename daemon/queue.py** (~455 lines) | Rename dataclass field: `QueuedMessage.session_id`→`QueuedMessage.instance_id`. Rename methods: `dequeue(session_id)`→`dequeue(instance_id)`, `enqueue(session_id)`→`enqueue(instance_id)`, `_peek_ready_message(session_id)`→`_peek_ready_message(instance_id)`, `is_empty(session_id)`→`is_empty(instance_id)`, `get_stats(session_id)`→`get_stats(instance_id)`, `dequeue_by_session(session_id)`→`dequeue_by_instance(instance_id)`. Rename class: `SessionCircuitBreaker`→`InstanceCircuitBreaker`. Update all internal dicts: `_states[session_id]`→`_states[instance_id]`, `_failure_counts[session_id]`→`_failure_counts[instance_id]`, etc. Rename method params in circuit breaker: `can_execute(session_id)`→`can_execute(instance_id)`, `record_success(session_id)`→`record_success(instance_id)`, `record_failure(session_id)`→`record_failure(instance_id)`. Also check for `SessionWatchdog` class if present. | `daemon/queue.py` (~455 lines) |

## Key Files
- `daemon/events.py` — SSE event broadcasting per agent instance (~427 lines)
- `daemon/queue.py` — Message queue with circuit breaker per agent instance (~455 lines)

## Detailed Rename Map

### events.py
| Old | New |
|-----|-----|
| `Event.session_id` field | `Event.instance_id` |
| `get_queue(self, session_id)` | `get_queue(self, instance_id)` |
| `cleanup_session(self, session_id)` | `cleanup_instance(self, instance_id)` |
| `get_events_since(self, session_id, ...)` | `get_events_since(self, instance_id, ...)` |
| `clear_queue(self, session_id)` | `clear_queue(self, instance_id)` |
| `get_stats(self, session_id)` | `get_stats(self, instance_id)` |
| `_queues[session_id]` | `_queues[instance_id]` |
| `_subscribers[session_id]` | `_subscribers[instance_id]` |

### queue.py
| Old | New |
|-----|-----|
| `QueuedMessage.session_id` | `QueuedMessage.instance_id` |
| `SessionCircuitBreaker` | `InstanceCircuitBreaker` |
| `dequeue(session_id, ...)` | `dequeue(instance_id, ...)` |
| `enqueue(session_id, ...)` | `enqueue(instance_id, ...)` |
| `_peek_ready_message(session_id)` | `_peek_ready_message(instance_id)` |
| `is_empty(session_id)` | `is_empty(instance_id)` |
| `get_stats(session_id)` | `get_stats(instance_id)` |
| `dequeue_by_session(session_id)` | `dequeue_by_instance(instance_id)` |
| `_states[session_id]` | `_states[instance_id]` |
| `_failure_counts[session_id]` | `_failure_counts[instance_id]` |
| `_last_failure_time[session_id]` | `_last_failure_time[instance_id]` |
| `can_execute(session_id)` | `can_execute(instance_id)` |
| `record_success(session_id)` | `record_success(instance_id)` |
| `record_failure(session_id)` | `record_failure(instance_id)` |

## Constraints
- Neither file imports `SessionManager` — they use `session_id` as a plain string key
- Both files are called by `manager.py` (Phase 3a) with method names that may have already changed. **Verify method call sites match**: if manager.py calls `self._events.cleanup_session(instance_id)`, events.py must have `cleanup_instance(instance_id)`.
- The `cleanup_session` method name is an important one — it's called from manager's terminate flow
- `SessionCircuitBreaker` is a class rename, not just a method — check if it's exported or referenced elsewhere

## Verification
```bash
# 1. No old names in events/queue files
grep -rn "session_id\|cleanup_session\|dequeue_by_session\|SessionCircuitBreaker\|SessionWatchdog" daemon/events.py daemon/queue.py | grep -v "db_session"

# 2. New names present
grep -c "instance_id\|cleanup_instance\|dequeue_by_instance\|InstanceCircuitBreaker" daemon/events.py daemon/queue.py

# 3. Class name not exported elsewhere
grep -rn "SessionCircuitBreaker\|from.*queue.*import.*Session" daemon/
```

## Deliverables
- [ ] `daemon/events.py` — Event.instance_id, all methods renamed, internal dicts updated
- [ ] `daemon/queue.py` — QueuedMessage.instance_id, all methods renamed, InstanceCircuitBreaker
- [ ] Method names match what manager.py (Phase 3a) calls
- [ ] Grep shows 0 old session names in events.py and queue.py
