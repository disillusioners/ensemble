# Quick Fix: Missing Repository Export

**Date**: 2026-05-16
**Branch**: feature/mcp-server-crud
**Commit**: `60390b4`

## Issue
When adding a new repository (`daemon/repositories/mcp_server/repository.py`) with a `create_mcp_server_repository` factory function, it was not exported from `daemon/repositories/__init__.py`. This caused an import error when the app tried to use the repository.

## Root Cause
Developer added the repository module but forgot to add the export to the package's `__init__.py`.

## Fix
Added `create_mcp_server_repository` to `daemon/repositories/__init__.py` exports.

## Lesson
When adding a new repository, always:
1. Create the repository module
2. Export the factory function from `__init__.py`
3. Wire it in the router/app initialization
