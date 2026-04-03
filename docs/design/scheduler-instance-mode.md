# Scheduler Instance Management Feature Design

## Overview

Add configurable instance reuse for scheduled tasks. Currently, schedulers always reuse the same instance (via LangGraph checkpointing). This proposal adds an `instance_mode` option allowing users to choose between:

- **New Instance** (default): Spawn fresh instance each run
- **Reuse Instance**: Reuse same instance across runs, with `#N` message prefix

---

## Scheduler Types

| Type | New Instance | Reuse Instance | Default |
|------|:-----------:|:-------------:|:-------:|
| cron | ✅ | ✅ | New Instance |
| interval | ✅ | ✅ | New Instance |
| one_time | ✅ | ❌ (forced) | New Instance |

---

## Configuration

### New Enum: `SchedulerInstanceMode`

**File:** `daemon/models.py`

```python
class SchedulerInstanceMode(str, Enum):
    """Instance management mode for scheduled executions."""
    
    NEW_INSTANCE = "new_instance"      # Spawn fresh instance each run (default)
    REUSE_INSTANCE = "reuse_instance"  # Reuse same instance across runs
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
    "instance_mode": "new_instance",   # "new_instance" or "reuse_instance"
    
    # Existing
    "project_id": "my-project",
    "priority": 5,
    "max_concurrent": 1,
}
```

---

## Message Formatting

### New Instance Mode

```
Daily health check
```

(No prefix, no continuation context)

### Reuse Instance Mode

```
#3 Daily health check

---
## Scheduled Task Continuation

This is **run #3** of a recurring scheduled task.

**Context:**
- Mode: Instance reuse (incremental work)

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
- Mode: Instance reuse (incremental work)

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

**Rationale:** Counter belongs to the scheduler, not the instance. If a reused instance crashes and a new one is created, the counter continues incrementing correctly.

---

## Implementation

### Files to Modify

| File | Changes |
|------|---------|
| `daemon/models.py` | Add `SchedulerInstanceMode` enum |
| `daemon/sources/adapters/scheduler.py` | Add `instance_mode` parsing, run counter, message formatting |
| `daemon/sources/mapper.py` | Add `force_new` parameter to `get_or_create_instance()` |
| `daemon/sources/registry.py` | Pass `force_new_instance` from metadata to mapper |
| `daemon/repositories/source/repository.py` | Add `increment_scheduler_run_counter()` method |
| `daemon/api.py` | Validate `instance_mode` in schedule create/update |

### 1. Model Changes (`daemon/models.py`)

```python
class SchedulerInstanceMode(str, Enum):
    """Instance management mode for scheduled executions."""
    
    NEW_INSTANCE = "new_instance"
    REUSE_INSTANCE = "reuse_instance"
```

### 2. Scheduler Adapter (`daemon/sources/adapters/scheduler.py`)

**New methods:**

```python
def _get_instance_mode(self) -> SchedulerInstanceMode:
    """Get instance mode from config, defaulting to NEW_INSTANCE."""
    mode = self._scheduler_config.get("instance_mode", "new_instance")
    # One-time schedules always use new instance
    if self._schedule_type == self.SCHEDULE_TYPE_ONE_TIME:
        return SchedulerInstanceMode.NEW_INSTANCE
    return SchedulerInstanceMode(mode)

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
            instance_mode = self._get_instance_mode()
            
            run_number = None
            if instance_mode == SchedulerInstanceMode.REUSE_INSTANCE:
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
                    "instance_mode": instance_mode.value,
                    "run_number": run_number,
                },
                "agent": self._agent,
                "force_new_instance": (instance_mode == SchedulerInstanceMode.NEW_INSTANCE),
            }
            
            await self._message_handler(self._to_message(message_content), metadata)
        finally:
            self._running_executions -= 1
```

### 3. InstanceMapper (`daemon/sources/mapper.py`)

```python
async def get_or_create_instance(
    self,
    source_id: str,
    external_user_id: str,
    agent_dir: str,
    force_new: bool = False,  # NEW
) -> str:
    """Get existing instance or create a new one."""
    
    mapping = self.get_mapping(source_id, external_user_id)
    
    # NEW: Force new instance if requested
    if force_new and mapping is not None:
        old_instance_id = mapping["agent_instance_id"]
        self.source_repo.delete_instance_mapping(mapping["mapping_id"])
        logger.info(
            f"Force new instance: deleted mapping for {source_id}:{external_user_id}, "
            f"old_instance={old_instance_id}"
        )
        mapping = None
    
    if mapping is not None:
        return mapping["agent_instance_id"]
    
    # ... rest of creation logic ...
```

### 4. SourceRegistry (`daemon/sources/registry.py`)

```python
async def _handle_message(self, source_id: str, msg: IncomingMessage) -> None:
    # ...
    
    # Check for force_new_instance flag from scheduler metadata
    force_new = msg.metadata.get("force_new_instance", False) if msg.metadata else False
    
    # Get or create the instance
    instance_id = await mapper.get_or_create_instance(
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
    instance_mode = config.get("instance_mode", "new_instance")
    
    # One-time schedules must use new instance
    if schedule_type == "one_time":
        config["instance_mode"] = "new_instance"
    elif instance_mode not in ["new_instance", "reuse_instance"]:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid instance_mode: {instance_mode}. "
                   f"Must be 'new_instance' or 'reuse_instance'."
        )
    
    # Reuse instance mode forces max_concurrent=1
    if instance_mode == "reuse_instance":
        config["max_concurrent"] = 1
    
    return config
```

---

## Edge Cases

### 1. Reused Instance Crashes

- Run counter stored in scheduler config (not instance) → counter continues correctly
- InstanceMapper creates new instance automatically
- Agent sees `#N` prefix but no prior context → should adapt

### 2. Mode Switch (reuse → new)

- Next run uses new instance
- Old reused instance orphaned
- Counter continues (represents scheduler invocations, not instance invocations)

### 3. Mode Switch (new → reuse)

- Next run creates/uses persistent instance
- Counter starts at 1 (new context for this mode)

### 4. Concurrent Execution

- For `reuse_instance` mode, implicitly force `max_concurrent=1`
- Prevents race conditions on shared instance state

### 5. One-Time Schedule

- `instance_mode` is ignored, always uses new instance
- Config normalization enforces this

---

## Testing Checklist

### New Instance Mode (Default)
- [ ] Each run creates fresh instance
- [ ] No run number prefix
- [ ] No context from previous runs

### Reuse Instance Mode
- [ ] Same instance used across runs
- [ ] Run number prefix appears (#1, #2, #3)
- [ ] Context persists between runs
- [ ] Counter increments correctly

### One-Time Schedules
- [ ] Always uses new instance
- [ ] Validation rejects `reuse_instance` mode

### Error Recovery
- [ ] Run counter continues after crash
- [ ] New instance created if old one dies
- [ ] Message indicates crash if applicable

---

## Future Considerations (Out of Scope)

1. **Reset counter API**: `POST /schedules/{id}/reset-counter`
2. **Instance cleanup**: Remove orphaned instances when mode changes
3. **Error context**: Include last execution error in continuation message
4. **Configurable template**: Allow users to customize continuation text
