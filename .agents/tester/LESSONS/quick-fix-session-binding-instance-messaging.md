# Quick Fix: Session Binding Error in enqueue_message

**Date**: 2026-05-23
**File**: `daemon/services/instance_messaging.py`
**Commit**: c1b860152acb59424b86e94bea0841c6bd0ad16d

## Issue
`POST /api/instances/:id/messages` returned HTTP 500:
```
"Instance <Instance at 0x...> is not bound to a Session; attribute refresh operation cannot proceed"
```

## Root Cause
In `enqueue_message()`, the `Instance` ORM object was fetched inside a `with Session(...)` block. After the block exited (session closed), line ~649 accessed `instance.agent_id` which triggered SQLAlchemy's lazy-load/refresh on a detached object.

## Fix
Capture `instance.agent_id` while the session is still active:
```python
instance_agent_id = None
instance = session.get(Instance, instance_id)
if instance:
    instance_agent_id = instance.agent_id  # Captured while session active
    ...
# After session closes:
agent_id=instance_agent_id  # Uses captured value instead of instance.agent_id
```

## Lesson
When using SQLAlchemy/SQLModel with session scope (`with Session(...) as session:`), always capture any ORM attributes needed outside the block BEFORE the block exits. Detached ORM objects cannot lazy-load attributes.
