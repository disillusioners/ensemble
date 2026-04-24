-- Migration: backfill NULL project_ids to system default project
-- Created: 2026-04-24
-- Author: system
-- Description: Backfill NULL/empty project_ids in job_queue_items and dead_letter_items
--              to the system default project. Phase 3 of system_default_project feature.

-- UP

-- System default project UUID (deterministic: uuid5(NAMESPACE_DNS, "__system_default__"))
-- https://www.iana.org/assignments/urn-namespaces/urn-namespaces.xhtml
-- NAMESPACE_DNS = 6ba7b810-9dad-11d1-80b4-00c04fd430c8
-- Result: 71931ae0-0f25-5fbf-853b-2a78cc978d7e
-- System FIFO queue ID pattern: 'sys-fifo-' || project_id
-- Full queue_id: sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e

-- STEP 1: Ensure system default project exists
-- (idempotent: INSERT OR IGNORE only creates if not exists)
INSERT OR IGNORE INTO projects (
    project_id,
    name,
    project_type,
    status,
    description,
    metadata,
    relationships,
    job_queue_paused,
    created_at,
    updated_at
) VALUES (
    '71931ae0-0f25-5fbf-853b-2a78cc978d7e',
    '__system_default__',
    'system',
    'active',
    'System default project for jobs without an explicit project',
    '{"is_system": true}',
    '{}',
    0,
    datetime('now'),
    datetime('now')
);

-- STEP 2: Ensure system FIFO queue exists for the system default project
-- (idempotent: INSERT OR IGNORE only creates if not exists)
INSERT OR IGNORE INTO job_queues (
    queue_id,
    project_id,
    queue_name,
    queue_name_lower,
    queue_type,
    concurrency_limit,
    is_system,
    is_paused,
    description,
    default_max_retries,
    created_at,
    updated_at
) VALUES (
    'sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e',
    '71931ae0-0f25-5fbf-853b-2a78cc978d7e',
    'system_fifo_queue',
    'system_fifo_queue',
    'fifo',
    1,
    1,
    0,
    'System FIFO queue - default, one job at a time',
    NULL,
    datetime('now'),
    datetime('now')
);

-- STEP 3: Backfill job_queue_items with NULL project_id
-- Idempotent: UPDATE with WHERE ... IS NULL affects 0 rows on subsequent runs
UPDATE job_queue_items
SET project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
WHERE project_id IS NULL;

-- STEP 4: Backfill job_queue_items with empty string project_id (normalization)
-- Handle any edge cases where project_id might be empty string
UPDATE job_queue_items
SET project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
WHERE project_id = '';

-- STEP 5: Backfill dead_letter_items with NULL or empty project_id
-- Idempotent: UPDATE with WHERE ... IS NULL affects 0 rows on subsequent runs
UPDATE dead_letter_items
SET project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
WHERE project_id IS NULL OR project_id = '';

-- STEP 6: Assign queue_id to orphaned jobs (system default project but NULL queue_id)
-- These jobs were created after Phase 2 normalization added project_id but before queue assignment
-- Use COALESCE to find the existing queue_id if it exists, or the migration's default queue_id
UPDATE job_queue_items
SET queue_id = COALESCE(
    (SELECT queue_id FROM job_queues WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e' AND queue_name_lower = 'system_fifo_queue'),
    'sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e'
)
WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  AND queue_id IS NULL;

-- STEP 7: Assign queue_id to orphaned dead_letter_items
UPDATE dead_letter_items
SET queue_id = COALESCE(
    (SELECT queue_id FROM job_queues WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e' AND queue_name_lower = 'system_fifo_queue'),
    'sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e'
)
WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  AND queue_id IS NULL;

-- Verification queries (informational only - counts remaining NULL project_ids):
SELECT COUNT(*) FROM job_queue_items WHERE project_id IS NULL;
SELECT COUNT(*) FROM dead_letter_items WHERE project_id IS NULL OR project_id = '';

-- DOWN

-- Note: Down migration cannot restore original NULL values (data loss)
-- This is intentional for this one-way normalization migration
-- We only clean up the queue_id assignments to the system FIFO queue
UPDATE job_queue_items
SET queue_id = NULL
WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  AND queue_id = COALESCE(
    (SELECT queue_id FROM job_queues WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e' AND queue_name_lower = 'system_fifo_queue'),
    'sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  );

UPDATE dead_letter_items
SET queue_id = NULL
WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  AND queue_id = COALESCE(
    (SELECT queue_id FROM job_queues WHERE project_id = '71931ae0-0f25-5fbf-853b-2a78cc978d7e' AND queue_name_lower = 'system_fifo_queue'),
    'sys-fifo-71931ae0-0f25-5fbf-853b-2a78cc978d7e'
  );
