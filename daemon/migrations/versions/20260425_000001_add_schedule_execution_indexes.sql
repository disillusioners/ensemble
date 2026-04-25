-- Migration: Add indexes to schedule_executions table for query optimization
-- Created: 2026-04-25
-- Author: system
-- Description: Add triggered_at index and composite (schedule_id, status) index
--              to improve scheduler query performance. Phase 1 of schedule optimization.

-- UP

-- Index on triggered_at for time-range queries (e.g., "executions in last hour")
CREATE INDEX IF NOT EXISTS idx_schedule_executions_triggered_at ON schedule_executions(triggered_at);

-- Composite index on (schedule_id, status) for filtering executions by schedule and status
CREATE INDEX IF NOT EXISTS idx_schedule_executions_schedule_id_status ON schedule_executions(schedule_id, status);

-- DOWN

DROP INDEX IF EXISTS idx_schedule_executions_triggered_at;
DROP INDEX IF EXISTS idx_schedule_executions_schedule_id_status;
