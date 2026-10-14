# Phase 3 Queue Management Review — Key Findings

**Date:** 2026-04-09
**Branch:** feature/job-queue-management
**Sessions:** 3 parallel (backend, frontend, integration)

## Critical Issues Found

### 1. Security — IDOR in Jobs Router (🔴)
- `jobs.py:212-268` — `list_jobs` accepts `queue_id` without validating it belongs to `project_id`
- `jobs.py:103-166` — `create_job` accepts `queue_id` without `project_id`, bypassing ownership checks
- Root cause: `job_queue_service.py:119-131` — `elif queue_id and project_id` branch never executes when `project_id` is None

### 2. Missing Auto-provision on Project Creation (🔴)
- `projects.py` — System queues only provisioned at daemon startup, not on project creation
- Fix: Wire JobQueueMgmtService into projects router and call auto_provision after project creation

### 3. Missing Queue Display in Job Detail Drawer (🔴)
- `job-detail-drawer.component.html` — No queue_id/queue_name shown anywhere
- Users cannot see which queue a job belongs to

## Warnings

- `updateJobFromSse()` doesn't update queue_id in frontend
- `delete_queue` discards reassigned_jobs count
- `_queue_to_response` hardcodes active_jobs=0, pending_jobs=0
- Generic exception handler leaks str(e) to clients
- Dead ng-zorro SCSS in job-create-dialog.scss

## Patterns to Remember
- Angular Material only — no ng-zorro imports found in TypeScript
- Signal-based state used correctly throughout frontend
- SSE events properly include queue_id on backend
