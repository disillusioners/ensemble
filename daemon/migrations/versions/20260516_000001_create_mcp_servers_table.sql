-- Migration: create_mcp_servers_table
-- Created: 2026-05-16
-- Author: system
-- Description: Create mcp_servers table for MCP server configuration storage

-- UP
CREATE TABLE IF NOT EXISTS mcp_servers (
    id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL UNIQUE,
    description VARCHAR,
    config JSON,
    is_active BOOLEAN DEFAULT 1,
    created_at VARCHAR,
    updated_at VARCHAR
);
CREATE INDEX IF NOT EXISTS ix_mcp_servers_name ON mcp_servers(name);

-- DOWN
DROP TABLE IF EXISTS mcp_servers;
