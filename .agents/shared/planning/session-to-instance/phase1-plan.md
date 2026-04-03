# Phase 1: Foundation — Models & Pydantic Types

## Objective
Rename all type/class names and field names in the foundational model files. These are the leaf dependencies — everything else imports from these, so they must change first. Includes ALL repository models (session, source, project, job_queue, message_queue) and Pydantic API models.

## Context
- No previous phase completed (this is the first phase)
- These files define the data types used throughout the codebase
- After this phase, import statements throughout the codebase will break until subsequent phases fix them

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename Pydantic models in daemon/models.py** | `SessionStatus`→`InstanceStatus`, `SchedulerSessionMode`→`SchedulerInstanceMode`, `SessionCreate`→`InstanceCreate`, `SessionInfo`→`InstanceInfo`, `SessionListResponse`→`InstanceListResponse`, `SessionMappingCreate`→`InstanceMappingCreate`, `SessionMappingInfo`→`InstanceMappingInfo`, `SessionMappingListResponse`→`InstanceMappingListResponse`. Rename fields: `session_id`→`instance_id`, `agent_session_id`→`agent_instance_id`. Update ErrorCodes: `SESSION_NOT_FOUND`→`INSTANCE_NOT_FOUND`, `SESSION_LIMIT_REACHED`→`INSTANCE_LIMIT_REACHED`, `SESSION_TERMINATED`→`INSTANCE_TERMINATED`. Update all field names and descriptions. | `daemon/models.py` (~690 lines) |
| 2 | **Rename ORM models in daemon/repositories/session/models.py** | `Session`→`Instance` (change `table=True` name to `"instances"`), `SessionStatus`→`InstanceStatus`, `SessionHierarchy`→`InstanceHierarchy` (table → `"instance_hierarchy"`), rename field `session_metadata`→`instance_metadata`, rename PK `session_id`→`instance_id`. Keep enum VALUES as-is (IDLE, RUNNING, etc.). | `daemon/repositories/session/models.py` (~94 lines) |
| 3 | **Rename models in daemon/repositories/source/models.py** | `SessionMapping`→`InstanceMapping` (table → `"instance_mappings"`), `agent_session_id`→`agent_instance_id`, `session_id`→`instance_id` in `ScheduleExecution`. Update all field references. | `daemon/repositories/source/models.py` |
| 4 | **Rename field in daemon/repositories/project/models.py** | `creator_session_id`→`creator_instance_id` in `Project` model. | `daemon/repositories/project/models.py` |
| 5 | **Rename field in daemon/repositories/job_queue/models.py** | `session_id`→`instance_id` in `JobItem` and `JobLockInfo`. Update any index names containing "session". | `daemon/repositories/job_queue/models.py` |
| 6 | **Rename field in daemon/repositories/message_queue/models.py** | `session_id`→`instance_id` in `MessageQueue` model (it's an indexed DB column). Update any index names. | `daemon/repositories/message_queue/models.py` (~74 lines) |

## Key Files
- `daemon/models.py` — Pydantic API models (~690 lines, 8 session-related types)
- `daemon/repositories/session/models.py` — ORM table models (~94 lines)
- `daemon/repositories/source/models.py` — SessionMapping, ScheduleExecution
- `daemon/repositories/project/models.py` — creator_session_id field
- `daemon/repositories/job_queue/models.py` — session_id in job tracking
- `daemon/repositories/message_queue/models.py` — session_id in message queue (~74 lines)

## Exclusions — DO NOT Rename These
- `db_session` (SQLAlchemy session parameter)
- `SQLModelSession` (SQLAlchemy session class)
- `with Session(engine) as db_session` (SQLAlchemy session context)
- `opencode_skill` session concept
- Enum VALUES like `IDLE`, `RUNNING`, `COMPLETED` — these stay the same

## Constraints
- **This phase will intentionally break imports** in downstream files. That's expected.
- Do NOT update downstream imports yet — that's Phase 2-6.
- Do NOT rename the `daemon/repositories/session/` directory yet — that's Phase 2.
- Do NOT modify any files outside the listed Key Files.
- Table name strings in `table=True` must change: `"sessions"`→`"instances"`, `"session_hierarchy"`→`"instance_hierarchy"`, `"session_mappings"`→`"instance_mappings"`

## Verification
```bash
# Check ALL model files have no old names
grep -rn "SessionStatus\|SessionInfo\|SessionCreate\|SessionListResponse\|SessionMapping\|session_id\|agent_session_id\|creator_session_id" daemon/models.py daemon/repositories/ | grep -v "db_session\|SQLModelSession\|__pycache__"
# Should return 0 hits

# Confirm new names present
grep -c "InstanceStatus\|InstanceInfo\|InstanceCreate\|InstanceMapping\|instance_id\|agent_instance_id\|creator_instance_id" daemon/models.py daemon/repositories/session/models.py daemon/repositories/source/models.py daemon/repositories/project/models.py daemon/repositories/job_queue/models.py daemon/repositories/message_queue/models.py
```

## Deliverables
- [ ] `daemon/models.py` — all 8 types renamed, all field names updated
- [ ] `daemon/repositories/session/models.py` — all 3 classes renamed, table names updated
- [ ] `daemon/repositories/source/models.py` — SessionMapping→InstanceMapping, fields updated
- [ ] `daemon/repositories/project/models.py` — creator_session_id→creator_instance_id
- [ ] `daemon/repositories/job_queue/models.py` — session_id→instance_id
- [ ] `daemon/repositories/message_queue/models.py` — session_id→instance_id
- [ ] Grep verification passes (0 old names, excluding exclusions)
