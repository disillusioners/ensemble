# Phase 10: Tests — File Renames, Classes, Functions, Fixtures, Assertions

## Objective
Rename all session references across the entire test suite: file names, test class names, test function names, fixtures, mock objects, assertions, and test data. This is the final phase that validates all previous renames.

## Context
- **Phases 1-9 completed**: All production code renamed
- Tests will have been broken since Phase 1 — this phase fixes them all
- Tests are the ultimate verification that the rename was done correctly

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename test files** | Use `git mv` for each: `test_session_spawn_session_instructive_errors.py`→`test_spawn_instance_instructive_errors.py`, `test_session_spawn_session_validation.py`→`test_spawn_instance_validation.py`, `test_session_title.py`→`test_instance_title.py`, `test_scheduler_session_mode.py`→`test_scheduler_instance_mode.py`, `integration/test_session_title_e2e.py`→`integration/test_instance_title_e2e.py`. | `tests/` |
| 2 | **Update test_manager.py** | Rename all test functions: `test_spawn_session*`→`test_spawn_instance*`, `test_terminate_session*`→`test_terminate_instance*`, `test_list_sessions*`→`test_list_instances*`. Update fixtures: `mock_session_manager`→`mock_instance_manager`, `session_id`→`instance_id`. Update imports: `SessionManager`→`InstanceManager`, `Session`→`Instance`, `SessionStatus`→`InstanceStatus`. Update mock setup and assertions. | `tests/test_manager.py` |
| 3 | **Update test_instance_title.py** (renamed) | Rename test class/functions, update `SessionManager`→`InstanceManager` mocks, `session_id`→`instance_id` assertions, `_generate_session_title`→`_generate_instance_title`. | `tests/test_instance_title.py` |
| 4 | **Update test_scheduler_instance_mode.py** (renamed) | Rename `SchedulerSessionMode`→`SchedulerInstanceMode`, `session_mode`→`instance_mode`, `reuse_session`→`reuse_instance`, all related test functions and assertions. | `tests/test_scheduler_instance_mode.py` |
| 5 | **Update test_spawn_instance_*.py** (renamed) | Rename `spawn_session`→`spawn_instance`, `SpawnSessionInput`→`SpawnInstanceInput`, `SessionManager`→`InstanceManager`, all param/mocker references. | `tests/test_spawn_instance_*.py` |
| 6 | **Update integration/test_instance_title_e2e.py** (renamed) | Rename API endpoint references from `/sessions`→`/instances`, `session_id`→`instance_id`, response model fields. | `tests/integration/test_instance_title_e2e.py` |
| 7 | **Update test_sources_mapper.py** | Rename `SessionMapper`→`InstanceMapper`, `get_or_create_session`→`get_or_create_instance`, `force_new_session`→`force_new_instance`. Update mock setup. | `tests/test_sources_mapper.py` |
| 8 | **Update test_persistence.py** | Rename `get_session_messages`→`get_instance_messages`, `session_id`→`instance_id`. | `tests/test_persistence.py` |
| 9 | **Update all other test files** | Do a comprehensive `grep -rn "session" tests/` and update any remaining references. Check conftest.py for shared fixtures. Check any test files not listed above. | `tests/` (all remaining) |
| 10 | **Update test conftest.py** | Rename any shared fixtures: `mock_session_repo`→`mock_instance_repo`, `session_id`→`instance_id`, `SessionManager`→`InstanceManager`. | `tests/conftest.py` (if exists) |

## Key Files
- `tests/test_session_spawn_session_instructive_errors.py` → rename
- `tests/test_session_spawn_session_validation.py` → rename
- `tests/test_session_title.py` → rename
- `tests/test_scheduler_session_mode.py` → rename
- `tests/integration/test_session_title_e2e.py` → rename
- `tests/test_manager.py`
- `tests/test_sources_mapper.py`
- `tests/test_persistence.py`
- `tests/conftest.py` (if exists)
- All other test files with session references

## Rename Patterns for Tests

### Test File Renames
| Old Name | New Name |
|----------|----------|
| `test_session_spawn_session_instructive_errors.py` | `test_spawn_instance_instructive_errors.py` |
| `test_session_spawn_session_validation.py` | `test_spawn_instance_validation.py` |
| `test_session_title.py` | `test_instance_title.py` |
| `test_scheduler_session_mode.py` | `test_scheduler_instance_mode.py` |
| `integration/test_session_title_e2e.py` | `integration/test_instance_title_e2e.py` |

### Test Function Patterns
| Old | New |
|-----|-----|
| `test_spawn_session_*` | `test_spawn_instance_*` |
| `test_terminate_session_*` | `test_terminate_instance_*` |
| `test_list_sessions_*` | `test_list_instances_*` |
| `test_get_session_*` | `test_get_instance_*` |
| `test_session_title_*` | `test_instance_title_*` |
| `test_scheduler_session_mode*` | `test_scheduler_instance_mode*` |

### Fixture Patterns
| Old | New |
|-----|-----|
| `mock_session_manager` | `mock_instance_manager` |
| `mock_session_repo` | `mock_instance_repo` |
| `session_id` (fixture) | `instance_id` |
| `sample_session` | `sample_instance` |

### Mock/Assertion Patterns
| Old | New |
|-----|-----|
| `mocker.patch("daemon.manager.SessionManager")` | `mocker.patch("daemon.manager.InstanceManager")` |
| `mock_session_manager.spawn_session` | `mock_instance_manager.spawn_instance` |
| `assert response["session_id"]` | `assert response["instance_id"]` |
| `"/api/sessions"` | `"/api/instances"` |

## Constraints
- Tests are the FINAL phase — all production code must be renamed first
- Do NOT modify any production code in this phase
- Some test files may import from both old and new paths during transition — ensure all point to new paths
- Integration tests that hit the API must use `/instances/` routes (Phase 6)
- The `db_session` exclusion still applies — don't rename ORM session mocks

## Final Verification (End of Refactor)

After this phase, run the COMPLETE verification suite:

```bash
# 1. FULL grep for remaining "session" in production code
grep -rn "session_id\|SessionManager\|SessionStatus\|SessionInfo\|SessionCreate\|SessionListResponse\|SessionMapping\|spawn_session\|terminate_session\|list_sessions\|get_session_info\|SQLModelSessionRepository\|create_session_repository\|build_session_graph\|create_session_tools\|max_sessions\|session_timeout" daemon/ --include="*.py" | grep -v "db_session\|SQLModelSession\|opencode"

# 2. FULL grep in frontend
grep -rn "SessionInfo\|SessionStatus\|createSession\|listSessions\|getSession\|deleteSession\|session_id\|sessionId\|currentSession" frontend/src/ | grep -v "node_modules"

# 3. FULL grep in tests
grep -rn "spawn_session\|terminate_session\|list_sessions\|get_session_info\|SessionManager\|session_id" tests/ | grep -v "db_session\|SQLModelSession\|opencode"

# 4. Python import check
python -c "from daemon.api import app; from daemon.manager import InstanceManager; from daemon.tools import create_instance_tools; print('All imports OK')"

# 5. Run test suite
cd /path/to/project && pytest tests/ -v

# 6. Frontend build
cd frontend && npm run build

# 7. Count total remaining "session" in codebase (should be near 0 excluding exclusions)
grep -rn "session" daemon/ frontend/src/ tests/ --include="*.py" --include="*.ts" --include="*.html" | grep -v "db_session\|SQLModelSession\|opencode\|node_modules\|__pycache__" | wc -l
```

## Deliverables
- [ ] All 5+ test files renamed
- [ ] All test class names updated
- [ ] All test function names updated
- [ ] All fixtures renamed
- [ ] All mocks and assertions updated
- [ ] `pytest tests/` passes (or at least no import errors)
- [ ] Final full-codebase grep shows 0 old session names (excluding exclusion list)
- [ ] Frontend build succeeds
