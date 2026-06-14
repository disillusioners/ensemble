-- Migration: create db_connections table
-- Created: 2026-06-15
-- Author: system
-- Description:
--   Phase 1 (Connection Registry Layer) of the Database Tool Category.
--   Stores named database connection configurations (host, port, user,
--   credentials) that agents can reference by ``connection_name`` when
--   invoking Database-category tools. Credentials are persisted as
--   opaque encrypted strings — encryption/decryption is the tool layer's
--   responsibility, not the repository's.
--
--   Schema mirrors ``daemon/repositories/db_connection/models.py``
--   ``DbConnectionConfig`` (SQLModel). The ``connection_name`` column is
--   UNIQUE with an index to support fast lookups and to enforce the
--   "one row per named connection" contract.

-- UP

CREATE TABLE IF NOT EXISTS db_connections (
    id              TEXT PRIMARY KEY,
    connection_name TEXT NOT NULL UNIQUE,
    db_type         TEXT NOT NULL,
    host            TEXT NOT NULL,
    port            INTEGER,
    database        TEXT,
    username        TEXT,
    credentials     TEXT,
    ssl_mode        TEXT NOT NULL DEFAULT 'prefer',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_db_connections_connection_name
    ON db_connections(connection_name);

-- DOWN

DROP INDEX IF EXISTS ix_db_connections_connection_name;
DROP TABLE IF EXISTS db_connections;
