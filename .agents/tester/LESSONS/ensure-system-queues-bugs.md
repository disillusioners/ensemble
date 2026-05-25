# Lessons Learned: Ensure System Queues Testing

## Bug: Queues Router Project Repository Not Initialized
**Date**: 2026-05-25
**Commit**: a7c2851
**Severity**: High (endpoint returns 503 without fix)

### Problem
The `POST /api/projects/{project_id}/queues/ensure-system` endpoint in `daemon/routers/queues.py` uses its own project repository dependency injection via `get_project_repository()`. However, this dependency was never initialized during app startup in `daemon/api.py`. This caused every call to return `503 {"error": "Project repository not initialized"}`.

### Root Cause
When the queues router was extracted as a separate module, its project repository dependency was not wired up in the main app startup sequence. The projects router's repository WAS initialized, but the queues router's was not.

### Fix
Added 3 lines in `daemon/api.py` to initialize the queues router's project repository alongside the existing projects router initialization.

### Lesson
**When adding new routers with their own dependencies**, always check that ALL dependency injection functions are initialized during app startup. A router that works in unit tests (where dependencies are mocked) may fail in production if the real app doesn't initialize those dependencies.

---

## Bug: Project Delete Cascade Orphaned Tables
**Date**: 2026-05-25
**Commit**: 1ce9a04

### Problem
When deleting a project, in-memory instance cleanup happened AFTER database deletion, so instances couldn't be found. Also missing cascade deletes for task, event, and message_queue tables.

### Lesson
**Cleanup order matters**: Always clean up in-memory state BEFORE deleting from database, so the cleanup code can find what it needs to clean.
