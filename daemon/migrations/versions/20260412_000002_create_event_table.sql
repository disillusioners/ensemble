-- Migration: create event table
-- Created: 2026-04-12
-- Author: system
-- Description: Create event table for SSE event persistence.
--              NO delivered/delivered_at columns - cursor-based delivery via Last-Event-ID.

-- UP

CREATE TABLE IF NOT EXISTS event (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id         TEXT NOT NULL,
    message_id          TEXT,
    kind                TEXT NOT NULL DEFAULT 'message_received'
                          CHECK(kind IN (
                            'message_received', 'processing_started',
                            'processing_completed', 'processing_failed',
                            'child_completed', 'child_failed',
                            'instance_completed', 'error'
                          )),
    data                TEXT,
    created_at          TEXT NOT NULL
);

-- Index for efficient SSE queries
CREATE INDEX IF NOT EXISTS idx_event_instance_created ON event(instance_id, created_at);

-- DOWN
DROP TABLE IF EXISTS event;
