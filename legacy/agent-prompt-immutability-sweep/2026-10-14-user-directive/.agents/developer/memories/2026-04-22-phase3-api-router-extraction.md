# Phase 3 — API Router Extraction

## What was done
Extracted all endpoints from monolithic `daemon/api.py` (2095 lines) into 7 dedicated router files in `daemon/routers/`.

## Results
- **api.py**: 2095 → 544 lines (74% reduction)
- **7 new router files**: agents.py, instances.py, messages.py, sources.py, mappings.py, schedules.py, webhooks.py
- **All 438 unit tests pass**
- **Commit**: `60c213c` — `refactor: Phase 3 — extract api.py endpoints into dedicated routers`

## Key Learnings

### app.state migration pattern
- 8 module-level globals migrated to `app.state` in lifespan startup
- `app.state.live_hub` already existed — new attributes must coexist
- Routers access state via `request.app.state.X` or module-level setters (set by `_setup_router_dependencies`)

### Router pattern
- Follow existing routers (jobs.py) as pattern reference
- Each router uses `APIRouter()` with `Depends()` for service injection
- Routers import from `daemon.utils`, `daemon.models`, `daemon.constants` — NEVER from `daemon.api`

### Backward compatibility
- `validate_agent_id` re-exported in api.py (from Phase 1)
- `send_message` re-exported in api.py for test compatibility
- Tests updated to import `send_message` from `daemon.routers.messages`
- Mock patches updated to target `daemon.routers.messages._manager`

### Remaining in api.py (justified)
- Lifespan initialization (198 lines) — infrastructure, not endpoints
- `_setup_router_dependencies` (64 lines) — wiring code
- `SelectiveAccessLogMiddleware` (67 lines) — middleware infrastructure
- Could be further extracted to `daemon/middleware.py` in future phase

### Deduplication complete
- `validate_instance_mode` moved to `daemon/utils.py`, imported directly in both sources.py and schedules.py
- No local wrappers remain
