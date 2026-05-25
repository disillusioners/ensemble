# Plan Overview: Ensure System Queues — Project Lifecycle Hooks + Ensure API

## Objective

Guarantee system job queues exist for every project by: (1) auto-creating them on project creation, (2) cleaning up all project-related data on project deletion, and (3) providing an "Ensure System Queues" API + frontend button to repair missing queues on demand.

## Scope Assessment

**MEDIUM-LARGE** — Spans 3 features across backend services, routers, and frontend UI. Each feature is self-contained but they share the queue provisioning logic.

## Context

- **Project**: agents-ensemble
- **Working Directory**: `/Users/nguyenminhkha/All/Code/opensource-projects/agents-ensemble`
- **Key Service**: `daemon/services/job_queue_mgmt_service.py` — contains `auto_provision_system_queues()` (idempotent, check-then-create)
- **4 System Queues**: `system_fifo_queue` (FIFO, 1), `system_parallel_queue` (PARALLEL, 5), `system_kb_fifo_queue` (FIFO, 1), `system_defer_queue` (DEFER, 1)
- **Project creation** already calls `auto_provision_system_queues()` via `BackgroundTasks` at `daemon/routers/projects.py:229-234`
- **No project DELETE endpoint** exists — `repository.delete()` exists but is not exposed via API
- **Orphaned data on delete**: instances, job_queues, job_queue_items, job_locks, dead_letter_items have `project_id` columns but NO FK CASCADE

## Key Findings from Exploration

### What Already Works
- ✅ `auto_provision_system_queues()` is idempotent — uses check-then-create per queue name
- ✅ Project creation already triggers system queue provisioning via `BackgroundTasks`
- ✅ `RESERVED_QUEUE_NAMES` set protects system queue names from user modification

### What's Missing
- ❌ No "ensure system queues" API endpoint (repair missing queues for existing projects)
- ❌ No project DELETE endpoint (repository method exists, not exposed)
- ❌ Project `delete()` doesn't clean up: instances, job_queues, job_items, job_locks, dead_letter_items
- ❌ No `ensureSystemQueues()` in frontend `queue.service.ts`
- ❌ No button in frontend queue-list header for triggering ensure

### Tables with `project_id` (cleanup targets)

| Table | CASCADE DELETE | Currently Cleaned | Action Needed |
|-------|:-:|:-:|---|
| `project_tags` (junction) | ✅ | ✅ | None |
| `project_shortnames` (junction) | ✅ | ✅ | None |
| `project_metadata_records` | ✅ | ✅ | None |
| `project_history` | ✅ | ✅ | None (implicit via FK) |
| `critical_notes` | ✅ | ✅ | None (implicit via FK) |
| **`instances`** | ❌ | ❌ | **Must add cleanup** |
| **`job_queues`** | ❌ | ❌ | **Must add cleanup** |
| **`job_queue_items`** | ❌ | ❌ | **Must add cleanup** |
| **`job_locks`** | ❌ | ❌ | **Must add cleanup** |
| **`dead_letter_items`** | ❌ | ❌ | **Must add cleanup** |

## Phase Index

| Phase | Name | Objective | Dependencies | Coupling | Est. Time |
|-------|------|-----------|-------------|----------|-----------|
| 1 | Project Delete Cleanup | Add DELETE endpoint + cleanup all project-related data | None | — | 2-3h |
| 2 | Ensure System Queues API | Add POST endpoint to ensure/repair system queues | None | independent | 1-2h |
| 3 | Frontend Ensure Button | Add "Ensure System Queues" button in queue-list UI | Phase 2 | loose | 1-2h |

### Coupling Assessment

| Phase Pair | Coupling | Reasoning |
|-----------|----------|-----------|
| Phase 1 → Phase 2 | **independent** | Different endpoints, different files. Phase 1 touches project router + repo; Phase 2 touches queue router + mgmt service |
| Phase 2 → Phase 3 | **loose** | Phase 3 calls the API endpoint from Phase 2. Only needs the endpoint URL/response shape, not implementation |

**Phases 1 and 2 can run in parallel.** Phase 3 depends on Phase 2's API contract only.

## Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Deleting instances with active jobs | high | Stop/cancel active jobs before deletion; check for running instances |
| Race condition: job enqueued while project is being deleted | medium | Pause project queue first, then delete; use DB transaction |
| Frontend button confusion: users clicking "Ensure" unnecessarily | low | Button clearly labeled; show toast with "all queues exist" or "created N missing queues" |
| SQLite foreign key enforcement may be off | medium | Explicit manual cleanup in delete method rather than relying on CASCADE |
| Deleting a project that other agents are using | high | Add safety check: refuse delete if active instances exist |

## Success Criteria

- [ ] `POST /api/projects/{project_id}/ensure-system-queues` creates missing system queues and returns status
- [ ] `DELETE /api/projects/{project_id}` deletes project + all related data (queues, jobs, locks, dead letters, instances)
- [ ] Frontend queue-list header has an "Ensure System Queues" button with visual feedback
- [ ] `auto_provision_system_queues()` remains the single source of truth for system queue provisioning
- [ ] No orphaned data after project deletion

## Tracking

- Created: 2025-05-25
- Last Updated: 2025-05-25
- Status: draft
