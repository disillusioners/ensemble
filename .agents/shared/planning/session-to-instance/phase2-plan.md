# Phase 2: Repository Layer — Directory, Classes, Exports, Message Queue Repo

## Objective
Rename the repository directory from `session/` to `instance/`, rename repository class, methods, and update all re-exports and factory functions. Also rename message_queue repository methods that reference session_id.

## Context
- **Phase 1 completed**: All model types renamed (Instance, InstanceStatus, InstanceHierarchy, InstanceMapping, instance_id fields including in message_queue models)
- Repository layer consumes the models from Phase 1 and provides the data access layer
- Other layers import repository symbols through `daemon/repositories/__init__.py` and `daemon/repositories/factory.py`

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename directory** `daemon/repositories/session/` → `daemon/repositories/instance/` | Use `git mv daemon/repositories/session daemon/repositories/instance`. This preserves git history. | Directory rename |
| 2 | **Update `daemon/repositories/instance/__init__.py`** | Rename exports: `SQLModelSessionRepository`→`SQLModelInstanceRepository`, `Session`→`Instance`, `SessionStatus`→`InstanceStatus`, `SessionHierarchy`→`InstanceHierarchy`. Update import paths from `.models` and `.repository`. | `daemon/repositories/instance/__init__.py` |
| 3 | **Update `daemon/repositories/instance/repository.py`** | Rename class `SQLModelSessionRepository`→`SQLModelInstanceRepository`. Rename all methods: `create_session`→`create_instance`, `get_session`→`get_instance`, `list_sessions`→`list_instances`, `update_session`→`update_instance`, `delete_session`→`delete_instance`, etc. Rename params: `session_id`→`instance_id`. **CRITICAL**: Skip `db_session` parameter — that's an ORM session. Update log strings. Update any SQL queries referencing `sessions` table name. | `daemon/repositories/instance/repository.py` (~346 lines) |
| 4 | **Update `daemon/repositories/__init__.py`** | Update re-exports: import from `.instance` instead of `.session`, rename all exports (`SQLModelInstanceRepository`, `Instance`, `InstanceStatus`, `InstanceHierarchy`). Update factory import. | `daemon/repositories/__init__.py` |
| 5 | **Update `daemon/repositories/factory.py`** | Rename `create_session_repository`→`create_instance_repository`. Update internal references: import path (`.instance.models`), table name references (`"instances"`), pk_column references. Update any migration version references if they mention "session". | `daemon/repositories/factory.py` |
| 6 | **Update `daemon/repositories/source/repository.py`** | Rename methods: `create_session_mapping`→`create_instance_mapping`, `get_session_mapping`→`get_instance_mapping`, etc. Update field references from Phase 1 changes. Update any SQL queries referencing `"session_mappings"` → `"instance_mappings"`. | `daemon/repositories/source/repository.py` |
| 7 | **Update `daemon/repositories/source/__init__.py`** | Update exports to use new class/function names from repository changes. | `daemon/repositories/source/__init__.py` |
| 8 | **Update `daemon/repositories/message_queue/repository.py`** | Rename methods: `get_by_session`→`get_by_instance`, `delete_by_session`→`delete_by_instance`. Rename params: `session_id`→`instance_id` in `is_empty()`, `get_stats()`, `list_pending()`, and any other methods. Update SQL queries to use `"instance_id"` column (renamed in Phase 1). | `daemon/repositories/message_queue/repository.py` (~612 lines) |
| 9 | **Update `daemon/repositories/message_queue/__init__.py`** | Verify exports are correct (no session-specific names to change — exports are `SQLModelMessageQueueRepository`, `MessageQueue`, `MessageStatus`). | `daemon/repositories/message_queue/__init__.py` |

## Key Files
- `daemon/repositories/session/` → `daemon/repositories/instance/` (directory rename)
- `daemon/repositories/instance/models.py` — already updated in Phase 1
- `daemon/repositories/instance/repository.py` — main repository class (~346 lines)
- `daemon/repositories/instance/__init__.py` — exports
- `daemon/repositories/__init__.py` — re-exports to rest of codebase
- `daemon/repositories/factory.py` — repository factory
- `daemon/repositories/source/repository.py` — mapping repository
- `daemon/repositories/source/__init__.py` — source exports
- `daemon/repositories/message_queue/repository.py` — message queue repository (~612 lines)
- `daemon/repositories/message_queue/__init__.py` — message queue exports

## Exclusions — DO NOT Rename
- `db_session` parameters in repository methods (SQLAlchemy session)
- `SQLModelSession` imports (SQLAlchemy session class)
- `with Session(engine) as db_session` patterns
- Any ORM-specific session patterns in repository.py

## Constraints
- Files importing from `daemon.repositories` will still break after this phase (manager.py, tools, etc.) — that's expected and handled in Phase 3-5
- Table names in SQL strings must already be updated from Phase 1 (`"instances"`, `"instance_mappings"`)

## Verification
```bash
# 1. Directory renamed
ls daemon/repositories/instance/  # should exist
ls daemon/repositories/session/   # should NOT exist

# 2. No old names in repository layer
grep -rn "SQLModelSessionRepository\|create_session_repository\|SessionStatus\|SessionHierarchy\|get_by_session\|delete_by_session\|dequeue_by_session" daemon/repositories/

# 3. Exclusions preserved
grep -rn "db_session\|SQLModelSession" daemon/repositories/instance/repository.py  # should still exist

# 4. Message queue repo uses new names
grep -rn "instance_id" daemon/repositories/message_queue/repository.py
```

## Deliverables
- [ ] Directory renamed: `session/` → `instance/`
- [ ] `SQLModelInstanceRepository` class fully renamed
- [ ] All repository methods renamed (create/get/list/update/delete instance)
- [ ] Factory function `create_instance_repository` works
- [ ] Re-exports in `__init__.py` files updated
- [ ] Source repository methods renamed for InstanceMapping
- [ ] Message queue repository methods renamed (get_by_instance, delete_by_instance, etc.)
- [ ] Grep shows 0 old session-specific names in repository layer
