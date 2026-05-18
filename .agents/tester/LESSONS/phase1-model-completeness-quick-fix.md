# Quick Fix: Phase 1 Model Completeness Tests

**Date:** 2026-05-18
**Branch:** feature/builtin-mcp-servers
**Commit:** `2f76162`

## Issue
Two existing tests failed after Phase 1 added new model exports and error codes:

1. `test_daemon_models_all_contains_expected_names` — Missing Phase 1 exports in `__all__` check:
   - `ConfigSchemaField`, `BuiltinServerConfigure`, `BuiltinServerTemplate`, `BuiltinTemplateListResponse`

2. `test_error_codes_values` — Missing `BUILTIN_SERVER_PROTECTED` error code

## Root Cause
Phase 1 added new Pydantic models and error codes but didn't update the test completeness assertions that check all expected names are exported.

## Fix
Added the new Phase 1 exports and error code to the test's expected values lists.

## Lesson
When adding new models/enums/constants to existing modules, check if there are "completeness" tests that enumerate all expected names and update them.

## Also Fixed
- `dc2a45c`: Added `modelcontextprotocol>=1.0.1` to `pyproject.toml` — Phase 1 imports `mcp` module which was not listed as a dependency.
