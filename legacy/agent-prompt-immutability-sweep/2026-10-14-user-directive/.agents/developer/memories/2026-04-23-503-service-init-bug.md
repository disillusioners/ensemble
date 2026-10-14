# 503 Service Initialization Bug After Refactoring

**Date:** 2026-04-23
**Project:** agents-ensemble

## Bug
After 6-phase refactoring, `/api/jobs` and `/api/projects` returned 503 "Service not initialized".

## Root Cause
During refactoring:
- Phase 3 migrated globals to `app.state` pattern
- Phase 5 replaced manual `set_X_service()`/`get_X_service()` with `create_service_dependency()` 
- Phase 3 tidy removed `_setup_router_dependencies()` — the ONLY place calling setters

Result: Services stored on `app.state` but module-level setters in routers never called → `Depends()` returned None → 503.

## The Two Inconsistent Patterns
1. **`Depends()` with module-level getters** — projects, queues, dlq, jobs_crud (needs setter calls)
2. **`request.app.state` direct access** — instances, messages, sources, schedules, mappings, webhooks

## Fix
Added missing setter calls in `api.py` lifespan context manager:
- `set_job_queue_service(job_queue_service)` for jobs_crud router
- `set_project_repository(manager._project_repository)` for projects router
- `set_job_queue_mgmt_service(job_queue_mgmt_service)` for projects and queues routers
- `get_dead_letter_svc.set_service(dead_letter_service)` for jobs_crud router

## Key Lesson
When two patterns coexist (app.state + module-level setters), BOTH must be wired. Removing `_setup_router_dependencies()` broke the module-level setter path. Always audit ALL routers for their service access pattern after refactoring initialization.
