# Phase 3: API Router Extraction

## Objective
Split the monolithic `daemon/api.py` (2114 lines, 33 endpoints) into domain-specific router modules, and migrate module-level globals to FastAPI's `app.state` pattern. This is the highest-impact phase for code organization.

## Coupling
- **Depends on**: Phase 1 (uses `parse_utc_datetime`, HTTPException helpers, constants, relocated `validate_agent_id`)
- **Coupling type**: loose
- **Shared files with other phases**: None (Phase 4 modifies manager.py, not api.py routers)
- **Shared APIs/interfaces**: New router modules consumed by app factory
- **Why this coupling**: Phase 1 utilities are used in routers. `validate_agent_id` is already in `utils.py` after Phase 1, so api.py can safely be split.

## Pre-flight Validation
```bash
git tag refactor-pre-phase3

# Record all endpoint paths and response codes
python -c "
from daemon.api import create_app
app = create_app()
for route in app.routes:
    if hasattr(route, 'methods') and hasattr(route, 'path'):
        print(f'{list(route.methods)} {route.path}')
" | sort > /tmp/api-endpoints-baseline.txt

# Record current globals
grep -n "^manager\|^start_time\|^credential_manager\|^job_queue_service\|^job_processor\|^job_queue_mgmt_service\|^retry_scheduler\|^dispatch_event_bus" daemon/api.py
```

## Rollback Procedure
```bash
# Restore api.py and remove new routers
git checkout refactor-pre-phase3 -- daemon/api.py
# Keep routers that already existed before refactoring
# Re-run tests
```

## Context
- Phase 1 completed: `validate_agent_id` now in `daemon/utils.py` with re-export from `api.py`
- Phase 2 completed: models in `daemon/models/` package
- `app.state.live_hub` already exists (lines 341, 370–371, 972) — new `app.state` attributes must coexist
- **CRITICAL**: Phase 5 depends on this phase completing. `routers/jobs.py` imports `validate_agent_id` — after Phase 1, it imports from `utils.py`, so Phase 3 splitting `api.py` is safe.

## Correct Module-Level Globals in `api.py`

The **actual** globals (lines 166–174):
```python
manager: InstanceManager = None
start_time: float = None
credential_manager = CredentialManager()
job_queue_service: JobQueueService = None
job_processor: JobProcessor = None
job_queue_mgmt_service: JobQueueMgmtService = None
retry_scheduler = None
dispatch_event_bus: DispatchEventBus = None
```

> **Note**: `source_dispatcher`, `scheduler_service`, `mapping_service`, `prompt_cache`, and `config` are NOT module-level globals — they are attributes of `InstanceManager` or accessed through the manager.

## Current Endpoint Groups in `api.py`

| Group | Endpoints | Line Range (approx.) |
|-------|-----------|---------------------|
| **Agents** | 3 (list, get, health) | ~50–250 |
| **Instances** | 6 (create, list, get, delete, messages, info) | ~250–600 |
| **Messages** | 3 (send, enqueue, queue stats) | ~600–900 |
| **Sources** | 7 (CRUD + test + actions) | ~900–1300 |
| **Mappings** | 2 (create, list) | ~1300–1450 |
| **Schedules** | 6 (CRUD + trigger + executions) | ~1450–1800 |
| **Webhooks** | 1 (Telegram webhook) | ~1800–1950 |
| **UI** | 2 (index, static) | ~1950–2114 |

## Existing `app.state` Usage (MUST preserve)

| Line | Usage | Code |
|------|-------|------|
| 341 | **Set** in lifespan startup | `app.state.live_hub = manager._live_hub` |
| 370–371 | **Check + shutdown** in lifespan | `if hasattr(app.state, 'live_hub'): await app.state.live_hub.shutdown()` |
| 972 | **Access** in SSE endpoint | `live_hub: LiveEventHub = request.app.state.live_hub` |

New `app.state` attributes must be added alongside `live_hub` without disrupting it.

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Create `daemon/routers/agents.py` | Extract 3 agent endpoints from `api.py` | `daemon/routers/agents.py` (new) |
| 2 | Create `daemon/routers/instances.py` | Extract 6 instance endpoints | `daemon/routers/instances.py` (new) |
| 3 | Create `daemon/routers/messages.py` | Extract 3 message endpoints (including `send_message` used by `tests/unit/test_vision.py`) | `daemon/routers/messages.py` (new) |
| 4 | Create `daemon/routers/sources.py` | Extract 7 source endpoints | `daemon/routers/sources.py` (new) |
| 5 | Create `daemon/routers/mappings.py` | Extract 2 mapping endpoints | `daemon/routers/mappings.py` (new) |
| 6 | Create `daemon/routers/schedules.py` | Extract 6 schedule endpoints | `daemon/routers/schedules.py` (new) |
| 7 | Create `daemon/routers/webhooks.py` | Extract 1 webhook endpoint | `daemon/routers/webhooks.py` (new) |
| 8 | Migrate globals to `app.state` | Move 8 module-level globals to FastAPI's `app.state` pattern, **coexisting** with existing `app.state.live_hub`. Add new attributes in the same lifespan startup function. | `daemon/api.py`, all new routers |
| 9 | Use `parse_utc_datetime` in routers | Replace all 25 datetime parsing occurrences in the extracted routers | All new routers |
| 10 | Use HTTPException helpers | Replace verbose HTTPException construction with utility helpers | All new routers |
| 11 | Slim down `api.py` | Reduce to app factory (~150 lines): app creation, middleware, CORS, error handlers, lifespan, router includes | `daemon/api.py` |
| 12 | Update startup/shutdown handlers | Add all new `app.state` attributes in existing lifespan startup; clear in shutdown; **preserve `live_hub` logic exactly** | `daemon/api.py` |
| 13 | Handle `send_message` test import | `tests/unit/test_vision.py` imports `send_message` from `daemon.api`. After extraction, add re-export in `api.py`: `from daemon.routers.messages import send_message`. **OR** update tests to import from `daemon.routers.messages` directly. | `daemon/api.py`, `tests/unit/test_vision.py` |
| 14 | Verify all endpoint paths unchanged | Run the same endpoint listing command from pre-flight and diff against baseline | — |

## Key Files
- `daemon/api.py` — Becomes slim app factory (~150 lines)
- `daemon/routers/agents.py` (new) — Agent endpoints
- `daemon/routers/instances.py` (new) — Instance endpoints
- `daemon/routers/messages.py` (new) — Message endpoints
- `daemon/routers/sources.py` (new) — Source endpoints
- `daemon/routers/mappings.py` (new) — Mapping endpoints
- `daemon/routers/schedules.py` (new) — Schedule endpoints
- `daemon/routers/webhooks.py` (new) — Webhook endpoints
- `daemon/routers/__init__.py` — Update exports
- `tests/unit/test_vision.py` — Update import of `send_message` (lines 705, 742)
- `tests/test_spawn_instance_instructive_errors.py` — Already updated in Phase 1

## Constraints
- All HTTP response shapes must be identical (same status codes, same JSON bodies)
- All URL paths must be identical (no path changes)
- Middleware, CORS, error handlers must remain in `api.py`
- The `create_app()` function signature must not change
- Startup/shutdown event order must be preserved
- `app.state.live_hub` usage must be preserved exactly (lines 341, 370–371, 972)
- Tests that hit API endpoints should pass without modification (except import path updates for `send_message`)

## Detailed Implementation Notes

### Router Pattern (for each new router)
```python
"""Router for [domain] endpoints."""

from fastapi import APIRouter, Depends, Request
from daemon.utils import parse_utc_datetime, raise_not_found

router = APIRouter()

def _get_manager(request: Request) -> "InstanceManager":
    return request.app.state.manager

@router.get("/api/agents")
async def list_agents(manager = Depends(_get_manager)):
    ...
```

### `app.state` Migration (coexisting with existing pattern)
```python
# In api.py lifespan startup — ADD to existing startup, don't replace
@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Existing live_hub setup (PRESERVE) ---
    app.state.live_hub = manager._live_hub
    # --- NEW: migrate other globals to app.state ---
    app.state.manager = manager
    app.state.start_time = start_time
    app.state.credential_manager = credential_manager
    app.state.job_queue_service = job_queue_service
    app.state.job_processor = job_processor
    app.state.job_queue_mgmt_service = job_queue_mgmt_service
    app.state.retry_scheduler = retry_scheduler
    app.state.dispatch_event_bus = dispatch_event_bus
    
    yield
    
    # --- Existing live_hub shutdown (PRESERVE) ---
    if hasattr(app.state, 'live_hub'):
        await app.state.live_hub.shutdown()
```

### `send_message` Test Import Handling
```python
# In api.py, add re-export for backward compatibility:
from daemon.routers.messages import send_message as send_message  # noqa: F401

# OR update tests directly:
# tests/unit/test_vision.py lines 705, 742:
#   from daemon.api import send_message  →  from daemon.routers.messages import send_message
```
**Recommendation**: Update tests directly — fewer indirection layers. Verify no other files import `send_message` from `daemon.api`.

### `api.py` Target Structure (post-refactor, ~150 lines)
```python
"""FastAPI application factory."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from daemon.routers import agents, instances, messages, sources, mappings, schedules, webhooks
from daemon.routers import jobs, projects  # existing (pre-refactor)

# Backward-compat re-exports
from daemon.utils import validate_agent_id  # noqa: F401 — tests import from here

def create_app() -> FastAPI:
    app = FastAPI(...)
    
    # CORS, middleware, error handlers
    app.add_middleware(CORSMiddleware, ...)
    
    # Mount routers
    app.include_router(agents.router)
    app.include_router(instances.router)
    app.include_router(messages.router)
    app.include_router(sources.router)
    app.include_router(mappings.router)
    app.include_router(schedules.router)
    app.include_router(webhooks.router)
    app.include_router(jobs.router)
    app.include_router(projects.router)
    
    # Lifespan (startup/shutdown)
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # ... app.state setup + live_hub
        yield
        # ... cleanup + live_hub shutdown
    
    return app
```

### Extraction Order (incremental, test after each)
1. **Webhooks** (1 endpoint, simplest)
2. **Agents** (3 endpoints, simple)
3. **Mappings** (2 endpoints, simple)
4. **Messages** (3 endpoints, test import to handle)
5. **Instances** (6 endpoints, medium complexity)
6. **Schedules** (6 endpoints, medium complexity)
7. **Sources** (7 endpoints, most complex)

After each extraction: run tests, verify app loads, check endpoint list matches baseline.

## Deliverables
- [ ] 7 new router files created in `daemon/routers/`
- [ ] `api.py` reduced to ~150 lines (app factory + middleware + lifespan)
- [ ] All 8 globals migrated to `app.state` (coexisting with existing `live_hub`)
- [ ] All 25 datetime parsing calls replaced with utility
- [ ] All HTTPException patterns replaced with helpers
- [ ] All existing URL paths and response shapes preserved
- [ ] `send_message` test import handled (re-export or test update)
- [ ] `validate_agent_id` re-export preserved in `api.py`
- [ ] Endpoint path list matches pre-flight baseline
- [ ] Full test suite passes
