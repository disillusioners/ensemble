# Lesson: list_context Task Spec Filename Discrepancy

**Date:** 2026-06-16
**Feature:** list_context tool improvement (richer preview + search/filter)

## Issue

The test task specification referenced the service test file as:
```
tests/unit/services/test_context_services.py
```

However, the **actual** file is:
```
tests/unit/services/test_context_tools.py
```

This naming mismatch could cause confusion when running tests from task specs.

## Impact

- Running the spec command literally would have produced a "file not found" error
- The opencode session correctly identified the real filename and used it

## Recommendation

When specifying test paths in task definitions, verify the actual file path exists.
The service layer tests for context tools follow the naming convention:
`tests/unit/services/test_{module_name}.py` where the module is `context_tools.py`.

## Context Files Mapping

| Layer | Source File | Test File |
|-------|------------|-----------|
| Service | `daemon/services/context_tools.py` | `tests/unit/services/test_context_tools.py` |
| Tool | `daemon/tools/context_tools.py` | `tests/unit/tools/test_context_tools.py` |
| MCP | `daemon/mcp/kb_server.py` | `tests/unit/test_mcp_kb_server_context.py` |
