# Phase 2: Models Split

## Objective
Split the monolithic `daemon/models.py` (737 lines) into domain-specific model modules while maintaining the same public API through re-exports. This makes models discoverable and keeps related schemas together.

## Coupling
- **Depends on**: Phase 1 (uses constants for default values)
- **Coupling type**: loose
- **Shared files with other phases**: Phase 4 (manager) imports from these models
- **Shared APIs/interfaces**: New model subpackage — consumed by routers, services, and manager
- **Why this coupling**: Manager decomposition (Phase 4) will import from the new model paths. Phase 1 constants are used in model defaults.

## Pre-flight Validation
```bash
git tag refactor-pre-phase2

# Record all current imports of models
grep -r "from daemon.models import" daemon/ tests/ --include="*.py" | sort > /tmp/model-imports-baseline.txt
grep -r "from daemon import models" daemon/ tests/ --include="*.py" >> /tmp/model-imports-baseline.txt
```

## Rollback Procedure
```bash
# Restore models.py from git and remove new package
git checkout refactor-pre-phase2 -- daemon/models.py
rm -rf daemon/models/
# Re-run tests
```

## Context
- Phase 1 completed: constants and utilities available
- Current `models.py` has 35 models across 8+ concern groups
- Phase 1 relocated `validate_agent_id` to `utils.py` — no impact on models

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/models/` package | Convert `daemon/models.py` to `daemon/models/` package | `daemon/models/__init__.py` (new) |
| 2 | Extract instance models | `InstanceStatus`, `InstanceCreate`, `InstanceInfo`, `InstanceListResponse` | `daemon/models/instance.py` (new) |
| 3 | Extract message models | `MessageCreate`, `MessageResponse` | `daemon/models/message.py` (new) |
| 4 | Extract agent models | `AgentInfo`, `AgentListResponse`, `AgentCreate`, `HealthResponse` | `daemon/models/agent.py` (new) |
| 5 | Extract source models | `SourceStatus`, `SourceType`, `SourceCreate`, `SourceUpdate`, `SourceInfo`, `SourceListResponse`, `SourceTestRequest`, `SourceTestResponse`, `SourceActionResponse` | `daemon/models/source.py` (new) |
| 6 | Extract schedule models | `SchedulerInstanceMode`, `ScheduleInfo`, `ScheduleListResponse`, `ScheduleUpdate`, `ScheduleExecutionInfo`, `ScheduleExecutionListResponse`, `ScheduleTriggerResponse` | `daemon/models/schedule.py` (new) |
| 7 | Extract mapping models | `InstanceMappingCreate`, `InstanceMappingInfo`, `InstanceMappingListResponse` | `daemon/models/mapping.py` (new) |
| 8 | Extract shared/error models | `ErrorCodes`, `ErrorResponse`, `DeleteResponse` | `daemon/models/common.py` (new) |
| 9 | Add re-exports in `__init__.py` | Re-export ALL models from submodules so existing imports (`from daemon.models import X`) continue to work | `daemon/models/__init__.py` |
| 10 | Verify all imports | Search codebase for all `from daemon.models import` patterns; ensure they still work | All files |
| 11 | Delete old `daemon/models.py` | Remove the original file after verifying all re-exports work | `daemon/models.py` (delete) |

## Key Files
- `daemon/models.py` → **deleted**, replaced by package
- `daemon/models/__init__.py` (new) — Re-exports all models
- `daemon/models/common.py` (new) — ErrorCodes, ErrorResponse, DeleteResponse
- `daemon/models/instance.py` (new) — Instance models + InstanceStatus enum
- `daemon/models/message.py` (new) — Message models
- `daemon/models/agent.py` (new) — Agent models
- `daemon/models/source.py` (new) — Source models + enums
- `daemon/models/schedule.py` (new) — Schedule models + SchedulerInstanceMode enum
- `daemon/models/mapping.py` (new) — Mapping models

## Constraints
- ALL existing import paths must continue to work (backward compatibility via `__init__.py`)
- No model field changes — exact same Pydantic schemas
- No model method changes — exact same validators, computed fields, etc.
- If any model has cross-concern references (e.g., InstanceInfo referencing SourceStatus), import from sibling module
- **Do NOT fix `Optional[T]` → `T | None` here** — that's Phase 6. Leave annotations as-is to minimize risk.

## Detailed Implementation Notes

### `daemon/models/__init__.py` Pattern
```python
"""Models package — re-exports for backward compatibility."""

from daemon.models.common import ErrorCodes, ErrorResponse, DeleteResponse
from daemon.models.instance import InstanceStatus, InstanceCreate, InstanceInfo, InstanceListResponse
from daemon.models.message import MessageCreate, MessageResponse
from daemon.models.agent import AgentInfo, AgentListResponse, AgentCreate, HealthResponse
from daemon.models.source import SourceStatus, SourceType, SourceCreate, SourceUpdate, SourceInfo, SourceListResponse, SourceTestRequest, SourceTestResponse, SourceActionResponse
from daemon.models.schedule import SchedulerInstanceMode, ScheduleInfo, ScheduleListResponse, ScheduleUpdate, ScheduleExecutionInfo, ScheduleExecutionListResponse, ScheduleTriggerResponse
from daemon.models.mapping import InstanceMappingCreate, InstanceMappingInfo, InstanceMappingListResponse

__all__ = [
    "ErrorCodes", "ErrorResponse", "DeleteResponse",
    "InstanceStatus", "InstanceCreate", "InstanceInfo", "InstanceListResponse",
    "MessageCreate", "MessageResponse",
    "AgentInfo", "AgentListResponse", "AgentCreate", "HealthResponse",
    "SourceStatus", "SourceType", "SourceCreate", "SourceUpdate", "SourceInfo",
    "SourceListResponse", "SourceTestRequest", "SourceTestResponse", "SourceActionResponse",
    "SchedulerInstanceMode", "ScheduleInfo", "ScheduleListResponse", "ScheduleUpdate",
    "ScheduleExecutionInfo", "ScheduleExecutionListResponse", "ScheduleTriggerResponse",
    "InstanceMappingCreate", "InstanceMappingInfo", "InstanceMappingListResponse",
]
```

### Dependency Order for Model Submodules
```
common.py    ← leaf (no sibling imports)
instance.py  ← may import InstanceStatus from common (check)
message.py   ← standalone
agent.py     ← standalone
source.py    ← may import SourceStatus, SourceType enums from common
schedule.py  ← standalone
mapping.py   ← standalone
```

### Import Verification
```bash
# After implementation, verify ALL these patterns work:
python -c "from daemon.models import InstanceCreate, MessageResponse, ScheduleInfo; print('OK')"
python -c "from daemon.models.instance import InstanceStatus; print('OK')"
python -c "from daemon.models.source import SourceType; print('OK')"
python -c "from daemon.models.common import ErrorCodes; print('OK')"

# Verify no import errors across the codebase
grep -rl "from daemon.models import" daemon/ tests/ | xargs -I{} python -c "import {}" 2>&1 || echo "FAIL"
```

## Deliverables
- [ ] `daemon/models/` package created with 7 submodules
- [ ] All 35 models in correct submodules
- [ ] `__init__.py` re-exports all models for backward compatibility
- [ ] `daemon/models.py` (old file) deleted
- [ ] All existing imports verified working
- [ ] Full test suite passes
