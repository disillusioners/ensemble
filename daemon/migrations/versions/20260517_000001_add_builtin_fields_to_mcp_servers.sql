-- Migration: add builtin fields to mcp_servers table
-- Created: 2026-05-17
-- Author: system
-- Description: Add is_builtin, config_schema, and config_schema_version columns for built-in MCP servers

-- UP

ALTER TABLE mcp_servers ADD COLUMN is_builtin BOOLEAN DEFAULT 0;
ALTER TABLE mcp_servers ADD COLUMN config_schema JSON;
ALTER TABLE mcp_servers ADD COLUMN config_schema_version VARCHAR DEFAULT '0';

-- DOWN

ALTER TABLE mcp_servers DROP COLUMN config_schema_version;
ALTER TABLE mcp_servers DROP COLUMN config_schema;
ALTER TABLE mcp_servers DROP COLUMN is_builtin;
