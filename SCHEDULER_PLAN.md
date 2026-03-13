# Scheduler Feature Plan

## Overview

Add a **scheduler** feature that triggers agents with messages on a schedule. Works like a "source" but triggers from internal timers instead of external events (Telegram, webhook).

## Goals

- Schedule messages to be sent to agents at specific times/intervals
- Support **both cron expressions AND interval-based scheduling**
- Reuse existing source infrastructure for consistency
- Full CRUD API for managing scheduled jobs
- Persist schedules in database for crash recovery
- **Track execution history** (when jobs ran)

## Design Decision: Scheduler as a Source Type

**Decision:** Implement scheduler as a new `SourceType.scheduler`

**Rationale:**
1. Reuses existing infrastructure (SourceRegistry, SessionMapper, queue)
2. Consistent API patterns (CRUD, start/stop)
3. SchedulerAdapter follows same interface as TelegramAdapter
4. No new concepts for users to learn

**How it works:**
```
SchedulerAdapter (internal timer)
    ↓ (on schedule trigger)
    _emit_message(IncomingMessage)
    ↓
SourceRegistry._handle_message()
    ↓
Queue → Agent processes → Response
```

## Features

### Scheduling Options

| Type | Config Key | Example | Description |
|------|------------|---------|-------------|
| `cron` | `schedule` | `0 9 * * 1-5` | Cron expression (9 AM weekdays) |
| `interval` | `interval_seconds` | `300` | Every N seconds |
| `once` | `run_at` | `2025-03-15T10:00:00Z` | One-time trigger at specific time |

### Job Configuration

```json
{
  "source_id": "morning-briefing",
  "source_type": "scheduler",
  "name": "Morning Briefing",
  "config": {
    "schedule": "0 8 * * 1-5",      // cron OR
    "interval_seconds": 3600,        // interval (one or the other)
    "agent": "leader",               // agent to trigger
    "message": "Generate morning briefing",  // message to send
    "timezone": "UTC",               // optional, default UTC
    "max_concurrent": 1              // don't trigger if previous running
  },
  "enabled": true
}
```

## User Decisions

| Question | Answer |
|----------|--------|
| Cron AND interval? | **Both** |
| Execution history tracking? | **Yes** |
| Missed schedules on restart? | **No** |

## Database Schema

### schedule_executions Table

```sql
CREATE TABLE schedule_executions (
    execution_id TEXT PRIMARY KEY,
    schedule_id TEXT NOT NULL,
    triggered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    session_id TEXT,           -- Session that was triggered
    status TEXT NOT NULL,      -- 'triggered', 'completed', 'failed'
    error_message TEXT,
    completed_at TIMESTAMP,
    FOREIGN KEY (schedule_id) REFERENCES source_configs(source_id) ON DELETE CASCADE
);

CREATE INDEX idx_schedule_executions_schedule ON schedule_executions(schedule_id);
CREATE INDEX idx_schedule_executions_triggered ON schedule_executions(triggered_at);
```

## File Structure

```
daemon/
├── sources/
│   ├── adapters/
│   │   ├── scheduler.py      # NEW: SchedulerAdapter
│   │   └── __init__.py       # UPDATE: export SchedulerAdapter
│   ├── registry.py           # UPDATE: handle scheduler type
│   └── ...
├── models.py                 # UPDATE: add scheduler to SourceType
├── persistence.py            # UPDATE: add schedule_executions table
└── api.py                    # Optional: schedule-specific endpoints
```

## API Endpoints

### Reuse Source Endpoints

Since scheduler is a source type, existing endpoints work:

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/sources` | Create scheduler (source_type=scheduler) |
| GET | `/sources` | List all sources (includes schedulers) |
| GET | `/sources/{schedule_id}` | Get scheduler info |
| PUT | `/sources/{schedule_id}` | Update scheduler |
| DELETE | `/sources/{schedule_id}` | Delete scheduler |
| POST | `/sources/{schedule_id}/start` | Start scheduler |
| POST | `/sources/{schedule_id}/stop` | Stop scheduler |

### Scheduler-Specific Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/schedules` | List only schedulers |
| POST | `/schedules/{id}/trigger` | Manually trigger a schedule |
| GET | `/schedules/{id}/executions` | Get execution history |

## Implementation Phases

### Phase 1: Core Implementation
- [ ] Add `scheduler` to `SourceType` enum in `models.py`
- [ ] Add `croniter` to `pyproject.toml` dependencies
- [ ] Create `daemon/sources/adapters/scheduler.py` with `SchedulerAdapter`
- [ ] Update `daemon/sources/adapters/__init__.py` to export SchedulerAdapter
- [ ] Update `registry.py` to handle `scheduler` type

### Phase 2: Database & Persistence
- [ ] Add `schedule_executions` table to `persistence.py`
- [ ] Add methods to record execution status

### Phase 3: API Endpoints
- [ ] Add `/schedules` endpoint (list schedulers only)
- [ ] Add `/schedules/{id}/trigger` endpoint (manual trigger)
- [ ] Add `/schedules/{id}/executions` endpoint (history)

### Phase 4: Testing
- [ ] Unit tests for SchedulerAdapter
- [ ] Integration tests for schedule triggering
- [ ] Test cron parsing and timezone handling
- [ ] Test execution history

## SchedulerAdapter Design

```python
class SchedulerAdapter(MessageSourceAdapter):
    """Adapter that triggers messages on a schedule.
    
    Supports:
    - Cron expressions (schedule: "0 9 * * 1-5")
    - Interval in seconds (interval_seconds: 300)
    - One-time triggers (run_at: "2025-03-15T10:00:00Z")
    """
    
    def __init__(self, config: SourceConfig, on_message: Callable):
        super().__init__(config, on_message)
        self._task: Optional[asyncio.Task] = None
        self._running = False
        self._agent = config.config.get("agent")
        self._message = config.config.get("message", "Scheduled trigger")
        self._timezone = config.config.get("timezone", "UTC")
        
    async def start(self) -> None:
        """Start the scheduler loop."""
        self._running = True
        self._task = asyncio.create_task(self._run_schedule())
        
    async def stop(self) -> None:
        """Stop the scheduler."""
        self._running = False
        if self._task:
            self._task.cancel()
            
    async def send(self, message: OutgoingMessage) -> bool:
        """Scheduler can't receive responses - no-op."""
        return True
        
    async def health_check(self) -> bool:
        """Check if scheduler is running."""
        return self._running
        
    async def _run_schedule(self) -> None:
        """Main scheduler loop."""
        while self._running:
            # Calculate next trigger time
            # Wait until trigger
            # Emit message
            # Record execution
            pass
```

## Dependencies

### Required (already in project)
- `asyncio` - for async scheduling
- `datetime` - for time handling
- `zoneinfo` - for timezone support (Python 3.9+)

### New Dependencies (add to pyproject.toml)
- `croniter>=3.0.0` - for cron expression parsing

## Configuration Example

```bash
# Create a cron-based scheduler
curl -X POST http://localhost:8079/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "morning-briefing",
    "source_type": "scheduler",
    "name": "Morning Briefing",
    "config": {
      "schedule": "0 8 * * 1-5",
      "agent": "leader",
      "message": "Generate morning briefing for today",
      "timezone": "America/New_York"
    },
    "enabled": true
  }'

# Create an interval-based scheduler
curl -X POST http://localhost:8079/sources \
  -H "Content-Type: application/json" \
  -d '{
    "source_id": "health-check",
    "source_type": "scheduler",
    "name": "Health Check",
    "config": {
      "interval_seconds": 300,
      "agent": "monitor",
      "message": "Run health check"
    },
    "enabled": true
  }'
```

## Edge Cases

1. **Timezone handling**: Use `zoneinfo` (Python 3.9+) for timezone-aware scheduling
2. **Missed schedules**: NOT triggering on restart (per user decision)
3. **Long-running jobs**: Don't trigger again if previous is still running (max_concurrent config)
4. **Error handling**: Log failures, record in execution history
5. **Invalid cron**: Validate on create/update, return error

## Success Criteria

- [x] Plan created and approved
- [ ] Can create/update/delete scheduled jobs via API
- [ ] Jobs trigger at correct times (cron and interval)
- [ ] Agent receives scheduled messages correctly
- [ ] Schedules persist across daemon restarts
- [ ] Can start/stop individual schedulers
- [ ] Execution history tracked
- [ ] Tests pass

## Estimated Effort

| Phase | Effort |
|-------|--------|
| Phase 1: Core | 2-3 hours |
| Phase 2: Persistence | 1 hour |
| Phase 3: API Endpoints | 1 hour |
| Phase 4: Testing | 2 hours |
| **Total** | **6-7 hours** |
