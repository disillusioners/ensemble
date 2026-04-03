# Phase 4: Sources Layer — Mapper, Registry, Scheduler Adapter

## Objective
Rename all session references in the sources subsystem: mapper, registry, and scheduler adapter. These handle how external sources (like the scheduler) create and manage agent instances.

## Context
- **Phase 1-3 completed**: Models, repository, and core daemon fully renamed
- Sources layer imports from the repository layer (Phase 2) and is consumed by the API layer (Phase 6)

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename daemon/sources/mapper.py** | Rename class `SessionMapper`→`InstanceMapper`. Rename methods: `get_session_mapping`→`get_instance_mapping`, `get_or_create_session`→`get_or_create_instance`, `force_new_session`→`force_new_instance`. Rename params: `session_id`→`instance_id`, `agent_session_id`→`agent_instance_id`, `session_repo`→`instance_repo`. Update imports from renamed repository layer. | `daemon/sources/mapper.py` |
| 2 | **Rename daemon/sources/registry.py** | Rename internal fields: `_session_repo`→`_instance_repo`, `session_id`→`instance_id`. Update imports. Update method calls to renamed mapper methods. | `daemon/sources/registry.py` |
| 3 | **Rename daemon/sources/adapters/scheduler.py** (~986 lines) | Rename: `session_mode`→`instance_mode`, `SchedulerSessionMode`→`SchedulerInstanceMode`, `_is_session_active`→`_is_instance_active`, `reuse_session`→`reuse_instance`, `session_repo`→`instance_repo`. Update imports from renamed repository layer. This is the second-largest file in the sources layer — systematic find-and-replace. | `daemon/sources/adapters/scheduler.py` |
| 4 | **Update daemon/sources/__init__.py** | Update exports: `SessionMapper`→`InstanceMapper`, any re-exported session-related names. | `daemon/sources/__init__.py` |
| 5 | **Update daemon/sources/adapters/__init__.py** | Update any exports referencing session names. | `daemon/sources/adapters/__init__.py` |

## Key Files
- `daemon/sources/mapper.py` — Instance mapping logic
- `daemon/sources/registry.py` — Source registry
- `daemon/sources/adapters/scheduler.py` — ~986 lines, scheduler integration
- `daemon/sources/__init__.py` — Package exports
- `daemon/sources/adapters/__init__.py` — Adapter exports

## Exclusions — DO NOT Rename
- `db_session` — SQLAlchemy session parameter (used in repository calls)
- Any scheduler-internal concepts unrelated to agent sessions

## Constraints
- scheduler.py is large (~986 lines) — focus on systematic rename, don't restructure
- Sources layer is consumed by API layer — Phase 6 will update the API consumers

## Verification
```bash
# 1. No old names in sources layer
grep -rn "SessionMapper\|get_or_create_session\|force_new_session\|session_mode\|SchedulerSessionMode\|_is_session_active\|reuse_session\|_session_repo" daemon/sources/

# 2. Exclusions preserved
grep -rn "db_session" daemon/sources/
```

## Deliverables
- [ ] `daemon/sources/mapper.py` — InstanceMapper class, all methods renamed
- [ ] `daemon/sources/registry.py` — _instance_repo, instance_id references
- [ ] `daemon/sources/adapters/scheduler.py` — SchedulerInstanceMode, all renames applied
- [ ] All `__init__.py` exports updated
- [ ] Grep shows 0 old session names in sources layer
