# Phase 6: API Layer — Routes, Routers, SSE Events, Error Codes

## Objective
Rename all session references in `daemon/api.py` and `daemon/routers/` (~3 files): routes from `/sessions` to `/instances`, route handler function names, parameter names, Pydantic schemas, SSE event types, and error codes. This is the HTTP contract layer.

## Context
- **Phases 1-5, 3b, 3c completed**: All backend code renamed (models, repos, manager, events, queue, services, sources, tools)
- API layer is the bridge between backend and frontend — it must reflect all upstream renames
- `daemon/routers/` files (schemas.py, jobs.py, projects.py) also reference session fields
- After this phase, the frontend (Phase 8) can update its API client to match

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | **Rename route paths in daemon/api.py** | Change all route decorators: `/sessions` → `/instances`, `/sessions/{session_id}` → `/instances/{instance_id}`. This affects ~8 route endpoints. | `daemon/api.py` (~1883 lines) |
| 2 | **Rename route handler functions in daemon/api.py** | `create_session`→`create_instance`, `list_sessions`→`list_instances`, `get_session`→`get_instance`, `terminate_session`→`terminate_instance`, `send_message` (keep), `get_messages` (keep), `get_message_status` (keep), `stream_events` (keep). | `daemon/api.py` |
| 3 | **Rename path/query parameters in daemon/api.py** | All `session_id` → `instance_id` in route parameters, query params, request bodies. Update Pydantic model references: `SessionCreate`→`InstanceCreate`, `SessionInfo`→`InstanceInfo`, `SessionListResponse`→`InstanceListResponse`. | `daemon/api.py` |
| 4 | **Rename SSE event types in daemon/api.py** | Update Server-Sent Events to use `instance_id` instead of `session_id` in event payloads. | `daemon/api.py` |
| 5 | **Update error codes in daemon/api.py** | All error responses using `SESSION_NOT_FOUND`→`INSTANCE_NOT_FOUND`, `SESSION_LIMIT_REACHED`→`INSTANCE_LIMIT_REACHED`, `SESSION_TERMINATED`→`INSTANCE_TERMINATED`. | `daemon/api.py` |
| 6 | **Update API router prefix/tags in daemon/api.py** | If router prefix is `prefix="/sessions"`, change to `prefix="/instances"`. Update tags: `tags=["sessions"]` → `tags=["instances"]`. | `daemon/api.py` |
| 7 | **Update SSE source mapping routes in daemon/api.py** | Routes under `/sources/{id}/mappings/` reference session-related types. Update to use `InstanceMapping*` types and `agent_instance_id`. | `daemon/api.py` |
| 8 | **Update daemon/routers/schemas.py** (~238 lines) | Rename fields: `session_id`→`instance_id` in `JobResponse`, `creator_session_id`→`creator_instance_id` in `ProjectResponse`. These are Pydantic schemas for API responses. | `daemon/routers/schemas.py` |
| 9 | **Update daemon/routers/jobs.py** | Rename all `job.session_id`→`job.instance_id` references (~4 occurrences at lines 76, 160, 481, 529). These read the DB model field (renamed in Phase 1). | `daemon/routers/jobs.py` |
| 10 | **Update daemon/routers/projects.py** | Rename `project.creator_session_id`→`project.creator_instance_id` (~1 occurrence at line 56). | `daemon/routers/projects.py` |
| 11 | **Update daemon/routers/__init__.py** | Verify router exports — no session-specific names to change, just verify import paths are correct. | `daemon/routers/__init__.py` |

## Key Files
- `daemon/api.py` — ~1883 lines, all HTTP routes (MAIN FILE)
- `daemon/routers/schemas.py` — ~238 lines, Pydantic response schemas
- `daemon/routers/jobs.py` — job API routes with session_id references
- `daemon/routers/projects.py` — project API routes with creator_session_id
- `daemon/routers/__init__.py` — router exports

## Detailed Route Rename Map (daemon/api.py)

| Old Route | New Route |
|-----------|-----------|
| `POST /api/sessions` | `POST /api/instances` |
| `GET /api/sessions` | `GET /api/instances` |
| `GET /api/sessions/{session_id}` | `GET /api/instances/{instance_id}` |
| `DELETE /api/sessions/{session_id}` | `DELETE /api/instances/{instance_id}` |
| `POST /api/sessions/{session_id}/messages` | `POST /api/instances/{instance_id}/messages` |
| `GET /api/sessions/{session_id}/messages/{message_id}` | `GET /api/instances/{instance_id}/messages/{message_id}` |
| `GET /api/sessions/{session_id}/messages` | `GET /api/instances/{instance_id}/messages` |
| `GET /api/sessions/{session_id}/events` | `GET /api/instances/{instance_id}/events` |

## Detailed Rename Map (daemon/routers/)

### schemas.py
| Old Field | New Field | In Schema |
|-----------|-----------|-----------|
| `session_id: Optional[str]` | `instance_id: Optional[str]` | `JobResponse` |
| `creator_session_id: Optional[str]` | `creator_instance_id: Optional[str]` | `ProjectResponse` |

### jobs.py
| Old | New |
|-----|-----|
| `job.session_id` (4 occurrences) | `job.instance_id` |

### projects.py
| Old | New |
|-----|-----|
| `project.creator_session_id` | `project.creator_instance_id` |

## Constraints
- **This is a breaking API change** — the frontend (Phase 8) must update simultaneously
- Keep OpenAPI schema consistent — operation IDs should match new function names
- SSE event format changes are breaking for any connected clients
- The route renaming must be complete and consistent — no half-renamed routes
- `daemon/routers/schemas.py` field names must match what the frontend expects

## Verification
```bash
# 1. No old route paths in api.py
grep -rn '"/sessions\|/sessions/' daemon/api.py

# 2. No old handler names
grep -rn "async def create_session\|async def list_sessions\|async def get_session\|async def terminate_session" daemon/api.py

# 3. No old parameter names in api + routers
grep -rn "session_id\|creator_session_id\|agent_session_id" daemon/api.py daemon/routers/ | grep -v "db_session"

# 4. New routes exist
grep -c '"/instances\|/instances/' daemon/api.py
grep -c "async def create_instance\|async def list_instances\|async def get_instance\|async def terminate_instance" daemon/api.py

# 5. Router schemas updated
grep -rn "session_id\|creator_session_id" daemon/routers/schemas.py

# 6. Import check
python -c "from daemon.api import app; print('API imports OK')"
```

## Deliverables
- [ ] All 8 session routes renamed to `/instances/` in daemon/api.py
- [ ] All handler functions renamed in daemon/api.py
- [ ] All path/query parameters use `instance_id` in daemon/api.py
- [ ] SSE events use `instance_id` in daemon/api.py
- [ ] Error codes use `INSTANCE_NOT_FOUND`, etc. in daemon/api.py
- [ ] Router tags/prefix updated in daemon/api.py
- [ ] `daemon/routers/schemas.py` — fields renamed
- [ ] `daemon/routers/jobs.py` — job.instance_id references
- [ ] `daemon/routers/projects.py` — creator_instance_id references
- [ ] Grep shows 0 old session route patterns in api.py and routers/
