# Quick Fix: MCP Test Connection — SSRF Validation Order + Indentation

**Date**: 2026-05-21
**Branch**: feature/mcp-test-button
**Commit**: 75bc70c

## Issue 1: SSRF Validation Order Bug

**File**: `daemon/mcp/config.py` lines 44-56
**Root Cause**: `is_link_local` check was placed AFTER `is_private` check. Python's `ipaddress` module marks IPv6 link-local addresses (fe80::/10) as BOTH link_local AND private. With `MCP_ALLOW_LOCAL=true`, the `is_private` check would return `False` (allowed), and link-local would never be checked — allowing link-local access when it should always be blocked.
**Fix**: Moved `is_link_local` check before `is_private` check. Link-local IPs are always blocked regardless of `allow_local` flag.

## Issue 2: Indentation Bug

**File**: `daemon/routers/mcp_servers.py` lines 101-131
**Root Cause**: Code block was indented with 12 spaces instead of 4, making it part of the wrong scope. The session cleanup and tool listing was unreachable at the correct scope level.
**Fix**: Corrected indentation from 12 spaces to 4 spaces.

## Lesson Learned
When testing SSRF protection, always test the interaction between checks and flag values (like `allow_local`). Check order matters when Python's ipaddress marks addresses with multiple properties.
