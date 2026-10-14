# Phase 1: Job Queue Schema & Migration

## What was done
- Added QueueType enum (FIFO, PARALLEL) to models.py
- Created JobQueue SQLModel for job_queues table with all fields
- Added queue_id FK to JobItem
- Created migration: 20260409_000001_add_job_queues_table.sql
- Added Pydantic schemas: JobQueueResponse, JobQueueCreateRequest, JobQueueUpdateRequest, JobQueueListResponse, JobQueueNotFoundResponse

## Key patterns
- QueueType stored as string in DB (CHECK constraint), enum in Python
- queue_name_lower for case-insensitive uniqueness at DB level
- System queue IDs: sys-fifo-{project_id}, sys-parallel-{project_id}
- FIFO queues force concurrency_limit=1 at app level (model_validator)
- Migration does DELETE all jobs first (clean slate approach)
- Reserved names: system_fifo_queue, system_parallel_queue

## Files modified
- daemon/repositories/job_queue/models.py
- daemon/routers/schemas.py
- daemon/migrations/versions/20260409_000001_add_job_queues_table.sql (new)

## Commit: 29220ea on feature/job-queue-management
