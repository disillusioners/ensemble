# Scheduler Session Management Feature Design

## Overview

Add configurable session reuse for scheduled tasks. Currently, schedulers always reuse the same session (via LangGraph checkpointing). This proposal adds a `session_mode` option allowing users to choose between:

- **New Session** (default): Spawn fresh session each run
- **Reuse Session**: Reuse same session across runs, with `#N` message prefix

---

## Scheduler Types

| Type | New Session | Reuse Session | Default |
|------|:-----------:|:-------------:|:-------:|
| cron | ✅ | ✅ | New Session |
| interval | ✅ | ✅ | New Session |
| one_time | ✅ | ❌ (forced) | New Session |

---

## Configuration

### New Enum: `SchedulerSessionMode`

**File:** `daemon/models.py`

```python
class SchedulerSessionMode(str, Enum):
    """Session management mode for scheduled executions."""
    
    NEW_SESSION = "new_session"      # Spawn fresh session each run (default)
    REUSE_SESSION = "reuse_session"  # Reuse same session across runs
```

### Scheduler Config Schema

```python
{
    "type": "cron",                    # "cron", "interval", "one_time"
    "schedule": "0 9 * * *",           # Cron expression
    "interval_seconds": 3600,          # Or interval mode
    "run_at": "2025-03-15T10:00:00Z",  # Or one-time mode
    "agent": "./agents/leader",
    "message": "Daily health check",
    "timezone": "UTC",
    
    # NEW
    "session_mode": "new_session",     # "new_session" or "reuse_session"
    
    # Existing
    "project_id": "my-project",
    "priority": 5,
    "max_concurrent": 1,
}
```

---

## Message Formatting

### New Session Mode

```
Daily health check
```

(No prefix, no continuation context)

### Reuse Session Mode

```
#3 Daily health check

---
## Scheduled Task Continuation

This is **run #3** of a recurring scheduled task.

**Context:**
- Mode: Session reuse (incremental work)

**Instructions:**
- Previous runs have built up conversation history you can reference
- Continue the work incrementally, building on earlier progress
- If earlier runs encountered errors, acknowledge them and try alternative approaches
- Summarize key findings/progress at the end for the next run
```

### Template

```python
CONTINUATION_TEMPLATE = """#{run_number} {original_message}

---
## Scheduled Task Continuation

This is **run #{run_number}** of a recurring scheduled task.

**Context:**
- Mode: Session reuse (incremental work)

**Instructions:**
- Previous runs have built up conversation history you can reference
- Continue the work incrementally, building on earlier progress
- If earlier runs encountered errors, acknowledge them and try alternative approaches
- Summarize key findings/progress at the end for the next run
"""
```

---

## Run Counter Storage

**Location:** Stored in scheduler source's `config` field as `_run_counter`

```python
{
    ...schedule_config...,
    "_run_counter": 42  # Internal tracking, auto-incremented
}
```

**Rationale:** Counter belongs to the scheduler, not the session. If a reused session crashes and a new one is created, the counter continues incrementing correctly.

---

## Implementation

### Files to Modify

| File | Changes |
|------|---------|
| `daemon/models.py` | Add `SchedulerSessionMode` enum |
| `daemon/sources/adapters/scheduler.py` | Add `session_mode` parsing, run counter, message formatting |
| `daemon/sources/mapper.py` | Add `force_new` parameter to `get_or_create_session()` |
| `daemon/sources/registry.py` | Pass `force_new_session` from metadata to mapper |
| `daemon/repositories/source/repository.py` | Add `increment_scheduler_run_counter()` method |
| `daemon/api.py` | Validate `session_mode` in schedule create/update |

### 1. Model Changes (`daemon/models.py`)

```python
class SchedulerSessionMode(str, Enum):
    """Session management mode for scheduled executions."""
    
    NEW_SESSION = "new_session"
    REUSE_SESSION = "reuse_session"
```

### 2. Scheduler Adapter (`daemon/sources/adapters/scheduler.py`)

**New methods:**

```python
def _get_session_mode(self) -> SchedulerSessionMode:
    """Get session mode from config, defaulting to NEW_SESSION."""
    mode = self._scheduler_config.get("session_mode", "new_session")
    # One-time schedules always use new session
    if self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
        return SchedulerSessionMode.NEW_SESSION
    return SchedulerSessionMode(mode)

def _increment_run_counter(self) -> int:
    """Atomically increment and return the run counter."""
    return self._source_repo.increment_scheduler_run_counter(self.source_id)

def _format_continuation_message(self, run_number: int, original_message: str) -> str:
    """Format message with continuation context."""
    return CONTINUATION_TEMPLATE.format(
        run_number=run_number,
        original_message=original_message,
    )
```

**Modified `_emit_scheduled_message()`:**

```python
async def _emit_scheduled_message(self) -> None:
    execution_id = str(uuid.uuid4())
    
    async def execute():
        self._running_executions += 1
        try:
            session_mode = self._get_session_mode()
            
            run_number = None
            if session_mode == SchedulerSessionMode.REUSE_SESSION:
                run_number = self._increment_run_counter()
            
            message_content = self._message_content
            if run_number is not None:
                message_content = self._format_continuation_message(
                    run_number=run_number,
                    original_message=self._message_content,
                )
            
            metadata = {
                "scheduler": {
                    "execution_id": execution_id,
                    "schedule_type": self._schedule_type,
                    "trigger_time": datetime.now(self._timezone).isoformat(),
                    "session_mode": session_mode.value,
                    "run_number": run_number,
                },
                "agent": self._agent,
                "force_new_session": (session_mode == SchedulerSessionMode.NEW_SESSION),
            }
            
            await self._message_handler(self._to_message(message_content), metadata)
        finally:
            self._running_executions -= 1
```

### 3. SessionMapper (`daemon/sources/mapper.py`)

```python
async def get_or_create_session(
    self,
    source_id: str,
    external_user_id: str,
    agent_dir: str,
    force_new: bool = False,  # NEW
) -> str:
    """Get existing session or create a new one."""
    
    mapping = self.get_mapping(source_id, external_user_id)
    
    # NEW: Force new session if requested
    if force_new and mapping is not None:
        old_session_id = mapping["agent_session_id"]
        self.source_repo.delete_session_mapping(mapping["mapping_id"])
        logger.info(
            f"Force new session: deleted mapping for {source_id}:{external_user_id}, "
            f"old_session={old_session_id}"
        )
        mapping = None
    
    if mapping is not None:
        return mapping["agent_session_id"]
    
    # ... rest of creation logic ...
```

### 4. SourceRegistry (`daemon/sources/registry.py`)

```python
async def _handle_message(self, source_id: str, msg: IncomingMessage) -> None:
    # ...
    
    # Check for force_new_session flag from scheduler metadata
    force_new = msg.metadata.get("force_new_session", False) if msg.metadata else False
    
    # Get or create the session
    session_id = await mapper.get_or_create_session(
        source_id=source_id,
        external_user_id=msg.external_user_id,
        agent_dir=agent_dir,
        force_new=force_new,
    )
```

### 5. Repository (`daemon/repositories/source/repository.py`)

```python
def increment_scheduler_run_counter(self, source_id: str) -> int:
    """Atomically increment the run counter for a scheduler."""
    with Session(self.engine) as session:
        source = session.exec(
            select(SourceModel).where(SourceModel.source_id == source_id)
        ).first()
        
        if not source:
            raise ValueError(f"Source not found: {source_id}")
        
        config = source.config or {}
        current_counter = config.get("_run_counter", 0)
        new_counter = current_counter + 1
        config["_run_counter"] = new_counter
        
        source.config = config
        session.add(source)
        session.commit()
        
        return new_counter
```

### 6. API Validation (`daemon/api.py`)

```python
def _validate_scheduler_config(config: dict) -> dict:
    """Validate and normalize scheduler config."""
    schedule_type = config.get("type")
    session_mode = config.get("session_mode", "new_session")
    
    # One-time schedules must use new session
    if schedule_type == "one_time":
        config["session_mode"] = "new_session"
    elif session_mode not in ["new_session", "reuse_session"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid session_mode: {session_mode}. "
                   f"Must be 'new_session' or 'reuse_session'."
        )
    
    # Reuse session mode forces max_concurrent=1
    if session_mode == "reuse_session":
        config["max_concurrent"] = 1
    
    return config
```

---

## Edge Cases

### 1. Reused Session Crashes

- Run counter stored in scheduler config (not session) → counter continues correctly
- SessionMapper creates new session automatically
- Agent sees `#N` prefix but no prior context → should adapt

### 2. Mode Switch (reuse → new)

- Next run uses new session
- Old reused session orphaned
- Counter continues (represents scheduler invocations, not session invocations)

### 3. Mode Switch (new → reuse)

- Next run creates/uses persistent session
- Counter starts at 1 (new context for this mode)

### 4. Concurrent Execution

- For `reuse_session` mode, implicitly force `max_concurrent=1`
- Prevents race conditions on shared session state

### 5. One-Time Schedule

- `session_mode` is ignored, always uses new session
- Config normalization enforces this

---

## Testing Checklist

### New Session Mode (Default)
- [ ] Each run creates fresh session
- [ ] No run number prefix
- [ ] No context from previous runs

### Reuse Session Mode
- [ ] Same session used across runs
- [ ] Run number prefix appears (#1, #2, #3)
- [ ] Context persists between runs
- [ ] Counter increments correctly

### One-Time Schedules
- [ ] Always uses new session
- [ ] Validation rejects `reuse_session` mode

### Error Recovery
- [ ] Run counter continues after crash
- [ ] New session created if old one dies
- [ ] Message indicates crash if applicable

---

## Future Considerations (Out of Scope)

1. **Reset counter API**: `POST /schedules/{id}/reset-counter`
2. **Session cleanup**: Remove orphaned sessions when mode changes
3. **Error context**: Include last execution error in continuation message
4. **Configurable template**: Allow users to customize continuation text
