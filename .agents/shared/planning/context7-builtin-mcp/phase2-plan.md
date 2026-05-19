# Phase 2: Registry Integration & npx Availability

## Objective
Register `Context7ServerDefinition` in the `BuiltinServerRegistry` so it's auto-bootstrapped on daemon startup. Add graceful handling for when `npx` is not available on the host system.

## Coupling
- **Depends on**: Phase 1 (Context7 server definition class)
- **Coupling type**: tight — imports the class from Phase 1's file
- **Shared files with other phases**: `daemon/mcp/builtin_servers/__init__.py` (modified)
- **Shared APIs/interfaces**: `BuiltinServerRegistry.register()`
- **Why this coupling**: Registration requires the class to exist

## Context
- Registry is in `daemon/mcp/builtin_servers/__init__.py`
- Current registration pattern (lines 57-60):
  ```python
  from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition
  _registry.register(WebFetchServerDefinition())
  ```
- Bootstrap is in `daemon/manager.py:540-605` — already iterates all registered definitions
- No changes needed to bootstrap logic — it's fully generic

## Tasks

| # | Task | Details | Key Files |
|---|------|---------|-----------|
| 1 | Add Context7 import and registration | Import `Context7ServerDefinition` and register it in `__init__.py`, following the existing pattern | `daemon/mcp/builtin_servers/__init__.py` |
| 2 | Verify npx fallback behavior | Confirm that the existing error handling in `McpConnectionManager._create_stdio_session()` and `McpService._discover_server_tools()` handles `npx` not found gracefully (subprocess `FileNotFoundError`) | `daemon/mcp/connection_manager.py`, `daemon/services/mcp_service.py` |
| 3 | (Optional) Add npx availability log at bootstrap | If deemed useful, log an info/warning if `npx` is not found on PATH at bootstrap time. This is purely informational — the server entry should still be created | `daemon/mcp/builtin_servers/context7.py` or `daemon/manager.py` |

## Key Files
- `daemon/mcp/builtin_servers/__init__.py` — Add registration (2 lines)
- `daemon/mcp/connection_manager.py` — Verify existing error handling (read-only)
- `daemon/services/mcp_service.py` — Verify existing error handling (read-only)

## Implementation Reference

### Change to `__init__.py`

Add after the existing WebFetch registration (lines 57-60):

```python
# Register built-in server definitions
from daemon.mcp.builtin_servers.webfetch import WebFetchServerDefinition
from daemon.mcp.builtin_servers.context7 import Context7ServerDefinition

_registry.register(WebFetchServerDefinition())
_registry.register(Context7ServerDefinition())
```

### npx Availability (Optional Enhancement)

If we want proactive logging, add a `check_prerequisites()` method to `BuiltinServerDefinition` (default: no-op) and override in `Context7ServerDefinition`:

```python
def check_prerequisites(self) -> list[str]:
    """Check if npx is available. Returns list of warnings."""
    import shutil
    warnings = []
    if not shutil.which("npx"):
        warnings.append(
            "npx is not found on PATH. Context7 requires Node.js/npx. "
            "Install Node.js: https://nodejs.org/"
        )
    return warnings
```

Then in `_bootstrap_builtin_servers()`, call `check_prerequisites()` and log warnings. **However**, this is optional — the existing error handling is sufficient. Consider deferring this to avoid modifying the ABC.

**Recommendation**: Skip the `check_prerequisites()` enhancement for now. The existing fault-tolerant bootstrap + connection error handling is sufficient. Add it later if users report confusion.

## Constraints
- Minimal changes to `__init__.py` — just 2 lines (import + register)
- No changes to `BuiltinServerDefinition` ABC unless prerequisite checking is desired
- No changes to bootstrap logic in `manager.py`
- Existing tests must still pass

## Deliverables
- [ ] `daemon/mcp/builtin_servers/__init__.py` registers Context7
- [ ] `get_registry().get_by_name("context7")` returns the definition
- [ ] `get_registry().get_all()` returns 2 definitions (webfetch + context7)
- [ ] Existing `test_builtin_mcp_servers.py` and `test_webfetch_builtin.py` still pass
