# Phase 2 — Models Split

## What was done
Split `daemon/models.py` (737 lines, ~30 models) into `daemon/models/` package with 7 submodules:
- `common.py` — ErrorCodes, ErrorResponse, DeleteResponse
- `instance.py` — InstanceStatus, InstanceCreate, InstanceInfo, InstanceListResponse
- `message.py` — MessageCreate, MessageResponse
- `agent.py` — AgentInfo, AgentListResponse, AgentCreate, HealthResponse
- `source.py` — SourceStatus, SourceType, SourceCreate, SourceUpdate, SourceInfo, SourceListResponse, SourceTestRequest, SourceTestResponse, SourceActionResponse
- `schedule.py` — SchedulerInstanceMode, ScheduleInfo, ScheduleListResponse, ScheduleUpdate, ScheduleExecutionInfo, ScheduleExecutionListResponse, ScheduleTriggerResponse
- `mapping.py` — InstanceMappingCreate, InstanceMappingInfo, InstanceMappingListResponse

## Key Learnings
1. **Cross-module reference in schedule.py**: ScheduleExecutionInfo references SourceStatus from source.py. The import was placed at the bottom of schedule.py to avoid circular imports when `__init__.py` imports both modules.
2. **Re-export pattern**: `__init__.py` uses explicit `from daemon.models.X import Y, Z` for each submodule, plus comprehensive `__all__` list.
3. **No external file changes needed**: The re-export strategy means zero changes to other files — all `from daemon.models import X` continue to work.
4. **message.py has mixed Optional[T] / T | None** — intentionally left as-is for Phase 6.

## Commit
- Hash: `2c82f23`
- Message: `refactor: Phase 2 — split models.py into domain submodules`
- Stats: 17 files changed, +1530/-743 lines
